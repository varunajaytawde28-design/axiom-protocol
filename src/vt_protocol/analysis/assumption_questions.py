"""Question Generator — bounded multiple-choice questions for domain assumptions.

Transforms detected DomainAssumptions into actionable questions using a template
bank keyed by pattern_id. Every question uses BOUNDED MULTIPLE CHOICE format
(never yes/no) to avoid acquiescence bias.

Design:
- Template bank covers all 19 detection patterns from assumptions.py
- Generic fallback templates keyed by AssumptionCategory for unknown patterns
- Placeholder extraction via regex on code_evidence snippets
- Optional LLM enrichment to refine template questions with domain context

Usage:
    from vt_protocol.analysis.assumption_questions import generate_question
    assumption = generate_question(assumption)  # populates .question and .options
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from vt_protocol.decisions.models import AssumptionCategory, DomainAssumption

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template Bank — keyed by pattern_id (from assumptions.py detection rules)
# ---------------------------------------------------------------------------

QUESTION_TEMPLATES: dict[str, dict] = {
    # --- DATA_SCOPE patterns ---
    "single_source_write": {
        "question": (
            "The code restricts writes to '{table}' from {function}(). "
            "Which matches your business reality?"
        ),
        "options": [
            "A) Correct — only {function}() should write to {table}",
            "B) Incomplete — external webhooks/APIs should also write to {table}",
            "C) Incomplete — batch imports/CSV uploads should also write to {table}",
            "D) Wrong — multiple services must write to {table}",
            "E) I need more context before deciding",
        ],
    },
    "narrow_where_clause": {
        "question": (
            "This query filters {table} with WHERE {condition}. "
            "Does this cover all needed data?"
        ),
        "options": [
            "A) Correct — only this subset is ever needed",
            "B) Incomplete — other filter values will be needed later",
            "C) Wrong — the query should return all rows without filtering",
            "D) I need more context before deciding",
        ],
    },
    "single_table_query": {
        "question": (
            "This code queries {table} without joins. "
            "Is all needed data in one table?"
        ),
        "options": [
            "A) Yes — {table} is self-contained",
            "B) No — should join with related tables for complete data",
            "C) Depends on the use case — sometimes joins are needed",
            "D) I need more context before deciding",
        ],
    },
    "hardcoded_table_name": {
        "question": (
            "This code references table '{table}' by hardcoded name. "
            "Is this table name stable across environments?"
        ),
        "options": [
            "A) Yes — this table name is fixed and permanent",
            "B) No — table names vary by environment or tenant",
            "C) Should use a configurable table name mapping",
            "D) I need more context before deciding",
        ],
    },
    # --- TEMPORAL patterns ---
    "hardcoded_date": {
        "question": (
            "This code uses a hardcoded date {date_value}. "
            "Is this boundary permanent?"
        ),
        "options": [
            "A) Yes — this is a fixed business cutoff date",
            "B) No — this should be configurable or computed dynamically",
            "C) This is a temporary value that should be updated periodically",
            "D) I need more context before deciding",
        ],
    },
    "no_migration_check": {
        "question": (
            "This code does not check for schema migration state before "
            "accessing '{table}'. Is the schema guaranteed to be current?"
        ),
        "options": [
            "A) Yes — migrations always run before this code executes",
            "B) No — this code can run against an older schema version",
            "C) Schema changes are rare enough that this is acceptable risk",
            "D) I need more context before deciding",
        ],
    },
    # --- ACCESS patterns ---
    "single_role_access": {
        "question": (
            "This endpoint requires role '{role}'. "
            "Should other roles also have access?"
        ),
        "options": [
            "A) Correct — only {role} should access this",
            "B) Incomplete — other roles (e.g., superadmin, service accounts) need access",
            "C) Wrong — this should be permission-based, not role-based",
            "D) I need more context before deciding",
        ],
    },
    "single_endpoint": {
        "question": (
            "Resource '{resource}' is only accessible via one endpoint. "
            "Is that sufficient?"
        ),
        "options": [
            "A) Yes — one access path is intentional",
            "B) No — other endpoints should also expose this data",
            "C) There should be both public and internal access paths",
            "D) I need more context before deciding",
        ],
    },
    "no_multitenancy": {
        "question": (
            "This code accesses '{resource}' without tenant isolation. "
            "Is this a single-tenant system?"
        ),
        "options": [
            "A) Yes — single-tenant deployment only",
            "B) No — must add tenant filtering for multi-tenant safety",
            "C) Currently single-tenant but multi-tenancy is planned",
            "D) I need more context before deciding",
        ],
    },
    # --- COMPLETENESS patterns ---
    "incomplete_enum": {
        "question": (
            "This code handles statuses: {values}. "
            "Are these all possible states?"
        ),
        "options": [
            "A) Yes — these are the only valid states",
            "B) No — additional states will be needed (e.g., {suggestion})",
            "C) The set of states should be extensible",
            "D) I need more context before deciding",
        ],
    },
    "no_null_handling": {
        "question": (
            "This code accesses '{key}' without null checking. "
            "Is the value always present?"
        ),
        "options": [
            "A) Yes — this field is always populated",
            "B) No — it can be null/missing and needs handling",
            "C) It's populated now but may not be after schema changes",
            "D) I need more context before deciding",
        ],
    },
    "no_pagination": {
        "question": (
            "This query fetches all results without pagination. "
            "Will the result set stay small?"
        ),
        "options": [
            "A) Yes — this table will always have <1000 rows",
            "B) No — the table will grow and needs pagination",
            "C) Depends on deployment — small in dev, large in prod",
            "D) I need more context before deciding",
        ],
    },
    "no_error_path": {
        "question": (
            "This code path in {function}() has no error/exception handling. "
            "Can this operation fail in production?"
        ),
        "options": [
            "A) No — inputs are validated upstream, failure is impossible here",
            "B) Yes — network/IO errors are possible and need handling",
            "C) Yes — but a global error handler catches exceptions from here",
            "D) I need more context before deciding",
        ],
    },
    # --- CONFIGURATION patterns ---
    "env_no_fallback": {
        "question": (
            "This code reads os.environ['{var_name}'] without a default. "
            "Is this env var guaranteed?"
        ),
        "options": [
            "A) Yes — deployment always sets {var_name}",
            "B) No — needs a sensible default for local development",
            "C) This should fail loudly if missing (current behavior is correct)",
            "D) I need more context before deciding",
        ],
    },
    "hardcoded_path": {
        "question": (
            "This code uses hardcoded path '{path}'. "
            "Does this path exist in all environments?"
        ),
        "options": [
            "A) Yes — this path is standard across all deployments",
            "B) No — should be configurable via env var or config file",
            "C) This is a development convenience that should be fixed before production",
            "D) I need more context before deciding",
        ],
    },
    "hardcoded_port": {
        "question": (
            "This code uses hardcoded port {port}. "
            "Is this port fixed across all environments?"
        ),
        "options": [
            "A) Yes — this is a well-known port that never changes",
            "B) No — should be configurable per environment",
            "C) This is fine for local dev but needs configuration in production",
            "D) I need more context before deciding",
        ],
    },
    # --- FRAMEWORK patterns ---
    "orm_no_loading_strategy": {
        "question": (
            "This ORM relationship uses default loading. "
            "Is that intentional?"
        ),
        "options": [
            "A) Yes — default (lazy) loading is fine for this use case",
            "B) No — should use eager loading to avoid N+1 queries",
            "C) Should use explicit loading strategy based on access patterns",
            "D) I need more context before deciding",
        ],
    },
    "no_cascade_behavior": {
        "question": (
            "This foreign key has no explicit cascade/delete behavior. "
            "What should happen on parent delete?"
        ),
        "options": [
            "A) Default cascade behavior is correct",
            "B) Should CASCADE (delete children with parent)",
            "C) Should SET NULL or PROTECT (preserve children)",
            "D) I need more context before deciding",
        ],
    },
    "framework_version_dep": {
        "question": (
            "This code depends on framework '{framework}' behavior that may "
            "change between versions. Is the version pinned?"
        ),
        "options": [
            "A) Yes — version is pinned and we control upgrades",
            "B) No — should pin the version to avoid breakage",
            "C) The specific behavior used is stable across versions",
            "D) I need more context before deciding",
        ],
    },
}


# ---------------------------------------------------------------------------
# Generic Fallback Templates — keyed by AssumptionCategory
# ---------------------------------------------------------------------------

_GENERIC_TEMPLATES: dict[AssumptionCategory, dict] = {
    AssumptionCategory.DATA_SCOPE: {
        "question": (
            "This code makes a data scope assumption: {summary}. "
            "Does the current scope match your business needs?"
        ),
        "options": [
            "A) The current scope is correct and complete",
            "B) The scope is too narrow — additional data sources are needed",
            "C) The scope is too broad — should be restricted further",
            "D) The scope is correct now but will change as requirements evolve",
            "E) I need more context before deciding",
        ],
    },
    AssumptionCategory.TEMPORAL: {
        "question": (
            "This code makes a time-related assumption: {summary}. "
            "Is the current temporal behavior correct?"
        ),
        "options": [
            "A) Correct — the current time handling matches business rules",
            "B) Incorrect — should use dynamic dates or time windows",
            "C) Partially correct — works now but needs periodic updating",
            "D) I need more context before deciding",
        ],
    },
    AssumptionCategory.ACCESS: {
        "question": (
            "This code makes an access control assumption: {summary}. "
            "Does the current access model match your security requirements?"
        ),
        "options": [
            "A) Correct — the access restrictions are appropriate",
            "B) Too restrictive — additional roles/paths need access",
            "C) Too permissive — access should be further restricted",
            "D) I need more context before deciding",
        ],
    },
    AssumptionCategory.COMPLETENESS: {
        "question": (
            "This code may be incomplete: {summary}. "
            "Is the current implementation sufficient?"
        ),
        "options": [
            "A) Yes — handles all cases that matter in practice",
            "B) No — missing edge cases that occur in production",
            "C) Acceptable for now but needs hardening before scale",
            "D) I need more context before deciding",
        ],
    },
    AssumptionCategory.CONFIGURATION: {
        "question": (
            "This code has a configuration assumption: {summary}. "
            "Does this configuration work across all environments?"
        ),
        "options": [
            "A) Yes — this configuration is universal",
            "B) No — needs to be environment-specific",
            "C) Works for now but should be externalized before production",
            "D) I need more context before deciding",
        ],
    },
    AssumptionCategory.FRAMEWORK: {
        "question": (
            "This code makes a framework assumption: {summary}. "
            "Is the assumed framework behavior reliable?"
        ),
        "options": [
            "A) Yes — this behavior is stable and well-documented",
            "B) No — this is an implementation detail that may change",
            "C) Should add explicit configuration instead of relying on defaults",
            "D) I need more context before deciding",
        ],
    },
}


# ---------------------------------------------------------------------------
# Placeholder Extraction
# ---------------------------------------------------------------------------

# Regex patterns for common code identifiers
_RE_FUNCTION_DEF = re.compile(r"\bdef\s+(\w+)\s*\(")
_RE_TABLE_FROM = re.compile(r"\bFROM\s+[`\"']?(\w+)[`\"']?", re.IGNORECASE)
_RE_TABLE_INTO = re.compile(r"\bINTO\s+[`\"']?(\w+)[`\"']?", re.IGNORECASE)
_RE_TABLE_UPDATE = re.compile(r"\bUPDATE\s+[`\"']?(\w+)[`\"']?", re.IGNORECASE)
_RE_WHERE_CLAUSE = re.compile(r"\bWHERE\s+(.+?)(?:\s*;|\s*\)|$)", re.IGNORECASE)
_RE_QUOTED_STRING = re.compile(r"""["']([^"']{2,})["']""")
_RE_ENVIRON = re.compile(r"""os\.environ\[["'](\w+)["']\]""")
_RE_ENVIRON_GET = re.compile(r"""os\.environ\.get\(["'](\w+)["']""")
_RE_DATE_LITERAL = re.compile(
    r"""["'](\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?)["']"""
)
_RE_PORT_NUMBER = re.compile(r"\bport\s*=\s*(\d{2,5})\b", re.IGNORECASE)
_RE_PATH_LITERAL = re.compile(r"""["'](/[\w/.\-]+)["']""")
_RE_ROLE_STRING = re.compile(
    r"""(?:role|permission|grant)\s*(?:==|=|:)\s*["'](\w+)["']""", re.IGNORECASE
)
_RE_ENUM_VALUES = re.compile(
    r"""(?:in|IN)\s*\(([^)]+)\)|(?:choices|CHOICES|status|STATUS)\s*=\s*\[([^\]]+)\]"""
)
_RE_DOT_ACCESS = re.compile(r"""\.(\w+)(?:\s*[^\w(])""")
_RE_RESOURCE_URL = re.compile(r"""/api/(?:v\d+/)?(\w+)""")
_RE_FRAMEWORK = re.compile(
    r"""\bimport\s+(django|flask|fastapi|sqlalchemy|alembic|celery|pydantic)\b""",
    re.IGNORECASE,
)
_RE_TABLE_MODEL = re.compile(r"""__tablename__\s*=\s*["'](\w+)["']""")


def _extract_placeholders(assumption: DomainAssumption) -> dict[str, str]:
    """Extract placeholder values from code evidence for template interpolation.

    Parses code_evidence[0].snippet to extract table names, function names,
    variable names, dates, paths, ports, roles, etc. Returns a dict of
    placeholder keys to their extracted values.

    Args:
        assumption: A DomainAssumption with code_evidence.

    Returns:
        Dict mapping placeholder names to extracted values.
        Missing placeholders get sensible defaults.
    """
    snippet = ""
    if assumption.code_evidence:
        snippet = assumption.code_evidence[0].snippet

    placeholders: dict[str, str] = {
        "summary": assumption.summary,
    }

    # Function name
    fn_match = _RE_FUNCTION_DEF.search(snippet)
    if fn_match:
        placeholders["function"] = fn_match.group(1)
    else:
        placeholders["function"] = "this_function"

    # Table name — try multiple SQL patterns then ORM __tablename__
    table: str | None = None
    for pattern in (_RE_TABLE_FROM, _RE_TABLE_INTO, _RE_TABLE_UPDATE, _RE_TABLE_MODEL):
        m = pattern.search(snippet)
        if m:
            table = m.group(1)
            break
    placeholders["table"] = table or "this_table"

    # WHERE condition
    where_match = _RE_WHERE_CLAUSE.search(snippet)
    placeholders["condition"] = (
        where_match.group(1).strip() if where_match else "condition"
    )

    # Environment variable name
    env_match = _RE_ENVIRON.search(snippet) or _RE_ENVIRON_GET.search(snippet)
    placeholders["var_name"] = env_match.group(1) if env_match else "ENV_VAR"

    # Hardcoded date
    date_match = _RE_DATE_LITERAL.search(snippet)
    placeholders["date_value"] = date_match.group(1) if date_match else "the date"

    # Port number
    port_match = _RE_PORT_NUMBER.search(snippet)
    placeholders["port"] = port_match.group(1) if port_match else "the port"

    # Filesystem path
    path_match = _RE_PATH_LITERAL.search(snippet)
    placeholders["path"] = path_match.group(1) if path_match else "/path/to/resource"

    # Role
    role_match = _RE_ROLE_STRING.search(snippet)
    if role_match:
        placeholders["role"] = role_match.group(1)
    else:
        # Try to find role from quoted strings as fallback
        quoted = _RE_QUOTED_STRING.findall(snippet)
        role_candidates = [
            q for q in quoted
            if q.lower() in ("admin", "user", "editor", "viewer", "superadmin",
                             "manager", "operator", "readonly", "owner")
        ]
        placeholders["role"] = role_candidates[0] if role_candidates else "the_role"

    # Resource (from URL patterns)
    resource_match = _RE_RESOURCE_URL.search(snippet)
    placeholders["resource"] = (
        resource_match.group(1) if resource_match else placeholders["table"]
    )

    # Enum / status values
    enum_match = _RE_ENUM_VALUES.search(snippet)
    if enum_match:
        raw_values = enum_match.group(1) or enum_match.group(2)
        placeholders["values"] = raw_values.strip()
    else:
        # Fallback: extract quoted strings that look like enum values
        quoted = _RE_QUOTED_STRING.findall(snippet)
        short_quoted = [q for q in quoted if len(q) < 30]
        placeholders["values"] = ", ".join(short_quoted[:5]) if short_quoted else "the listed values"

    # Suggestion for incomplete_enum — a plausible missing state
    _COMMON_MISSING_STATES = {
        "active": "suspended, archived",
        "pending": "expired, cancelled",
        "completed": "failed, cancelled",
        "open": "closed, archived",
        "approved": "rejected, revoked",
        "enabled": "deprecated, maintenance",
    }
    suggestion = "additional states"
    for known, sugg in _COMMON_MISSING_STATES.items():
        if known in placeholders["values"].lower():
            suggestion = sugg
            break
    placeholders["suggestion"] = suggestion

    # Key for null checking
    dot_matches = _RE_DOT_ACCESS.findall(snippet)
    placeholders["key"] = dot_matches[0] if dot_matches else "the_field"

    # Framework
    fw_match = _RE_FRAMEWORK.search(snippet)
    placeholders["framework"] = fw_match.group(1) if fw_match else "the framework"

    return placeholders


# ---------------------------------------------------------------------------
# Template Application
# ---------------------------------------------------------------------------


def _apply_template(
    template: dict,
    placeholders: dict[str, str],
) -> tuple[str, list[str]]:
    """Apply placeholder values to a question template.

    Args:
        template: Dict with 'question' and 'options' keys.
        placeholders: Dict of placeholder name -> value.

    Returns:
        Tuple of (question_text, list_of_option_strings).
    """
    question = template["question"]
    options: list[str] = list(template["options"])

    # Substitute all {placeholder} tokens with extracted values
    for key, value in placeholders.items():
        token = "{" + key + "}"
        question = question.replace(token, value)
        options = [opt.replace(token, value) for opt in options]

    return question, options


# ---------------------------------------------------------------------------
# LLM Enrichment (optional refinement)
# ---------------------------------------------------------------------------

_ENRICHMENT_PROMPT = """\
Given this code assumption:
Category: {category}
Code: {snippet}
Template question: {question}
Template options: {options}

Refine the question and options to be more specific to this codebase.
Rules:
- Keep bounded multiple choice format (4-5 options)
- Option A should validate the current code behavior
- Options B-D should represent increasingly broader scope
- Last option is always "I need more context before deciding"
- Be specific — reference actual names from the code

Return JSON: {{"question": "...", "options": ["A) ...", "B) ...", ...]}}"""


async def enrich_question_llm(
    assumption: DomainAssumption,
    *,
    provider: Any = None,
) -> DomainAssumption:
    """Use LLM to refine the template question with domain context.

    If provider is None or the LLM call fails, returns the assumption with
    the template-based question unchanged.

    Args:
        assumption: A DomainAssumption that already has .question and .options
            populated from the template bank.
        provider: An LLMProvider instance. If None, skips enrichment.

    Returns:
        The assumption (mutated in place) with potentially refined question
        and options.
    """
    if provider is None:
        return assumption

    if not assumption.question or not assumption.options:
        logger.debug("Skipping LLM enrichment — no template question to refine")
        return assumption

    snippet = ""
    if assumption.code_evidence:
        snippet = assumption.code_evidence[0].snippet

    options_text = "\n".join(assumption.options)

    prompt = _ENRICHMENT_PROMPT.format(
        category=assumption.category.value,
        snippet=snippet,
        question=assumption.question,
        options=options_text,
    )

    try:
        # LLMProvider.check() is synchronous — call it directly
        result = provider.check(
            system_prompt=(
                "You are a code analysis assistant. Refine assumption "
                "questions to be more specific. Always return valid JSON."
            ),
            user_msg=prompt,
        )

        if result is None:
            logger.debug("LLM enrichment returned None — keeping template question")
            return assumption

        # The provider's check() returns parsed JSON; extract question fields.
        # The standard check() parser expects contradiction fields, so we need
        # to handle both raw-dict returns and re-parsed responses.
        enriched: dict[str, Any] | None = None

        if isinstance(result, dict):
            # If the provider returned question/options directly, use them
            if "question" in result and "options" in result:
                enriched = result
            else:
                # Try extracting from a nested 'content' or raw text field
                for key in ("content", "text", "raw"):
                    if key in result and isinstance(result[key], str):
                        try:
                            enriched = json.loads(result[key])
                        except (json.JSONDecodeError, TypeError):
                            pass
                        if enriched and "question" in enriched:
                            break

        if enriched and "question" in enriched and "options" in enriched:
            refined_options = enriched["options"]
            # Validate: must be a list of 4-5 strings
            if (
                isinstance(refined_options, list)
                and 4 <= len(refined_options) <= 5
                and all(isinstance(o, str) for o in refined_options)
            ):
                # Ensure last option is the "need more context" escape hatch
                last = refined_options[-1].lower()
                if "more context" in last or "need more" in last:
                    assumption.question = enriched["question"]
                    assumption.options = refined_options
                    logger.debug("LLM enrichment applied successfully")
                else:
                    logger.debug(
                        "LLM enrichment rejected — last option missing "
                        "'more context' escape hatch"
                    )
            else:
                logger.debug(
                    "LLM enrichment rejected — invalid options format "
                    "(need 4-5 string items, got %s)",
                    len(refined_options) if isinstance(refined_options, list) else type(refined_options),
                )
        else:
            logger.debug("LLM enrichment returned no usable question/options")

    except Exception:
        logger.exception("LLM enrichment failed — keeping template question")

    return assumption


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_question(assumption: DomainAssumption) -> DomainAssumption:
    """Generate a bounded multiple-choice question for the assumption.

    Uses the template bank (keyed by pattern_id) to produce a question and
    4-5 options. Falls back to generic category templates for unknown
    pattern_ids.

    Does NOT modify the assumption's status — the caller decides when to
    transition from DETECTED to PROPOSED.

    Args:
        assumption: A DomainAssumption in any status (typically DETECTED).

    Returns:
        The same assumption with .question and .options populated.
        If both are already populated, returns unchanged.
    """
    # Don't overwrite existing questions (idempotent)
    if assumption.question and assumption.options:
        return assumption

    placeholders = _extract_placeholders(assumption)

    # Try pattern-specific template first
    template = QUESTION_TEMPLATES.get(assumption.pattern_id)

    # Fall back to generic category template
    if template is None:
        template = _GENERIC_TEMPLATES.get(assumption.category)

    if template is None:
        # Ultimate fallback — should not happen if _GENERIC_TEMPLATES covers
        # all AssumptionCategory values
        logger.warning(
            "No template found for pattern_id=%r category=%r",
            assumption.pattern_id,
            assumption.category,
        )
        assumption.question = (
            f"This code assumes: {assumption.summary}. "
            f"Is this assumption valid?"
        )
        assumption.options = [
            "A) Yes — this assumption is correct",
            "B) Partially — the assumption is too narrow",
            "C) No — this assumption is wrong",
            "D) I need more context before deciding",
        ]
        return assumption

    question, options = _apply_template(template, placeholders)

    assumption.question = question
    assumption.options = options

    return assumption

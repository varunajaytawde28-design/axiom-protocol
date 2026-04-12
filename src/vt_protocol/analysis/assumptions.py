"""Assumption Extractor — regex-based detection of domain assumptions in Python source.

Scans Python files for 6 categories of implicit domain assumptions:
DATA_SCOPE, TEMPORAL, ACCESS, COMPLETENESS, CONFIGURATION, FRAMEWORK.

Uses pure regex pattern matching (no tree-sitter, no AST) for reliability.
Confidence scores and severity levels compensate for regex imprecision.

From SPEC: AI agents embed domain assumptions silently. This module surfaces
them so humans can validate, reject, or defer each assumption through the
VT Protocol lifecycle.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from vt_protocol.decisions.models import (
    AssumptionCategory,
    AssumptionStatus,
    CodeEvidence,
    DomainAssumption,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skip directories when scanning
# ---------------------------------------------------------------------------

_SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".eggs",
    "dist",
    "build",
    ".nox",
})

# ---------------------------------------------------------------------------
# Pattern dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssumptionPattern:
    """A single regex-based detection rule for domain assumptions."""

    pattern_id: str
    category: AssumptionCategory
    description: str
    regex: re.Pattern[str]
    severity: str  # low | medium | high | critical
    base_confidence: float
    summary_template: str = ""
    multiline: bool = False


# ---------------------------------------------------------------------------
# Pattern Registry — 19 patterns across 6 categories
# ---------------------------------------------------------------------------


def _build_patterns() -> tuple[AssumptionPattern, ...]:
    """Build and return the full pattern registry.

    Compiled once at module load time. Each pattern carries a summary_template
    that can include ``{match}`` and ``{file}`` placeholders.
    """
    patterns: list[AssumptionPattern] = []

    # === DATA_SCOPE ===

    # 1. single_source_write — SQL INSERT/UPDATE or ORM .create()/.save()/.add()
    patterns.append(AssumptionPattern(
        pattern_id="single_source_write",
        category=AssumptionCategory.DATA_SCOPE,
        description="Write to a table found in only one function — assumes no external write sources",
        regex=re.compile(
            r"(?:"
            r"INSERT\s+INTO\s+[`\"\']?(\w+)[`\"\']?"
            r"|UPDATE\s+[`\"\']?(\w+)[`\"\']?\s+SET"
            r"|\.create\s*\("
            r"|\.save\s*\("
            r"|\.add\s*\("
            r"|\.bulk_create\s*\("
            r"|\.update_or_create\s*\("
            r")",
            re.IGNORECASE,
        ),
        severity="high",
        base_confidence=0.7,
        summary_template="Write to {match} found in one location — assumes no external write sources",
    ))

    # 2. narrow_where_clause — WHERE with small literal set
    patterns.append(AssumptionPattern(
        pattern_id="narrow_where_clause",
        category=AssumptionCategory.DATA_SCOPE,
        description="WHERE clause with string/int literals — assumes subset scope",
        regex=re.compile(
            r"WHERE\s+\w+\s*(?:=\s*['\"][\w\-]+['\"]|IN\s*\([^)]{1,120}\))",
            re.IGNORECASE,
        ),
        severity="medium",
        base_confidence=0.6,
        summary_template="WHERE clause filters on literal values — assumes data subset is complete",
    ))

    # 3. single_table_query — SELECT FROM without JOIN
    patterns.append(AssumptionPattern(
        pattern_id="single_table_query",
        category=AssumptionCategory.DATA_SCOPE,
        description="SELECT from single table without JOIN — assumes all data in one table",
        regex=re.compile(
            r"SELECT\s+.{1,200}?\s+FROM\s+[`\"\']?(\w+)[`\"\']?"
            r"(?!\s+(?:JOIN|INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|CROSS\s+JOIN|FULL\s+JOIN))",
            re.IGNORECASE | re.DOTALL,
        ),
        severity="low",
        base_confidence=0.5,
        multiline=True,
        summary_template="Query reads from single table {match} without JOINs — assumes no cross-table relationships needed",
    ))

    # 4. hardcoded_table_name — __tablename__, Table(), or raw SQL table references
    patterns.append(AssumptionPattern(
        pattern_id="hardcoded_table_name",
        category=AssumptionCategory.DATA_SCOPE,
        description="Hardcoded table name — flag for schema tracking",
        regex=re.compile(
            r"(?:"
            r"__tablename__\s*=\s*['\"](\w+)['\"]"
            r"|Table\s*\(\s*['\"](\w+)['\"]"
            r"|db\.Model"
            r")",
        ),
        severity="low",
        base_confidence=0.5,
        summary_template="Hardcoded table name '{match}' — track for schema evolution",
    ))

    # === TEMPORAL ===

    # 5. hardcoded_date — datetime(YYYY, M, D) or date string literals
    patterns.append(AssumptionPattern(
        pattern_id="hardcoded_date",
        category=AssumptionCategory.TEMPORAL,
        description="Hardcoded date literal — assumes fixed temporal boundary",
        regex=re.compile(
            r"(?:"
            r"datetime\s*\(\s*\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}"
            r"|date\s*\(\s*\d{4}\s*,\s*\d{1,2}\s*,\s*\d{1,2}"
            r"|['\"](?:19|20)\d{2}[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])['\"]"
            r"|['\"](?:0[1-9]|[12]\d|3[01])[-/](?:0[1-9]|1[0-2])[-/](?:19|20)\d{2}['\"]"
            r")",
        ),
        severity="high",
        base_confidence=0.7,
        summary_template="Hardcoded date '{match}' — assumes fixed temporal boundary",
    ))

    # 6. no_migration_check — DB write without migration logic nearby
    patterns.append(AssumptionPattern(
        pattern_id="no_migration_check",
        category=AssumptionCategory.TEMPORAL,
        description="DB write without migration/backfill logic in the same file",
        regex=re.compile(
            r"(?:"
            r"\.execute\s*\(\s*['\"](?:INSERT|UPDATE|ALTER|CREATE)"
            r"|session\.commit\s*\("
            r"|db\.session\.commit\s*\("
            r"|\.migrate\s*\("
            r")",
            re.IGNORECASE,
        ),
        severity="low",
        base_confidence=0.4,
        summary_template="DB write without migration/backfill logic — assumes schema is stable",
    ))

    # === ACCESS ===

    # 7. single_role_access — single role check
    patterns.append(AssumptionPattern(
        pattern_id="single_role_access",
        category=AssumptionCategory.ACCESS,
        description="Single-role access check — assumes no multi-role requirements",
        regex=re.compile(
            r"(?:"
            r"@require_role\s*\(\s*['\"](\w+)['\"]\s*\)"
            r"|@login_required"
            r"|@permission_required\s*\(\s*['\"](\w+)['\"]\s*\)"
            r"|role\s*==\s*['\"](\w+)['\"]"
            r"|\.has_role\s*\(\s*['\"](\w+)['\"]\s*\)"
            r"|\.has_perm\s*\(\s*['\"][\w.]+['\"]\s*\)"
            r"|current_user\.is_(\w+)"
            r")",
        ),
        severity="high",
        base_confidence=0.7,
        summary_template="Single role check '{match}' — assumes no additional access control needed",
    ))

    # 8. single_endpoint — one route for a resource
    patterns.append(AssumptionPattern(
        pattern_id="single_endpoint",
        category=AssumptionCategory.ACCESS,
        description="Single endpoint for a resource — assumes no alternative access paths",
        regex=re.compile(
            r"(?:"
            r"@(?:app|router|api|blueprint)\.(?:route|get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]"
            r"|@api_view\s*\("
            r"|path\s*\(\s*['\"]([^'\"]+)['\"]"
            r")",
        ),
        severity="medium",
        base_confidence=0.5,
        summary_template="Single endpoint '{match}' for resource — assumes no alternative access paths",
    ))

    # 9. no_multitenancy — DB queries without tenant/org filter
    patterns.append(AssumptionPattern(
        pattern_id="no_multitenancy",
        category=AssumptionCategory.ACCESS,
        description="DB query without tenant_id/org_id filter — assumes single tenancy",
        regex=re.compile(
            r"(?:"
            r"\.query\s*\("
            r"|\.filter\s*\("
            r"|\.filter_by\s*\("
            r"|\.objects\.(?:filter|get|all|exclude)\s*\("
            r"|SELECT\s+.{1,200}?\s+FROM"
            r")",
            re.IGNORECASE | re.DOTALL,
        ),
        severity="low",
        base_confidence=0.4,
        multiline=True,
        summary_template="DB query without tenant/org filter — assumes single-tenant access",
    ))

    # === COMPLETENESS ===

    # 10. incomplete_enum — small literal sets or short if/elif without else
    patterns.append(AssumptionPattern(
        pattern_id="incomplete_enum",
        category=AssumptionCategory.COMPLETENESS,
        description="Small enum or incomplete status check — assumes limited state space",
        regex=re.compile(
            r"(?:"
            r"status\s+IN\s*\(\s*(?:'[^']*'\s*,\s*){0,3}'[^']*'\s*\)"
            r"|class\s+\w+\s*\(\s*(?:str\s*,\s*)?Enum\s*\)"
            r"|if\s+\w+\s*==\s*['\"][^'\"]+['\"]"
            r")",
            re.IGNORECASE,
        ),
        severity="medium",
        base_confidence=0.6,
        summary_template="Incomplete status/enum check '{match}' — may not cover all possible states",
    ))

    # 11. no_null_handling — dict key access without .get() or result without None check
    patterns.append(AssumptionPattern(
        pattern_id="no_null_handling",
        category=AssumptionCategory.COMPLETENESS,
        description="Direct dict/result access without null safety — assumes key/value always exists",
        regex=re.compile(
            r"(?:"
            r"\w+\[['\"](\w+)['\"]\]"
            r"|\.first\s*\(\s*\)"
            r"|\.one\s*\(\s*\)"
            r"|\.scalar\s*\(\s*\)"
            r")",
        ),
        severity="medium",
        base_confidence=0.5,
        summary_template="Direct access without null check — assumes '{match}' always exists",
    ))

    # 12. no_pagination — .all() or SELECT * without LIMIT
    patterns.append(AssumptionPattern(
        pattern_id="no_pagination",
        category=AssumptionCategory.COMPLETENESS,
        description="Unbounded query without pagination — assumes small result set",
        regex=re.compile(
            r"(?:"
            r"\.all\s*\(\s*\)"
            r"|\.objects\.all\s*\(\s*\)"
            r"|SELECT\s+\*\s+FROM(?!.*(?:LIMIT|OFFSET|TOP))"
            r"|\.fetchall\s*\(\s*\)"
            r")",
            re.IGNORECASE,
        ),
        severity="medium",
        base_confidence=0.5,
        summary_template="Unbounded query '{match}' — assumes result set fits in memory",
    ))

    # 13. no_error_path — bare except pass or missing except for DB/HTTP calls
    patterns.append(AssumptionPattern(
        pattern_id="no_error_path",
        category=AssumptionCategory.COMPLETENESS,
        description="Bare except/pass or missing error handling — assumes happy path",
        regex=re.compile(
            r"(?:"
            r"except\s*:\s*\n\s*pass"
            r"|except\s+\w+(?:\s+as\s+\w+)?:\s*\n\s*pass"
            r"|except\s+Exception\s*:\s*\n\s*pass"
            r")",
            re.MULTILINE,
        ),
        severity="medium",
        base_confidence=0.4,
        multiline=True,
        summary_template="Bare except/pass — swallows errors, assumes happy path always succeeds",
    ))

    # === CONFIGURATION ===

    # 14. env_no_fallback — os.getenv without default or os.environ[KEY]
    patterns.append(AssumptionPattern(
        pattern_id="env_no_fallback",
        category=AssumptionCategory.CONFIGURATION,
        description="Environment variable access without fallback — assumes env is always set",
        regex=re.compile(
            r"(?:"
            r"os\.getenv\s*\(\s*['\"](\w+)['\"]\s*\)"
            r"|os\.environ\s*\[\s*['\"](\w+)['\"]\s*\]"
            r")",
        ),
        severity="high",
        base_confidence=0.7,
        summary_template="Environment variable '{match}' accessed without fallback — assumes always set",
    ))

    # 15. hardcoded_path — literal filesystem paths
    patterns.append(AssumptionPattern(
        pattern_id="hardcoded_path",
        category=AssumptionCategory.CONFIGURATION,
        description="Hardcoded filesystem path — assumes fixed deployment layout",
        regex=re.compile(
            r"['\"]("
            r"/tmp/[^\s'\"]*"
            r"|/var/[^\s'\"]*"
            r"|/etc/[^\s'\"]*"
            r"|/home/[^\s'\"]*"
            r"|/opt/[^\s'\"]*"
            r"|/usr/local/[^\s'\"]*"
            r"|C:\\\\[^\s'\"]*"
            r"|C:/[^\s'\"]*"
            r")['\"]",
        ),
        severity="medium",
        base_confidence=0.6,
        summary_template="Hardcoded path '{match}' — assumes fixed filesystem layout",
    ))

    # 16. hardcoded_port — common port numbers in bind/connect context
    patterns.append(AssumptionPattern(
        pattern_id="hardcoded_port",
        category=AssumptionCategory.CONFIGURATION,
        description="Hardcoded port number — assumes fixed network configuration",
        regex=re.compile(
            r"(?:"
            r"(?:port|PORT)\s*[=:]\s*(\d{2,5})"
            r"|localhost:(\d{2,5})"
            r"|127\.0\.0\.1:(\d{2,5})"
            r"|0\.0\.0\.0:(\d{2,5})"
            r"|bind\s*\(\s*\(?['\"][^'\"]*['\"],\s*(\d{2,5})"
            r"|connect\s*\(\s*\(?['\"][^'\"]*['\"],\s*(\d{2,5})"
            r"|:(\d{2,5})/\w"  # URL-like :port/path
            r")",
        ),
        severity="medium",
        base_confidence=0.5,
        summary_template="Hardcoded port {match} — assumes fixed network configuration",
    ))

    # === FRAMEWORK ===

    # 17. orm_no_loading_strategy — relationship() without lazy=
    patterns.append(AssumptionPattern(
        pattern_id="orm_no_loading_strategy",
        category=AssumptionCategory.FRAMEWORK,
        description="SQLAlchemy relationship() without explicit lazy= — relies on ORM default",
        regex=re.compile(
            r"relationship\s*\([^)]*\)",
        ),
        severity="medium",
        base_confidence=0.6,
        summary_template="relationship() without explicit loading strategy — assumes SQLAlchemy default (lazy='select')",
    ))

    # 18. no_cascade_behavior — ForeignKey without on_delete/cascade
    patterns.append(AssumptionPattern(
        pattern_id="no_cascade_behavior",
        category=AssumptionCategory.FRAMEWORK,
        description="ForeignKey without cascade/on_delete — relies on framework default",
        regex=re.compile(
            r"(?:"
            r"ForeignKey\s*\([^)]*\)"
            r"|models\.ForeignKey\s*\([^)]*\)"
            r")",
        ),
        severity="medium",
        base_confidence=0.6,
        summary_template="ForeignKey without explicit cascade behavior — assumes framework default on delete",
    ))

    # 19. framework_version_dep — imports from framework internals
    patterns.append(AssumptionPattern(
        pattern_id="framework_version_dep",
        category=AssumptionCategory.FRAMEWORK,
        description="Import from framework internals — assumes specific framework version",
        regex=re.compile(
            r"(?:from|import)\s+(?:"
            r"django\.db\.backends"
            r"|django\.utils\.encoding"
            r"|django\.utils\.six"
            r"|flask\._\w+"
            r"|flask\.globals"
            r"|sqlalchemy\.engine\.strategies"
            r"|sqlalchemy\.orm\.strategies"
            r"|celery\.app\.amqp"
            r"|celery\._state"
            r"|fastapi\.routing"
            r"|starlette\._\w+"
            r"|werkzeug\._\w+"
            r"|werkzeug\.internal"
            r")",
        ),
        severity="medium",
        base_confidence=0.5,
        summary_template="Import from framework internals '{match}' — may break on version upgrade",
    ))

    return tuple(patterns)


# Module-level pattern registry — compiled once at import time.
PATTERNS: tuple[AssumptionPattern, ...] = _build_patterns()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_COMMENT_LINE_RE = re.compile(r"^\s*#")
_DOCSTRING_DELIM_RE = re.compile(r'^\s*(?:\"\"\"|\'\'\')')

# Patterns to detect test file paths.
_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:test_|tests/|testing/|conftest\.py|_test\.py$)"
)

# For the no_multitenancy filter: lines containing tenant/org references.
_TENANT_FILTER_RE = re.compile(
    r"tenant_id|org_id|organization_id|team_id|account_id|workspace_id",
    re.IGNORECASE,
)

# For env_no_fallback: detect os.getenv with a second arg (has default).
_GETENV_WITH_DEFAULT_RE = re.compile(
    r"os\.getenv\s*\(\s*['\"](\w+)['\"]\s*,\s*[^)]+\)",
)

# For orm_no_loading_strategy: detect relationship() WITH lazy=.
_RELATIONSHIP_WITH_LAZY_RE = re.compile(
    r"relationship\s*\([^)]*lazy\s*=",
)

# For no_cascade_behavior: detect ForeignKey WITH on_delete or cascade.
_FK_WITH_CASCADE_RE = re.compile(
    r"ForeignKey\s*\([^)]*(?:on_delete|cascade)\s*=",
    re.IGNORECASE,
)
_DJANGO_FK_WITH_ON_DELETE_RE = re.compile(
    r"models\.ForeignKey\s*\([^)]*on_delete\s*=",
)

# For no_migration_check: detect migration-related keywords in a file.
_MIGRATION_KEYWORD_RE = re.compile(
    r"(?:migration|migrate|alembic|backfill|schema_version|RunSQL|RunPython)",
    re.IGNORECASE,
)


def _is_test_path(path: Path) -> bool:
    """Return True if path looks like a test file or is inside a test directory."""
    return bool(_TEST_PATH_RE.search(str(path)))


def _extract_context(lines: list[str], line_idx: int, radius: int = 2) -> str:
    """Extract surrounding lines for context display.

    Args:
        lines: All lines in the file.
        line_idx: 0-based index of the match line.
        radius: Number of lines above and below to include.

    Returns:
        Multi-line string with context, each line prefixed with its 1-based number.
    """
    start = max(0, line_idx - radius)
    end = min(len(lines), line_idx + radius + 1)
    context_parts: list[str] = []
    for i in range(start, end):
        marker = ">>>" if i == line_idx else "   "
        context_parts.append(f"{marker} {i + 1:4d} | {lines[i]}")
    return "\n".join(context_parts)


def _in_docstring(lines: list[str], line_idx: int) -> bool:
    """Heuristic check: is the given line inside a triple-quoted docstring?

    Walks backwards counting triple-quote delimiters. An odd count means
    we are inside a docstring.
    """
    count = 0
    for i in range(line_idx):
        text = lines[i]
        count += text.count('"""') + text.count("'''")
    return count % 2 == 1


def _in_comment(line: str) -> bool:
    """Return True if line is a comment line (starts with #)."""
    return bool(_COMMENT_LINE_RE.match(line))


def _format_summary(template: str, match_text: str, file_path: str) -> str:
    """Build a human-readable summary from the template."""
    # Trim match text for readability.
    cleaned = match_text.strip()
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
    return template.format(match=cleaned, file=file_path)


def _extract_first_group(m: re.Match[str]) -> str:
    """Return the first non-None captured group, or the full match."""
    for g in m.groups():
        if g is not None:
            return g
    return m.group(0)


# ---------------------------------------------------------------------------
# Core scanning logic
# ---------------------------------------------------------------------------


def _scan_source(
    path: Path,
    source: str,
    patterns: Sequence[AssumptionPattern] = PATTERNS,
) -> list[DomainAssumption]:
    """Scan a single source string against all patterns.

    Args:
        path: File path (for evidence reporting).
        source: Source text.
        patterns: Patterns to match (defaults to full registry).

    Returns:
        List of DomainAssumption with status=DETECTED.
    """
    if not source.strip():
        return []

    lines = source.splitlines()
    file_str = str(path)
    assumptions: list[DomainAssumption] = []

    # Pre-check: does file contain tenant filter references?
    has_tenant_filter = bool(_TENANT_FILTER_RE.search(source))

    # Pre-check: does file contain migration keywords?
    has_migration_keywords = bool(_MIGRATION_KEYWORD_RE.search(source))

    for pattern in patterns:
        # --- Pattern-specific pre-filters ---

        # no_multitenancy: skip if file already references tenant filtering.
        if pattern.pattern_id == "no_multitenancy" and has_tenant_filter:
            continue

        # no_migration_check: skip if file contains migration keywords.
        if pattern.pattern_id == "no_migration_check" and has_migration_keywords:
            continue

        if pattern.multiline:
            # Multi-line patterns: use finditer on full source.
            for m in pattern.regex.finditer(source):
                span_start = m.start()
                line_idx = source[:span_start].count("\n")

                # Skip comments and docstrings.
                if line_idx < len(lines):
                    if _in_comment(lines[line_idx]):
                        continue
                    if _in_docstring(lines, line_idx):
                        continue

                match_text = _extract_first_group(m)
                context = _extract_context(lines, line_idx)
                snippet = lines[line_idx].rstrip() if line_idx < len(lines) else m.group(0)

                assumption = _build_assumption(
                    pattern=pattern,
                    file_str=file_str,
                    line_num=line_idx + 1,
                    snippet=snippet,
                    context=context,
                    match_text=match_text,
                )
                if assumption is not None:
                    assumptions.append(assumption)
        else:
            # Line-by-line matching.
            for line_idx, line in enumerate(lines):
                # Skip comment lines.
                if _in_comment(line):
                    continue
                # Skip lines inside docstrings.
                if _in_docstring(lines, line_idx):
                    continue

                m = pattern.regex.search(line)
                if m is None:
                    continue

                match_text = _extract_first_group(m)

                # --- Pattern-specific post-filters ---
                if not _passes_post_filter(pattern, m, line, lines, line_idx, source):
                    continue

                context = _extract_context(lines, line_idx)
                snippet = line.rstrip()

                assumption = _build_assumption(
                    pattern=pattern,
                    file_str=file_str,
                    line_num=line_idx + 1,
                    snippet=snippet,
                    context=context,
                    match_text=match_text,
                )
                if assumption is not None:
                    assumptions.append(assumption)

    return assumptions


def _passes_post_filter(
    pattern: AssumptionPattern,
    m: re.Match[str],
    line: str,
    lines: list[str],
    line_idx: int,
    source: str,
) -> bool:
    """Apply pattern-specific post-match filters.

    Returns True if the match should be kept, False if it should be filtered out.
    """
    pid = pattern.pattern_id

    if pid == "env_no_fallback":
        # Filter out os.getenv() calls that have a default second argument.
        if _GETENV_WITH_DEFAULT_RE.search(line):
            return False
        return True

    if pid == "orm_no_loading_strategy":
        # Keep only relationship() calls WITHOUT lazy=.
        if _RELATIONSHIP_WITH_LAZY_RE.search(line):
            return False
        return True

    if pid == "no_cascade_behavior":
        # Keep only ForeignKey calls WITHOUT on_delete/cascade.
        if _FK_WITH_CASCADE_RE.search(line):
            return False
        if _DJANGO_FK_WITH_ON_DELETE_RE.search(line):
            return False
        return True

    if pid == "incomplete_enum":
        # For Enum class definitions: check if member count < 4.
        # For if/elif chains: check if there's no else.
        if "class " in line and "Enum" in line:
            return _enum_member_count_low(lines, line_idx)
        if line.strip().startswith("if "):
            return _if_chain_missing_else(lines, line_idx)
        return True

    if pid == "no_null_handling":
        # Filter out dict[key] if preceded by .get() on same var, or if it's
        # a type annotation context (e.g., dict["key"]).
        if "get(" in line or "Optional" in line or "->" in line:
            return False
        return True

    if pid == "hardcoded_port":
        # Only flag known service ports.
        match_text = _extract_first_group(m)
        try:
            port = int(match_text)
        except (ValueError, TypeError):
            return False
        known_ports = {
            80, 443, 3000, 3306, 5000, 5432, 5433, 5672, 6379, 6380,
            8000, 8080, 8443, 8888, 9000, 9090, 9200, 9300, 11211,
            15672, 27017, 27018, 28015,
        }
        return port in known_ports

    return True


def _enum_member_count_low(lines: list[str], class_line_idx: int) -> bool:
    """Check if an Enum class has fewer than 4 members.

    Scan forward from the class definition looking for member assignments.
    """
    member_count = 0
    indent = len(lines[class_line_idx]) - len(lines[class_line_idx].lstrip())

    for i in range(class_line_idx + 1, min(class_line_idx + 30, len(lines))):
        stripped = lines[i].strip()
        if not stripped:
            continue
        # End of class body (line at same or lesser indent that isn't blank).
        current_indent = len(lines[i]) - len(lines[i].lstrip())
        if current_indent <= indent and stripped and not stripped.startswith("#"):
            break
        # Count lines that look like enum member assignments: NAME = value
        if re.match(r"^[A-Z_][A-Z0-9_]*\s*=", stripped):
            member_count += 1

    return member_count < 4


def _if_chain_missing_else(lines: list[str], if_line_idx: int) -> bool:
    """Check if an if/elif chain lacks an else clause.

    Scan forward from the if statement looking for elif/else at the same indent.
    """
    indent = len(lines[if_line_idx]) - len(lines[if_line_idx].lstrip())
    has_elif = False
    has_else = False

    for i in range(if_line_idx + 1, min(if_line_idx + 40, len(lines))):
        stripped = lines[i].strip()
        if not stripped:
            continue
        current_indent = len(lines[i]) - len(lines[i].lstrip())
        if current_indent < indent:
            break
        if current_indent == indent:
            if stripped.startswith("elif "):
                has_elif = True
            elif stripped.startswith("else:") or stripped.startswith("else "):
                has_else = True
                break
            elif not stripped.startswith("#"):
                # Another statement at the same level — chain ended.
                break

    # Only flag if there was at least one elif but no else.
    return has_elif and not has_else


def _build_assumption(
    *,
    pattern: AssumptionPattern,
    file_str: str,
    line_num: int,
    snippet: str,
    context: str,
    match_text: str,
) -> DomainAssumption | None:
    """Build a DomainAssumption from a pattern match.

    Returns None if the assumption would be degenerate (empty summary, etc.).
    """
    summary = _format_summary(
        pattern.summary_template or pattern.description,
        match_text,
        file_str,
    )

    evidence = CodeEvidence(
        file=file_str,
        line=line_num,
        snippet=snippet,
    )

    return DomainAssumption(
        category=pattern.category,
        status=AssumptionStatus.DETECTED,
        pattern_id=pattern.pattern_id,
        summary=summary,
        code_evidence=[evidence],
        confidence=pattern.base_confidence,
        severity=pattern.severity,
    )


# ---------------------------------------------------------------------------
# Cross-file analysis: single_source_write deduplication
# ---------------------------------------------------------------------------


_TABLE_NAME_IN_WRITE_RE = re.compile(
    r"(?:"
    r"INSERT\s+INTO\s+[`\"\']?(\w+)[`\"\']?"
    r"|UPDATE\s+[`\"\']?(\w+)[`\"\']?\s+SET"
    r")",
    re.IGNORECASE,
)


def _collect_write_targets(source: str) -> set[str]:
    """Extract table names written to via INSERT INTO or UPDATE."""
    tables: set[str] = set()
    for m in _TABLE_NAME_IN_WRITE_RE.finditer(source):
        for g in m.groups():
            if g is not None:
                tables.add(g.lower())
    return tables


def _deduplicate_single_source_writes(
    all_assumptions: list[DomainAssumption],
    file_sources: dict[str, str],
) -> list[DomainAssumption]:
    """Remove single_source_write assumptions where multiple files write to the same table.

    Scans all file sources to build a map of table -> set of files that write to it.
    If a table is written from more than one file, single_source_write assumptions
    referencing that table are removed.
    """
    # Build table -> writing files map.
    table_writers: dict[str, set[str]] = {}
    for fpath, src in file_sources.items():
        for table in _collect_write_targets(src):
            table_writers.setdefault(table, set()).add(fpath)

    # Tables written from multiple files.
    multi_write_tables: set[str] = {
        table for table, writers in table_writers.items() if len(writers) > 1
    }

    if not multi_write_tables:
        return all_assumptions

    # Filter out single_source_write assumptions referencing multi-write tables.
    filtered: list[DomainAssumption] = []
    for a in all_assumptions:
        if a.pattern_id == "single_source_write":
            # Check if the summary references any multi-write table.
            summary_lower = a.summary.lower()
            if any(table in summary_lower for table in multi_write_tables):
                logger.debug(
                    "Filtered single_source_write for multi-write table in %s",
                    a.code_evidence[0].file if a.code_evidence else "unknown",
                )
                continue
        filtered.append(a)
    return filtered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_file(
    path: Path,
    *,
    source: str | None = None,
) -> list[DomainAssumption]:
    """Scan a single Python file for domain assumptions.

    Args:
        path: File path (for reporting in code_evidence).
        source: Optional pre-read source text (avoids re-reading the file).

    Returns:
        List of DomainAssumption with status=DETECTED.
    """
    path = Path(path)

    if _is_test_path(path):
        logger.debug("Skipping test file: %s", path)
        return []

    if source is None:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as exc:
            logger.warning("Cannot read %s: %s", path, exc)
            return []

    return _scan_source(path, source)


def scan_directory(
    root: Path,
    *,
    extensions: tuple[str, ...] = (".py",),
    max_depth: int = 8,
) -> list[DomainAssumption]:
    """Scan all files in a directory tree for domain assumptions.

    Skips hidden directories, __pycache__, .git, node_modules, .venv,
    and test files.

    Args:
        root: Root directory to scan.
        extensions: File extensions to include (default: only .py files).
        max_depth: Maximum directory depth to recurse into.

    Returns:
        List of DomainAssumption with status=DETECTED.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        logger.warning("Not a directory: %s", root)
        return []

    all_assumptions: list[DomainAssumption] = []
    file_sources: dict[str, str] = {}

    for file_path in _walk_files(root, extensions=extensions, max_depth=max_depth):
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            continue

        file_sources[str(file_path)] = source
        assumptions = _scan_source(file_path, source)
        all_assumptions.extend(assumptions)

    # Cross-file deduplication for single_source_write.
    all_assumptions = _deduplicate_single_source_writes(all_assumptions, file_sources)

    logger.info(
        "Scanned %d files under %s, found %d assumptions",
        len(file_sources),
        root,
        len(all_assumptions),
    )
    return all_assumptions


def scan_changed_files(
    root: Path,
    changed_files: list[str],
) -> list[DomainAssumption]:
    """Scan only specific changed files for domain assumptions.

    Designed for incremental pipeline use — e.g., scan only files modified
    in a git commit or PR.

    Args:
        root: Project root directory (for resolving relative paths).
        changed_files: List of file paths (relative to root or absolute).

    Returns:
        List of DomainAssumption with status=DETECTED.
    """
    root = Path(root).resolve()
    all_assumptions: list[DomainAssumption] = []
    file_sources: dict[str, str] = {}

    for raw_path in changed_files:
        file_path = Path(raw_path)
        if not file_path.is_absolute():
            file_path = root / file_path

        if not file_path.exists():
            logger.debug("Changed file does not exist, skipping: %s", file_path)
            continue

        if not file_path.suffix == ".py":
            continue

        if _is_test_path(file_path):
            logger.debug("Skipping test file: %s", file_path)
            continue

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            continue

        file_sources[str(file_path)] = source
        assumptions = _scan_source(file_path, source)
        all_assumptions.extend(assumptions)

    # Cross-file deduplication for single_source_write.
    all_assumptions = _deduplicate_single_source_writes(all_assumptions, file_sources)

    logger.info(
        "Scanned %d changed files, found %d assumptions",
        len(file_sources),
        len(all_assumptions),
    )
    return all_assumptions


# ---------------------------------------------------------------------------
# Directory walker
# ---------------------------------------------------------------------------


def _walk_files(
    root: Path,
    *,
    extensions: tuple[str, ...],
    max_depth: int,
    _current_depth: int = 0,
) -> list[Path]:
    """Recursively collect files matching extensions, respecting skip rules.

    Args:
        root: Directory to walk.
        extensions: Allowed file extensions.
        max_depth: Maximum recursion depth.
        _current_depth: Internal tracker for current depth.

    Returns:
        Sorted list of file Paths.
    """
    if _current_depth > max_depth:
        return []

    results: list[Path] = []

    try:
        entries = sorted(root.iterdir())
    except (OSError, PermissionError) as exc:
        logger.warning("Cannot list directory %s: %s", root, exc)
        return []

    for entry in entries:
        if entry.name.startswith(".") and entry.is_dir():
            continue
        if entry.is_dir():
            if entry.name in _SKIP_DIRS:
                continue
            results.extend(
                _walk_files(
                    entry,
                    extensions=extensions,
                    max_depth=max_depth,
                    _current_depth=_current_depth + 1,
                )
            )
        elif entry.is_file():
            if entry.suffix in extensions:
                if _is_test_path(entry):
                    continue
                results.append(entry)

    return results

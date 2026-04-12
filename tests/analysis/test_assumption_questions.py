"""Tests for assumption question generator — bounded multiple-choice questions.

Covers:
- Pattern-specific question generation (TestGenerateQuestion)
- Question format invariants (TestQuestionFormat)
- Placeholder extraction from code snippets (TestPlaceholderExtraction)
- Generic fallback templates (TestGenericFallback)
- Idempotency guarantees (TestQuestionIdempotent)
"""

from __future__ import annotations

import pytest

from vt_protocol.analysis.assumption_questions import (
    QUESTION_TEMPLATES,
    _extract_placeholders,
    generate_question,
)
from vt_protocol.decisions.models import (
    AssumptionCategory,
    AssumptionStatus,
    CodeEvidence,
    DomainAssumption,
)


# ---------------------------------------------------------------------------
# Helpers — build DomainAssumption with realistic code_evidence
# ---------------------------------------------------------------------------


def _make_assumption(
    pattern_id: str,
    category: AssumptionCategory,
    snippet: str,
    *,
    summary: str = "",
    file: str = "app/service.py",
    line: int = 42,
    status: AssumptionStatus = AssumptionStatus.DETECTED,
) -> DomainAssumption:
    """Create a DomainAssumption with a single CodeEvidence entry."""
    return DomainAssumption(
        category=category,
        pattern_id=pattern_id,
        summary=summary or f"Assumption from pattern {pattern_id}",
        status=status,
        code_evidence=[
            CodeEvidence(file=file, line=line, snippet=snippet),
        ],
    )


# ---------------------------------------------------------------------------
# TestGenerateQuestion — pattern-specific question generation
# ---------------------------------------------------------------------------


class TestGenerateQuestion:
    """Verify that each known pattern_id produces a well-formed question
    referencing the right code identifiers."""

    def test_single_source_write_question(self):
        assumption = _make_assumption(
            pattern_id="single_source_write",
            category=AssumptionCategory.DATA_SCOPE,
            snippet=(
                "def place_order(order):\n"
                "    db.execute('INSERT INTO transactions VALUES (?)', order)"
            ),
            summary="Only place_order writes to transactions table",
        )

        result = generate_question(assumption)

        assert "transactions" in result.question
        assert "place_order" in result.question
        assert 4 <= len(result.options) <= 5
        last_option = result.options[-1].lower()
        assert "more context" in last_option or "context" in last_option

    def test_narrow_where_question(self):
        assumption = _make_assumption(
            pattern_id="narrow_where_clause",
            category=AssumptionCategory.DATA_SCOPE,
            snippet="SELECT * FROM orders WHERE source = 'internal'",
            summary="Query filters orders to internal source only",
        )

        result = generate_question(assumption)

        # The question should reference the filter condition
        assert "source" in result.question.lower() or "internal" in result.question.lower()
        assert len(result.options) >= 3

    def test_hardcoded_date_question(self):
        assumption = _make_assumption(
            pattern_id="hardcoded_date",
            category=AssumptionCategory.TEMPORAL,
            snippet="cutoff = datetime(2023, 1, 1)\ndata = df[df['date'] >= '2023-01-01']",
            summary="Hardcoded date boundary 2023-01-01",
        )

        result = generate_question(assumption)

        # Should mention the date or the word "date"/"boundary"
        q_lower = result.question.lower()
        assert "2023-01-01" in result.question or "date" in q_lower
        assert len(result.options) >= 3

    def test_single_role_question(self):
        assumption = _make_assumption(
            pattern_id="single_role_access",
            category=AssumptionCategory.ACCESS,
            snippet='@require_role("admin")\ndef delete_user(user_id):',
            summary="Only admin role can delete users",
        )

        result = generate_question(assumption)

        assert "admin" in result.question.lower()
        assert len(result.options) >= 3

    def test_env_no_fallback_question(self):
        assumption = _make_assumption(
            pattern_id="env_no_fallback",
            category=AssumptionCategory.CONFIGURATION,
            snippet='api_key = os.environ["API_KEY"]',
            summary="API_KEY env var must be set",
        )

        result = generate_question(assumption)

        assert "API_KEY" in result.question
        assert len(result.options) >= 3

    def test_incomplete_enum_question(self):
        assumption = _make_assumption(
            pattern_id="incomplete_enum",
            category=AssumptionCategory.COMPLETENESS,
            snippet="SELECT * FROM users WHERE status IN ('active', 'inactive')",
            summary="User status limited to active and inactive",
        )

        result = generate_question(assumption)

        q_lower = result.question.lower()
        assert "active" in q_lower or "status" in q_lower
        assert len(result.options) >= 3

    def test_no_null_handling_question(self):
        assumption = _make_assumption(
            pattern_id="no_null_handling",
            category=AssumptionCategory.COMPLETENESS,
            snippet="name = user.profile.display_name\nreturn name.upper()",
            summary="Accessing display_name without null check",
        )

        result = generate_question(assumption)

        # Should mention null or the field being accessed
        assert result.question  # non-empty
        assert len(result.options) >= 3

    def test_orm_no_loading_question(self):
        assumption = _make_assumption(
            pattern_id="orm_no_loading_strategy",
            category=AssumptionCategory.FRAMEWORK,
            snippet=(
                "class Order(Base):\n"
                "    __tablename__ = 'orders'\n"
                "    items = relationship('OrderItem')"
            ),
            summary="ORM relationship uses default lazy loading",
        )

        result = generate_question(assumption)

        q_lower = result.question.lower()
        assert "loading" in q_lower or "orm" in q_lower
        assert len(result.options) >= 3

    def test_no_cascade_question(self):
        assumption = _make_assumption(
            pattern_id="no_cascade_behavior",
            category=AssumptionCategory.FRAMEWORK,
            snippet=(
                "class OrderItem(Base):\n"
                "    order_id = Column(Integer, ForeignKey('orders.id'))"
            ),
            summary="Foreign key has no cascade behavior defined",
        )

        result = generate_question(assumption)

        q_lower = result.question.lower()
        assert "cascade" in q_lower or "delete" in q_lower
        assert len(result.options) >= 3


# ---------------------------------------------------------------------------
# TestQuestionFormat — format invariants across all patterns
# ---------------------------------------------------------------------------


class TestQuestionFormat:
    """Verify structural invariants that hold for every generated question."""

    @pytest.fixture(params=list(QUESTION_TEMPLATES.keys()))
    def pattern_assumption(self, request) -> DomainAssumption:
        """Generate a DomainAssumption for each known pattern_id with a
        snippet that contains enough signal for placeholder extraction."""
        pattern_id = request.param
        # Map pattern_id → (category, realistic snippet)
        snippets = {
            "single_source_write": (
                AssumptionCategory.DATA_SCOPE,
                "def save_order(o):\n    db.execute('INSERT INTO orders VALUES (?)', o)",
            ),
            "narrow_where_clause": (
                AssumptionCategory.DATA_SCOPE,
                "SELECT * FROM events WHERE type = 'click'",
            ),
            "single_table_query": (
                AssumptionCategory.DATA_SCOPE,
                "SELECT id, name FROM users",
            ),
            "hardcoded_table_name": (
                AssumptionCategory.DATA_SCOPE,
                "cursor.execute('SELECT * FROM audit_log')",
            ),
            "hardcoded_date": (
                AssumptionCategory.TEMPORAL,
                "df = df[df['date'] >= '2023-01-01']",
            ),
            "no_migration_check": (
                AssumptionCategory.TEMPORAL,
                "cursor.execute('SELECT * FROM settings')",
            ),
            "single_role_access": (
                AssumptionCategory.ACCESS,
                'role = "admin"\n@require_role("admin")',
            ),
            "single_endpoint": (
                AssumptionCategory.ACCESS,
                '@app.get("/api/v1/users")\ndef list_users():',
            ),
            "no_multitenancy": (
                AssumptionCategory.ACCESS,
                "SELECT * FROM documents",
            ),
            "incomplete_enum": (
                AssumptionCategory.COMPLETENESS,
                "WHERE status IN ('pending', 'completed')",
            ),
            "no_null_handling": (
                AssumptionCategory.COMPLETENESS,
                "return obj.value + 1",
            ),
            "no_pagination": (
                AssumptionCategory.COMPLETENESS,
                "SELECT * FROM logs",
            ),
            "no_error_path": (
                AssumptionCategory.COMPLETENESS,
                "def send_email(to):\n    smtp.send(to, body)",
            ),
            "env_no_fallback": (
                AssumptionCategory.CONFIGURATION,
                'secret = os.environ["DB_PASSWORD"]',
            ),
            "hardcoded_path": (
                AssumptionCategory.CONFIGURATION,
                'config = open("/etc/app/config.yaml")',
            ),
            "hardcoded_port": (
                AssumptionCategory.CONFIGURATION,
                "server = Server(port=8080)",
            ),
            "orm_no_loading_strategy": (
                AssumptionCategory.FRAMEWORK,
                "items = relationship('Item')",
            ),
            "no_cascade_behavior": (
                AssumptionCategory.FRAMEWORK,
                "order_id = Column(Integer, ForeignKey('orders.id'))",
            ),
            "framework_version_dep": (
                AssumptionCategory.FRAMEWORK,
                "import django\nfrom django.db import models",
            ),
        }
        category, snippet = snippets[pattern_id]
        return _make_assumption(
            pattern_id=pattern_id,
            category=category,
            snippet=snippet,
            summary=f"Test assumption for {pattern_id}",
        )

    def test_options_count(self, pattern_assumption):
        result = generate_question(pattern_assumption)
        assert 3 <= len(result.options) <= 5, (
            f"Pattern {pattern_assumption.pattern_id} produced "
            f"{len(result.options)} options (expected 3-5)"
        )

    def test_last_option_is_context(self, pattern_assumption):
        result = generate_question(pattern_assumption)
        last = result.options[-1].lower()
        assert "context" in last or "more" in last, (
            f"Pattern {pattern_assumption.pattern_id}: last option should be "
            f"the 'need more context' escape hatch, got: {result.options[-1]}"
        )

    def test_no_yes_no_questions(self, pattern_assumption):
        result = generate_question(pattern_assumption)
        q = result.question.strip()
        assert not q.startswith("Does "), (
            f"Pattern {pattern_assumption.pattern_id}: question starts with "
            f"'Does' (yes/no). Question: {q}"
        )
        assert not q.startswith("Is "), (
            f"Pattern {pattern_assumption.pattern_id}: question starts with "
            f"'Is' (yes/no). Question: {q}"
        )

    def test_question_not_empty(self, pattern_assumption):
        result = generate_question(pattern_assumption)
        assert result.question.strip(), (
            f"Pattern {pattern_assumption.pattern_id}: question is empty"
        )


# ---------------------------------------------------------------------------
# TestPlaceholderExtraction — regex-based extraction from snippets
# ---------------------------------------------------------------------------


class TestPlaceholderExtraction:
    """Verify that _extract_placeholders pulls the right identifiers
    from realistic code snippets."""

    def test_extract_table_from_insert(self):
        assumption = _make_assumption(
            pattern_id="single_source_write",
            category=AssumptionCategory.DATA_SCOPE,
            snippet="db.execute('INSERT INTO transactions (id, amount) VALUES (?, ?)')",
        )
        placeholders = _extract_placeholders(assumption)
        assert placeholders["table"] == "transactions"

    def test_extract_function_name(self):
        assumption = _make_assumption(
            pattern_id="no_error_path",
            category=AssumptionCategory.COMPLETENESS,
            snippet="def process_payment(order_id):\n    charge(order_id)",
        )
        placeholders = _extract_placeholders(assumption)
        assert placeholders["function"] == "process_payment"

    def test_extract_role(self):
        assumption = _make_assumption(
            pattern_id="single_role_access",
            category=AssumptionCategory.ACCESS,
            snippet='role = "admin"\nif user.role == "admin":',
        )
        placeholders = _extract_placeholders(assumption)
        assert placeholders["role"] == "admin"

    def test_extract_env_var(self):
        assumption = _make_assumption(
            pattern_id="env_no_fallback",
            category=AssumptionCategory.CONFIGURATION,
            snippet='secret = os.environ["DATABASE_URL"]',
        )
        placeholders = _extract_placeholders(assumption)
        assert placeholders["var_name"] == "DATABASE_URL"


# ---------------------------------------------------------------------------
# TestGenericFallback — unknown patterns and category-level templates
# ---------------------------------------------------------------------------


class TestGenericFallback:
    """Verify that unknown pattern_ids still produce valid questions via
    category-level fallback templates."""

    def test_unknown_pattern_gets_generic(self):
        assumption = _make_assumption(
            pattern_id="nonexistent_pattern",
            category=AssumptionCategory.DATA_SCOPE,
            snippet="SELECT * FROM mysterious_table",
            summary="Unknown pattern should still get a question",
        )

        result = generate_question(assumption)

        # Should have a question and options despite unknown pattern_id
        assert result.question.strip()
        assert len(result.options) >= 3
        # Last option should still be the escape hatch
        last = result.options[-1].lower()
        assert "context" in last or "more" in last

    def test_all_categories_have_generic(self):
        """Every AssumptionCategory should produce a valid question even
        when pattern_id is unrecognized."""
        for category in AssumptionCategory:
            assumption = _make_assumption(
                pattern_id="totally_unknown_pattern_xyz",
                category=category,
                snippet="some_code()",
                summary=f"Generic assumption for {category.value}",
            )

            result = generate_question(assumption)

            assert result.question.strip(), (
                f"Category {category.value} produced empty question for unknown pattern"
            )
            assert len(result.options) >= 3, (
                f"Category {category.value} produced {len(result.options)} options "
                f"for unknown pattern (expected >= 3)"
            )


# ---------------------------------------------------------------------------
# TestQuestionIdempotent — generate_question does not mutate status
# ---------------------------------------------------------------------------


class TestQuestionIdempotent:
    def test_generate_does_not_change_status(self):
        """generate_question should populate question/options but must NOT
        alter the assumption's status (the caller transitions DETECTED → PROPOSED)."""
        for status in (AssumptionStatus.DETECTED, AssumptionStatus.PROPOSED):
            assumption = _make_assumption(
                pattern_id="single_source_write",
                category=AssumptionCategory.DATA_SCOPE,
                snippet="def save(x):\n    db.execute('INSERT INTO items VALUES (?)', x)",
                summary="Only save() writes to items",
                status=status,
            )

            result = generate_question(assumption)

            assert result.status == status, (
                f"generate_question changed status from {status} to {result.status}"
            )

    def test_does_not_overwrite_existing_question(self):
        """If question and options are already populated, generate_question
        should return the assumption unchanged (idempotent)."""
        assumption = _make_assumption(
            pattern_id="env_no_fallback",
            category=AssumptionCategory.CONFIGURATION,
            snippet='os.environ["SOME_KEY"]',
            summary="SOME_KEY must be set",
        )
        assumption.question = "Pre-existing question?"
        assumption.options = ["A) Yes", "B) No", "C) Maybe", "D) I need more context"]

        result = generate_question(assumption)

        assert result.question == "Pre-existing question?"
        assert result.options == ["A) Yes", "B) No", "C) Maybe", "D) I need more context"]

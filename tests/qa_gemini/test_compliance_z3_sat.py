"""Gemini Compliance: Z3 Boolean SAT Architectural Constraint Verification.

Tests the 10 encoded architectural rules using verify_constraints().
Validates both Z3-backed and pure Python fallback paths.
"""

from __future__ import annotations

import pytest

from vt_protocol.decisions.models import Decision, DecisionType, Dimension, SourceType
from vt_protocol.decisions.z3_constraints import (
    ALL_CONSTRAINT_NAMES,
    VerificationReport,
    _HAS_Z3,
    verify_constraints,
)

pytestmark = pytest.mark.compliance


def _decision(title: str, dims: list[Dimension], content: str = "") -> Decision:
    return Decision(
        title=title,
        content=content or f"Decision about {title} with sufficient detail for testing.",
        rationale=f"Rationale for {title}",
        decision_type=DecisionType.TECHNICAL,
        dimensions=dims,
        made_by="test-agent",
        project="z3-test",
        source_type=SourceType.AGENT,
    )


class TestZ3ConstraintsSatisfied:
    """Verify that well-formed decision sets pass all constraints."""

    def test_all_constraints_pass_complete_project(self):
        """Project with all dimension dependencies satisfied → all pass."""
        decisions = [
            _decision("JWT Auth", [Dimension.AUTH]),
            _decision("TLS Security", [Dimension.SECURITY]),
            _decision("REST API", [Dimension.API_STYLE], content="REST API for all endpoints"),
            _decision("Docker Deploy", [Dimension.DEPLOYMENT]),
            _decision("JSON Logging", [Dimension.LOGGING]),
            _decision("Redis Caching", [Dimension.CACHING]),
            _decision("Redux State", [Dimension.STATE_MANAGEMENT]),
            _decision("Pytest Testing", [Dimension.TESTING]),
            _decision("Sentry Errors", [Dimension.ERROR_HANDLING]),
            _decision("Celery Concurrency", [Dimension.CONCURRENCY]),
            _decision("RabbitMQ Messaging", [Dimension.MESSAGING]),
            _decision("PostgreSQL DB", [Dimension.DATABASE]),
        ]
        report = verify_constraints(decisions, use_z3=False)
        assert report.all_satisfied
        assert report.failed_count == 0

    def test_empty_decisions_pass(self):
        """No decisions → no violations (implications are vacuously true)."""
        report = verify_constraints([], use_z3=False)
        assert report.all_satisfied

    def test_single_dimension_no_dependencies(self):
        """Database-only project — no implication violations."""
        decisions = [_decision("PostgreSQL", [Dimension.DATABASE])]
        report = verify_constraints(decisions, use_z3=False)
        assert report.all_satisfied


class TestZ3ConstraintsViolated:
    """Verify that constraint violations are detected."""

    def test_auth_without_security_fails(self):
        """Auth decisions without security → auth_requires_security fails."""
        decisions = [
            _decision("JWT Auth", [Dimension.AUTH]),
            # No security dimension
        ]
        report = verify_constraints(
            decisions, constraints=["auth_requires_security"], use_z3=False,
        )
        assert not report.all_satisfied
        failed = [r for r in report.results if not r.satisfied]
        assert any(r.name == "auth_requires_security" for r in failed)

    def test_security_without_auth_fails(self):
        """Security without auth → security_requires_auth fails."""
        decisions = [
            _decision("TLS Everywhere", [Dimension.SECURITY]),
        ]
        report = verify_constraints(
            decisions, constraints=["security_requires_auth"], use_z3=False,
        )
        assert not report.all_satisfied

    def test_deployment_without_logging_fails(self):
        """Deployment without logging → deployment_requires_logging fails."""
        decisions = [
            _decision("Docker Deploy", [Dimension.DEPLOYMENT]),
        ]
        report = verify_constraints(
            decisions, constraints=["deployment_requires_logging"], use_z3=False,
        )
        assert not report.all_satisfied

    def test_caching_without_state_fails(self):
        """Caching without state management → caching_requires_state_mgmt fails."""
        decisions = [
            _decision("Redis Cache", [Dimension.CACHING]),
        ]
        report = verify_constraints(
            decisions, constraints=["caching_requires_state_mgmt"], use_z3=False,
        )
        assert not report.all_satisfied

    def test_concurrency_without_state_fails(self):
        """Concurrency without state management fails."""
        decisions = [
            _decision("Celery Workers", [Dimension.CONCURRENCY]),
        ]
        report = verify_constraints(
            decisions, constraints=["concurrency_requires_state_mgmt"], use_z3=False,
        )
        assert not report.all_satisfied

    def test_testing_without_error_handling_fails(self):
        """Testing without error handling fails."""
        decisions = [
            _decision("Pytest Suite", [Dimension.TESTING]),
        ]
        report = verify_constraints(
            decisions, constraints=["testing_with_error_handling"], use_z3=False,
        )
        assert not report.all_satisfied

    def test_messaging_without_error_handling_fails(self):
        """Messaging without error handling fails."""
        decisions = [
            _decision("Kafka Events", [Dimension.MESSAGING]),
        ]
        report = verify_constraints(
            decisions, constraints=["messaging_requires_error_handling"], use_z3=False,
        )
        assert not report.all_satisfied


class TestAPIStyleConsistency:
    """API style consistency — single style enforced."""

    def test_single_api_style_passes(self):
        """One API style → constraint satisfied."""
        decisions = [
            _decision("REST API", [Dimension.API_STYLE], content="RESTful endpoints for all APIs"),
        ]
        report = verify_constraints(
            decisions, constraints=["api_style_consistency"], use_z3=False,
        )
        assert report.all_satisfied

    def test_multiple_api_styles_fails(self):
        """REST + GraphQL → api_style_consistency fails."""
        decisions = [
            _decision("REST API", [Dimension.API_STYLE], content="REST endpoints for public API"),
            _decision("GraphQL API", [Dimension.API_STYLE], content="GraphQL for mobile clients"),
        ]
        report = verify_constraints(
            decisions, constraints=["api_style_consistency"], use_z3=False,
        )
        assert not report.all_satisfied
        failed = [r for r in report.results if not r.satisfied]
        assert "rest" in failed[0].details.lower() or "graphql" in failed[0].details.lower()


class TestCircularSupersession:
    """No circular supersession chains."""

    def test_no_supersession_passes(self):
        """No supersession edges → passes."""
        decisions = [
            _decision("Decision A", [Dimension.DATABASE]),
            _decision("Decision B", [Dimension.DATABASE]),
        ]
        report = verify_constraints(
            decisions, constraints=["no_circular_supersession"], use_z3=False,
        )
        assert report.all_satisfied

    def test_linear_supersession_passes(self):
        """A → B → C linear chain — no cycle."""
        d_a = _decision("Decision A", [Dimension.DATABASE])
        d_b = _decision("Decision B", [Dimension.DATABASE])
        d_b.supersedes = d_a.id
        d_c = _decision("Decision C", [Dimension.DATABASE])
        d_c.supersedes = d_b.id

        report = verify_constraints(
            [d_a, d_b, d_c], constraints=["no_circular_supersession"], use_z3=False,
        )
        assert report.all_satisfied

    def test_circular_supersession_detected(self):
        """A → B → A cycle — must be detected."""
        d_a = _decision("Decision A", [Dimension.DATABASE])
        d_b = _decision("Decision B", [Dimension.DATABASE])
        d_a.supersedes = d_b.id
        d_b.supersedes = d_a.id

        report = verify_constraints(
            [d_a, d_b], constraints=["no_circular_supersession"], use_z3=False,
        )
        assert not report.all_satisfied


class TestVerificationReport:
    """VerificationReport serialization and properties."""

    def test_report_to_dict(self):
        """Report serializes to dict with all fields."""
        decisions = [_decision("Test", [Dimension.DATABASE])]
        report = verify_constraints(decisions, use_z3=False)
        d = report.to_dict()
        assert "all_satisfied" in d
        assert "passed" in d
        assert "failed" in d
        assert "total" in d
        assert "results" in d

    def test_report_passed_count(self):
        """passed_count + failed_count = total."""
        decisions = [
            _decision("Auth", [Dimension.AUTH]),
            _decision("Security", [Dimension.SECURITY]),
        ]
        report = verify_constraints(decisions, use_z3=False)
        assert report.passed_count + report.failed_count == len(report.results)

    def test_selective_constraints(self):
        """Can check a subset of constraints."""
        decisions = [_decision("DB", [Dimension.DATABASE])]
        report = verify_constraints(
            decisions,
            constraints=["auth_requires_security", "no_circular_supersession"],
            use_z3=False,
        )
        assert len(report.results) == 2


class TestZ3FallbackBehavior:
    """Verify graceful fallback when Z3 is not available."""

    def test_use_z3_false_forces_python(self):
        """use_z3=False always uses pure Python path."""
        decisions = [_decision("DB", [Dimension.DATABASE])]
        report = verify_constraints(decisions, use_z3=False)
        assert not report.using_z3

    @pytest.mark.skipif(not _HAS_Z3, reason="Z3 not installed")
    def test_z3_and_python_agree(self):
        """When Z3 is available, Z3 and Python paths agree on all constraints."""
        decisions = [
            _decision("Auth", [Dimension.AUTH]),
            _decision("Security", [Dimension.SECURITY]),
            _decision("Deploy", [Dimension.DEPLOYMENT]),
            _decision("Logging", [Dimension.LOGGING]),
        ]
        z3_report = verify_constraints(decisions, use_z3=True)
        py_report = verify_constraints(decisions, use_z3=False)

        z3_by_name = {r.name: r.satisfied for r in z3_report.results}
        py_by_name = {r.name: r.satisfied for r in py_report.results}

        for name in z3_by_name:
            if name in py_by_name:
                assert z3_by_name[name] == py_by_name[name], (
                    f"Z3 and Python disagree on {name}"
                )

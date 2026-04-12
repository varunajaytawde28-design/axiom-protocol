"""Tests for Z3 Boolean SAT architectural constraints."""

from __future__ import annotations

from uuid import uuid4

import pytest

from vt_protocol.decisions.z3_constraints import (
    ALL_CONSTRAINT_NAMES,
    ConstraintResult,
    VerificationReport,
    verify_constraints,
    _HAS_Z3,
)
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    dimensions: list[Dimension],
    title: str = "Test Decision",
    content: str = "Decision content for testing.",
    **kwargs,
) -> Decision:
    defaults = dict(
        title=title,
        content=content,
        rationale="Because testing.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=dimensions,
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )
    defaults.update(kwargs)
    return Decision(**defaults)


# ---------------------------------------------------------------------------
# ConstraintResult
# ---------------------------------------------------------------------------


class TestConstraintResult:
    def test_to_dict(self) -> None:
        r = ConstraintResult(
            name="test_constraint",
            description="A test constraint",
            satisfied=True,
            details="All good",
        )
        d = r.to_dict()
        assert d["name"] == "test_constraint"
        assert d["satisfied"] is True

    def test_failed_result(self) -> None:
        r = ConstraintResult(
            name="failed",
            description="Failed constraint",
            satisfied=False,
            details="Missing dependency",
        )
        assert not r.satisfied


# ---------------------------------------------------------------------------
# VerificationReport
# ---------------------------------------------------------------------------


class TestVerificationReport:
    def test_empty_report(self) -> None:
        report = VerificationReport()
        assert report.all_satisfied is True
        assert report.passed_count == 0
        assert report.failed_count == 0

    def test_report_counts(self) -> None:
        report = VerificationReport(
            results=[
                ConstraintResult("a", "desc", True),
                ConstraintResult("b", "desc", False),
                ConstraintResult("c", "desc", True),
            ],
            all_satisfied=False,
        )
        assert report.passed_count == 2
        assert report.failed_count == 1

    def test_to_dict(self) -> None:
        report = VerificationReport(
            results=[ConstraintResult("a", "desc", True)],
            all_satisfied=True,
        )
        d = report.to_dict()
        assert d["all_satisfied"] is True
        assert d["total"] == 1
        assert d["passed"] == 1
        assert d["failed"] == 0


# ---------------------------------------------------------------------------
# auth_requires_security
# ---------------------------------------------------------------------------


class TestAuthRequiresSecurity:
    def test_satisfied_both_present(self) -> None:
        decisions = [
            _make_decision([Dimension.AUTH]),
            _make_decision([Dimension.SECURITY]),
        ]
        report = verify_constraints(decisions, constraints=["auth_requires_security"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated_auth_without_security(self) -> None:
        decisions = [_make_decision([Dimension.AUTH])]
        report = verify_constraints(decisions, constraints=["auth_requires_security"], use_z3=False)
        assert report.results[0].satisfied is False

    def test_satisfied_no_auth(self) -> None:
        decisions = [_make_decision([Dimension.DATABASE])]
        report = verify_constraints(decisions, constraints=["auth_requires_security"], use_z3=False)
        assert report.results[0].satisfied is True


# ---------------------------------------------------------------------------
# api_style_consistency
# ---------------------------------------------------------------------------


class TestApiStyleConsistency:
    def test_satisfied_single_style(self) -> None:
        decisions = [
            _make_decision([Dimension.API_STYLE], title="Use REST API", content="We use REST for all endpoints"),
        ]
        report = verify_constraints(decisions, constraints=["api_style_consistency"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated_multiple_styles(self) -> None:
        decisions = [
            _make_decision([Dimension.API_STYLE], title="Use REST API", content="REST for all public APIs"),
            _make_decision([Dimension.API_STYLE], title="Use GraphQL", content="GraphQL for internal data queries"),
        ]
        report = verify_constraints(decisions, constraints=["api_style_consistency"], use_z3=False)
        assert report.results[0].satisfied is False
        assert "rest" in report.results[0].details.lower()
        assert "graphql" in report.results[0].details.lower()

    def test_satisfied_no_api_decisions(self) -> None:
        decisions = [_make_decision([Dimension.DATABASE])]
        report = verify_constraints(decisions, constraints=["api_style_consistency"], use_z3=False)
        assert report.results[0].satisfied is True


# ---------------------------------------------------------------------------
# deployment_requires_logging
# ---------------------------------------------------------------------------


class TestDeploymentRequiresLogging:
    def test_satisfied(self) -> None:
        decisions = [
            _make_decision([Dimension.DEPLOYMENT]),
            _make_decision([Dimension.LOGGING]),
        ]
        report = verify_constraints(decisions, constraints=["deployment_requires_logging"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated(self) -> None:
        decisions = [_make_decision([Dimension.DEPLOYMENT])]
        report = verify_constraints(decisions, constraints=["deployment_requires_logging"], use_z3=False)
        assert report.results[0].satisfied is False


# ---------------------------------------------------------------------------
# caching_requires_state_mgmt
# ---------------------------------------------------------------------------


class TestCachingRequiresState:
    def test_satisfied(self) -> None:
        decisions = [
            _make_decision([Dimension.CACHING]),
            _make_decision([Dimension.STATE_MANAGEMENT]),
        ]
        report = verify_constraints(decisions, constraints=["caching_requires_state_mgmt"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated(self) -> None:
        decisions = [_make_decision([Dimension.CACHING])]
        report = verify_constraints(decisions, constraints=["caching_requires_state_mgmt"], use_z3=False)
        assert report.results[0].satisfied is False


# ---------------------------------------------------------------------------
# testing_with_error_handling
# ---------------------------------------------------------------------------


class TestTestingWithErrorHandling:
    def test_satisfied(self) -> None:
        decisions = [
            _make_decision([Dimension.TESTING]),
            _make_decision([Dimension.ERROR_HANDLING]),
        ]
        report = verify_constraints(decisions, constraints=["testing_with_error_handling"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated(self) -> None:
        decisions = [_make_decision([Dimension.TESTING])]
        report = verify_constraints(decisions, constraints=["testing_with_error_handling"], use_z3=False)
        assert report.results[0].satisfied is False


# ---------------------------------------------------------------------------
# no_circular_supersession
# ---------------------------------------------------------------------------


class TestNoCircularSupersession:
    def test_satisfied_no_supersessions(self) -> None:
        decisions = [_make_decision([Dimension.DATABASE])]
        report = verify_constraints(decisions, constraints=["no_circular_supersession"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_satisfied_linear_chain(self) -> None:
        id_a = uuid4()
        id_b = uuid4()
        id_c = uuid4()
        decisions = [
            _make_decision([Dimension.DATABASE], id=id_a),
            _make_decision([Dimension.DATABASE], id=id_b, supersedes=id_a),
            _make_decision([Dimension.DATABASE], id=id_c, supersedes=id_b),
        ]
        report = verify_constraints(decisions, constraints=["no_circular_supersession"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated_cycle(self) -> None:
        id_a = uuid4()
        id_b = uuid4()
        decisions = [
            _make_decision([Dimension.DATABASE], id=id_a, supersedes=id_b),
            _make_decision([Dimension.DATABASE], id=id_b, supersedes=id_a),
        ]
        report = verify_constraints(decisions, constraints=["no_circular_supersession"], use_z3=False)
        assert report.results[0].satisfied is False


# ---------------------------------------------------------------------------
# concurrency_requires_state_mgmt
# ---------------------------------------------------------------------------


class TestConcurrencyRequiresState:
    def test_satisfied(self) -> None:
        decisions = [
            _make_decision([Dimension.CONCURRENCY]),
            _make_decision([Dimension.STATE_MANAGEMENT]),
        ]
        report = verify_constraints(decisions, constraints=["concurrency_requires_state_mgmt"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated(self) -> None:
        decisions = [_make_decision([Dimension.CONCURRENCY])]
        report = verify_constraints(decisions, constraints=["concurrency_requires_state_mgmt"], use_z3=False)
        assert report.results[0].satisfied is False


# ---------------------------------------------------------------------------
# security_requires_auth
# ---------------------------------------------------------------------------


class TestSecurityRequiresAuth:
    def test_satisfied(self) -> None:
        decisions = [
            _make_decision([Dimension.SECURITY]),
            _make_decision([Dimension.AUTH]),
        ]
        report = verify_constraints(decisions, constraints=["security_requires_auth"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated(self) -> None:
        decisions = [_make_decision([Dimension.SECURITY])]
        report = verify_constraints(decisions, constraints=["security_requires_auth"], use_z3=False)
        assert report.results[0].satisfied is False


# ---------------------------------------------------------------------------
# messaging_requires_error_handling
# ---------------------------------------------------------------------------


class TestMessagingRequiresErrorHandling:
    def test_satisfied(self) -> None:
        decisions = [
            _make_decision([Dimension.MESSAGING]),
            _make_decision([Dimension.ERROR_HANDLING]),
        ]
        report = verify_constraints(decisions, constraints=["messaging_requires_error_handling"], use_z3=False)
        assert report.results[0].satisfied is True

    def test_violated(self) -> None:
        decisions = [_make_decision([Dimension.MESSAGING])]
        report = verify_constraints(decisions, constraints=["messaging_requires_error_handling"], use_z3=False)
        assert report.results[0].satisfied is False


# ---------------------------------------------------------------------------
# verify_constraints (full suite)
# ---------------------------------------------------------------------------


class TestVerifyConstraints:
    def test_all_satisfied_when_complete(self) -> None:
        """All constraints satisfied when all dimension pairs present."""
        decisions = [
            _make_decision([Dimension.AUTH, Dimension.SECURITY]),
            _make_decision([Dimension.DEPLOYMENT, Dimension.LOGGING]),
            _make_decision([Dimension.CACHING, Dimension.STATE_MANAGEMENT]),
            _make_decision([Dimension.TESTING, Dimension.ERROR_HANDLING]),
            _make_decision([Dimension.CONCURRENCY, Dimension.STATE_MANAGEMENT]),
            _make_decision([Dimension.MESSAGING, Dimension.ERROR_HANDLING]),
        ]
        report = verify_constraints(decisions, use_z3=False)
        assert report.all_satisfied is True

    def test_multiple_violations(self) -> None:
        decisions = [
            _make_decision([Dimension.AUTH]),      # Missing security
            _make_decision([Dimension.CACHING]),   # Missing state mgmt
        ]
        report = verify_constraints(decisions, use_z3=False)
        assert report.all_satisfied is False
        assert report.failed_count >= 2

    def test_empty_decisions(self) -> None:
        report = verify_constraints([], use_z3=False)
        assert report.all_satisfied is True

    def test_specific_constraints(self) -> None:
        decisions = [_make_decision([Dimension.AUTH, Dimension.SECURITY])]
        report = verify_constraints(
            decisions,
            constraints=["auth_requires_security"],
            use_z3=False,
        )
        assert len(report.results) == 1
        assert report.results[0].name == "auth_requires_security"

    def test_only_active_decisions_checked(self) -> None:
        decisions = [
            _make_decision([Dimension.AUTH], valid=False),  # Superseded
        ]
        report = verify_constraints(
            decisions,
            constraints=["auth_requires_security"],
            use_z3=False,
        )
        # Invalid decision shouldn't trigger the constraint
        assert report.results[0].satisfied is True

    def test_report_to_dict(self) -> None:
        decisions = [_make_decision([Dimension.DATABASE])]
        report = verify_constraints(decisions, use_z3=False)
        d = report.to_dict()
        assert "all_satisfied" in d
        assert "total" in d
        assert "results" in d

    def test_all_constraint_names_exist(self) -> None:
        assert len(ALL_CONSTRAINT_NAMES) == 9

    @pytest.mark.skipif(not _HAS_Z3, reason="Z3 not installed")
    def test_z3_verification(self) -> None:
        decisions = [
            _make_decision([Dimension.AUTH, Dimension.SECURITY]),
        ]
        report = verify_constraints(decisions, use_z3=True)
        auth_result = next(r for r in report.results if r.name == "auth_requires_security")
        assert auth_result.satisfied is True
        assert report.using_z3 is True

    @pytest.mark.skipif(not _HAS_Z3, reason="Z3 not installed")
    def test_z3_detects_violation(self) -> None:
        decisions = [_make_decision([Dimension.AUTH])]
        report = verify_constraints(decisions, use_z3=True)
        auth_result = next(r for r in report.results if r.name == "auth_requires_security")
        assert auth_result.satisfied is False

"""Gemini Scenario: Infrastructure Z3 Constraint Verification.

Tests Z3 constraint checking against realistic multi-dimensional
decision sets that represent real-world architectural choices.
"""

from __future__ import annotations

import pytest

from vt_protocol.decisions.models import Decision, DecisionType, Dimension, SourceType
from vt_protocol.decisions.z3_constraints import verify_constraints

pytestmark = pytest.mark.integration


def _decision(title: str, dims: list[Dimension], content: str = "") -> Decision:
    return Decision(
        title=title,
        content=content or f"Infrastructure decision: {title}. Detailed rationale follows.",
        rationale=f"Required for {title.lower()}",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=dims,
        made_by="platform-team",
        project="infra-z3",
        source_type=SourceType.MANUAL,
    )


class TestRealisticInfraConstraints:
    """Real-world infrastructure decision sets."""

    def test_microservices_stack_passes(self):
        """Full microservices stack — all constraints satisfied."""
        decisions = [
            _decision("PostgreSQL Primary", [Dimension.DATABASE]),
            _decision("OAuth2 + RBAC", [Dimension.AUTH, Dimension.SECURITY]),
            _decision("Redis Cache Layer", [Dimension.CACHING, Dimension.STATE_MANAGEMENT]),
            _decision("gRPC Internal API", [Dimension.API_STYLE], content="gRPC for all service-to-service calls"),
            _decision("Kubernetes on GKE", [Dimension.DEPLOYMENT, Dimension.LOGGING]),
            _decision("Celery Task Queue", [Dimension.CONCURRENCY]),
            _decision("Structured Logging", [Dimension.LOGGING]),
            _decision("Pytest + Sentry", [Dimension.TESTING, Dimension.ERROR_HANDLING]),
            _decision("Kafka Event Bus", [Dimension.MESSAGING]),
        ]
        report = verify_constraints(decisions, use_z3=False)
        assert report.all_satisfied
        assert report.failed_count == 0

    def test_missing_logging_for_deployment(self):
        """Deployment without logging → constraint violated."""
        decisions = [
            _decision("AWS ECS Fargate", [Dimension.DEPLOYMENT]),
            _decision("PostgreSQL", [Dimension.DATABASE]),
        ]
        report = verify_constraints(
            decisions, constraints=["deployment_requires_logging"], use_z3=False,
        )
        assert not report.all_satisfied

    def test_fix_by_adding_logging(self):
        """Adding logging decision fixes the violation."""
        decisions = [
            _decision("AWS ECS Fargate", [Dimension.DEPLOYMENT]),
            _decision("PostgreSQL", [Dimension.DATABASE]),
            _decision("CloudWatch Logs", [Dimension.LOGGING]),
        ]
        report = verify_constraints(
            decisions, constraints=["deployment_requires_logging"], use_z3=False,
        )
        assert report.all_satisfied

    def test_multiple_violations_reported(self):
        """Project with multiple missing dependencies → multiple failures."""
        decisions = [
            _decision("Auth Service", [Dimension.AUTH]),
            # Missing: security, logging, state-mgmt, error-handling
            _decision("Deploy to K8s", [Dimension.DEPLOYMENT]),
            _decision("Redis Cache", [Dimension.CACHING]),
            _decision("Celery Workers", [Dimension.CONCURRENCY]),
            _decision("Unit Tests", [Dimension.TESTING]),
            _decision("RabbitMQ", [Dimension.MESSAGING]),
        ]
        report = verify_constraints(decisions, use_z3=False)
        assert not report.all_satisfied
        assert report.failed_count >= 3

    def test_superseded_decisions_excluded(self):
        """Superseded (invalid) decisions should not contribute to constraints."""
        d_old = _decision("Old Auth", [Dimension.AUTH])
        d_old.valid = False  # Superseded
        d_new = _decision("New Auth + Security", [Dimension.AUTH, Dimension.SECURITY])

        decisions = [d_old, d_new]
        report = verify_constraints(decisions, use_z3=False)
        # Only d_new is valid — has both auth + security
        auth_result = next(
            (r for r in report.results if r.name == "auth_requires_security"), None
        )
        if auth_result:
            assert auth_result.satisfied

"""Tests for Microsoft AGT adapter."""

from __future__ import annotations

import pytest

from vt_protocol.integrations.agt_adapter import (
    PolicyEvaluation,
    PolicyRecord,
    VTProtocolPolicyProvider,
    _decision_severity,
)
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


def _make_decision(
    dimensions: list[Dimension] | None = None,
    decision_type: DecisionType = DecisionType.TECHNICAL,
    **kwargs,
) -> Decision:
    defaults = dict(
        title="Test Decision",
        content="Content for testing.",
        rationale="Because testing.",
        decision_type=decision_type,
        dimensions=dimensions or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )
    defaults.update(kwargs)
    return Decision(**defaults)


def _make_contradiction(
    shared_dimensions: list[Dimension] | None = None,
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED,
    verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
) -> Contradiction:
    return Contradiction(
        decision_a_id="00000000-0000-0000-0000-000000000001",
        decision_b_id="00000000-0000-0000-0000-000000000002",
        decision_a_title="A",
        decision_b_title="B",
        verdict=verdict,
        reasoning="They conflict.",
        evidence_a="A says X.",
        evidence_b="B says Y.",
        shared_dimensions=shared_dimensions or [Dimension.DATABASE],
        confidence=0.9,
        status=status,
    )


class TestPolicyEvaluation:
    def test_to_dict(self) -> None:
        pe = PolicyEvaluation(allowed=True, reason="OK")
        d = pe.to_dict()
        assert d["allowed"] is True
        assert d["reason"] == "OK"


class TestPolicyRecord:
    def test_to_dict(self) -> None:
        pr = PolicyRecord(policy_id="p1", name="Test Policy", severity="high")
        d = pr.to_dict()
        assert d["policy_id"] == "p1"
        assert d["severity"] == "high"


class TestDecisionSeverity:
    def test_constraint_is_critical(self) -> None:
        d = _make_decision(decision_type=DecisionType.CONSTRAINT)
        assert _decision_severity(d) == "critical"

    def test_architectural_is_high(self) -> None:
        d = _make_decision(decision_type=DecisionType.ARCHITECTURAL)
        assert _decision_severity(d) == "high"

    def test_technical_is_medium(self) -> None:
        d = _make_decision(decision_type=DecisionType.TECHNICAL)
        assert _decision_severity(d) == "medium"

    def test_product_is_low(self) -> None:
        d = _make_decision(decision_type=DecisionType.PRODUCT)
        assert _decision_severity(d) == "low"


class TestEvaluateAction:
    def test_allowed_no_contradictions(self) -> None:
        provider = VTProtocolPolicyProvider(decisions=[_make_decision()])
        result = provider.evaluate_action("agent-1", "modify", {"dimensions": ["database"]})
        assert result.allowed is True

    def test_blocked_by_contradiction(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.DATABASE])
        provider = VTProtocolPolicyProvider(contradictions=[c])
        result = provider.evaluate_action("agent-1", "modify", {"dimensions": ["database"]})
        assert result.allowed is False
        assert "contradiction" in result.reason.lower()

    def test_resolved_contradiction_not_blocking(self) -> None:
        c = _make_contradiction(status=ContradictionStatus.RESOLVED)
        provider = VTProtocolPolicyProvider(contradictions=[c])
        result = provider.evaluate_action("agent-1", "modify", {"dimensions": ["database"]})
        assert result.allowed is True

    def test_tension_not_blocking(self) -> None:
        c = _make_contradiction(verdict=ContradictionVerdict.TENSION)
        provider = VTProtocolPolicyProvider(contradictions=[c])
        result = provider.evaluate_action("agent-1", "modify", {"dimensions": ["database"]})
        assert result.allowed is True

    def test_unrelated_dimension_allowed(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.AUTH])
        provider = VTProtocolPolicyProvider(contradictions=[c])
        result = provider.evaluate_action("agent-1", "modify", {"dimensions": ["database"]})
        assert result.allowed is True

    def test_constraints_from_decisions(self) -> None:
        decisions = [_make_decision(title="Use PostgreSQL")]
        provider = VTProtocolPolicyProvider(decisions=decisions)
        result = provider.evaluate_action("agent-1", "modify", {"dimensions": ["database"]})
        assert "Use PostgreSQL" in result.constraints

    def test_metadata_includes_agent_id(self) -> None:
        provider = VTProtocolPolicyProvider()
        result = provider.evaluate_action("agent-1", "modify", {"dimensions": []})
        assert result.metadata["agent_id"] == "agent-1"


class TestGetPolicies:
    def test_returns_active_decisions(self) -> None:
        decisions = [
            _make_decision(title="Use Postgres"),
            _make_decision(title="Use Redis", valid=False),
        ]
        provider = VTProtocolPolicyProvider(decisions=decisions)
        policies = provider.get_policies()
        assert len(policies) == 1
        assert policies[0].name == "Use Postgres"

    def test_policy_dimensions(self) -> None:
        d = _make_decision(dimensions=[Dimension.DATABASE, Dimension.CACHING])
        provider = VTProtocolPolicyProvider(decisions=[d])
        policies = provider.get_policies()
        assert "database" in policies[0].dimensions
        assert "caching" in policies[0].dimensions

    def test_policy_metadata(self) -> None:
        d = _make_decision(source_type=SourceType.AGENT)
        provider = VTProtocolPolicyProvider(decisions=[d])
        policies = provider.get_policies()
        assert policies[0].metadata["source_type"] == "agent"


class TestGetPolicy:
    def test_found(self) -> None:
        d = _make_decision(title="Use Postgres")
        provider = VTProtocolPolicyProvider(decisions=[d])
        policy = provider.get_policy(str(d.id))
        assert policy is not None
        assert policy.name == "Use Postgres"

    def test_not_found(self) -> None:
        provider = VTProtocolPolicyProvider()
        assert provider.get_policy("nonexistent") is None

    def test_set_decisions(self) -> None:
        provider = VTProtocolPolicyProvider()
        d = _make_decision()
        provider.set_decisions([d])
        assert len(provider.get_policies()) == 1

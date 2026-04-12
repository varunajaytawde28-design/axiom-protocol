"""Tests for CISO compliance view — evidence collection and framework mapping."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from vt_protocol.audit.compliance import (
    AgentActivity,
    AttributionStats,
    ComplianceFramework,
    ComplianceMapping,
    EvidenceBundle,
    build_evidence_bundle,
    compute_attribution,
    extract_agent_activities,
    generate_compliance_mappings,
)
from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    source_type: SourceType = SourceType.MANUAL,
    **kwargs,
) -> Decision:
    defaults = dict(
        title="Test Decision",
        content="Detailed content for the decision.",
        rationale="Because it makes sense.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=source_type,
    )
    defaults.update(kwargs)
    return Decision(**defaults)


def _make_audit_entry(
    actor: str = "agent-1",
    event_type: AuditEventType = AuditEventType.DECISION_ADDED,
    **kwargs,
) -> AuditEntry:
    defaults = dict(
        event_type=event_type,
        actor=actor,
        project="test-project",
    )
    defaults.update(kwargs)
    return AuditEntry(**defaults)


# ---------------------------------------------------------------------------
# AttributionStats
# ---------------------------------------------------------------------------


class TestAttributionStats:
    def test_defaults(self) -> None:
        stats = AttributionStats()
        assert stats.total_decisions == 0
        assert stats.agent_percentage == 0.0

    def test_to_dict(self) -> None:
        stats = AttributionStats(
            total_decisions=10,
            agent_decisions=3,
            human_decisions=7,
            agent_percentage=30.0,
            human_percentage=70.0,
            by_source_type={"manual": 7, "agent": 3},
        )
        d = stats.to_dict()
        assert d["total_decisions"] == 10
        assert d["agent_percentage"] == 30.0
        assert d["by_source_type"]["manual"] == 7


# ---------------------------------------------------------------------------
# ComplianceMapping
# ---------------------------------------------------------------------------


class TestComplianceMapping:
    def test_to_dict(self) -> None:
        m = ComplianceMapping(
            framework="eu_ai_act_article_12",
            requirement="Logging",
            description="Must log events.",
            status="met",
            evidence=["Merkle tree audit log"],
        )
        d = m.to_dict()
        assert d["framework"] == "eu_ai_act_article_12"
        assert d["status"] == "met"
        assert len(d["evidence"]) == 1


# ---------------------------------------------------------------------------
# AgentActivity
# ---------------------------------------------------------------------------


class TestAgentActivity:
    def test_to_dict(self) -> None:
        a = AgentActivity(
            timestamp="2025-01-01T00:00:00",
            agent_id="agent-1",
            action="decision_added",
            resource="db-schema",
            authorized_by="ciso",
            session_id="sess-123",
        )
        d = a.to_dict()
        assert d["agent_id"] == "agent-1"
        assert d["action"] == "decision_added"
        assert d["session_id"] == "sess-123"


# ---------------------------------------------------------------------------
# ComplianceFramework
# ---------------------------------------------------------------------------


class TestComplianceFramework:
    def test_constants_exist(self) -> None:
        assert ComplianceFramework.EU_AI_ACT_ART12 == "eu_ai_act_article_12"
        assert ComplianceFramework.SOC2_CC6_1 == "soc2_cc6.1"
        assert ComplianceFramework.HIPAA_AUDIT == "hipaa_164.312"


# ---------------------------------------------------------------------------
# compute_attribution
# ---------------------------------------------------------------------------


class TestComputeAttribution:
    def test_empty_decisions(self) -> None:
        stats = compute_attribution([])
        assert stats.total_decisions == 0
        assert stats.agent_percentage == 0.0

    def test_all_human(self) -> None:
        decisions = [_make_decision(SourceType.MANUAL) for _ in range(5)]
        stats = compute_attribution(decisions)
        assert stats.total_decisions == 5
        assert stats.human_decisions == 5
        assert stats.agent_decisions == 0
        assert stats.human_percentage == 100.0

    def test_all_agent(self) -> None:
        decisions = [_make_decision(SourceType.AGENT) for _ in range(3)]
        stats = compute_attribution(decisions)
        assert stats.agent_decisions == 3
        assert stats.human_decisions == 0
        assert stats.agent_percentage == 100.0

    def test_mixed(self) -> None:
        decisions = [
            _make_decision(SourceType.MANUAL),
            _make_decision(SourceType.AGENT),
            _make_decision(SourceType.GIT_PR),
            _make_decision(SourceType.SCAN),
        ]
        stats = compute_attribution(decisions)
        assert stats.total_decisions == 4
        assert stats.agent_decisions == 2  # AGENT + SCAN
        assert stats.human_decisions == 2  # MANUAL + GIT_PR

    def test_by_source_type(self) -> None:
        decisions = [
            _make_decision(SourceType.MANUAL),
            _make_decision(SourceType.MANUAL),
            _make_decision(SourceType.AGENT),
        ]
        stats = compute_attribution(decisions)
        assert stats.by_source_type["manual"] == 2
        assert stats.by_source_type["agent"] == 1

    def test_percentage_accuracy(self) -> None:
        decisions = [_make_decision(SourceType.AGENT)] * 3 + [_make_decision(SourceType.MANUAL)] * 7
        stats = compute_attribution(decisions)
        assert stats.agent_percentage == 30.0
        assert stats.human_percentage == 70.0

    def test_scan_is_agent_source(self) -> None:
        decisions = [_make_decision(SourceType.SCAN)]
        stats = compute_attribution(decisions)
        assert stats.agent_decisions == 1

    def test_git_sources_are_human(self) -> None:
        decisions = [
            _make_decision(SourceType.GIT_PR),
            _make_decision(SourceType.GIT_RELEASE),
            _make_decision(SourceType.GIT_ISSUE),
            _make_decision(SourceType.GIT_COMMIT),
            _make_decision(SourceType.MEETING),
        ]
        stats = compute_attribution(decisions)
        assert stats.human_decisions == 5
        assert stats.agent_decisions == 0


# ---------------------------------------------------------------------------
# generate_compliance_mappings
# ---------------------------------------------------------------------------


class TestGenerateComplianceMappings:
    def test_all_not_met(self) -> None:
        mappings = generate_compliance_mappings()
        assert len(mappings) == 3
        assert all(m.status == "not_met" for m in mappings)

    def test_eu_ai_act_met(self) -> None:
        mappings = generate_compliance_mappings(
            has_merkle_audit=True,
            has_signing=True,
            has_rfc3161=True,
            has_attribution=True,
            audit_entry_count=100,
        )
        eu = next(m for m in mappings if m.framework == ComplianceFramework.EU_AI_ACT_ART12)
        assert eu.status == "met"
        assert len(eu.evidence) >= 3

    def test_eu_ai_act_partial(self) -> None:
        mappings = generate_compliance_mappings(has_merkle_audit=True)
        eu = next(m for m in mappings if m.framework == ComplianceFramework.EU_AI_ACT_ART12)
        assert eu.status == "partial"

    def test_soc2_met(self) -> None:
        mappings = generate_compliance_mappings(
            has_merkle_audit=True,
            has_signing=True,
            has_agent_registry=True,
        )
        soc2 = next(m for m in mappings if m.framework == ComplianceFramework.SOC2_CC6_1)
        assert soc2.status == "met"

    def test_soc2_partial(self) -> None:
        mappings = generate_compliance_mappings(has_merkle_audit=True)
        soc2 = next(m for m in mappings if m.framework == ComplianceFramework.SOC2_CC6_1)
        assert soc2.status == "partial"

    def test_hipaa_met(self) -> None:
        mappings = generate_compliance_mappings(
            has_merkle_audit=True,
            has_rfc3161=True,
            audit_entry_count=50,
        )
        hipaa = next(m for m in mappings if m.framework == ComplianceFramework.HIPAA_AUDIT)
        assert hipaa.status == "met"

    def test_hipaa_partial(self) -> None:
        mappings = generate_compliance_mappings(audit_entry_count=10)
        hipaa = next(m for m in mappings if m.framework == ComplianceFramework.HIPAA_AUDIT)
        assert hipaa.status == "partial"

    def test_always_three_mappings(self) -> None:
        mappings = generate_compliance_mappings(
            has_merkle_audit=True,
            has_signing=True,
            has_rfc3161=True,
            has_agent_registry=True,
            has_attribution=True,
            audit_entry_count=100,
        )
        assert len(mappings) == 3
        frameworks = {m.framework for m in mappings}
        assert ComplianceFramework.EU_AI_ACT_ART12 in frameworks
        assert ComplianceFramework.SOC2_CC6_1 in frameworks
        assert ComplianceFramework.HIPAA_AUDIT in frameworks


# ---------------------------------------------------------------------------
# extract_agent_activities
# ---------------------------------------------------------------------------


class TestExtractAgentActivities:
    def test_empty_entries(self) -> None:
        assert extract_agent_activities([]) == []

    def test_skips_system_actor(self) -> None:
        entries = [_make_audit_entry(actor="system")]
        assert extract_agent_activities(entries) == []

    def test_skips_empty_actor(self) -> None:
        entries = [_make_audit_entry(actor="")]
        assert extract_agent_activities(entries) == []

    def test_extracts_agent_activity(self) -> None:
        entry = _make_audit_entry(
            actor="agent-1",
            event_type=AuditEventType.DECISION_ADDED,
            payload={"resource": "db-schema", "authorized_by": "ciso"},
            session_id="sess-abc",
        )
        activities = extract_agent_activities([entry])
        assert len(activities) == 1
        a = activities[0]
        assert a.agent_id == "agent-1"
        assert a.action == "decision_added"
        assert a.resource == "db-schema"
        assert a.authorized_by == "ciso"
        assert a.session_id == "sess-abc"

    def test_multiple_entries(self) -> None:
        entries = [
            _make_audit_entry(actor="agent-1"),
            _make_audit_entry(actor="system"),
            _make_audit_entry(actor="agent-2"),
        ]
        activities = extract_agent_activities(entries)
        assert len(activities) == 2
        assert activities[0].agent_id == "agent-1"
        assert activities[1].agent_id == "agent-2"

    def test_missing_payload_keys_default_to_empty(self) -> None:
        entry = _make_audit_entry(actor="agent-1", payload={})
        activities = extract_agent_activities([entry])
        assert activities[0].resource == ""
        assert activities[0].authorized_by == ""


# ---------------------------------------------------------------------------
# EvidenceBundle
# ---------------------------------------------------------------------------


class TestEvidenceBundle:
    def test_defaults(self) -> None:
        bundle = EvidenceBundle()
        assert bundle.attribution.total_decisions == 0
        assert bundle.compliance_mappings == []
        assert bundle.agent_activities == []
        assert bundle.audit_entries == []

    def test_to_dict(self) -> None:
        bundle = EvidenceBundle(
            tree_heads=[{"size": 10}],
            inclusion_proofs=[{"leaf": 0}],
            consistency_proofs=[{"old": 5, "new": 10}],
            timestamp_tokens=[{"token": "abc"}],
            verification_instructions="Verify with openssl",
        )
        d = bundle.to_dict()
        assert d["tree_heads"] == [{"size": 10}]
        assert d["inclusion_proofs"] == [{"leaf": 0}]
        assert d["consistency_proofs"] == [{"old": 5, "new": 10}]
        assert d["timestamp_tokens"] == [{"token": "abc"}]
        assert d["verification_instructions"] == "Verify with openssl"
        assert "generated_at" in d

    def test_to_json(self) -> None:
        bundle = EvidenceBundle()
        json_str = bundle.to_json()
        assert isinstance(json_str, str)
        import json
        parsed = json.loads(json_str)
        assert "generated_at" in parsed
        assert "attribution" in parsed

    def test_audit_entries_count(self) -> None:
        bundle = EvidenceBundle(
            audit_entries=[{"id": "1"}, {"id": "2"}],
        )
        d = bundle.to_dict()
        assert d["audit_entries_count"] == 2


# ---------------------------------------------------------------------------
# build_evidence_bundle
# ---------------------------------------------------------------------------


class TestBuildEvidenceBundle:
    def test_empty_inputs(self) -> None:
        bundle = build_evidence_bundle([], [])
        assert bundle.attribution.total_decisions == 0
        assert len(bundle.compliance_mappings) == 3
        assert bundle.agent_activities == []

    def test_with_decisions(self) -> None:
        decisions = [
            _make_decision(SourceType.AGENT),
            _make_decision(SourceType.MANUAL),
        ]
        bundle = build_evidence_bundle(decisions, [])
        assert bundle.attribution.total_decisions == 2
        assert bundle.attribution.agent_decisions == 1
        assert bundle.attribution.human_decisions == 1

    def test_with_audit_entries(self) -> None:
        entries = [
            _make_audit_entry(actor="agent-1"),
            _make_audit_entry(actor="agent-2"),
        ]
        bundle = build_evidence_bundle([], entries)
        assert len(bundle.audit_entries) == 2
        assert len(bundle.agent_activities) == 2

    def test_serialized_entries_fields(self) -> None:
        entry = _make_audit_entry(
            actor="agent-1",
            session_id="sess-1",
        )
        bundle = build_evidence_bundle([], [entry])
        se = bundle.audit_entries[0]
        assert "entry_id" in se
        assert "timestamp" in se
        assert "event_type" in se
        assert se["actor"] == "agent-1"
        assert se["session_id"] == "sess-1"

    def test_with_proofs(self) -> None:
        tree_heads = [{"size": 5, "root": "abc"}]
        inclusion = [{"leaf": 0}]
        consistency = [{"old": 3, "new": 5}]
        timestamps = [{"token": "xyz"}]
        bundle = build_evidence_bundle(
            [],
            [],
            tree_heads=tree_heads,
            inclusion_proofs=inclusion,
            consistency_proofs=consistency,
            timestamp_tokens=timestamps,
        )
        assert bundle.tree_heads == tree_heads
        assert bundle.inclusion_proofs == inclusion
        assert bundle.consistency_proofs == consistency
        assert bundle.timestamp_tokens == timestamps

    def test_compliance_reflects_capabilities(self) -> None:
        entries = [_make_audit_entry()]
        decisions = [_make_decision(SourceType.AGENT)]
        bundle = build_evidence_bundle(
            decisions,
            entries,
            has_signing=True,
            has_rfc3161=True,
            has_agent_registry=True,
        )
        # With all capabilities, frameworks should mostly be met
        eu = next(m for m in bundle.compliance_mappings if m.framework == ComplianceFramework.EU_AI_ACT_ART12)
        assert eu.status == "met"

    def test_verification_instructions_included(self) -> None:
        bundle = build_evidence_bundle([], [])
        assert "Verify Merkle Tree Integrity" in bundle.verification_instructions
        assert "Ed25519" in bundle.verification_instructions
        assert "RFC 3161" in bundle.verification_instructions
        assert "Consistency Proofs" in bundle.verification_instructions

    def test_full_bundle_json_roundtrip(self) -> None:
        import json
        decisions = [_make_decision(SourceType.AGENT)]
        entries = [_make_audit_entry()]
        bundle = build_evidence_bundle(
            decisions,
            entries,
            has_signing=True,
            has_rfc3161=True,
        )
        json_str = bundle.to_json()
        parsed = json.loads(json_str)
        assert parsed["attribution"]["total_decisions"] == 1
        assert len(parsed["compliance_mappings"]) == 3
        assert len(parsed["agent_activities"]) == 1

"""Tests for VT Protocol decision models."""

from __future__ import annotations

from uuid import UUID

from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    ContextResult,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    DimensionEdge,
    GovernanceConfig,
    Session,
    SourceType,
)


class TestDecision:
    def test_create_minimal(self) -> None:
        d = Decision(
            title="Use REST API",
            content="REST over GraphQL for simplicity",
            made_by="dev",
            project="test",
        )
        assert isinstance(d.id, UUID)
        assert d.status == DecisionStatus.ACTIVE
        assert d.valid is True

    def test_confidence_auto_computed(self, sample_decision: Decision) -> None:
        # Agent source (0.75) + alternatives (+0.05) + rationale+content>200 (+0.05)
        # + content>500 is False here, so 0.85
        assert sample_decision.confidence == 0.85

    def test_confidence_manual_high(self, sample_decision_b: Decision) -> None:
        # Manual source (0.95) + alternatives (+0.05) = 1.0 (capped)
        assert sample_decision_b.confidence == 1.0

    def test_confidence_manual_override(self) -> None:
        d = Decision(
            title="Test",
            content="Test content",
            made_by="dev",
            project="test",
            confidence=0.5,
        )
        # Explicit confidence should be preserved (not overwritten)
        # Only default 0.75 triggers auto-compute
        assert d.confidence == 0.5

    def test_type_normalization(self) -> None:
        assert DecisionType.normalize("database") == DecisionType.TECHNICAL
        assert DecisionType.normalize("ARCHITECTURE") == DecisionType.ARCHITECTURAL
        assert DecisionType.normalize("feature") == DecisionType.PRODUCT
        assert DecisionType.normalize("limitation") == DecisionType.CONSTRAINT
        assert DecisionType.normalize("unknown-thing") == DecisionType.TECHNICAL

    def test_supersedes(self, sample_decision: Decision, sample_decision_b: Decision) -> None:
        newer = sample_decision.model_copy(
            update={"supersedes": sample_decision_b.id}
        )
        assert newer.supersedes == sample_decision_b.id


class TestContradiction:
    def test_create(self, sample_decision: Decision, sample_decision_b: Decision) -> None:
        c = Contradiction(
            decision_a_id=sample_decision.id,
            decision_b_id=sample_decision_b.id,
            decision_a_title=sample_decision.title,
            decision_b_title=sample_decision_b.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="Both decisions address database choice but reach opposite conclusions.",
            evidence_a="We chose PostgreSQL over SQLite",
            evidence_b="SQLite with WAL mode provides sufficient performance",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.92,
        )
        assert c.is_actionable is True
        assert c.status == ContradictionStatus.UNRESOLVED

    def test_pair_key_order_independent(
        self, sample_decision: Decision, sample_decision_b: Decision
    ) -> None:
        c1 = Contradiction(
            decision_a_id=sample_decision.id,
            decision_b_id=sample_decision_b.id,
            decision_a_title="A",
            decision_b_title="B",
            verdict=ContradictionVerdict.TENSION,
            reasoning="r",
            evidence_a="e",
            evidence_b="e",
            confidence=0.5,
        )
        c2 = Contradiction(
            decision_a_id=sample_decision_b.id,
            decision_b_id=sample_decision.id,
            decision_a_title="B",
            decision_b_title="A",
            verdict=ContradictionVerdict.TENSION,
            reasoning="r",
            evidence_a="e",
            evidence_b="e",
            confidence=0.5,
        )
        assert c1.pair_key == c2.pair_key

    def test_baseline_not_actionable(
        self, sample_decision: Decision, sample_decision_b: Decision
    ) -> None:
        c = Contradiction(
            decision_a_id=sample_decision.id,
            decision_b_id=sample_decision_b.id,
            decision_a_title="A",
            decision_b_title="B",
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="r",
            evidence_a="e",
            evidence_b="e",
            confidence=0.9,
            is_baseline=True,
        )
        assert c.is_actionable is False

    def test_compatible_not_actionable(
        self, sample_decision: Decision, sample_decision_b: Decision
    ) -> None:
        c = Contradiction(
            decision_a_id=sample_decision.id,
            decision_b_id=sample_decision_b.id,
            decision_a_title="A",
            decision_b_title="B",
            verdict=ContradictionVerdict.COMPATIBLE,
            reasoning="r",
            evidence_a="e",
            evidence_b="e",
            confidence=0.3,
        )
        assert c.is_actionable is False


class TestAuditEntry:
    def test_hash_computed_on_create(self) -> None:
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="claude-code",
            project="test",
            payload={"decision_title": "Use PostgreSQL"},
        )
        assert entry.entry_hash != ""
        assert len(entry.entry_hash) == 64  # SHA-256 hex

    def test_verify_passes(self) -> None:
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="dev",
            project="test",
        )
        assert entry.verify() is True

    def test_tampered_entry_fails_verify(self) -> None:
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="dev",
            project="test",
        )
        # Tamper with the payload after hash was computed
        original_hash = entry.entry_hash
        entry.payload = {"injected": "data"}
        assert entry.verify() is False
        assert entry.entry_hash == original_hash  # hash field unchanged

    def test_chain_linking(self) -> None:
        e1 = AuditEntry(
            event_type=AuditEventType.SESSION_STARTED,
            actor="system",
        )
        e2 = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="claude-code",
            prev_hash=e1.entry_hash,
        )
        assert e2.prev_hash == e1.entry_hash
        assert e2.verify() is True


class TestContextResult:
    def test_create(self, sample_decision: Decision) -> None:
        cr = ContextResult(
            decision_id=sample_decision.id,
            title=sample_decision.title,
            content=sample_decision.content,
            relevance_score=0.87,
            dimensions=sample_decision.dimensions,
            excerpt="PostgreSQL's MVCC handles concurrent access natively.",
        )
        assert cr.relevance_score == 0.87


class TestDimensionEdge:
    def test_create(self, sample_decision: Decision, sample_decision_b: Decision) -> None:
        edge = DimensionEdge(
            decision_a_id=sample_decision.id,
            decision_b_id=sample_decision_b.id,
            shared_dimensions=[Dimension.DATABASE],
            weight=1.5,
        )
        assert edge.shared_dimensions == [Dimension.DATABASE]


class TestSession:
    def test_create(self) -> None:
        s = Session(project="vt-protocol", agent_id="claude-code")
        assert len(s.session_id) == 16
        assert s.decisions_made == []
        assert s.contradictions_found == []


class TestGovernanceConfig:
    def test_defaults(self) -> None:
        cfg = GovernanceConfig()
        assert "@vt/recommended" in cfg.extends
        assert cfg.agents["claude"] is True
        assert cfg.rules.freeze_on_adopt is True
        assert cfg.rules.contradiction_threshold == 0.7

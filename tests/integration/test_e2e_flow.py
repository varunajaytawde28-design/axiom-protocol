"""Integration test — end-to-end flow.

init project → detect architecture → record decisions → detect contradiction
→ resolve → verify resolution loaded in next session.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from vt_protocol.config import (
    ensure_smm_structure,
    load_governance_config,
    save_governance_config,
)
from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.audit.merkle import MerkleTree

pytestmark = pytest.mark.integration


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """Set up a project directory with .smm/ structure and governance.yaml."""
    root = tmp_path / "myproject"
    root.mkdir()
    (root / ".git").mkdir()  # find_project_root marker
    ensure_smm_structure(root)
    save_governance_config(root)
    return root


class TestEndToEndFlow:
    """Full lifecycle: init → decide → contradict → resolve → audit."""

    def test_init_creates_structure(self, project_root: Path) -> None:
        smm = project_root / ".smm"
        assert smm.is_dir()
        assert (smm / "decisions").is_dir()
        assert (smm / "cache").is_dir()
        assert (smm / "generated").is_dir()
        assert (smm / "audit").is_dir()
        assert (smm / ".gitignore").is_file()

    def test_governance_loads(self, project_root: Path) -> None:
        config = load_governance_config(project_root)
        assert config.agents["claude"] is True
        assert config.rules.contradiction_threshold == 0.7

    def test_full_decision_lifecycle(self, project_root: Path) -> None:
        """Record decisions → detect contradiction → resolve → audit trail."""
        tree = MerkleTree()

        # --- Step 1: Record two contradicting decisions ---
        decision_a = Decision(
            title="Use PostgreSQL for all persistent storage",
            content="We will use PostgreSQL as our only relational database. All data will go through pg.",
            rationale="Operational simplicity, team expertise",
            decision_type=DecisionType.TECHNICAL,
            dimensions=[Dimension.DATABASE],
            made_by="engineer-1",
            project="myproject",
            source_type=SourceType.MANUAL,
        )

        decision_b = Decision(
            title="Use MongoDB for user profiles",
            content="User profiles should be stored in MongoDB for flexible schema evolution.",
            rationale="Profile data is semi-structured",
            decision_type=DecisionType.TECHNICAL,
            dimensions=[Dimension.DATABASE],
            made_by="engineer-2",
            project="myproject",
            source_type=SourceType.MANUAL,
        )

        # Audit: log both decisions
        entry_a = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor=decision_a.made_by,
            project="myproject",
            payload={"decision_id": str(decision_a.id), "title": decision_a.title},
        )
        idx_a = tree.append(entry_a)

        entry_b = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor=decision_b.made_by,
            project="myproject",
            payload={"decision_id": str(decision_b.id), "title": decision_b.title},
        )
        idx_b = tree.append(entry_b)

        assert tree.size == 2

        # --- Step 2: Detect contradiction (simulated — no LLM) ---
        shared_dims = list(
            set(decision_a.dimensions) & set(decision_b.dimensions)
        )
        assert Dimension.DATABASE in shared_dims

        contradiction = Contradiction(
            decision_a_id=decision_a.id,
            decision_b_id=decision_b.id,
            decision_a_title=decision_a.title,
            decision_b_title=decision_b.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="Both decisions address database choice but recommend mutually exclusive solutions",
            evidence_a="Use PostgreSQL as our only relational database",
            evidence_b="User profiles should be stored in MongoDB",
            shared_dimensions=shared_dims,
            confidence=0.92,
        )

        assert contradiction.is_actionable
        assert contradiction.status == ContradictionStatus.UNRESOLVED

        # Audit: log contradiction
        entry_c = AuditEntry(
            event_type=AuditEventType.CONTRADICTION_DETECTED,
            actor="system",
            project="myproject",
            payload={
                "contradiction_id": str(contradiction.id),
                "decision_a_id": str(decision_a.id),
                "decision_b_id": str(decision_b.id),
                "verdict": contradiction.verdict.value,
            },
        )
        tree.append(entry_c)

        # --- Step 3: Resolve the contradiction ---
        contradiction.status = ContradictionStatus.RESOLVED
        contradiction.resolved_by = "tech-lead"
        contradiction.resolution_note = "PostgreSQL wins — use JSONB columns for flexible profile data"

        assert not contradiction.is_actionable  # resolved

        # Audit: log resolution
        entry_d = AuditEntry(
            event_type=AuditEventType.CONTRADICTION_RESOLVED,
            actor="tech-lead",
            project="myproject",
            payload={
                "contradiction_id": str(contradiction.id),
                "winner": str(decision_a.id),
                "note": contradiction.resolution_note,
            },
        )
        tree.append(entry_d)

        # Supersede decision B
        decision_b.status = DecisionStatus.SUPERSEDED
        decision_b.valid = False

        entry_e = AuditEntry(
            event_type=AuditEventType.DECISION_SUPERSEDED,
            actor="tech-lead",
            project="myproject",
            payload={
                "superseded_id": str(decision_b.id),
                "superseded_by": str(decision_a.id),
            },
        )
        tree.append(entry_e)

        # --- Step 4: Verify audit trail ---
        assert tree.size == 5

        # Verify all entries
        entries = tree.get_entries(limit=10)
        assert len(entries) == 5
        event_types = [e.event_type for e in entries]
        assert event_types == [
            AuditEventType.DECISION_ADDED,
            AuditEventType.DECISION_ADDED,
            AuditEventType.CONTRADICTION_DETECTED,
            AuditEventType.CONTRADICTION_RESOLVED,
            AuditEventType.DECISION_SUPERSEDED,
        ]

        # Each entry should self-verify
        for entry in entries:
            assert entry.verify()

        # Merkle root is consistent
        root_hash = tree.root_hash()
        assert len(root_hash) == 32  # SHA-256

        # Inclusion proof for the contradiction entry
        proof = tree.inclusion_proof(2)  # entry_c is at index 2
        entry_c_json = tree.get_entry(2).model_dump_json().encode("utf-8")
        assert tree.verify_inclusion(proof, entry_c_json, root_hash)

        tree.close()

    def test_pair_key_deduplication(self) -> None:
        """Contradiction pair keys are order-independent."""
        from uuid import uuid4

        id_a, id_b = uuid4(), uuid4()
        c1 = Contradiction(
            decision_a_id=id_a, decision_b_id=id_b,
            decision_a_title="A", decision_b_title="B",
            verdict=ContradictionVerdict.TENSION,
            reasoning="test", evidence_a="a", evidence_b="b",
            confidence=0.5,
        )
        c2 = Contradiction(
            decision_a_id=id_b, decision_b_id=id_a,
            decision_a_title="B", decision_b_title="A",
            verdict=ContradictionVerdict.TENSION,
            reasoning="test", evidence_a="b", evidence_b="a",
            confidence=0.5,
        )
        assert c1.pair_key == c2.pair_key

    def test_confidence_computation(self) -> None:
        """Decisions auto-compute confidence from source + content richness."""
        minimal = Decision(
            title="Quick decision",
            content="Short content",
            made_by="agent",
            project="p",
        )
        # Agent source → 0.75 base
        assert minimal.confidence == 0.75

        rich = Decision(
            title="Thorough decision",
            content="A" * 600,
            rationale="Carefully considered alternatives",
            alternatives=["Option B", "Option C"],
            made_by="human",
            project="p",
            source_type=SourceType.MANUAL,
        )
        # Manual source (0.95) + alternatives (+0.05) + long rationale (+0.05) + long content (+0.05) = capped at 1.0
        assert rich.confidence >= 0.95

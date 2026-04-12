"""Tests for compliance dashboard API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.dashboard.app import (
    DashboardState,
    app,
    reset_state,
    set_state,
)
from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    reset_state()
    yield
    reset_state()


def _make_decision(
    source_type: SourceType = SourceType.MANUAL,
    **kwargs,
) -> Decision:
    defaults = dict(
        title="Test Decision",
        content="Detailed content for the decision to test.",
        rationale="Because it makes sense for testing.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=source_type,
    )
    defaults.update(kwargs)
    return Decision(**defaults)


def _make_state(
    tmp_path: Path,
    decisions: list[Decision] | None = None,
) -> DashboardState:
    state = DashboardState(project_root=tmp_path)
    state.decisions = decisions or []
    state.contradictions = []
    return state


def _make_entry(
    actor: str = "agent-1",
    event_type: AuditEventType = AuditEventType.DECISION_ADDED,
    **kwargs,
) -> AuditEntry:
    return AuditEntry(
        event_type=event_type,
        actor=actor,
        project="test-project",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# GET /api/compliance
# ---------------------------------------------------------------------------


class TestComplianceEndpoint:
    def test_empty_state(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["attribution"]["total_decisions"] == 0
        assert len(data["compliance_mappings"]) == 3
        assert data["agent_activities"] == []

    def test_with_decisions(self, client: TestClient, tmp_path: Path) -> None:
        decisions = [
            _make_decision(SourceType.AGENT),
            _make_decision(SourceType.MANUAL),
            _make_decision(SourceType.SCAN),
        ]
        set_state(_make_state(tmp_path, decisions=decisions))
        resp = client.get("/api/compliance")
        data = resp.json()
        assert data["attribution"]["total_decisions"] == 3
        assert data["attribution"]["agent_decisions"] == 2  # AGENT + SCAN
        assert data["attribution"]["human_decisions"] == 1

    def test_with_merkle_tree(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "audit").mkdir(parents=True)
        tree = MerkleTree(tmp_path / ".smm" / "audit" / "audit.db", check_same_thread=False)
        entry = _make_entry(actor="agent-1")
        tree.append(entry)

        state = _make_state(tmp_path)
        state._merkle = tree
        set_state(state)

        resp = client.get("/api/compliance")
        data = resp.json()
        assert len(data["agent_activities"]) == 1
        assert data["agent_activities"][0]["agent_id"] == "agent-1"

    def test_compliance_mappings_structure(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance")
        data = resp.json()
        for m in data["compliance_mappings"]:
            assert "framework" in m
            assert "requirement" in m
            assert "status" in m
            assert "evidence" in m
            assert m["status"] in ("met", "partial", "not_met")

    def test_signing_key_detected(self, client: TestClient, tmp_path: Path) -> None:
        smm = tmp_path / ".smm"
        smm.mkdir(parents=True)
        (smm / "signing_key").write_bytes(b"fake-key")
        (smm / "audit").mkdir()
        tree = MerkleTree(smm / "audit" / "audit.db", check_same_thread=False)
        tree.append(_make_entry())

        state = _make_state(tmp_path, decisions=[_make_decision()])
        state._merkle = tree
        set_state(state)

        resp = client.get("/api/compliance")
        data = resp.json()
        # With signing + merkle + attribution, EU AI Act should be met
        eu = next(m for m in data["compliance_mappings"] if "eu_ai_act" in m["framework"])
        assert eu["status"] in ("met", "partial")


# ---------------------------------------------------------------------------
# GET /api/compliance/export
# ---------------------------------------------------------------------------


class TestComplianceExportEndpoint:
    def test_empty_export(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "generated_at" in data
        assert "attribution" in data
        assert "compliance_mappings" in data
        assert "verification_instructions" in data

    def test_export_with_entries(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "audit").mkdir(parents=True)
        tree = MerkleTree(tmp_path / ".smm" / "audit" / "audit.db", check_same_thread=False)
        for _ in range(5):
            tree.append(_make_entry())

        state = _make_state(tmp_path, decisions=[_make_decision()])
        state._merkle = tree
        set_state(state)

        resp = client.get("/api/compliance/export")
        data = resp.json()
        assert data["audit_entries_count"] == 5
        assert len(data["audit_entries"]) == 5
        assert len(data["tree_heads"]) == 1
        assert data["tree_heads"][0]["tree_size"] == 5

    def test_export_includes_inclusion_proofs(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "audit").mkdir(parents=True)
        tree = MerkleTree(tmp_path / ".smm" / "audit" / "audit.db", check_same_thread=False)
        for _ in range(3):
            tree.append(_make_entry())

        state = _make_state(tmp_path)
        state._merkle = tree
        set_state(state)

        resp = client.get("/api/compliance/export")
        data = resp.json()
        assert len(data["inclusion_proofs"]) > 0
        proof = data["inclusion_proofs"][0]
        assert "leaf_index" in proof
        assert "root_hash_hex" in proof
        assert "proof_hashes_hex" in proof

    def test_export_verification_instructions(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance/export")
        data = resp.json()
        assert "Merkle Tree Integrity" in data["verification_instructions"]
        assert "Ed25519" in data["verification_instructions"]


# ---------------------------------------------------------------------------
# GET /api/compliance/anchoring
# ---------------------------------------------------------------------------


class TestComplianceAnchoringEndpoint:
    def test_empty_anchoring(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance/anchoring")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["latest"] is None
        assert data["anchors"] == []

    def test_with_timestamp_files(self, client: TestClient, tmp_path: Path) -> None:
        ts_dir = tmp_path / ".smm" / "audit" / "timestamps"
        ts_dir.mkdir(parents=True)

        token_data = {
            "tree_size": 10,
            "root_hash_hex": "abcdef123456",
            "tsa_url": "http://timestamp.digicert.com",
            "token_hex": "deadbeef",
            "response_status": "ok",
            "verified": True,
        }
        (ts_dir / "ts_001.json").write_text(json.dumps(token_data))

        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance/anchoring")
        data = resp.json()
        assert data["total"] == 1
        assert data["total_anchored"] == 1
        assert data["latest"]["tree_size"] == 10
        assert data["latest"]["root_hash_hex"] == "abcdef123456"

    def test_multiple_timestamps(self, client: TestClient, tmp_path: Path) -> None:
        ts_dir = tmp_path / ".smm" / "audit" / "timestamps"
        ts_dir.mkdir(parents=True)

        for i in range(3):
            token_data = {
                "tree_size": (i + 1) * 10,
                "root_hash_hex": f"hash{i}",
                "response_status": "ok" if i != 1 else "error:500",
            }
            (ts_dir / f"ts_{i:03d}.json").write_text(json.dumps(token_data))

        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance/anchoring")
        data = resp.json()
        assert data["total"] == 3
        assert data["total_anchored"] == 2  # Two "ok" responses

    def test_malformed_timestamp_skipped(self, client: TestClient, tmp_path: Path) -> None:
        ts_dir = tmp_path / ".smm" / "audit" / "timestamps"
        ts_dir.mkdir(parents=True)
        (ts_dir / "bad.json").write_text("not valid json{{{")
        (ts_dir / "good.json").write_text(json.dumps({
            "tree_size": 5,
            "root_hash_hex": "abc",
            "response_status": "ok",
        }))

        set_state(_make_state(tmp_path))
        resp = client.get("/api/compliance/anchoring")
        data = resp.json()
        assert data["total"] == 1  # Only the valid one

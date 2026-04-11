"""Tests for dashboard backend API endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    """Reset global state between tests."""
    reset_state()
    yield
    reset_state()


def _make_decision(
    title: str = "Use PostgreSQL",
    dimensions: list[Dimension] | None = None,
    valid: bool = True,
) -> Decision:
    return Decision(
        title=title,
        content=f"Decision about {title}. Full description with details.",
        rationale=f"Because {title} is the best choice.",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=dimensions or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
        valid=valid,
    )


def _make_contradiction(
    decision_a: Decision | None = None,
    decision_b: Decision | None = None,
    verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED,
) -> Contradiction:
    a = decision_a or _make_decision("Decision A")
    b = decision_b or _make_decision("Decision B")
    return Contradiction(
        decision_a_id=a.id,
        decision_b_id=b.id,
        decision_a_title=a.title,
        decision_b_title=b.title,
        verdict=verdict,
        status=status,
        reasoning="These decisions conflict on database choice.",
        evidence_a="Uses PostgreSQL",
        evidence_b="Uses MySQL",
        confidence=0.9,
    )


def _make_state(
    tmp_path: Path,
    decisions: list[Decision] | None = None,
    contradictions: list[Contradiction] | None = None,
) -> DashboardState:
    state = DashboardState(project_root=tmp_path)
    state.decisions = decisions or []
    state.contradictions = contradictions or []
    return state


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_empty(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["total_decisions"] == 0
        assert data["coherence_score"] == 1.0

    def test_health_with_decisions(self, client: TestClient, tmp_path: Path) -> None:
        decisions = [_make_decision(f"D{i}") for i in range(5)]
        set_state(_make_state(tmp_path, decisions=decisions))
        resp = client.get("/api/health")
        data = resp.json()
        assert data["active_decisions"] == 5

    def test_health_degraded(self, client: TestClient, tmp_path: Path) -> None:
        d_a = _make_decision("A")
        d_b = _make_decision("B")
        c = _make_contradiction(d_a, d_b)
        set_state(_make_state(tmp_path, decisions=[d_a, d_b], contradictions=[c]))
        resp = client.get("/api/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["actionable_contradictions"] == 1
        assert data["coherence_score"] < 1.0


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class TestDecisions:
    def test_list_empty(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["decisions"] == []

    def test_list_with_data(self, client: TestClient, tmp_path: Path) -> None:
        decisions = [_make_decision(f"D{i}") for i in range(3)]
        set_state(_make_state(tmp_path, decisions=decisions))
        resp = client.get("/api/decisions")
        data = resp.json()
        assert data["total"] == 3
        assert len(data["decisions"]) == 3

    def test_filter_by_dimension(self, client: TestClient, tmp_path: Path) -> None:
        decisions = [
            _make_decision("DB choice", [Dimension.DATABASE]),
            _make_decision("Auth choice", [Dimension.AUTH]),
        ]
        set_state(_make_state(tmp_path, decisions=decisions))
        resp = client.get("/api/decisions?dimension=auth")
        data = resp.json()
        assert data["total"] == 1
        assert data["decisions"][0]["title"] == "Auth choice"

    def test_filter_by_status(self, client: TestClient, tmp_path: Path) -> None:
        d1 = _make_decision("Active")
        d2 = _make_decision("Superseded")
        d2.status = DecisionStatus.SUPERSEDED
        set_state(_make_state(tmp_path, decisions=[d1, d2]))
        resp = client.get("/api/decisions?status=superseded")
        data = resp.json()
        assert data["total"] == 1

    def test_invalid_dimension(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/decisions?dimension=invalid")
        assert resp.status_code == 400

    def test_pagination(self, client: TestClient, tmp_path: Path) -> None:
        decisions = [_make_decision(f"D{i}") for i in range(10)]
        set_state(_make_state(tmp_path, decisions=decisions))
        resp = client.get("/api/decisions?limit=3&offset=2")
        data = resp.json()
        assert data["total"] == 10
        assert len(data["decisions"]) == 3

    def test_decision_detail(self, client: TestClient, tmp_path: Path) -> None:
        d = _make_decision("PostgreSQL")
        set_state(_make_state(tmp_path, decisions=[d]))
        resp = client.get(f"/api/decisions/{d.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "PostgreSQL"
        assert "related_contradictions" in data

    def test_decision_detail_not_found(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get(f"/api/decisions/{uuid4()}")
        assert resp.status_code == 404

    def test_decision_detail_invalid_uuid(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/decisions/not-a-uuid")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Contradictions
# ---------------------------------------------------------------------------


class TestContradictions:
    def test_list_empty(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/contradictions")
        data = resp.json()
        assert data["total"] == 0

    def test_list_unresolved(self, client: TestClient, tmp_path: Path) -> None:
        c = _make_contradiction()
        set_state(_make_state(tmp_path, contradictions=[c]))
        resp = client.get("/api/contradictions")
        data = resp.json()
        assert data["total"] == 1
        assert data["contradictions"][0]["verdict"] == "contradiction"

    def test_filter_by_status(self, client: TestClient, tmp_path: Path) -> None:
        c1 = _make_contradiction(status=ContradictionStatus.UNRESOLVED)
        c2 = _make_contradiction(status=ContradictionStatus.RESOLVED)
        set_state(_make_state(tmp_path, contradictions=[c1, c2]))
        resp = client.get("/api/contradictions?status=resolved")
        data = resp.json()
        assert data["total"] == 1

    def test_resolve(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
        c = _make_contradiction()
        set_state(_make_state(tmp_path, contradictions=[c]))
        resp = client.post(
            f"/api/contradictions/{c.id}/resolve",
            json={"winner_id": str(c.decision_a_id), "rationale": "A is better"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"
        # Check state is updated
        from vt_protocol.dashboard.app import get_state
        state = get_state()
        assert state.contradictions[0].status == ContradictionStatus.RESOLVED

    def test_resolve_not_found(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.post(
            f"/api/contradictions/{uuid4()}/resolve",
            json={"winner_id": "x", "rationale": "test"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


class TestGraph:
    def test_empty_graph(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/graph")
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_graph_with_nodes(self, client: TestClient, tmp_path: Path) -> None:
        decisions = [_make_decision(f"D{i}") for i in range(3)]
        set_state(_make_state(tmp_path, decisions=decisions))
        resp = client.get("/api/graph")
        data = resp.json()
        assert len(data["nodes"]) == 3

    def test_graph_shared_dimension_edges(self, client: TestClient, tmp_path: Path) -> None:
        d1 = _make_decision("A", [Dimension.DATABASE])
        d2 = _make_decision("B", [Dimension.DATABASE])
        set_state(_make_state(tmp_path, decisions=[d1, d2]))
        resp = client.get("/api/graph")
        data = resp.json()
        shared_edges = [e for e in data["edges"] if e["data"]["type"] == "SHARED_DIMENSION"]
        assert len(shared_edges) == 1

    def test_graph_contradiction_edges(self, client: TestClient, tmp_path: Path) -> None:
        d1 = _make_decision("A")
        d2 = _make_decision("B")
        c = _make_contradiction(d1, d2)
        set_state(_make_state(tmp_path, decisions=[d1, d2], contradictions=[c]))
        resp = client.get("/api/graph")
        data = resp.json()
        contra_edges = [e for e in data["edges"] if e["data"]["type"] == "CONTRADICTION"]
        assert len(contra_edges) == 1


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_no_merkle(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/audit")
        data = resp.json()
        assert data["total"] == 0

    def test_audit_with_entries(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "audit").mkdir(parents=True)
        tree = MerkleTree(tmp_path / ".smm" / "audit" / "audit.db", check_same_thread=False)
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="test",
            project="test-project",
            payload={"title": "Use PostgreSQL"},
        )
        tree.append(entry)

        state = _make_state(tmp_path)
        state._merkle = tree
        set_state(state)

        resp = client.get("/api/audit")
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["event_type"] == "decision_added"

    def test_audit_with_verification(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "audit").mkdir(parents=True)
        tree = MerkleTree(tmp_path / ".smm" / "audit" / "audit.db", check_same_thread=False)
        for i in range(3):
            tree.append(AuditEntry(
                event_type=AuditEventType.DECISION_ADDED,
                actor="test",
                project="test",
                payload={"index": i},
            ))

        state = _make_state(tmp_path)
        state._merkle = tree
        set_state(state)

        resp = client.get("/api/audit?verify=true")
        data = resp.json()
        assert data["total"] == 3
        for e in data["entries"]:
            assert e["verified"] is True


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class TestSessions:
    def test_sessions_empty(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/api/sessions")
        data = resp.json()
        assert data["total"] == 0
        assert data["sessions"] == []


# ---------------------------------------------------------------------------
# Index page
# ---------------------------------------------------------------------------


class TestIndex:
    def test_index_returns_html(self, client: TestClient, tmp_path: Path) -> None:
        set_state(_make_state(tmp_path))
        resp = client.get("/")
        assert resp.status_code == 200
        assert "VT Protocol" in resp.text

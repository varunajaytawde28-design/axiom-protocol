"""Integration test — Dashboard endpoints.

Start FastAPI app → hit all endpoints → verify responses match state.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    Session,
    SourceType,
)

pytestmark = pytest.mark.integration


def _make_decision(title: str, *, dims: list[Dimension] | None = None) -> Decision:
    return Decision(
        title=title,
        content=f"Content for {title}",
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="dashboard-test",
        source_type=SourceType.MANUAL,
    )


def _make_contradiction(d1: Decision, d2: Decision) -> Contradiction:
    return Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="These decisions conflict",
        evidence_a=d1.content[:50],
        evidence_b=d2.content[:50],
        shared_dimensions=list(set(d1.dimensions) & set(d2.dimensions)),
        confidence=0.85,
    )


@pytest.fixture()
def state(tmp_path: Path) -> DashboardState:
    """Create a populated dashboard state."""
    d1 = _make_decision("Use PostgreSQL", dims=[Dimension.DATABASE])
    d2 = _make_decision("Use MongoDB", dims=[Dimension.DATABASE])
    d3 = _make_decision("Use REST API", dims=[Dimension.API_STYLE])

    c1 = _make_contradiction(d1, d2)

    tree = MerkleTree(check_same_thread=False)
    for d in [d1, d2, d3]:
        tree.append(AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor=d.made_by,
            project="dashboard-test",
            payload={"decision_id": str(d.id), "title": d.title},
        ))

    session = Session(project="dashboard-test", agent_id="test-agent")

    ds = DashboardState(project_root=tmp_path)
    ds.decisions = [d1, d2, d3]
    ds.contradictions = [c1]
    ds.sessions = [session]
    ds._merkle = tree

    # Persist contradictions to disk so api_contradictions (which re-reads
    # from disk per Bug 2 fix) can find them.
    contra_dir = tmp_path / ".smm" / "contradictions"
    contra_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{str(c1.id)[:8]}.json"
    (contra_dir / filename).write_text(c1.model_dump_json(indent=2))

    return ds


@pytest.fixture()
def client(state: DashboardState) -> TestClient:
    """Create a test client with populated state."""
    set_state(state)
    yield TestClient(app)
    reset_state()


class TestDashboardIntegration:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        # "degraded" because there's an unresolved actionable contradiction
        assert data["status"] == "degraded"
        assert data["total_decisions"] == 3
        assert data["total_contradictions"] == 1

    def test_list_decisions(self, client: TestClient) -> None:
        resp = client.get("/api/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["decisions"]) == 3

    def test_filter_decisions_by_dimension(self, client: TestClient) -> None:
        resp = client.get("/api/decisions?dimension=database")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        for d in data["decisions"]:
            assert "database" in d["dimensions"]

    def test_get_single_decision(self, client: TestClient, state: DashboardState) -> None:
        decision_id = str(state.decisions[0].id)
        resp = client.get(f"/api/decisions/{decision_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Use PostgreSQL"
        # Should include related contradictions
        assert "related_contradictions" in data

    def test_get_nonexistent_decision(self, client: TestClient) -> None:
        resp = client.get(f"/api/decisions/{uuid4()}")
        assert resp.status_code == 404

    def test_list_contradictions(self, client: TestClient) -> None:
        resp = client.get("/api/contradictions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["contradictions"]) == 1
        assert data["contradictions"][0]["verdict"] == "contradiction"

    def test_resolve_contradiction(self, client: TestClient, state: DashboardState) -> None:
        c_id = str(state.contradictions[0].id)
        winner_id = str(state.decisions[0].id)
        resp = client.post(
            f"/api/contradictions/{c_id}/resolve",
            json={"winner_id": winner_id, "rationale": "PostgreSQL is the standard"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"

    def test_graph_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) >= 3  # At least 3 decisions

    def test_audit_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        # 3 decision_added + 1 contradiction_detected + 3 merkle entries
        assert len(data["entries"]) >= 3

    def test_audit_with_limit(self, client: TestClient) -> None:
        resp = client.get("/api/audit?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 2

    def test_sessions_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["project"] == "dashboard-test"

    def test_index_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_resolve_then_verify_state(self, client: TestClient, state: DashboardState) -> None:
        """Resolve a contradiction and verify it no longer appears as unresolved."""
        c_id = str(state.contradictions[0].id)
        winner_id = str(state.decisions[0].id)

        # Resolve
        resp = client.post(
            f"/api/contradictions/{c_id}/resolve",
            json={"winner_id": winner_id, "rationale": "team decision"},
        )
        assert resp.status_code == 200

        # Verify health reflects resolution
        resp = client.get("/api/health")
        data = resp.json()
        # The contradiction still exists but is resolved — no actionable ones
        assert data["total_contradictions"] == 1
        assert data["status"] == "healthy"

    def test_full_endpoint_sequence(self, client: TestClient, state: DashboardState) -> None:
        """Hit every endpoint in sequence."""
        # Health
        assert client.get("/api/health").status_code == 200

        # Decisions
        decisions_resp = client.get("/api/decisions")
        assert decisions_resp.status_code == 200
        d_id = decisions_resp.json()["decisions"][0]["id"]

        # Single decision
        assert client.get(f"/api/decisions/{d_id}").status_code == 200

        # Contradictions
        assert client.get("/api/contradictions").status_code == 200

        # Graph
        assert client.get("/api/graph").status_code == 200

        # Audit
        assert client.get("/api/audit").status_code == 200

        # Sessions
        assert client.get("/api/sessions").status_code == 200

        # Index
        assert client.get("/").status_code == 200

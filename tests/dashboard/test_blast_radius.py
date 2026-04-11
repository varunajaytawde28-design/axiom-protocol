"""Tests for blast-radius visualization endpoint and gate CLI."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)


def _decision(
    title: str,
    *,
    dims: list[Dimension] | None = None,
    supersedes=None,
) -> Decision:
    return Decision(
        title=title,
        content=f"Content for {title}",
        rationale="Good rationale",
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
        supersedes=supersedes,
    )


def _contradiction(d1: Decision, d2: Decision) -> Contradiction:
    return Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="conflict",
        evidence_a="A",
        evidence_b="B",
        shared_dimensions=list(set(d1.dimensions) & set(d2.dimensions)),
        confidence=0.9,
    )


@pytest.fixture()
def setup_state(tmp_path: Path):
    """Create a state with interconnected decisions."""
    d1 = _decision("Use PostgreSQL", dims=[Dimension.DATABASE])
    d2 = _decision("Use MongoDB", dims=[Dimension.DATABASE])
    d3 = _decision("Use Redis cache", dims=[Dimension.CACHING])
    d4 = _decision("Use REST API", dims=[Dimension.API_STYLE])
    d5 = _decision("Use SQLAlchemy", dims=[Dimension.DATABASE], supersedes=d2.id)

    c1 = _contradiction(d1, d2)

    ds = DashboardState(project_root=tmp_path)
    ds.decisions = [d1, d2, d3, d4, d5]
    ds.contradictions = [c1]
    ds._merkle = MerkleTree(check_same_thread=False)

    set_state(ds)
    yield {
        "d1": d1, "d2": d2, "d3": d3, "d4": d4, "d5": d5,
        "c1": c1,
    }
    reset_state()


@pytest.fixture()
def client(setup_state) -> TestClient:
    return TestClient(app)


class TestBlastRadiusEndpoint:
    def test_basic_blast_radius(self, client: TestClient, setup_state: dict) -> None:
        d1 = setup_state["d1"]
        resp = client.get(f"/api/blast-radius/{d1.id}")
        assert resp.status_code == 200
        data = resp.json()

        assert data["decision"]["title"] == "Use PostgreSQL"
        assert data["impact_score"] >= 0
        assert "graph" in data
        assert len(data["graph"]["nodes"]) >= 1

    def test_related_decisions_found(self, client: TestClient, setup_state: dict) -> None:
        d1 = setup_state["d1"]
        resp = client.get(f"/api/blast-radius/{d1.id}")
        data = resp.json()

        # d2 and d5 share DATABASE dimension
        related_ids = [r["id"] for r in data["related_decisions"]]
        assert str(setup_state["d2"].id) in related_ids
        assert str(setup_state["d5"].id) in related_ids
        # d3 (caching) should NOT be related
        assert str(setup_state["d3"].id) not in related_ids

    def test_contradictions_found(self, client: TestClient, setup_state: dict) -> None:
        d1 = setup_state["d1"]
        resp = client.get(f"/api/blast-radius/{d1.id}")
        data = resp.json()

        assert len(data["contradictions"]) == 1
        assert data["contradictions"][0]["verdict"] == "contradiction"

    def test_supersession_chain(self, client: TestClient, setup_state: dict) -> None:
        d2 = setup_state["d2"]
        resp = client.get(f"/api/blast-radius/{d2.id}")
        data = resp.json()

        # d5 supersedes d2
        chain_ids = [ch["id"] for ch in data["supersession_chain"]]
        assert str(setup_state["d5"].id) in chain_ids

    def test_isolated_decision(self, client: TestClient, setup_state: dict) -> None:
        d4 = setup_state["d4"]  # REST API — only API_STYLE, no shared dims
        resp = client.get(f"/api/blast-radius/{d4.id}")
        data = resp.json()

        assert data["related_decisions"] == []
        assert data["contradictions"] == []
        assert data["impact_score"] == 0.0

    def test_graph_has_center_node(self, client: TestClient, setup_state: dict) -> None:
        d1 = setup_state["d1"]
        resp = client.get(f"/api/blast-radius/{d1.id}")
        data = resp.json()

        center_nodes = [n for n in data["graph"]["nodes"] if n["data"]["type"] == "center"]
        assert len(center_nodes) == 1
        assert center_nodes[0]["data"]["id"] == str(d1.id)

    def test_graph_edges(self, client: TestClient, setup_state: dict) -> None:
        d1 = setup_state["d1"]
        resp = client.get(f"/api/blast-radius/{d1.id}")
        data = resp.json()

        edge_types = {e["data"]["type"] for e in data["graph"]["edges"]}
        assert "SHARED_DIMENSION" in edge_types or "CONTRADICTION" in edge_types

    def test_not_found(self, client: TestClient) -> None:
        resp = client.get(f"/api/blast-radius/{uuid4()}")
        assert resp.status_code == 404

    def test_invalid_uuid(self, client: TestClient) -> None:
        resp = client.get("/api/blast-radius/not-a-uuid")
        assert resp.status_code == 400

    def test_total_affected_count(self, client: TestClient, setup_state: dict) -> None:
        d1 = setup_state["d1"]
        resp = client.get(f"/api/blast-radius/{d1.id}")
        data = resp.json()

        # total_affected includes related decisions + chain + contradictions
        assert data["total_affected"] >= 1

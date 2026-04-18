"""Integration test: Dashboard API.

Tests all dashboard REST endpoints using httpx.AsyncClient
against the real FastAPI app with DashboardState injection.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

from tests.helpers.decision_factory import make_contradiction, make_decision
from tests.helpers.repo_factory import create_project

pytestmark = pytest.mark.integration


@pytest.fixture
def decisions():
    return [
        make_decision(title="Use PostgreSQL", dimensions=[Dimension.DATABASE]),
        make_decision(title="Use Redis", dimensions=[Dimension.CACHING]),
        make_decision(title="REST API", dimensions=[Dimension.API_STYLE]),
        make_decision(title="JWT Auth", dimensions=[Dimension.AUTH]),
    ]


@pytest.fixture
def contradictions(decisions):
    return [make_contradiction(decisions[0], decisions[1])]


@pytest.fixture
def dashboard_state(tmp_path, decisions, contradictions):
    root = create_project(tmp_path)
    state = DashboardState(project_root=root)
    state.decisions = decisions
    state.contradictions = contradictions
    # Persist contradictions to disk so api_contradictions (which re-reads
    # from disk per Bug 2 fix) can find them.
    contra_dir = root / ".smm" / "contradictions"
    contra_dir.mkdir(parents=True, exist_ok=True)
    for c in contradictions:
        filename = f"{str(c.id)[:8]}.json"
        (contra_dir / filename).write_text(c.model_dump_json(indent=2))
    set_state(state)
    yield state
    reset_state()


class TestHealthEndpoint:
    async def test_health(self, dashboard_state, decisions):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_decisions"] == 4
            assert data["active_decisions"] == 4
            assert data["actionable_contradictions"] == 1
            assert data["status"] == "degraded"
            assert 0 < data["coherence_score"] < 1.0


class TestDecisionsEndpoint:
    async def test_list_all(self, dashboard_state, decisions):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/decisions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 4

    async def test_filter_by_dimension(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/decisions?dimension=database")
            data = resp.json()
            assert data["total"] == 1
            assert data["decisions"][0]["title"] == "Use PostgreSQL"

    async def test_invalid_dimension(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/decisions?dimension=invalid")
            assert resp.status_code == 400

    async def test_pagination(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/decisions?limit=2&offset=0")
            data = resp.json()
            assert len(data["decisions"]) == 2
            assert data["total"] == 4

    async def test_decision_detail(self, dashboard_state, decisions):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/decisions/{decisions[0].id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "Use PostgreSQL"

    async def test_decision_detail_not_found(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/decisions/{uuid4()}")
            assert resp.status_code == 404


class TestContradictionsEndpoint:
    async def test_list_unresolved(self, dashboard_state, contradictions):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/contradictions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1

    async def test_resolve(self, dashboard_state, decisions, contradictions):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            c = contradictions[0]
            resp = await client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(decisions[0].id), "rationale": "PostgreSQL is better"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "resolved"

    async def test_resolve_not_found(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/contradictions/{uuid4()}/resolve",
                json={"winner_id": "x", "rationale": "test"},
            )
            assert resp.status_code == 404


class TestGraphEndpoint:
    async def test_graph_structure(self, dashboard_state, decisions):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/graph")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["nodes"]) == 4
            # At least the contradiction edge
            contra_edges = [e for e in data["edges"] if e["data"]["type"] == "CONTRADICTION"]
            assert len(contra_edges) == 1


class TestBlastRadiusEndpoint:
    async def test_blast_radius(self, dashboard_state, decisions, contradictions):
        target = decisions[0]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/blast-radius/{target.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["decision"]["title"] == "Use PostgreSQL"
            assert "impact_score" in data
            assert "graph" in data

    async def test_blast_radius_not_found(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/blast-radius/{uuid4()}")
            assert resp.status_code == 404


class TestAuditEndpoint:
    async def test_audit_has_entries(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/audit")
            assert resp.status_code == 200
            data = resp.json()
            # Audit entries are synthesized from decisions + contradictions in state
            assert data["total"] >= 4  # at least 4 decision_added entries


class TestSessionsEndpoint:
    async def test_sessions_empty(self, dashboard_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/sessions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 0

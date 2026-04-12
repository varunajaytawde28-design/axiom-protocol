"""Performance test: Dashboard API Response Times.

Measures response times of dashboard endpoints under load.
Uses real FastAPI app with DashboardState injection.
"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import Dimension

from tests.helpers.decision_factory import make_contradiction, make_decision
from tests.helpers.repo_factory import create_project

pytestmark = [pytest.mark.performance, pytest.mark.slow]


@pytest.fixture
def large_state(tmp_path):
    """Create state with 200 decisions and 20 contradictions."""
    root = create_project(tmp_path)
    decisions = [
        make_decision(
            title=f"Decision {i}",
            dimensions=[list(Dimension)[i % 12]],
        )
        for i in range(200)
    ]
    contradictions = [
        make_contradiction(decisions[i], decisions[i + 1])
        for i in range(0, 40, 2)
    ]
    state = DashboardState(project_root=root)
    state.decisions = decisions
    state.contradictions = contradictions
    set_state(state)
    yield decisions, contradictions
    reset_state()


class TestEndpointResponseTimes:
    async def test_health_under_1s(self, large_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start = time.perf_counter()
            for _ in range(50):
                resp = await client.get("/api/health")
                assert resp.status_code == 200
            elapsed = time.perf_counter() - start
            # 50 requests < 5s = <100ms each
            assert elapsed < 5.0, f"50 health requests took {elapsed:.2f}s"

    async def test_decisions_list_under_1s(self, large_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start = time.perf_counter()
            for _ in range(20):
                resp = await client.get("/api/decisions?limit=50")
                assert resp.status_code == 200
            elapsed = time.perf_counter() - start
            assert elapsed < 5.0, f"20 decision list requests took {elapsed:.2f}s"

    async def test_graph_under_2s(self, large_state):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start = time.perf_counter()
            resp = await client.get("/api/graph")
            elapsed = time.perf_counter() - start
            assert resp.status_code == 200
            assert elapsed < 2.0, f"Graph with 200 decisions took {elapsed:.2f}s"

    async def test_blast_radius_under_1s(self, large_state):
        decisions, _ = large_state
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start = time.perf_counter()
            resp = await client.get(f"/api/blast-radius/{decisions[0].id}")
            elapsed = time.perf_counter() - start
            assert resp.status_code == 200
            assert elapsed < 1.0, f"Blast radius took {elapsed:.2f}s"

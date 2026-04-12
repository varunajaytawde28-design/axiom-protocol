"""Integration test: Three-Way Collision.

Three decisions that pairwise contradict on the same dimension.
Verifies collision detection, dashboard graph representation,
and blast-radius analysis.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from vt_protocol.cli.commands import main
from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import ContradictionVerdict

from tests.helpers.decision_factory import make_contradiction, make_three_way_collision
from tests.helpers.repo_factory import create_project, write_contradiction, write_decision

pytestmark = pytest.mark.integration


class TestThreeWayCollision:
    """Three decisions pairwise contradict on api-style."""

    @pytest.fixture
    def project(self, tmp_path):
        root = create_project(tmp_path)
        d1, d2, d3 = make_three_way_collision()
        write_decision(root, d1, filename="api-rest.json")
        write_decision(root, d2, filename="api-graphql.json")
        write_decision(root, d3, filename="api-grpc.json")
        return root, (d1, d2, d3)

    def test_three_contradictions_detected(self, project):
        """Three pairwise contradictions should be created."""
        root, (d1, d2, d3) = project

        # Write all 3 pairwise contradictions
        for a, b in [(d1, d2), (d1, d3), (d2, d3)]:
            c = make_contradiction(a, b, reasoning=f"{a.title} vs {b.title}")
            write_contradiction(root, c)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert data["actionable_contradictions"] == 3

    async def test_graph_shows_contradiction_edges(self, project):
        """Dashboard graph shows 3 contradiction edges between the 3 decisions."""
        root, (d1, d2, d3) = project

        contradictions = []
        for a, b in [(d1, d2), (d1, d3), (d2, d3)]:
            contradictions.append(make_contradiction(a, b))

        state = DashboardState(project_root=root)
        state.decisions = [d1, d2, d3]
        state.contradictions = contradictions
        set_state(state)

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/graph")
                assert resp.status_code == 200
                data = resp.json()
                assert len(data["nodes"]) == 3
                contra_edges = [
                    e for e in data["edges"]
                    if e["data"]["type"] == "CONTRADICTION"
                ]
                assert len(contra_edges) == 3
        finally:
            reset_state()

    async def test_blast_radius_includes_both_contradictions(self, project):
        """Blast radius for d1 should include both d2 and d3 via contradictions."""
        root, (d1, d2, d3) = project

        contradictions = [
            make_contradiction(d1, d2),
            make_contradiction(d1, d3),
            make_contradiction(d2, d3),
        ]

        state = DashboardState(project_root=root)
        state.decisions = [d1, d2, d3]
        state.contradictions = contradictions
        set_state(state)

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/api/blast-radius/{d1.id}")
                assert resp.status_code == 200
                data = resp.json()
                # d1 has contradictions with both d2 and d3
                assert len(data["contradictions"]) == 2
                assert data["impact_score"] > 0
        finally:
            reset_state()

    def test_gate_fails_on_three_way(self, project):
        """Quality gate blocks when 3 pairwise contradictions exist."""
        root, (d1, d2, d3) = project
        for a, b in [(d1, d2), (d1, d3), (d2, d3)]:
            write_contradiction(root, make_contradiction(a, b))

        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 1

    async def test_resolve_one_still_fails(self, project):
        """Resolving one contradiction still leaves two actionable."""
        root, (d1, d2, d3) = project
        contradictions = [
            make_contradiction(d1, d2),
            make_contradiction(d1, d3),
            make_contradiction(d2, d3),
        ]

        state = DashboardState(project_root=root)
        state.decisions = [d1, d2, d3]
        state.contradictions = contradictions
        set_state(state)

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Resolve first contradiction
                resp = await client.post(
                    f"/api/contradictions/{contradictions[0].id}/resolve",
                    json={"winner_id": str(d1.id), "rationale": "REST wins"},
                )
                assert resp.status_code == 200

                # Health should still show actionable contradictions
                resp = await client.get("/api/health")
                data = resp.json()
                assert data["actionable_contradictions"] == 2
        finally:
            reset_state()

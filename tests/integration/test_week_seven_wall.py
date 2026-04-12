"""Integration test: The Week-Seven Wall.

After ~7 weeks of development, a project accumulates enough decisions
that contradictions emerge naturally. This test creates 20 decisions
across all 12 dimensions and verifies the full pipeline:
  - CLI check passes with no contradictions
  - Dashboard health shows coherence 1.0
  - Introducing conflicting decisions is detected
  - Quality gate blocks on unresolved contradictions
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from vt_protocol.cli.commands import main
from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionVerdict,
    Decision,
    Dimension,
)

from tests.helpers.decision_factory import (
    make_conflicting_pair,
    make_contradiction,
    make_week_seven_decisions,
)
from tests.helpers.repo_factory import create_project, write_contradiction, write_decision

pytestmark = pytest.mark.integration


class TestWeekSevenWall:
    """20 decisions, all 12 dimensions, full pipeline."""

    @pytest.fixture
    def project(self, tmp_path):
        root = create_project(tmp_path)
        decisions = make_week_seven_decisions()
        for i, d in enumerate(decisions):
            write_decision(root, d, filename=f"{i + 1:03d}-{d.dimensions[0].value}.json")
        return root, decisions

    def test_check_passes_clean(self, project):
        """CLI check passes when there are no contradictions."""
        root, _ = project
        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["actionable_contradictions"] == 0
        assert data["status"] == "pass"

    def test_check_reports_all_decisions(self, project):
        """CLI check reports all 20 decisions."""
        root, decisions = project
        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == len(decisions)

    def test_all_12_dimensions_covered(self, project):
        """All 12 core dimensions are represented in the decision set."""
        _, decisions = project
        dims_seen = set()
        for d in decisions:
            for dim in d.dimensions:
                dims_seen.add(dim)
        assert dims_seen == set(Dimension)

    async def test_dashboard_health_clean(self, project):
        """Dashboard /api/health shows coherence 1.0 with no contradictions."""
        root, decisions = project
        state = DashboardState(project_root=root)
        state.decisions = decisions
        state.contradictions = []
        set_state(state)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data["coherence_score"] == 1.0
                assert data["actionable_contradictions"] == 0
                assert data["active_decisions"] == len(decisions)
        finally:
            reset_state()

    def test_contradiction_detected_after_conflict(self, project):
        """Adding a conflicting decision pair creates a detectable contradiction."""
        root, decisions = project
        d_a, d_b = make_conflicting_pair()
        write_decision(root, d_a, filename="conflict-a.json")
        write_decision(root, d_b, filename="conflict-b.json")

        # Write a contradiction between them
        c = make_contradiction(d_a, d_b)
        write_contradiction(root, c)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["actionable_contradictions"] >= 1
        assert data["status"] == "fail"

    def test_gate_blocks_on_contradiction(self, project):
        """Quality gate returns exit code 1 when contradictions exist."""
        root, decisions = project
        d_a, d_b = make_conflicting_pair()
        write_decision(root, d_a, filename="conflict-a.json")
        write_decision(root, d_b, filename="conflict-b.json")
        c = make_contradiction(d_a, d_b)
        write_contradiction(root, c)

        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["passed"] is False

    def test_gate_passes_when_clean(self, project):
        """Quality gate passes with no contradictions."""
        root, _ = project
        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["passed"] is True

    async def test_dashboard_decisions_endpoint(self, project):
        """Dashboard /api/decisions returns all decisions."""
        root, decisions = project
        state = DashboardState(project_root=root)
        state.decisions = decisions
        set_state(state)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/decisions")
                assert resp.status_code == 200
                data = resp.json()
                assert data["total"] == len(decisions)
                assert len(data["decisions"]) == len(decisions)
        finally:
            reset_state()

    async def test_dashboard_filter_by_dimension(self, project):
        """Dashboard can filter decisions by dimension."""
        root, decisions = project
        state = DashboardState(project_root=root)
        state.decisions = decisions
        set_state(state)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/decisions?dimension=database")
                assert resp.status_code == 200
                data = resp.json()
                assert data["total"] >= 1
                for d in data["decisions"]:
                    assert "database" in d["dimensions"]
        finally:
            reset_state()

    async def test_dashboard_graph_has_shared_dimension_edges(self, project):
        """Dashboard graph shows shared-dimension edges between decisions."""
        root, decisions = project
        state = DashboardState(project_root=root)
        state.decisions = decisions
        set_state(state)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/graph")
                assert resp.status_code == 200
                data = resp.json()
                assert len(data["nodes"]) == len(decisions)
                # Decisions sharing dimensions create edges
                shared_edges = [e for e in data["edges"] if e["data"]["type"] == "SHARED_DIMENSION"]
                assert len(shared_edges) > 0
        finally:
            reset_state()

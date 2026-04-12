"""Integration test: 50-Pair False Positive Storm.

50 pairs of compatible decisions should produce ZERO actionable
contradictions. Tests that the system does not generate false positives
when decisions are genuinely compatible.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from vt_protocol.cli.commands import main
from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import ContradictionVerdict

from tests.helpers.decision_factory import make_compatible_pair, make_contradiction
from tests.helpers.repo_factory import create_project, write_decision

pytestmark = pytest.mark.integration


class TestFalsePositiveStorm:
    """50 compatible decision pairs must not trigger false positives."""

    @pytest.fixture
    def project_with_50_pairs(self, tmp_path):
        root = create_project(tmp_path)
        all_decisions = []
        for i in range(50):
            d_a, d_b = make_compatible_pair(i)
            write_decision(root, d_a, filename=f"pair-{i:03d}-a.json")
            write_decision(root, d_b, filename=f"pair-{i:03d}-b.json")
            all_decisions.extend([d_a, d_b])
        return root, all_decisions

    def test_check_passes_clean(self, project_with_50_pairs):
        """CLI check with 100 decisions, zero contradictions."""
        root, _ = project_with_50_pairs
        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == 100
        assert data["actionable_contradictions"] == 0

    def test_gate_passes_clean(self, project_with_50_pairs):
        """Quality gate passes with 100 compatible decisions."""
        root, _ = project_with_50_pairs
        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["passed"] is True

    async def test_dashboard_shows_all_100(self, project_with_50_pairs):
        """Dashboard lists all 100 decisions."""
        root, decisions = project_with_50_pairs
        state = DashboardState(project_root=root)
        state.decisions = decisions
        set_state(state)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/decisions?limit=200")
                assert resp.status_code == 200
                data = resp.json()
                assert data["total"] == 100
        finally:
            reset_state()

    async def test_health_coherence_perfect(self, project_with_50_pairs):
        """Health endpoint shows perfect coherence with zero contradictions."""
        root, decisions = project_with_50_pairs
        state = DashboardState(project_root=root)
        state.decisions = decisions
        set_state(state)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data["coherence_score"] == 1.0
                assert data["status"] == "healthy"
        finally:
            reset_state()

    async def test_compatible_contradictions_not_actionable(self, project_with_50_pairs):
        """COMPATIBLE-verdict contradictions should not be actionable."""
        root, decisions = project_with_50_pairs
        # Create compatible contradictions between each pair
        contradictions = []
        for i in range(0, len(decisions), 2):
            c = make_contradiction(
                decisions[i], decisions[i + 1],
                verdict=ContradictionVerdict.COMPATIBLE,
                reasoning="These decisions are complementary tools.",
                confidence=0.9,
            )
            contradictions.append(c)

        state = DashboardState(project_root=root)
        state.decisions = decisions
        state.contradictions = contradictions
        set_state(state)
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/health")
                assert resp.status_code == 200
                data = resp.json()
                # COMPATIBLE contradictions are not actionable
                assert data["actionable_contradictions"] == 0
                assert data["coherence_score"] == 1.0
        finally:
            reset_state()

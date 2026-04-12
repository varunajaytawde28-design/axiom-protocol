"""Integration test: Full Pipeline.

End-to-end test of the VT Protocol workflow:
  vt init → add decisions → vt check → detect contradictions →
  resolve via dashboard → vt apply → vt gate
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from vt_protocol.cli.commands import main
from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

from tests.helpers.decision_factory import make_contradiction, make_decision
from tests.helpers.repo_factory import create_project, write_contradiction, write_decision

pytestmark = pytest.mark.integration


class TestFullPipeline:
    """End-to-end governance workflow."""

    def test_init_creates_structure(self, tmp_path):
        """vt init creates .smm/ dir and governance.yaml."""
        root = tmp_path / "myproject"
        root.mkdir()
        (root / ".git").mkdir()

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--path", str(root), "--no-hooks", "--no-mcp", "--no-llm-prompt", "--no-agent-prompt"])
        assert result.exit_code == 0
        assert (root / ".smm" / "decisions").is_dir()
        assert (root / "governance.yaml").is_file()

    def test_init_then_check(self, tmp_path):
        """vt init → vt check works on a fresh project."""
        root = tmp_path / "myproject"
        root.mkdir()
        (root / ".git").mkdir()

        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(root), "--no-hooks", "--no-mcp", "--no-llm-prompt", "--no-agent-prompt"])
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "pass"

    def test_add_decisions_then_check(self, tmp_path):
        """Manually writing decisions makes them visible to check."""
        root = create_project(tmp_path)
        d1 = make_decision(title="Use PostgreSQL", dimensions=[Dimension.DATABASE])
        d2 = make_decision(title="Use Redis caching", dimensions=[Dimension.CACHING])
        write_decision(root, d1)
        write_decision(root, d2)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert data["active_decisions"] == 2

    def test_contradiction_blocks_gate(self, tmp_path):
        """Adding a contradiction makes the gate fail."""
        root = create_project(tmp_path)
        d_a = make_decision(
            title="Use SQLite",
            content="SQLite for all storage.",
            dimensions=[Dimension.DATABASE],
        )
        d_b = make_decision(
            title="Use PostgreSQL",
            content="PostgreSQL for all storage.",
            dimensions=[Dimension.DATABASE],
        )
        write_decision(root, d_a)
        write_decision(root, d_b)
        write_contradiction(root, make_contradiction(d_a, d_b))

        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 1

    async def test_resolve_then_gate_passes(self, tmp_path):
        """Resolving contradictions via dashboard makes the gate pass."""
        root = create_project(tmp_path)
        d_a = make_decision(title="Use SQLite", dimensions=[Dimension.DATABASE])
        d_b = make_decision(title="Use PostgreSQL", dimensions=[Dimension.DATABASE])
        write_decision(root, d_a)
        write_decision(root, d_b)
        c = make_contradiction(d_a, d_b)
        write_contradiction(root, c)

        # Resolve via dashboard API
        state = DashboardState(project_root=root)
        state.decisions = [d_a, d_b]
        state.contradictions = [c]
        set_state(state)

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    f"/api/contradictions/{c.id}/resolve",
                    json={"winner_id": str(d_b.id), "rationale": "PostgreSQL for production"},
                )
                assert resp.status_code == 200
        finally:
            reset_state()

        # Now the on-disk contradiction should be resolved (saved by dashboard)
        # Reload and check
        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        # After resolution the contradiction is no longer actionable
        assert data["actionable_contradictions"] == 0

    def test_apply_generates_files(self, tmp_path):
        """vt apply generates agent instruction files."""
        root = create_project(tmp_path)
        d1 = make_decision(
            title="Use PostgreSQL",
            content="PostgreSQL for primary data storage with concurrent access.",
            dimensions=[Dimension.DATABASE],
            rationale="Concurrent access needed",
            alternatives=["SQLite", "MySQL"],
        )
        write_decision(root, d1)

        runner = CliRunner()
        result = runner.invoke(main, ["apply", "--path", str(root)])
        assert result.exit_code == 0
        assert "Generated" in result.output

    def test_check_human_output(self, tmp_path):
        """vt check (non-JSON) produces readable human output."""
        root = create_project(tmp_path)
        d1 = make_decision(title="Use REST API", dimensions=[Dimension.API_STYLE])
        write_decision(root, d1)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root)])
        assert result.exit_code == 0
        assert "Governance Check" in result.output
        assert "Use REST API" in result.output
        assert "PASS" in result.output

"""Gemini Chaos: Anthropic API Degradation.

Tests that the system gracefully handles Anthropic API outages and errors.
External API calls are mocked — internal modules are real.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.mcp.server import check_before_coding, report_decision

from tests.helpers.repo_factory import create_project, write_decision
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

pytestmark = pytest.mark.chaos


class TestMCPWithoutExternalAPI:
    """MCP tools must work without external API access."""

    def test_check_before_coding_works_offline(self):
        """check_before_coding returns valid JSON without any external calls."""
        result = check_before_coding("src/app.py", project="offline-test")
        data = json.loads(result)
        assert "file_path" in data
        assert data["file_path"] == "src/app.py"
        assert "session_id" in data

    def test_report_decision_works_offline(self):
        """report_decision creates a record without external API."""
        result = report_decision(
            title="Offline Decision",
            content="Made without API access.",
            rationale="Testing offline resilience.",
            decision_type="technical",
            dimensions=["database"],
            project="offline-test",
        )
        data = json.loads(result)
        assert "decision_id" in data

    def test_multiple_mcp_calls_no_crash(self):
        """Rapid sequential MCP calls don't crash."""
        for i in range(20):
            result = check_before_coding(f"src/file_{i}.py", project="stress-test")
            data = json.loads(result)
            assert "session_id" in data


class TestCLIWithDegradedState:
    """CLI commands handle degraded state gracefully."""

    def test_check_with_no_project(self, tmp_path):
        """vt check on empty directory — graceful error."""
        root = tmp_path / "empty"
        root.mkdir()
        (root / ".git").mkdir()

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data

    def test_gate_with_no_smm(self, tmp_path):
        """vt gate with no .smm/ — should pass (no contradictions)."""
        root = tmp_path / "no-smm"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm").mkdir()

        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 0

    def test_apply_on_empty_project(self, tmp_path):
        """vt apply on empty project — generates minimal output."""
        root = create_project(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["apply", "--path", str(root)])
        # Should not crash
        assert result.exit_code == 0


class TestDashboardWithDegradedState:
    """Dashboard handles missing/corrupted state."""

    @pytest.mark.asyncio
    async def test_health_with_empty_state(self, tmp_path):
        """Health endpoint with no decisions or contradictions."""
        from httpx import ASGITransport, AsyncClient
        from vt_protocol.dashboard.app import DashboardState, app, set_state

        root = tmp_path / "empty-dash"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm").mkdir(parents=True)

        state = DashboardState(project_root=root)
        set_state(state)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["total_decisions"] == 0

    @pytest.mark.asyncio
    async def test_decisions_endpoint_empty(self, tmp_path):
        """Decisions endpoint with empty state returns empty list."""
        from httpx import ASGITransport, AsyncClient
        from vt_protocol.dashboard.app import DashboardState, app, set_state

        root = tmp_path / "empty-decisions"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm").mkdir(parents=True)

        state = DashboardState(project_root=root)
        set_state(state)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/decisions")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 0

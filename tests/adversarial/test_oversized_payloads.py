"""Adversarial test: Oversized Payloads.

Tests that the system handles very large decisions, many decisions,
and deeply nested structures without crashing.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.decisions.models import Decision, Dimension
from vt_protocol.mcp.server import _sessions, report_decision, validate_change

from tests.helpers.decision_factory import make_decision
from tests.helpers.repo_factory import create_project, write_decision

pytestmark = pytest.mark.adversarial


class TestOversizedContent:
    """Very large content fields."""

    def test_large_content_decision(self, tmp_path):
        """Decision with 100KB content survives pipeline."""
        root = create_project(tmp_path)
        big_content = "A" * 100_000
        d = make_decision(
            title="Big decision",
            content=big_content,
            dimensions=[Dimension.DATABASE],
        )
        write_decision(root, d)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == 1

    def test_many_alternatives(self, tmp_path):
        """Decision with 1000 alternatives."""
        root = create_project(tmp_path)
        d = make_decision(
            title="Many alternatives",
            alternatives=[f"alt-{i}" for i in range(1000)],
        )
        write_decision(root, d)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0

    def test_many_decisions(self, tmp_path):
        """200 decisions in one project."""
        root = create_project(tmp_path)
        for i in range(200):
            d = make_decision(
                title=f"Decision {i}",
                dimensions=[list(Dimension)[i % len(Dimension)]],
            )
            write_decision(root, d, filename=f"d-{i:03d}.json")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == 200


class TestOversizedMCPPayloads:
    """Oversized inputs to MCP tools."""

    @pytest.fixture(autouse=True)
    def _clear_sessions(self):
        _sessions.clear()
        yield
        _sessions.clear()

    def test_large_diff_validate(self):
        """validate_change with a very large diff."""
        big_diff = "\n".join([f"+ line {i}" for i in range(10_000)])
        result = validate_change(diff=big_diff, project="test")
        data = json.loads(result)
        assert "status" in data

    def test_large_content_report(self):
        """report_decision with very large content."""
        result = report_decision(
            title="Big report",
            content="X" * 50_000,
            project="test",
        )
        data = json.loads(result)
        assert "decision_id" in data

    def test_many_dimensions_report(self):
        """report_decision with all 12 dimensions."""
        result = report_decision(
            title="All dimensions",
            content="A decision touching everything.",
            dimensions=[d.value for d in Dimension],
            project="test",
        )
        data = json.loads(result)
        assert len(data["dimensions"]) == 12

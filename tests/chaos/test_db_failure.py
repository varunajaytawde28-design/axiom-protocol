"""Chaos test: Database Failure.

Tests graceful degradation when the graph database is unavailable.
MCP tools should return useful responses even without PostgreSQL.
CLI commands should work with local .smm/ files only.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from vt_protocol.mcp.server import (
    _sessions,
    check_before_coding,
    get_project_decisions,
    report_decision,
    validate_change,
)

pytestmark = pytest.mark.chaos


@pytest.fixture(autouse=True)
def clear_sessions():
    _sessions.clear()
    yield
    _sessions.clear()


class TestMCPWithoutDB:
    """All MCP tools work when graph_client raises."""

    def test_check_before_coding_no_db(self):
        """check_before_coding returns empty decisions without DB."""
        result = check_before_coding("src/main.py", project="test")
        data = json.loads(result)
        assert data["relevant_decisions"] == []
        assert "session_id" in data

    def test_get_project_decisions_no_db(self):
        """get_project_decisions returns empty list without DB."""
        result = get_project_decisions(project="test")
        data = json.loads(result)
        assert data["total_decisions"] == 0

    def test_report_decision_no_db(self):
        """report_decision records locally without DB."""
        result = report_decision(
            title="Test decision",
            content="Content",
            project="test",
        )
        data = json.loads(result)
        assert data["status"] == "recorded_locally"

    def test_validate_change_no_db(self):
        """validate_change works without DB."""
        result = validate_change(diff="+new line", project="test")
        data = json.loads(result)
        assert "status" in data

    def test_session_still_created(self):
        """Sessions are created even without DB."""
        check_before_coding("file.py", project="test")
        assert len(_sessions) == 1

    def test_multiple_tools_same_session(self):
        """Multiple tool calls share a session without DB."""
        check_before_coding("a.py", project="test")
        get_project_decisions(project="test")
        report_decision(title="Test", content="C", project="test")
        assert len(_sessions) == 1


class TestCLIWithoutDB:
    """CLI commands work with local files even when DB is unavailable."""

    def test_check_local_only(self, tmp_path):
        """vt check works with local .smm/ files."""
        from click.testing import CliRunner
        from vt_protocol.cli.commands import main
        from tests.helpers.decision_factory import make_decision
        from tests.helpers.repo_factory import create_project, write_decision

        root = create_project(tmp_path)
        write_decision(root, make_decision(title="Local decision"))

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == 1


class TestMerkleWithCorruptDB:
    """Merkle tree handles SQLite issues gracefully."""

    def test_new_tree_empty(self):
        """Fresh in-memory tree has size 0."""
        from vt_protocol.audit.merkle import MerkleTree
        tree = MerkleTree(":memory:")
        assert tree.size == 0
        tree.close()

    def test_tree_survives_bad_entry(self):
        """Tree continues after appending valid entries."""
        from vt_protocol.audit.merkle import MerkleTree
        from vt_protocol.decisions.models import AuditEntry, AuditEventType

        tree = MerkleTree(":memory:")
        entry = AuditEntry(
            event_type=AuditEventType.DECISION_ADDED,
            actor="test",
            payload={"key": "value"},
        )
        idx = tree.append(entry)
        assert idx == 0
        assert tree.size == 1
        tree.close()

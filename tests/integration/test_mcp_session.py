"""Integration test: MCP Session Flow.

Tests the 5 MCP tools using actual server.py functions:
  check_before_coding → report_decision → validate_change →
  get_project_decisions → get_resolution

Since MCP tools gracefully handle unavailable DB (returning empty lists),
these tests exercise the real tool logic without PostgreSQL.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionVerdict,
    Decision,
    Dimension,
)
from vt_protocol.mcp.server import (
    _sessions,
    check_before_coding,
    get_project_decisions,
    get_resolution,
    report_decision,
    validate_change,
)

from tests.helpers.decision_factory import make_contradiction, make_decision

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clear_sessions():
    """Reset MCP sessions before each test."""
    _sessions.clear()
    yield
    _sessions.clear()


class TestCheckBeforeCoding:
    """Tool 1: check_before_coding."""

    def test_returns_json(self):
        """Tool returns valid JSON."""
        result = check_before_coding("src/models.py", project="test")
        data = json.loads(result)
        assert "session_id" in data
        assert "project" in data
        assert data["project"] == "test"

    def test_file_path_in_response(self):
        """Response includes the file path."""
        result = check_before_coding("src/db/schema.py", project="test")
        data = json.loads(result)
        assert data["file_path"] == "src/db/schema.py"

    def test_creates_session(self):
        """Calling check_before_coding creates a session."""
        assert len(_sessions) == 0
        result = check_before_coding("src/main.py", project="test")
        assert len(_sessions) == 1
        data = json.loads(result)
        session_id = data["session_id"]
        assert session_id in _sessions

    def test_reuses_session(self):
        """Multiple calls for same project reuse the session."""
        check_before_coding("a.py", project="test")
        check_before_coding("b.py", project="test")
        assert len(_sessions) == 1

    def test_empty_decisions_without_db(self):
        """Without DB, returns empty relevant_decisions."""
        result = check_before_coding("src/main.py", project="test")
        data = json.loads(result)
        assert data["relevant_decisions"] == []


class TestReportDecision:
    """Tool 4: report_decision."""

    def test_reports_decision(self):
        """Tool creates and returns a decision."""
        result = report_decision(
            title="Use PostgreSQL",
            content="PostgreSQL for primary storage.",
            rationale="Concurrent access needed",
            decision_type="technical",
            dimensions=["database"],
            project="test",
        )
        data = json.loads(result)
        assert "decision_id" in data
        assert data["title"] == "Use PostgreSQL"
        assert data["dimensions"] == ["database"]

    def test_status_recorded_locally(self):
        """Without DB, status is 'recorded_locally'."""
        result = report_decision(
            title="Use Redis",
            content="Redis for caching layer.",
            dimensions=["caching"],
            project="test",
        )
        data = json.loads(result)
        assert data["status"] == "recorded_locally"

    def test_tracks_session(self):
        """Decision is linked to an MCP session."""
        result = report_decision(
            title="Use Docker",
            content="Docker containers for deployment.",
            dimensions=["deployment"],
            project="test",
        )
        data = json.loads(result)
        assert "session_id" in data
        session = _sessions[data["session_id"]]
        # Without DB, decisions_made is not populated (graph client unavailable),
        # but the session itself is created
        assert session.project == "test"

    def test_invalid_dimension_ignored(self):
        """Unknown dimension names are silently ignored."""
        result = report_decision(
            title="Test",
            content="Test content",
            dimensions=["nonexistent_dimension"],
            project="test",
        )
        data = json.loads(result)
        assert data["dimensions"] == []

    def test_supersedes(self):
        """Can report a decision that supersedes another."""
        r1 = report_decision(
            title="Original",
            content="Original decision.",
            dimensions=["database"],
            project="test",
        )
        d1_id = json.loads(r1)["decision_id"]

        r2 = report_decision(
            title="Updated",
            content="Updated decision.",
            dimensions=["database"],
            project="test",
            supersedes=d1_id,
        )
        data = json.loads(r2)
        assert data["supersedes"] == d1_id


class TestValidateChange:
    """Tool 2: validate_change."""

    def test_clean_diff_passes(self):
        """A diff with no new dependencies passes."""
        result = validate_change(
            diff="- old_line\n+ new_line",
            project="test",
        )
        data = json.loads(result)
        assert data["status"] == "pass"
        assert data["dependency_check"]["passed"] is True

    def test_detects_new_python_deps(self):
        """Detects new Python dependencies in diff."""
        diff = (
            "+fastapi>=0.100\n"
            "+sqlalchemy>=2.0\n"
            "+celery>=5.3\n"
            "+redis>=5.0\n"
        )
        result = validate_change(diff=diff, project="test")
        data = json.loads(result)
        assert len(data["dependency_check"]["new_deps_found"]) == 4
        assert data["dependency_check"]["passed"] is False

    def test_within_dep_limit(self):
        """Deps within limit still pass."""
        diff = "+httpx>=0.27\n+pydantic>=2.7\n"
        result = validate_change(diff=diff, project="test")
        data = json.loads(result)
        assert data["dependency_check"]["passed"] is True


class TestGetProjectDecisions:
    """Tool 3: get_project_decisions."""

    def test_returns_json(self):
        """Returns valid JSON with decision list."""
        result = get_project_decisions(project="test")
        data = json.loads(result)
        assert "decisions" in data
        assert data["project"] == "test"

    def test_empty_without_db(self):
        """Without DB, returns empty decisions list."""
        result = get_project_decisions(project="test")
        data = json.loads(result)
        assert data["total_decisions"] == 0

    def test_creates_session(self):
        """Calling increments context_injections."""
        get_project_decisions(project="test")
        session = list(_sessions.values())[0]
        assert session.context_injections == 1

        get_project_decisions(project="test")
        assert session.context_injections == 2


class TestGetResolution:
    """Tool 5: get_resolution."""

    def test_invalid_uuid(self):
        """Invalid UUID returns error."""
        result = get_resolution("not-a-uuid", project="test")
        data = json.loads(result)
        assert "error" in data

    def test_nonexistent_contradiction(self):
        """Valid UUID but not found returns error."""
        from uuid import uuid4
        result = get_resolution(str(uuid4()), project="test")
        data = json.loads(result)
        assert "error" in data

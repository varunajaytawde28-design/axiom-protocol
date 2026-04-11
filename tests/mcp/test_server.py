"""Tests for MCP server — 5 tools.

All database operations are mocked so tests run without PostgreSQL.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.mcp.server import (
    _detect_new_deps_in_diff,
    _filter_relevant_decisions,
    _get_or_create_session,
    _sessions,
    check_before_coding,
    get_project_decisions,
    get_resolution,
    report_decision,
    validate_change,
)


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear session state between tests."""
    _sessions.clear()
    yield
    _sessions.clear()


def _make_decision(**kwargs) -> Decision:
    defaults = {
        "title": "Test decision",
        "content": "Test content for the decision",
        "made_by": "test",
        "project": "test-project",
        "dimensions": [Dimension.DATABASE],
    }
    defaults.update(kwargs)
    return Decision(**defaults)


class TestCheckBeforeCoding:
    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test-project")
    def test_returns_relevant_decisions(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.get_decisions.return_value = [
            _make_decision(title="Use PostgreSQL", dimensions=[Dimension.DATABASE]),
        ]
        client.get_unresolved_contradictions.return_value = []

        result = json.loads(check_before_coding("src/models/user.py", project="test-project"))
        assert result["project"] == "test-project"
        assert len(result["relevant_decisions"]) == 1
        assert result["relevant_decisions"][0]["title"] == "Use PostgreSQL"

    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test-project")
    def test_creates_session(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.get_decisions.return_value = []
        client.get_unresolved_contradictions.return_value = []

        result = json.loads(check_before_coding("foo.py", project="test-project"))
        assert "session_id" in result
        assert len(_sessions) == 1

    @patch("vt_protocol.mcp.server._get_graph_client", side_effect=Exception("no db"))
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_graceful_without_db(self, mock_project, mock_client) -> None:
        result = json.loads(check_before_coding("foo.py", project="test"))
        assert result["relevant_decisions"] == []


class TestValidateChange:
    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    @patch("vt_protocol.mcp.server._load_config", return_value=None)
    def test_passes_clean_diff(self, mock_config, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.get_decisions.return_value = []

        result = json.loads(validate_change("+ fixed a bug", project="test"))
        assert result["status"] == "pass"

    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    @patch("vt_protocol.mcp.server._load_config", return_value=None)
    def test_warns_many_deps(self, mock_config, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.get_decisions.return_value = []

        diff = "\n".join([
            "+ fastapi>=0.111",
            "+ redis>=5.0",
            "+ celery>=5.3",
            "+ pydantic>=2.7",
        ])
        result = json.loads(validate_change(diff, project="test"))
        assert result["status"] == "warning"
        assert len(result["dependency_check"]["new_deps_found"]) == 4


class TestGetProjectDecisions:
    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_returns_all_decisions(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.get_decisions.return_value = [
            _make_decision(title="Decision A"),
            _make_decision(title="Decision B"),
        ]

        result = json.loads(get_project_decisions(project="test"))
        assert result["total_decisions"] == 2

    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_filter_by_dimension(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.get_decisions.return_value = [
            _make_decision(title="DB decision", dimensions=[Dimension.DATABASE]),
            _make_decision(title="Auth decision", dimensions=[Dimension.AUTH]),
        ]

        result = json.loads(get_project_decisions(project="test", dimension="database"))
        assert result["total_decisions"] == 1
        assert result["decisions"][0]["title"] == "DB decision"

    @patch("vt_protocol.mcp.server._get_graph_client", side_effect=Exception("no db"))
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_graceful_without_db(self, mock_project, mock_client) -> None:
        result = json.loads(get_project_decisions(project="test"))
        assert result["total_decisions"] == 0


class TestReportDecision:
    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_records_decision(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.add_decision.return_value = uuid4()

        result = json.loads(report_decision(
            title="Use Redis for caching",
            content="Redis provides sub-ms latency for frequently accessed data",
            dimensions=["caching"],
            project="test",
        ))
        assert result["status"] == "recorded"
        assert result["title"] == "Use Redis for caching"
        assert "caching" in result["dimensions"]
        client.add_decision.assert_called_once()

    @patch("vt_protocol.mcp.server._get_graph_client", side_effect=Exception("no db"))
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_records_locally_without_db(self, mock_project, mock_client) -> None:
        result = json.loads(report_decision(
            title="Local decision",
            content="Recorded without database",
            project="test",
        ))
        assert result["status"] == "recorded_locally"

    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_supersedes_old_decision(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        old_id = uuid4()
        client.supersede.return_value = uuid4()

        result = json.loads(report_decision(
            title="New DB choice",
            content="Switching to PostgreSQL",
            supersedes=str(old_id),
            project="test",
        ))
        assert result["supersedes"] == str(old_id)
        client.supersede.assert_called_once()

    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_tracks_in_session(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        new_id = uuid4()
        client.add_decision.return_value = new_id

        report_decision(title="Test", content="Test decision", project="test")
        assert len(_sessions) == 1
        session = list(_sessions.values())[0]
        assert new_id in session.decisions_made


class TestGetResolution:
    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_found(self, mock_project, mock_client) -> None:
        cid = uuid4()
        client = MagicMock()
        mock_client.return_value = client
        client.get_unresolved_contradictions.return_value = [
            Contradiction(
                id=cid,
                decision_a_id=uuid4(),
                decision_b_id=uuid4(),
                decision_a_title="A",
                decision_b_title="B",
                verdict=ContradictionVerdict.CONTRADICTION,
                reasoning="They conflict",
                evidence_a="Evidence from A",
                evidence_b="Evidence from B",
                confidence=0.9,
                shared_dimensions=[Dimension.DATABASE],
            ),
        ]

        result = json.loads(get_resolution(str(cid), project="test"))
        assert result["verdict"] == "contradiction"
        assert result["reasoning"] == "They conflict"

    @patch("vt_protocol.mcp.server._get_graph_client")
    @patch("vt_protocol.mcp.server._detect_project", return_value="test")
    def test_not_found(self, mock_project, mock_client) -> None:
        client = MagicMock()
        mock_client.return_value = client
        client.get_unresolved_contradictions.return_value = []

        result = json.loads(get_resolution(str(uuid4()), project="test"))
        assert "error" in result

    def test_invalid_uuid(self) -> None:
        result = json.loads(get_resolution("not-a-uuid", project="test"))
        assert "error" in result


class TestHelpers:
    def test_filter_relevant_by_database(self) -> None:
        decisions = [
            _make_decision(title="PG", dimensions=[Dimension.DATABASE]),
            _make_decision(title="Auth", dimensions=[Dimension.AUTH]),
        ]
        result = _filter_relevant_decisions(decisions, "src/models/user.py")
        assert len(result) == 1
        assert result[0].title == "PG"

    def test_filter_returns_all_for_unknown_path(self) -> None:
        decisions = [_make_decision(), _make_decision()]
        result = _filter_relevant_decisions(decisions, "src/utils/random.py")
        assert len(result) == 2  # No dimension match → return all

    def test_detect_deps_python(self) -> None:
        diff = "+ fastapi>=0.111\n+ redis>=5.0\n"
        deps = _detect_new_deps_in_diff(diff)
        assert "fastapi" in deps
        assert "redis" in deps

    def test_detect_deps_empty(self) -> None:
        assert _detect_new_deps_in_diff("no deps here") == []

    def test_session_reuse(self) -> None:
        s1 = _get_or_create_session("project-a")
        s2 = _get_or_create_session("project-a")
        assert s1.session_id == s2.session_id

    def test_session_per_project(self) -> None:
        s1 = _get_or_create_session("project-a")
        s2 = _get_or_create_session("project-b")
        assert s1.session_id != s2.session_id

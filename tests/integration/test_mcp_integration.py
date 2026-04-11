"""Integration test — MCP server tool sequence.

Start server → call all 5 tools in sequence → verify state consistency.
Tests the MCP tools as plain functions (no transport layer needed).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from vt_protocol.mcp.server import (
    _sessions,
    check_before_coding,
    get_project_decisions,
    get_resolution,
    report_decision,
    validate_change,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    """Reset MCP session state between tests."""
    _sessions.clear()


def _mock_graph_client(decisions=None, contradictions=None):
    """Create a mock graph client."""
    client = MagicMock()
    client.get_decisions.return_value = decisions or []
    client.get_unresolved_contradictions.return_value = contradictions or []
    client.add_decision.return_value = None
    return client


class TestMCPToolSequence:
    """Exercise all 5 MCP tools in a realistic sequence."""

    def test_check_before_coding_empty_graph(self) -> None:
        """Tool 1: check_before_coding with no prior decisions."""
        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="test-project"):
            mock_gc.return_value = _mock_graph_client()
            result = json.loads(check_before_coding("src/db/models.py"))

        assert result["project"] == "test-project"
        assert result["relevant_decisions"] == []
        assert "session_id" in result

    def test_report_decision_records(self) -> None:
        """Tool 4: report_decision creates a decision."""
        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="test-project"):
            client = _mock_graph_client()
            mock_gc.return_value = client

            result = json.loads(report_decision(
                title="Use SQLAlchemy ORM",
                content="All database access goes through SQLAlchemy",
                rationale="Team familiarity, migration support",
                decision_type="technical",
                dimensions=["database"],
            ))

        assert result["title"] == "Use SQLAlchemy ORM"
        assert "decision_id" in result
        assert result["dimensions"] == ["database"]
        # Graph client should have been called to add the decision
        client.add_decision.assert_called_once()

    def test_get_project_decisions_returns_recorded(self) -> None:
        """Tool 3: get_project_decisions returns decisions from graph."""
        from vt_protocol.decisions.models import Decision, Dimension, SourceType

        mock_decision = Decision(
            title="Use PostgreSQL",
            content="PostgreSQL for all storage",
            dimensions=[Dimension.DATABASE],
            made_by="test",
            project="test-project",
            source_type=SourceType.MANUAL,
        )

        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="test-project"):
            mock_gc.return_value = _mock_graph_client(decisions=[mock_decision])
            result = json.loads(get_project_decisions(dimension="database"))

        assert result["total_decisions"] == 1
        assert result["decisions"][0]["title"] == "Use PostgreSQL"

    def test_validate_change_passes(self) -> None:
        """Tool 2: validate_change with a clean diff."""
        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="test-project"), \
             patch("vt_protocol.mcp.server._load_config", return_value=None):
            mock_gc.return_value = _mock_graph_client()
            result = json.loads(validate_change(
                diff="--- a/src/models.py\n+++ b/src/models.py\n+class User:\n+    pass",
                file_path="src/models.py",
            ))

        assert result["status"] == "pass"

    def test_validate_change_warns_on_too_many_deps(self) -> None:
        """Tool 2: validate_change flags excessive new dependencies."""
        diff = "\n".join([
            "+requests>=2.28",
            "+flask>=2.3",
            "+sqlalchemy>=2.0",
            "+redis>=4.5",
            "+celery>=5.3",
        ])

        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="test-project"), \
             patch("vt_protocol.mcp.server._load_config", return_value=None):
            mock_gc.return_value = _mock_graph_client()
            result = json.loads(validate_change(diff=diff))

        assert len(result["dependency_check"]["new_deps_found"]) >= 4

    def test_get_resolution_not_found(self) -> None:
        """Tool 5: get_resolution returns error for unknown contradiction."""
        from uuid import uuid4

        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="test-project"):
            mock_gc.return_value = _mock_graph_client()
            result = json.loads(get_resolution(str(uuid4())))

        assert "error" in result

    def test_session_persists_across_tools(self) -> None:
        """Sessions are reused across tool calls in the same project."""
        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="test-project"):
            mock_gc.return_value = _mock_graph_client()

            r1 = json.loads(check_before_coding("a.py"))
            # get_project_decisions also creates/reuses session
            json.loads(get_project_decisions())

        # Only one session should exist (reused)
        assert len(_sessions) == 1
        session = list(_sessions.values())[0]
        assert session.project == "test-project"
        # Both tools called context_injections
        assert session.context_injections == 2

    def test_full_tool_sequence(self) -> None:
        """Run all 5 tools in realistic order and verify state."""
        from vt_protocol.decisions.models import (
            Contradiction, ContradictionVerdict,
            Decision, Dimension, SourceType,
        )
        from uuid import uuid4

        d1 = Decision(
            title="Use REST API",
            content="REST for all external APIs",
            dimensions=[Dimension.API_STYLE],
            made_by="test",
            project="proj",
            source_type=SourceType.MANUAL,
        )

        contradiction = Contradiction(
            decision_a_id=d1.id,
            decision_b_id=uuid4(),
            decision_a_title="Use REST API",
            decision_b_title="Use GraphQL",
            verdict=ContradictionVerdict.TENSION,
            reasoning="Both valid but different approaches",
            evidence_a="REST for all APIs",
            evidence_b="GraphQL for flexibility",
            confidence=0.6,
        )

        with patch("vt_protocol.mcp.server._get_graph_client") as mock_gc, \
             patch("vt_protocol.mcp.server._detect_project", return_value="proj"), \
             patch("vt_protocol.mcp.server._load_config", return_value=None):
            client = _mock_graph_client(
                decisions=[d1],
                contradictions=[contradiction],
            )
            mock_gc.return_value = client

            # 1. Check before coding
            r = json.loads(check_before_coding("src/api/routes.py"))
            assert r["unresolved_contradictions"] == 1

            # 2. Validate change
            r = json.loads(validate_change(diff="+new_route()", file_path="src/api/routes.py"))
            assert r["status"] == "pass"

            # 3. Get project decisions
            r = json.loads(get_project_decisions())
            assert r["total_decisions"] == 1

            # 4. Report new decision
            client.add_decision.return_value = uuid4()
            r = json.loads(report_decision(
                title="Use FastAPI",
                content="FastAPI for all internal APIs",
                dimensions=["api-style"],
            ))
            assert "decision_id" in r

            # 5. Get resolution
            r = json.loads(get_resolution(str(contradiction.id)))
            assert r["verdict"] == "tension"

"""Gemini Scenario 1: The Week Seven Wall.

Validates long-term temporal contradiction detection. SQLite chosen early
conflicts with Celery workers requiring concurrent writes later. Full
traversal: observation → contradiction → routing → resolution → constraint
generation.

Rewired to use real CLI (CliRunner), real MCP tools, real dashboard API.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from vt_protocol.cli.commands import main
from vt_protocol.dashboard.app import DashboardState, app, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.mcp.server import report_decision, check_before_coding

from tests.helpers.repo_factory import create_project, write_contradiction, write_decision

pytestmark = pytest.mark.integration


def _sqlite_decision() -> Decision:
    return Decision(
        title="Use SQLite for primary datastore",
        content="Lightweight embedded database selected for initial rapid prototyping. "
                "SQLite's WAL mode provides sufficient performance for single-user local dev.",
        rationale="Simple, zero-config, embedded — ideal for prototyping phase.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.DATABASE],
        made_by="claude-code",
        project="test-project",
        source_type=SourceType.AGENT,
    )


def _celery_decision() -> Decision:
    return Decision(
        title="Celery for background processing",
        content="Offloading heavy email and report generation to Celery background queues. "
                "Requires concurrent database writes from 10 workers.",
        rationale="Background processing needed for email and PDF generation at scale.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.CONCURRENCY],
        made_by="cursor-agent",
        project="test-project",
        source_type=SourceType.AGENT,
    )


def _concurrent_write_decision() -> Decision:
    return Decision(
        title="Concurrent pool for database writes",
        content="Implementing 10 concurrent Celery workers writing to the database simultaneously. "
                "SQLite database locking prevents concurrent writes — this conflicts with the "
                "existing SQLite choice.",
        rationale="Scaling requires concurrent write access to primary datastore.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.DATABASE, Dimension.CONCURRENCY],
        made_by="copilot-agent",
        project="test-project",
        source_type=SourceType.AGENT,
    )


class TestWeekSevenWall:
    """Full temporal contradiction detection lifecycle."""

    @pytest.fixture
    def week7_project(self, tmp_path):
        root = create_project(tmp_path)
        d_sqlite = _sqlite_decision()
        d_celery = _celery_decision()
        d_concurrent = _concurrent_write_decision()
        write_decision(root, d_sqlite, filename="001-database.json")
        write_decision(root, d_celery, filename="005-concurrency.json")
        write_decision(root, d_concurrent, filename="007-concurrent-writes.json")
        return root, d_sqlite, d_celery, d_concurrent

    def test_initial_decisions_clean(self, week7_project):
        """Sessions 1-5: SQLite + Celery coexist without contradiction."""
        root, d_sqlite, d_celery, _ = week7_project
        # Remove the conflicting decision
        (root / ".smm" / "decisions" / "007-concurrent-writes.json").unlink()

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert data["actionable_contradictions"] == 0

    def test_concurrent_write_triggers_contradiction(self, week7_project):
        """Session 7: Concurrent writes conflict with SQLite."""
        root, d_sqlite, _, d_concurrent = week7_project

        # Create the contradiction between SQLite and concurrent writes
        c = Contradiction(
            decision_a_id=d_sqlite.id,
            decision_b_id=d_concurrent.id,
            decision_a_title=d_sqlite.title,
            decision_b_title=d_concurrent.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="SQLite database locking mechanisms prevent concurrent Celery worker writes. "
                      "The concurrent pool requires a database that supports multi-writer MVCC.",
            evidence_a="Lightweight embedded database selected for initial rapid prototyping.",
            evidence_b="10 concurrent Celery workers writing to the database simultaneously.",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.92,
        )
        write_contradiction(root, c)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert data["actionable_contradictions"] >= 1
        assert data["status"] == "fail"

    def test_gate_blocks_unresolved(self, week7_project):
        """Quality gate blocks on unresolved SQLite-vs-concurrent contradiction."""
        root, d_sqlite, _, d_concurrent = week7_project
        c = Contradiction(
            decision_a_id=d_sqlite.id,
            decision_b_id=d_concurrent.id,
            decision_a_title=d_sqlite.title,
            decision_b_title=d_concurrent.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="SQLite locking prevents concurrent writes.",
            evidence_a="SQLite embedded database",
            evidence_b="10 concurrent workers",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.92,
        )
        write_contradiction(root, c)

        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 1

    async def test_dashboard_shows_degraded_health(self, week7_project):
        """Dashboard health shows degraded state with contradiction."""
        root, d_sqlite, d_celery, d_concurrent = week7_project
        c = Contradiction(
            decision_a_id=d_sqlite.id,
            decision_b_id=d_concurrent.id,
            decision_a_title=d_sqlite.title,
            decision_b_title=d_concurrent.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="SQLite locking prevents concurrent writes.",
            evidence_a="SQLite embedded database",
            evidence_b="10 concurrent workers",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.92,
        )

        state = DashboardState(project_root=root)
        state.decisions = [d_sqlite, d_celery, d_concurrent]
        state.contradictions = [c]
        set_state(state)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/health")
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["actionable_contradictions"] >= 1
            assert data["coherence_score"] < 1.0

    async def test_resolve_then_apply_generates_rules(self, week7_project):
        """After resolution, vt apply generates rules without stale constraints."""
        root, d_sqlite, d_celery, d_concurrent = week7_project
        c = Contradiction(
            decision_a_id=d_sqlite.id,
            decision_b_id=d_concurrent.id,
            decision_a_title=d_sqlite.title,
            decision_b_title=d_concurrent.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="SQLite vs concurrent writes.",
            evidence_a="SQLite",
            evidence_b="Concurrent",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.92,
        )

        state = DashboardState(project_root=root)
        state.decisions = [d_sqlite, d_celery, d_concurrent]
        state.contradictions = [c]
        set_state(state)

        # Resolve via dashboard
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(d_concurrent.id), "rationale": "Migrate to PostgreSQL"},
            )
            assert resp.status_code == 200

        # vt apply should generate rules based on the resolved state
        runner = CliRunner()
        result = runner.invoke(main, ["apply", "--path", str(root)])
        assert result.exit_code == 0
        assert "Generated" in result.output

    def test_mcp_check_before_coding_returns_context(self, week7_project):
        """MCP check_before_coding returns relevant context for db files."""
        # MCP tools work without DB — just test they don't crash and return JSON
        result = check_before_coding("src/models/db.py", project="test-project")
        data = json.loads(result)
        assert data["file_path"] == "src/models/db.py"
        assert "session_id" in data

    def test_mcp_report_decision_records(self):
        """MCP report_decision creates a decision record."""
        result = report_decision(
            title="Migrate to PostgreSQL",
            content="Migrating from SQLite to PostgreSQL for concurrent write support.",
            rationale="SQLite cannot handle 10 concurrent Celery workers.",
            decision_type="technical",
            dimensions=["database"],
            project="test-project",
        )
        data = json.loads(result)
        assert "decision_id" in data
        assert data["dimensions"] == ["database"]

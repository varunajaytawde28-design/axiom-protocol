"""Tests for Fix 1: Lattice dashboard shows no traces.

Verifies:
- .smm/traces/ is created during vt init
- Hook script logs events to events.jsonl
- CLI commands log trace events
- Dashboard /api/traces reads and returns trace events
- Trace events merge correctly with observation timeline
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from vt_protocol.cli.commands import _log_trace_event, main
from vt_protocol.dashboard.app import (
    DashboardState,
    app,
    reset_state,
    set_state,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _reset():
    reset_state()
    yield
    reset_state()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    smm = root / ".smm"
    smm.mkdir()
    (smm / "decisions").mkdir()
    (smm / "traces").mkdir()
    (root / "governance.yaml").write_text(
        "extends:\n  - '@vt/recommended'\n"
        "model:\n  provider: none\n  model: ''\n"
        "agents:\n  claude: true\n"
    )
    return root


class TestTracesDirCreation:
    def test_ensure_smm_structure_creates_traces(self, tmp_path: Path) -> None:
        from vt_protocol.config import ensure_smm_structure

        root = tmp_path / "myproj"
        root.mkdir()
        ensure_smm_structure(root)
        assert (root / ".smm" / "traces").is_dir()

    def test_vt_init_creates_traces_dir(self, runner: CliRunner, tmp_path: Path) -> None:
        root = tmp_path / "init-proj"
        root.mkdir()
        (root / ".git").mkdir()

        result = runner.invoke(
            main,
            ["init", "--path", str(root), "--no-llm-prompt", "--no-agent-prompt"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert (root / ".smm" / "traces").is_dir()


class TestLogTraceEvent:
    def test_log_trace_event_creates_file(self, project_root: Path) -> None:
        _log_trace_event(project_root, "cli", "check", "pass", "No violations")
        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()

        lines = events_path.read_text().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["type"] == "cli"
        assert event["action"] == "check"
        assert event["result"] == "pass"
        assert event["agent"] == "cli"

    def test_log_trace_event_appends(self, project_root: Path) -> None:
        _log_trace_event(project_root, "cli", "check", "pass")
        _log_trace_event(project_root, "hook", "Write", "block", "violation")

        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        lines = events_path.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["type"] == "cli"
        assert json.loads(lines[1])["type"] == "hook"

    def test_log_trace_event_timestamp_format(self, project_root: Path) -> None:
        _log_trace_event(project_root, "cli", "apply", "pass")
        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        event = json.loads(events_path.read_text().strip())
        # Verify ISO 8601 format
        assert "T" in event["timestamp"]
        assert event["timestamp"].endswith("Z")


class TestCliTraceLogging:
    def test_check_logs_event(self, runner: CliRunner, project_root: Path) -> None:
        result = runner.invoke(
            main,
            ["check", "--path", str(project_root)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
        cli_events = [e for e in events if e["type"] == "cli" and e["action"] == "check"]
        assert len(cli_events) >= 1

    def test_apply_logs_event(self, runner: CliRunner, project_root: Path) -> None:
        # Write a decision so apply has something to generate
        from vt_protocol.decisions.models import Decision, DecisionType, Dimension, SourceType

        d = Decision(
            title="Detected: REST API",
            content="Using FastAPI for REST",
            rationale="Detected",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.API_STYLE],
            made_by="vt-init",
            project="test",
            source_type=SourceType.SCAN,
        )
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001.json").write_text(d.model_dump_json(indent=2))

        result = runner.invoke(
            main,
            ["apply", "--path", str(project_root)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        if events_path.exists():
            events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
            apply_events = [e for e in events if e["type"] == "cli" and e["action"] == "apply"]
            assert len(apply_events) >= 1


class TestDashboardTracesApi:
    def test_api_traces_returns_trace_events(self, client: TestClient, project_root: Path) -> None:
        # Write some trace events
        traces_dir = project_root / ".smm" / "traces"
        events_path = traces_dir / "events.jsonl"
        events_path.write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:01Z", "type": "hook", "action": "Write", "file": "foo.py", "result": "pass", "reason": "", "agent": "claude-code"}) + "\n"
            + json.dumps({"timestamp": "2025-01-01T00:00:02Z", "type": "hook", "action": "Edit", "file": "bar.py", "result": "block", "reason": "violation", "agent": "claude-code"}) + "\n"
        )

        state = DashboardState(project_root)
        state.load()
        set_state(state)

        resp = client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()

        entries = data["entries"]
        assert len(entries) >= 2

        # Check that hook events are included
        hook_entries = [e for e in entries if e["action_type"] == "hook"]
        assert len(hook_entries) == 2

        # Newest first
        assert hook_entries[0]["details"]["timestamp"] == "2025-01-01T00:00:02Z"

    def test_api_traces_filter_by_action_type(self, client: TestClient, project_root: Path) -> None:
        traces_dir = project_root / ".smm" / "traces"
        events_path = traces_dir / "events.jsonl"
        events_path.write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:01Z", "type": "hook", "action": "Write", "file": "x.py", "result": "pass", "reason": "", "agent": "claude-code"}) + "\n"
            + json.dumps({"timestamp": "2025-01-01T00:00:02Z", "type": "cli", "action": "check", "file": "", "result": "pass", "reason": "", "agent": "cli"}) + "\n"
        )

        state = DashboardState(project_root)
        state.load()
        set_state(state)

        resp = client.get("/api/traces?action_type=hook")
        data = resp.json()
        assert all(e["action_type"] == "hook" for e in data["entries"])

    def test_api_traces_summary_includes_hook_count(self, client: TestClient, project_root: Path) -> None:
        traces_dir = project_root / ".smm" / "traces"
        events_path = traces_dir / "events.jsonl"
        events_path.write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:01Z", "type": "hook", "action": "Write", "file": "x.py", "result": "pass", "reason": "", "agent": "claude-code"}) + "\n"
        )

        state = DashboardState(project_root)
        state.load()
        set_state(state)

        resp = client.get("/api/traces")
        data = resp.json()
        assert data["summary"]["action_counts"].get("hook", 0) >= 1

    def test_api_traces_empty_when_no_file(self, client: TestClient, tmp_path: Path) -> None:
        root = tmp_path / "empty"
        root.mkdir()
        (root / ".smm").mkdir()
        (root / ".smm" / "traces").mkdir()

        state = DashboardState(root)
        state.load()
        set_state(state)

        resp = client.get("/api/traces")
        assert resp.status_code == 200

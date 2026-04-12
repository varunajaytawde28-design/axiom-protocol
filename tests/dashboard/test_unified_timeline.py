"""Tests for the unified activity timeline in dashboard /api/traces."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.observation.cache import FileEntry, save_snapshot, categorize_path


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_state()


def _make_file_entry(path: str, content_hash: str, size: int = 100) -> FileEntry:
    return FileEntry(path=path, content_hash=content_hash, size=size, category=categorize_path(path))


def _write_observation_data(obs_dir: Path, now: float) -> None:
    """Write all observation data types to .smm/observation/."""
    # Spans (LLM calls)
    spans = [
        {
            "span_id": "span_llm_00001",
            "trace_id": "trace00000000001",
            "agent_id": "claude-backend",
            "model": "claude-haiku-4-5-20251001",
            "provider": "anthropic",
            "input_messages": "[]",
            "output": "response",
            "tokens_in": 100,
            "tokens_out": 50,
            "cost_usd": 0.001,
            "latency_ms": 350.0,
            "status": "success",
            "timestamp": now,
        },
    ]
    (obs_dir / "spans.json").write_text(json.dumps(spans))

    # MCP tool calls
    mcp_calls = [
        {
            "entry_id": "mcp_call_000001",
            "timestamp": now - 10,
            "agent_id": "claude-backend",
            "session_id": "sess-1",
            "action_type": "mcp_tool",
            "tool_name": "read_file",
            "summary": "MCP filesystem: read_file(path=src/main.py)",
            "severity": "info",
            "details": {"category": "filesystem", "arguments": {"path": "src/main.py"}},
            "duration_ms": 5.0,
        },
        {
            "entry_id": "mcp_call_000002",
            "timestamp": now - 5,
            "agent_id": "claude-backend",
            "session_id": "sess-1",
            "action_type": "mcp_tool",
            "tool_name": "write_file",
            "summary": "MCP filesystem: write_file(path=src/new.py)",
            "severity": "info",
            "details": {"category": "filesystem"},
            "duration_ms": 8.0,
        },
    ]
    (obs_dir / "mcp_calls.json").write_text(json.dumps(mcp_calls))

    # Shell executions
    shell_execs = [
        {
            "entry_id": "shell_exec_0001",
            "timestamp": now - 8,
            "agent_id": "claude-backend",
            "session_id": "sess-1",
            "action_type": "shell_command",
            "tool_name": "bash",
            "summary": "$ python -m pytest",
            "severity": "info",
            "details": {"command": "python -m pytest", "exit_code": 0, "dangerous": False, "danger_reasons": []},
            "duration_ms": 5000.0,
        },
        {
            "entry_id": "shell_exec_0002",
            "timestamp": now - 3,
            "agent_id": "claude-backend",
            "session_id": "sess-1",
            "action_type": "shell_command",
            "tool_name": "bash",
            "summary": "[DANGEROUS] $ rm -rf /tmp/data",
            "severity": "critical",
            "details": {"command": "rm -rf /tmp/data", "exit_code": 0, "dangerous": True, "danger_reasons": ["Recursive force delete"]},
            "duration_ms": 50.0,
        },
    ]
    (obs_dir / "shell_executions.json").write_text(json.dumps(shell_execs))

    # Git operations
    git_ops = [
        {
            "entry_id": "git_op_00000001",
            "timestamp": now - 1,
            "agent_id": "claude-backend",
            "session_id": "sess-1",
            "action_type": "git_operation",
            "tool_name": "git_commit",
            "summary": "git commit: Fix login bug (main)",
            "severity": "info",
            "details": {"operation": "commit", "message": "Fix login bug", "branch": "main", "files_changed": ["src/auth.py"]},
            "duration_ms": 0.0,
        },
    ]
    (obs_dir / "git_operations.json").write_text(json.dumps(git_ops))

    # File reads
    file_reads = [
        {
            "entry_id": "file_read_00001",
            "timestamp": now - 15,
            "agent_id": "claude-backend",
            "session_id": "sess-1",
            "action_type": "file_read",
            "tool_name": "read_file",
            "summary": "Read: src/main.py",
            "severity": "info",
            "details": {"file_path": "src/main.py"},
            "duration_ms": 0.0,
        },
        {
            "entry_id": "file_read_00002",
            "timestamp": now - 14,
            "agent_id": "claude-backend",
            "session_id": "sess-1",
            "action_type": "file_read",
            "tool_name": "read_file",
            "summary": "Read: src/utils.py",
            "severity": "info",
            "details": {"file_path": "src/utils.py"},
            "duration_ms": 0.0,
        },
    ]
    (obs_dir / "file_reads.json").write_text(json.dumps(file_reads))


@pytest.fixture
def project_with_all_observations(tmp_path: Path) -> Path:
    """Project with ALL observation data types."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "cache").mkdir(parents=True)
    obs_dir = tmp_path / ".smm" / "observation"
    obs_dir.mkdir(parents=True)

    now = time.time()
    _write_observation_data(obs_dir, now)

    return tmp_path


# ---------------------------------------------------------------------------
# Unified timeline tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traces_returns_all_action_types(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()
        action_types = {e["action_type"] for e in data["entries"]}
        assert "llm_call" in action_types
        assert "mcp_tool" in action_types
        assert "shell_command" in action_types
        assert "git_operation" in action_types
        assert "file_read" in action_types


@pytest.mark.asyncio
async def test_traces_total_count(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        data = resp.json()
        # 1 span + 2 mcp + 2 shell + 1 git + 2 file_reads = 8
        assert data["total"] == 8
        assert len(data["entries"]) == 8


@pytest.mark.asyncio
async def test_traces_sorted_by_timestamp(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        data = resp.json()
        timestamps = [e["timestamp"] for e in data["entries"]]
        assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_traces_filter_by_action_type(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces?action_type=shell_command")
        data = resp.json()
        assert data["total"] == 2
        assert all(e["action_type"] == "shell_command" for e in data["entries"])


@pytest.mark.asyncio
async def test_traces_filter_by_agent_id(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces?agent_id=claude-backend")
        data = resp.json()
        assert data["total"] == 8
        assert all(e["agent_id"] == "claude-backend" for e in data["entries"])


@pytest.mark.asyncio
async def test_traces_filter_by_severity(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces?severity=critical")
        data = resp.json()
        assert data["total"] >= 1
        assert all(e["severity"] == "critical" for e in data["entries"])


@pytest.mark.asyncio
async def test_traces_filter_no_match(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces?agent_id=nonexistent")
        data = resp.json()
        assert data["total"] == 0


@pytest.mark.asyncio
async def test_traces_summary_has_action_counts(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        data = resp.json()
        summary = data["summary"]
        assert "action_counts" in summary
        assert summary["action_counts"]["llm_call"] == 1
        assert summary["action_counts"]["mcp_tool"] == 2
        assert summary["action_counts"]["shell_command"] == 2
        assert summary["action_counts"]["git_operation"] == 1
        assert summary["action_counts"]["file_read"] == 2


@pytest.mark.asyncio
async def test_traces_summary_has_severity_counts(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        data = resp.json()
        severity = data["summary"]["severity_counts"]
        assert severity.get("info", 0) >= 1
        assert severity.get("critical", 0) >= 1


@pytest.mark.asyncio
async def test_traces_pagination(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces?limit=3&offset=0")
        data = resp.json()
        assert len(data["entries"]) == 3
        assert data["total"] == 8

        resp2 = await client.get("/api/traces?limit=3&offset=3")
        data2 = resp2.json()
        assert len(data2["entries"]) == 3


@pytest.mark.asyncio
async def test_traces_empty_project(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm").mkdir()
    state = DashboardState(project_root=tmp_path)
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["entries"] == []


# ---------------------------------------------------------------------------
# Signals includes dangerous shell commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signals_includes_dangerous_commands(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/signals")
        data = resp.json()
        # Should have at least one critical signal from the dangerous rm -rf command
        assert len(data["critical"]) >= 1
        names = [s["name"] for s in data["critical"]]
        assert "dangerous_command" in names


# ---------------------------------------------------------------------------
# DashboardState loading of new data
# ---------------------------------------------------------------------------


def test_load_mcp_calls(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    assert len(state._mcp_calls) == 2


def test_load_shell_executions(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    assert len(state._shell_executions) == 2


def test_load_git_operations(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    assert len(state._git_operations) == 1


def test_load_file_reads(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    assert len(state._file_reads) == 2


def test_activity_timeline_built(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    assert len(state._activity_timeline) == 8


def test_activity_timeline_sorted(project_with_all_observations: Path) -> None:
    state = DashboardState(project_root=project_with_all_observations)
    state.load()
    timestamps = [e["timestamp"] for e in state._activity_timeline]
    assert timestamps == sorted(timestamps, reverse=True)

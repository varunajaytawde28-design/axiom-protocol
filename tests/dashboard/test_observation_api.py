"""Tests for dashboard observation endpoints (Lattice)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.observation.cache import (
    ChangeCategory,
    FileEntry,
    save_snapshot,
)
from vt_protocol.observation.models import CausalEdge, Span


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_state()


def _make_file_entry(path: str, content_hash: str, size: int = 100) -> FileEntry:
    from vt_protocol.observation.cache import categorize_path

    return FileEntry(
        path=path,
        content_hash=content_hash,
        size=size,
        category=categorize_path(path),
    )


@pytest.fixture
def project_with_observation(tmp_path: Path) -> Path:
    """Project with observation data (snapshots, spans, edges, trajectory)."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "audit").mkdir(parents=True)
    (tmp_path / ".smm" / "cache").mkdir(parents=True)
    (tmp_path / ".smm" / "observation").mkdir(parents=True)

    # Write before/after snapshots with changes
    before = {
        "src/main.py": _make_file_entry("src/main.py", "aaa111", 500),
        "src/old.py": _make_file_entry("src/old.py", "bbb222", 200),
        "requirements.txt": _make_file_entry("requirements.txt", "ccc333", 50),
    }
    after = {
        "src/main.py": _make_file_entry("src/main.py", "aaa999", 600),
        "src/new_feature.py": _make_file_entry("src/new_feature.py", "ddd444", 300),
        "requirements.txt": _make_file_entry("requirements.txt", "ccc999", 80),
    }
    save_snapshot(before, tmp_path / ".smm" / "cache" / "snapshot_before.json")
    save_snapshot(after, tmp_path / ".smm" / "cache" / "snapshot_after.json")

    # Write spans
    now = time.time()
    spans = [
        {
            "span_id": "abcd1234abcd1234",
            "trace_id": "trace001trace001",
            "agent_id": "claude-backend",
            "model": "claude-haiku-4-5-20251001",
            "provider": "anthropic",
            "input_messages": "[]",
            "output": "Hello world",
            "tokens_in": 100,
            "tokens_out": 50,
            "cost_usd": 0.001,
            "latency_ms": 450.0,
            "status": "success",
            "timestamp": now,
        },
        {
            "span_id": "efgh5678efgh5678",
            "trace_id": "trace001trace001",
            "agent_id": "claude-backend",
            "model": "gpt-4o",
            "provider": "openai",
            "input_messages": "[]",
            "output": "Response text",
            "tokens_in": 200,
            "tokens_out": 100,
            "cost_usd": 0.005,
            "latency_ms": 800.0,
            "status": "success",
            "timestamp": now - 60,
            "tainted_source": "abcd1234abcd1234",
        },
    ]
    (tmp_path / ".smm" / "observation" / "spans.json").write_text(
        json.dumps(spans, indent=2)
    )

    # Write causal edges
    edges = [
        {
            "source_span_id": "abcd1234abcd1234",
            "target_span_id": "efgh5678efgh5678",
            "edge_type": "taint",
            "confidence": 1.0,
        },
    ]
    (tmp_path / ".smm" / "observation" / "edges.json").write_text(
        json.dumps(edges, indent=2)
    )

    # Write trajectory alerts
    trajectory = [
        {
            "alert_type": "loop_detected",
            "severity": "warning",
            "message": "Loop detected: 'file_edit:src/main.py' repeated 3 times",
            "event_count": 3,
            "metadata": {"action": "file_edit:src/main.py", "count": 3},
        },
    ]
    (tmp_path / ".smm" / "observation" / "trajectory.json").write_text(
        json.dumps(trajectory, indent=2)
    )

    # Write a decision with a secret in content
    d = Decision(
        title="API Integration",
        content="Use the endpoint with key sk-proj-ABCDEFGHIJKLMNOPQRST1234 for auth.",
        rationale="Needed for integration",
        dimensions=[Dimension.API_STYLE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )
    (tmp_path / ".smm" / "decisions" / "001-api.json").write_text(
        d.model_dump_json(indent=2)
    )

    return tmp_path


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    """Project with no observation data."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# GET /api/signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_signals_with_data(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("green", "yellow", "red")
        assert data["snapshot_available"] is True
        assert data["file_changes"]["added"] >= 1
        assert data["file_changes"]["removed"] >= 1
        assert data["file_changes"]["modified"] >= 1


@pytest.mark.asyncio
async def test_signals_empty_project(empty_project: Path) -> None:
    state = DashboardState(project_root=empty_project)
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "green"
        assert data["total"] == 0
        assert data["snapshot_available"] is False


@pytest.mark.asyncio
async def test_signals_severity_grouping(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/signals")
        data = resp.json()
        # All severity groups should be lists
        assert isinstance(data["critical"], list)
        assert isinstance(data["warning"], list)
        assert isinstance(data["info"], list)
        # Total should equal sum of groups
        total = len(data["critical"]) + len(data["warning"]) + len(data["info"])
        assert data["total"] == total


# ---------------------------------------------------------------------------
# GET /api/traces
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traces_with_spans(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["entries"]) == 2
        assert data["summary"]["total_cost_usd"] > 0
        assert data["summary"]["total_tokens_in"] == 300
        assert data["summary"]["total_tokens_out"] == 150
        assert "anthropic" in data["summary"]["providers"]
        assert "openai" in data["summary"]["providers"]


@pytest.mark.asyncio
async def test_traces_pagination(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces?limit=1&offset=0")
        data = resp.json()
        assert len(data["entries"]) == 1
        assert data["total"] == 2

        resp2 = await client.get("/api/traces?limit=1&offset=1")
        data2 = resp2.json()
        assert len(data2["entries"]) == 1
        assert data2["entries"][0]["entry_id"] != data["entries"][0]["entry_id"]


@pytest.mark.asyncio
async def test_traces_entry_fields(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        data = resp.json()
        entry = data["entries"][0]
        # Check unified timeline entry fields
        expected_fields = {
            "entry_id", "timestamp", "agent_id", "action_type",
            "tool_name", "summary", "severity", "details", "duration_ms",
        }
        assert expected_fields <= set(entry.keys())
        assert entry["action_type"] == "llm_call"


@pytest.mark.asyncio
async def test_traces_empty(empty_project: Path) -> None:
    state = DashboardState(project_root=empty_project)
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/traces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["entries"] == []


# ---------------------------------------------------------------------------
# GET /api/provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provenance_graph(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/provenance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_spans"] == 2
        assert data["summary"]["total_edges"] == 1
        assert "taint" in data["summary"]["edge_types"]
        assert len(data["graph"]["nodes"]) == 2
        assert len(data["graph"]["edges"]) == 1


@pytest.mark.asyncio
async def test_provenance_edge_format(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/provenance")
        data = resp.json()
        edge = data["graph"]["edges"][0]["data"]
        assert edge["source"] == "abcd1234abcd1234"
        assert edge["target"] == "efgh5678efgh5678"
        assert edge["type"] == "taint"
        assert edge["confidence"] == 1.0


@pytest.mark.asyncio
async def test_provenance_node_format(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/provenance")
        data = resp.json()
        node = data["graph"]["nodes"][0]["data"]
        assert "id" in node
        assert "model" in node
        assert "provider" in node
        assert "agent_id" in node


@pytest.mark.asyncio
async def test_provenance_empty(empty_project: Path) -> None:
    state = DashboardState(project_root=empty_project)
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/provenance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_spans"] == 0
        assert data["summary"]["total_edges"] == 0
        assert data["graph"]["nodes"] == []
        assert data["graph"]["edges"] == []


# ---------------------------------------------------------------------------
# GET /api/secrets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secrets_detects_key(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/secrets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "alert"
        assert data["total_matches"] >= 1
        assert data["files_scanned"] >= 1
        # Should detect the OpenAI key pattern
        types = [m["secret_type"] for m in data["matches"]]
        assert any("key" in t for t in types)


@pytest.mark.asyncio
async def test_secrets_clean_project(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm" / "decisions").mkdir(parents=True)

    d = Decision(
        title="Clean Decision",
        content="Use PostgreSQL for the primary database.",
        rationale="Best fit",
        dimensions=[Dimension.DATABASE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )
    (tmp_path / ".smm" / "decisions" / "001.json").write_text(
        d.model_dump_json(indent=2)
    )

    state = DashboardState(project_root=tmp_path)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/secrets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "clean"
        assert data["total_matches"] == 0


@pytest.mark.asyncio
async def test_secrets_by_type(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/secrets")
        data = resp.json()
        assert isinstance(data["by_type"], dict)
        total_from_types = sum(data["by_type"].values())
        assert total_from_types == data["total_matches"]


# ---------------------------------------------------------------------------
# GET /api/scope-creep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_creep_with_data(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/scope-creep")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_alerts"] >= 1
        assert len(data["alerts"]) >= 1
        assert data["file_changes_summary"]["total"] >= 1
        assert isinstance(data["file_changes_summary"]["by_category"], dict)


@pytest.mark.asyncio
async def test_scope_creep_empty(empty_project: Path) -> None:
    state = DashboardState(project_root=empty_project)
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/scope-creep")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_alerts"] == 0
        assert data["scope_signal"] is None
        assert data["file_changes_summary"]["total"] == 0


@pytest.mark.asyncio
async def test_scope_creep_alert_format(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/scope-creep")
        data = resp.json()
        alert = data["alerts"][0]
        assert "alert_type" in alert
        assert "severity" in alert
        assert "message" in alert


# ---------------------------------------------------------------------------
# DashboardState observation loading
# ---------------------------------------------------------------------------


def test_load_observation_snapshots(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    assert state._snapshot_diff is not None
    assert state._snapshot_diff.total_changes >= 1


def test_load_observation_spans(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    assert len(state._spans) == 2
    assert state._spans[0].span_id == "abcd1234abcd1234"


def test_load_observation_edges(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    assert len(state._edges) == 1
    assert state._edges[0].edge_type == "taint"


def test_load_observation_trajectory(project_with_observation: Path) -> None:
    state = DashboardState(project_root=project_with_observation)
    state.load()
    assert len(state._trajectory_alerts) == 1
    assert state._trajectory_alerts[0]["alert_type"] == "loop_detected"


def test_load_observation_missing_dirs(empty_project: Path) -> None:
    state = DashboardState(project_root=empty_project)
    state._load_observation()
    assert state._signals == []
    assert state._spans == []
    assert state._edges == []
    assert state._trajectory_alerts == []
    assert state._snapshot_diff is None

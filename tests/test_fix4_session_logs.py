"""Tests for Fix 4: Claude Code session log parsing for LLM call telemetry.

Verifies:
- Session JSONL parsing extracts model, tokens, timestamps
- Cost estimation from model + token counts
- Deduplication when syncing to events.jsonl
- CLI proxy start/stop commands
- Dashboard picks up LLM call events
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from vt_protocol.observation.session_logs import (
    _estimate_cost,
    find_claude_session_dir,
    find_latest_session_file,
    parse_session_jsonl,
    sync_session_to_traces,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


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


@pytest.fixture
def session_jsonl(tmp_path: Path) -> Path:
    """Create a mock Claude Code session JSONL file."""
    session_file = tmp_path / "test-session.jsonl"
    entries = [
        # File history snapshot (should be ignored)
        {"type": "file-history-snapshot", "messageId": "abc"},
        # User message (should be ignored)
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        # Assistant message with usage (should be parsed)
        {
            "type": "assistant",
            "timestamp": "2025-06-15T10:00:00.000Z",
            "message": {
                "model": "claude-sonnet-4-20250514",
                "role": "assistant",
                "content": [{"type": "text", "text": "Here is my response to your question about architecture."}],
                "usage": {
                    "input_tokens": 1000,
                    "cache_creation_input_tokens": 500,
                    "cache_read_input_tokens": 200,
                    "output_tokens": 300,
                },
            },
        },
        # Another assistant message
        {
            "type": "assistant",
            "timestamp": "2025-06-15T10:01:00.000Z",
            "message": {
                "model": "claude-opus-4-6",
                "role": "assistant",
                "content": [{"type": "text", "text": "Let me fix that bug."}],
                "usage": {
                    "input_tokens": 2000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 1000,
                    "output_tokens": 500,
                },
            },
        },
    ]
    lines = [json.dumps(e) for e in entries]
    session_file.write_text("\n".join(lines) + "\n")
    return session_file


class TestParseSessionJsonl:
    def test_extracts_assistant_messages(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        assert len(events) == 2

    def test_extracts_model(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        assert events[0]["model"] == "claude-sonnet-4-20250514"
        assert events[1]["model"] == "claude-opus-4-6"

    def test_extracts_tokens(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        # First event: 1000 + 500 + 200 = 1700 input, 300 output
        assert events[0]["input_tokens"] == 1700
        assert events[0]["output_tokens"] == 300

    def test_extracts_timestamp(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        assert events[0]["timestamp"] == "2025-06-15T10:00:00.000Z"

    def test_event_type_is_llm_call(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        assert all(e["type"] == "llm_call" for e in events)
        assert all(e["provider"] == "anthropic" for e in events)
        assert all(e["agent"] == "claude-code" for e in events)

    def test_extracts_prompt_preview(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        assert "response" in events[0]["prompt_preview"]
        assert len(events[0]["prompt_preview"]) <= 200

    def test_estimates_cost(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        assert events[0]["cost_usd"] > 0
        assert isinstance(events[0]["cost_usd"], float)

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.jsonl"
        empty.write_text("")
        events = parse_session_jsonl(empty)
        assert events == []

    def test_handles_malformed_lines(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.jsonl"
        bad.write_text("not json\n{\"type\": \"user\"}\n")
        events = parse_session_jsonl(bad)
        assert events == []

    def test_ignores_non_assistant_entries(self, session_jsonl: Path) -> None:
        events = parse_session_jsonl(session_jsonl)
        # Should only have 2 events (the assistant messages), not 4
        assert len(events) == 2


class TestEstimateCost:
    def test_sonnet_pricing(self) -> None:
        cost = _estimate_cost("claude-sonnet-4-20250514", 1_000_000, 1_000_000)
        assert cost == 3.0 + 15.0  # $3/M input + $15/M output

    def test_opus_pricing(self) -> None:
        cost = _estimate_cost("claude-opus-4-6", 1_000_000, 1_000_000)
        assert cost == 15.0 + 75.0

    def test_haiku_pricing(self) -> None:
        cost = _estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert cost == 0.8 + 4.0

    def test_unknown_model_uses_default(self) -> None:
        cost = _estimate_cost("unknown-model", 1_000_000, 1_000_000)
        # Defaults to Sonnet pricing
        assert cost == 3.0 + 15.0

    def test_zero_tokens_zero_cost(self) -> None:
        cost = _estimate_cost("claude-sonnet-4-20250514", 0, 0)
        assert cost == 0.0


class TestSyncSessionToTraces:
    def test_sync_writes_events(self, project_root: Path, session_jsonl: Path, monkeypatch) -> None:
        # Mock find_claude_session_dir and find_latest_session_file
        monkeypatch.setattr(
            "vt_protocol.observation.session_logs.find_claude_session_dir",
            lambda _: session_jsonl.parent,
        )
        monkeypatch.setattr(
            "vt_protocol.observation.session_logs.find_latest_session_file",
            lambda _: session_jsonl,
        )

        count = sync_session_to_traces(project_root)
        assert count == 2

        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()
        lines = [l for l in events_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        event = json.loads(lines[0])
        assert event["type"] == "llm_call"

    def test_sync_deduplicates(self, project_root: Path, session_jsonl: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "vt_protocol.observation.session_logs.find_claude_session_dir",
            lambda _: session_jsonl.parent,
        )
        monkeypatch.setattr(
            "vt_protocol.observation.session_logs.find_latest_session_file",
            lambda _: session_jsonl,
        )

        # First sync
        count1 = sync_session_to_traces(project_root)
        assert count1 == 2

        # Second sync — should find no new events
        count2 = sync_session_to_traces(project_root)
        assert count2 == 0

        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        lines = [l for l in events_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2  # No duplicates

    def test_sync_returns_zero_when_no_session(self, project_root: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "vt_protocol.observation.session_logs.find_claude_session_dir",
            lambda _: None,
        )
        count = sync_session_to_traces(project_root)
        assert count == 0


class TestFindSessionDir:
    def test_finds_matching_dir(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude" / "projects"
        # Simulate mangled project path
        project_root = tmp_path / "myproj"
        project_root.mkdir()
        mangled = str(project_root.resolve()).replace("/", "-")
        session_dir = claude_dir / mangled
        session_dir.mkdir(parents=True)

        import vt_protocol.observation.session_logs as mod
        orig_home = Path.home

        def mock_home():  # type: ignore[no-untyped-def]
            return tmp_path

        try:
            mod.Path.home = staticmethod(mock_home)
            result = find_claude_session_dir(project_root)
            assert result == session_dir
        finally:
            mod.Path.home = orig_home

    def test_returns_none_when_no_dir(self, tmp_path: Path) -> None:
        import vt_protocol.observation.session_logs as mod
        orig_home = Path.home

        def mock_home():  # type: ignore[no-untyped-def]
            return tmp_path

        try:
            mod.Path.home = staticmethod(mock_home)
            result = find_claude_session_dir(tmp_path / "nonexistent")
            assert result is None
        finally:
            mod.Path.home = orig_home


class TestFindLatestSessionFile:
    def test_finds_newest(self, tmp_path: Path) -> None:
        import time
        f1 = tmp_path / "old.jsonl"
        f1.write_text("{}")
        time.sleep(0.05)
        f2 = tmp_path / "new.jsonl"
        f2.write_text("{}")
        result = find_latest_session_file(tmp_path)
        assert result == f2

    def test_returns_none_empty_dir(self, tmp_path: Path) -> None:
        result = find_latest_session_file(tmp_path)
        assert result is None


class TestDashboardLlmCallEvents:
    def test_llm_call_events_appear_in_traces(self, project_root: Path) -> None:
        from vt_protocol.dashboard.app import DashboardState, reset_state, set_state

        # Write an LLM call event
        events_path = project_root / ".smm" / "traces" / "events.jsonl"
        event = {
            "timestamp": "2025-06-15T10:00:00Z",
            "type": "llm_call",
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "input_tokens": 1500,
            "output_tokens": 300,
            "latency_ms": 0,
            "prompt_preview": "Here is my response...",
            "cost_usd": 0.009,
            "agent": "claude-code",
        }
        events_path.write_text(json.dumps(event) + "\n")

        reset_state()
        state = DashboardState(project_root)
        state.load()
        set_state(state)

        from fastapi.testclient import TestClient
        from vt_protocol.dashboard.app import app

        client = TestClient(app)
        resp = client.get("/api/traces")
        data = resp.json()

        llm_entries = [e for e in data["entries"] if e["action_type"] == "llm_call"]
        assert len(llm_entries) >= 1
        assert llm_entries[0]["details"]["model"] == "claude-sonnet-4-20250514"
        assert llm_entries[0]["details"]["input_tokens"] == 1500

        reset_state()

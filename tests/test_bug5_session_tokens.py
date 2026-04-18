"""Tests for Bug 5: Session summary shows correct token counts and cost.

Verifies:
- /api/traces aggregates tokens from _trace_events (llm_call type)
- /api/traces aggregates tokens from _spans
- _estimate_llm_cost() returns correct values for sonnet/opus/haiku
- total_tokens_in and total_tokens_out include trace event tokens
- total_cost_usd includes cost from trace events
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vt_protocol.dashboard.app import (
    DashboardState,
    _estimate_llm_cost,
    app,
    reset_state,
    set_state,
)


class TestEstimateLlmCost:
    def test_sonnet_pricing(self) -> None:
        # $3/M input, $15/M output
        cost = _estimate_llm_cost("claude-sonnet-4-6", 1_000_000, 0)
        assert abs(cost - 3.0) < 0.001

        cost = _estimate_llm_cost("claude-sonnet-4-6", 0, 1_000_000)
        assert abs(cost - 15.0) < 0.001

    def test_opus_pricing(self) -> None:
        # $15/M input, $75/M output
        cost = _estimate_llm_cost("claude-opus-4-6", 1_000_000, 0)
        assert abs(cost - 15.0) < 0.001

        cost = _estimate_llm_cost("claude-opus-4-6", 0, 1_000_000)
        assert abs(cost - 75.0) < 0.001

    def test_haiku_pricing(self) -> None:
        # $0.80/M input, $4/M output
        cost = _estimate_llm_cost("claude-haiku-4-5-20251001", 1_000_000, 0)
        assert abs(cost - 0.80) < 0.001

        cost = _estimate_llm_cost("claude-haiku-4-5-20251001", 0, 1_000_000)
        assert abs(cost - 4.0) < 0.001

    def test_default_falls_back_to_sonnet(self) -> None:
        cost = _estimate_llm_cost("unknown-model", 1_000_000, 0)
        assert abs(cost - 3.0) < 0.001

    def test_zero_tokens_is_zero_cost(self) -> None:
        assert _estimate_llm_cost("claude-sonnet-4-6", 0, 0) == 0.0

    def test_mixed_tokens(self) -> None:
        # 44144 in, 145 out with sonnet: (44144 * 3 + 145 * 15) / 1_000_000
        expected = (44144 * 3 + 145 * 15) / 1_000_000
        cost = _estimate_llm_cost("claude-sonnet-4-6", 44_144, 145)
        assert abs(cost - expected) < 0.000001


@pytest.fixture()
def state_with_trace_events(tmp_path: Path):
    """DashboardState with llm_call trace events written to disk."""
    import json

    traces_dir = tmp_path / ".smm" / "traces"
    traces_dir.mkdir(parents=True)

    events = [
        {
            "timestamp": "2026-04-14T10:00:00Z",
            "type": "llm_call",
            "model": "claude-sonnet-4-6",
            "input_tokens": 44144,
            "output_tokens": 145,
            "cost_usd": (44144 * 3 + 145 * 15) / 1_000_000,
            "agent": "claude-code",
        },
        {
            "timestamp": "2026-04-14T10:01:00Z",
            "type": "llm_call",
            "model": "claude-sonnet-4-6",
            "input_tokens": 1000,
            "output_tokens": 50,
            "cost_usd": (1000 * 3 + 50 * 15) / 1_000_000,
            "agent": "claude-code",
        },
        # hook event — should NOT be counted in tokens
        {
            "timestamp": "2026-04-14T10:02:00Z",
            "type": "hook",
            "action": "Write",
            "file": "foo.py",
            "result": "pass",
            "reason": "",
            "agent": "claude-code",
        },
    ]
    # Write to disk so _load_trace_events() picks them up inside api_traces
    (traces_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )

    ds = DashboardState(project_root=tmp_path)
    ds._spans = []  # no spans — only trace events
    set_state(ds)
    yield ds
    reset_state()


class TestApiTracesAggregatesTokens:
    def test_total_tokens_in_includes_trace_events(
        self, tmp_path: Path, state_with_trace_events: DashboardState
    ) -> None:
        client = TestClient(app)
        resp = client.get("/api/traces").json()
        summary = resp["summary"]

        # 44144 + 1000 = 45144
        assert summary["total_tokens_in"] == 45144

    def test_total_tokens_out_includes_trace_events(
        self, tmp_path: Path, state_with_trace_events: DashboardState
    ) -> None:
        client = TestClient(app)
        resp = client.get("/api/traces").json()
        summary = resp["summary"]

        # 145 + 50 = 195
        assert summary["total_tokens_out"] == 195

    def test_total_cost_usd_not_zero(
        self, tmp_path: Path, state_with_trace_events: DashboardState
    ) -> None:
        client = TestClient(app)
        resp = client.get("/api/traces").json()
        summary = resp["summary"]

        assert summary["total_cost_usd"] > 0.0

    def test_hook_events_not_counted_in_tokens(
        self, tmp_path: Path, state_with_trace_events: DashboardState
    ) -> None:
        """Hook events must not be added to token totals."""
        client = TestClient(app)
        resp = client.get("/api/traces").json()
        summary = resp["summary"]

        # Still only 44144 + 1000 — the hook event contributes 0 tokens
        assert summary["total_tokens_in"] == 45144

    def test_zero_tokens_when_no_llm_events(self, tmp_path: Path) -> None:
        """When there are no llm_call events, totals must be 0."""
        import json

        traces_dir = tmp_path / ".smm" / "traces"
        traces_dir.mkdir(parents=True)
        hook_event = {
            "type": "hook", "action": "Write", "file": "x.py",
            "result": "pass", "reason": "", "agent": "claude-code",
            "timestamp": "2026-04-14T10:00:00Z",
        }
        (traces_dir / "events.jsonl").write_text(json.dumps(hook_event) + "\n")

        ds = DashboardState(project_root=tmp_path)
        ds._spans = []
        set_state(ds)

        try:
            client = TestClient(app)
            resp = client.get("/api/traces").json()
            assert resp["summary"]["total_tokens_in"] == 0
            assert resp["summary"]["total_tokens_out"] == 0
            assert resp["summary"]["total_cost_usd"] == 0.0
        finally:
            reset_state()

    def test_uses_precomputed_cost_from_trace_event(
        self, tmp_path: Path
    ) -> None:
        """If cost_usd is already in the trace event, use it directly."""
        import json as _json

        traces_dir = tmp_path / ".smm" / "traces"
        traces_dir.mkdir(parents=True)

        event = {
            "type": "llm_call",
            "model": "claude-opus-4-6",
            "input_tokens": 1000,
            "output_tokens": 100,
            "cost_usd": 0.123456,  # pre-computed
            "agent": "claude-code",
            "timestamp": "2026-04-14T10:00:00Z",
        }
        (traces_dir / "events.jsonl").write_text(_json.dumps(event) + "\n")

        ds = DashboardState(project_root=tmp_path)
        ds._spans = []
        set_state(ds)

        try:
            client = TestClient(app)
            resp = client.get("/api/traces").json()
            assert abs(resp["summary"]["total_cost_usd"] - 0.123456) < 0.000001
        finally:
            reset_state()

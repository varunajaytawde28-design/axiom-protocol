"""Tests for seven golden signals detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.observation.cache import ChangeCategory, FileEntry, SnapshotDiff
from vt_protocol.observation.models import Span
from vt_protocol.observation.signals import (
    LLMCallTracker,
    Signal,
    detect_dependency_changes,
    detect_file_changes,
    detect_intent_drift,
    detect_pattern_violations,
    detect_scope_creep,
    score_config_sensitivity,
)


def _make_snapshot(files: dict[str, str]) -> dict[str, FileEntry]:
    return {
        path: FileEntry(path=path, content_hash=hash_, size=100, category=ChangeCategory.SOURCE)
        for path, hash_ in files.items()
    }


def _make_diff(
    added: list[str] | None = None,
    removed: list[str] | None = None,
    modified: list[str] | None = None,
) -> SnapshotDiff:
    from vt_protocol.observation.cache import categorize_path

    return SnapshotDiff(
        added=[
            FileEntry(path=f, content_hash="hash", size=100, category=categorize_path(f))
            for f in (added or [])
        ],
        removed=[
            FileEntry(path=f, content_hash="hash", size=100, category=categorize_path(f))
            for f in (removed or [])
        ],
        modified=[
            (
                FileEntry(path=f, content_hash="old", size=100, category=categorize_path(f)),
                FileEntry(path=f, content_hash="new", size=100, category=categorize_path(f)),
            )
            for f in (modified or [])
        ],
    )


class TestFileChanges:
    def test_no_changes(self) -> None:
        snap = _make_snapshot({"a.py": "abc"})
        signals = detect_file_changes(snap, snap)
        assert signals == []

    def test_large_batch(self) -> None:
        before = _make_snapshot({f"f{i}.py": f"h{i}" for i in range(60)})
        after = _make_snapshot({f"f{i}.py": f"h{i}_mod" for i in range(60)})
        signals = detect_file_changes(before, after)
        assert any(s.name == "file_changes" for s in signals)


class TestDependencyChanges:
    def test_no_deps(self) -> None:
        diff = _make_diff(added=["src/main.py"])
        signals = detect_dependency_changes(diff)
        assert signals == []

    def test_dep_added(self) -> None:
        diff = _make_diff(added=["requirements.txt"])
        signals = detect_dependency_changes(diff)
        assert any(s.name == "dependency_added" for s in signals)

    def test_dep_modified(self) -> None:
        diff = _make_diff(modified=["pyproject.toml"])
        signals = detect_dependency_changes(diff)
        assert any(s.name == "dependency_modified" for s in signals)

    def test_dep_removed(self) -> None:
        diff = _make_diff(removed=["package.json"])
        signals = detect_dependency_changes(diff)
        assert any(s.name == "dependency_removed" for s in signals)


class TestConfigSensitivity:
    def test_critical_pattern(self) -> None:
        signal = score_config_sensitivity(".env", "DATABASE_URL=postgres://...")
        assert signal is not None
        assert signal.severity == "critical"

    def test_warning_pattern(self) -> None:
        signal = score_config_sensitivity("config.py", "DEBUG = True")
        assert signal is not None
        assert signal.severity == "warning"

    def test_info_for_generic(self) -> None:
        signal = score_config_sensitivity("config.yaml", "theme: dark")
        assert signal is not None
        assert signal.severity == "info"


class TestScopeCreep:
    def test_no_creep(self) -> None:
        signal = detect_scope_creep(
            "fix database connection",
            ["src/db/connection.py", "src/db/pool.py"],
        )
        assert signal is None

    def test_creep_detected(self) -> None:
        signal = detect_scope_creep(
            "fix database connection",
            ["src/frontend/styles.css", "src/frontend/app.tsx"],
        )
        assert signal is not None
        assert signal.name == "scope_creep"

    def test_empty_inputs(self) -> None:
        assert detect_scope_creep("", ["file.py"]) is None
        assert detect_scope_creep("task", []) is None


class TestIntentDrift:
    def test_no_drift(self) -> None:
        signal = detect_intent_drift(
            "refactor database layer",
            ["edited database module", "updated database tests"],
        )
        assert signal is None

    def test_drift_detected(self) -> None:
        signal = detect_intent_drift(
            "refactor database layer",
            ["deployed frontend", "updated CSS styles"],
        )
        assert signal is not None
        assert signal.name == "intent_drift"

    def test_empty(self) -> None:
        assert detect_intent_drift("", []) is None


class TestPatternViolations:
    def test_test_removed(self) -> None:
        before = {"functions": ["test_add", "test_remove", "helper"]}
        after = {"functions": ["helper"]}
        signals = detect_pattern_violations(before, after)
        assert any(s.name == "test_removed" for s in signals)

    def test_public_api_removed(self) -> None:
        before = {"functions": ["get_data", "process"]}
        after = {"functions": []}
        signals = detect_pattern_violations(before, after)
        assert any(s.name == "public_api_removed" for s in signals)

    def test_no_violations(self) -> None:
        before = {"functions": ["a", "b"]}
        after = {"functions": ["a", "b", "c"]}
        signals = detect_pattern_violations(before, after)
        assert signals == []


class TestLLMCallTracker:
    def _make_span(self, cost: float = 0.001, latency: float = 200.0, model: str = "claude-haiku") -> Span:
        import time
        return Span(
            span_id="test",
            trace_id="trace",
            timestamp=time.time(),
            model=model,
            provider="anthropic",
            input_messages="[]",
            output="text",
            tokens_in=100,
            tokens_out=50,
            cost_usd=cost,
            latency_ms=latency,
        )

    def test_no_anomaly_initially(self) -> None:
        tracker = LLMCallTracker()
        for _ in range(3):
            signals = tracker.record(self._make_span())
            assert signals == []

    def test_cost_spike(self) -> None:
        tracker = LLMCallTracker()
        # Build baseline
        for _ in range(5):
            tracker.record(self._make_span(cost=0.001))
        # Spike
        signals = tracker.record(self._make_span(cost=0.1))
        assert any(s.name == "cost_spike" for s in signals)

    def test_latency_spike(self) -> None:
        tracker = LLMCallTracker()
        for _ in range(5):
            tracker.record(self._make_span(latency=100))
        signals = tracker.record(self._make_span(latency=1000))
        assert any(s.name == "latency_spike" for s in signals)

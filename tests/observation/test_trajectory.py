"""Tests for agent trajectory monitors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.observation.trajectory import (
    AlertSeverity,
    AlertType,
    TrajectoryAlert,
    TrajectoryEvent,
    TrajectoryMonitor,
    detect_scope_drift,
)


# ---------------------------------------------------------------------------
# TrajectoryEvent
# ---------------------------------------------------------------------------


class TestTrajectoryEvent:
    def test_signature_with_target(self) -> None:
        e = TrajectoryEvent(action="file_edit", target="src/main.py")
        assert e.signature == "file_edit:src/main.py"

    def test_signature_without_target(self) -> None:
        e = TrajectoryEvent(action="test_run")
        assert e.signature == "test_run"

    def test_default_timestamp(self) -> None:
        e = TrajectoryEvent(action="test")
        assert e.timestamp is not None


# ---------------------------------------------------------------------------
# TrajectoryAlert
# ---------------------------------------------------------------------------


class TestTrajectoryAlert:
    def test_to_dict(self) -> None:
        a = TrajectoryAlert(
            alert_type=AlertType.LOOP_DETECTED,
            severity=AlertSeverity.WARNING,
            message="Loop detected",
            events=[TrajectoryEvent(action="test")] * 3,
            metadata={"count": 3},
        )
        d = a.to_dict()
        assert d["alert_type"] == "loop_detected"
        assert d["severity"] == "warning"
        assert d["event_count"] == 3


# ---------------------------------------------------------------------------
# TrajectoryMonitor — loop detection
# ---------------------------------------------------------------------------


class TestLoopDetection:
    def test_no_loop_for_few_events(self) -> None:
        m = TrajectoryMonitor()
        alerts = m.record(TrajectoryEvent(action="edit"))
        assert len(alerts) == 0

    def test_detects_simple_loop(self) -> None:
        m = TrajectoryMonitor(loop_threshold=3)
        alerts = []
        for _ in range(3):
            alerts = m.record(TrajectoryEvent(action="edit", target="file.py"))
        assert any(a.alert_type == AlertType.LOOP_DETECTED for a in alerts)

    def test_no_loop_for_varied_actions(self) -> None:
        m = TrajectoryMonitor(loop_threshold=3)
        m.record(TrajectoryEvent(action="edit"))
        m.record(TrajectoryEvent(action="test"))
        alerts = m.record(TrajectoryEvent(action="commit"))
        assert not any(a.alert_type == AlertType.LOOP_DETECTED for a in alerts)

    def test_loop_message_includes_action(self) -> None:
        m = TrajectoryMonitor(loop_threshold=3)
        for _ in range(3):
            alerts = m.record(TrajectoryEvent(action="llm_call", target="check"))
        loop_alerts = [a for a in alerts if a.alert_type == AlertType.LOOP_DETECTED]
        assert len(loop_alerts) == 1
        assert "llm_call:check" in loop_alerts[0].message

    def test_loop_window_respected(self) -> None:
        m = TrajectoryMonitor(loop_window=5, loop_threshold=3)
        # Fill with varied events
        for i in range(10):
            m.record(TrajectoryEvent(action=f"action_{i}"))
        # Now add repeated events
        alerts = []
        for _ in range(3):
            alerts = m.record(TrajectoryEvent(action="repeated"))
        assert any(a.alert_type == AlertType.LOOP_DETECTED for a in alerts)


# ---------------------------------------------------------------------------
# TrajectoryMonitor — thrashing detection
# ---------------------------------------------------------------------------


class TestThrashDetection:
    def test_detects_thrashing(self) -> None:
        m = TrajectoryMonitor(thrash_threshold=4)
        actions = ["A", "B"] * 4  # A→B→A→B→A→B→A→B
        alerts = []
        for a in actions:
            alerts = m.record(TrajectoryEvent(action=a))

        thrash_alerts = [a for a in m.alerts if a.alert_type == AlertType.THRASH_DETECTED]
        assert len(thrash_alerts) > 0

    def test_no_thrash_for_sequential(self) -> None:
        m = TrajectoryMonitor(thrash_threshold=4)
        for a in ["A", "B", "C", "D", "E"]:
            m.record(TrajectoryEvent(action=a))
        assert not any(a.alert_type == AlertType.THRASH_DETECTED for a in m.alerts)

    def test_thrash_message(self) -> None:
        m = TrajectoryMonitor(thrash_threshold=4)
        for a in ["edit", "test"] * 4:
            m.record(TrajectoryEvent(action=a))

        thrash_alerts = [a for a in m.alerts if a.alert_type == AlertType.THRASH_DETECTED]
        if thrash_alerts:
            assert "oscillating" in thrash_alerts[0].message


# ---------------------------------------------------------------------------
# TrajectoryMonitor — stall detection
# ---------------------------------------------------------------------------


class TestStallDetection:
    def test_detects_stall(self) -> None:
        m = TrajectoryMonitor(stall_timeout=60)
        t1 = datetime.now(timezone.utc)
        t2 = t1 + timedelta(seconds=120)

        m.record(TrajectoryEvent(action="start", timestamp=t1))
        alerts = m.record(TrajectoryEvent(action="resume", timestamp=t2))
        assert any(a.alert_type == AlertType.STALL_DETECTED for a in alerts)

    def test_no_stall_for_quick_events(self) -> None:
        m = TrajectoryMonitor(stall_timeout=60)
        t1 = datetime.now(timezone.utc)
        t2 = t1 + timedelta(seconds=5)

        m.record(TrajectoryEvent(action="start", timestamp=t1))
        alerts = m.record(TrajectoryEvent(action="next", timestamp=t2))
        assert not any(a.alert_type == AlertType.STALL_DETECTED for a in alerts)

    def test_stall_gap_in_metadata(self) -> None:
        m = TrajectoryMonitor(stall_timeout=10)
        t1 = datetime.now(timezone.utc)
        t2 = t1 + timedelta(seconds=30)

        m.record(TrajectoryEvent(action="a", timestamp=t1))
        alerts = m.record(TrajectoryEvent(action="b", timestamp=t2))
        stall_alerts = [a for a in alerts if a.alert_type == AlertType.STALL_DETECTED]
        assert stall_alerts[0].metadata["gap_seconds"] == pytest.approx(30, abs=1)


# ---------------------------------------------------------------------------
# TrajectoryMonitor — general
# ---------------------------------------------------------------------------


class TestTrajectoryMonitorGeneral:
    def test_event_count(self) -> None:
        m = TrajectoryMonitor()
        m.record(TrajectoryEvent(action="a"))
        m.record(TrajectoryEvent(action="b"))
        assert m.event_count == 2

    def test_events_immutable(self) -> None:
        m = TrajectoryMonitor()
        m.record(TrajectoryEvent(action="a"))
        events = m.events
        events.clear()  # Shouldn't affect internal state
        assert m.event_count == 1

    def test_reset(self) -> None:
        m = TrajectoryMonitor(loop_threshold=2)
        m.record(TrajectoryEvent(action="a"))
        m.record(TrajectoryEvent(action="a"))
        assert m.event_count > 0
        m.reset()
        assert m.event_count == 0
        assert len(m.alerts) == 0

    def test_summary(self) -> None:
        m = TrajectoryMonitor()
        m.record(TrajectoryEvent(action="edit"))
        m.record(TrajectoryEvent(action="edit"))
        m.record(TrajectoryEvent(action="test"))
        s = m.summary()
        assert s["total_events"] == 3
        assert s["action_counts"]["edit"] == 2
        assert s["action_counts"]["test"] == 1


# ---------------------------------------------------------------------------
# detect_scope_drift
# ---------------------------------------------------------------------------


class TestScopeDrift:
    def test_no_drift(self) -> None:
        events = [
            TrajectoryEvent(action="edit", target="src/main.py"),
            TrajectoryEvent(action="edit", target="src/utils.py"),
        ]
        result = detect_scope_drift(events, {"src/main.py", "src/utils.py"})
        assert result is None

    def test_detects_drift(self) -> None:
        events = [
            TrajectoryEvent(action="edit", target="src/main.py"),
            TrajectoryEvent(action="edit", target="docs/readme.md"),
            TrajectoryEvent(action="edit", target="tests/unrelated.py"),
        ]
        result = detect_scope_drift(
            events, {"src/main.py"},
            drift_threshold=0.5,
        )
        assert result is not None
        assert result.alert_type == AlertType.SCOPE_DRIFT

    def test_no_drift_empty_events(self) -> None:
        result = detect_scope_drift([], {"src/main.py"})
        assert result is None

    def test_no_drift_empty_targets(self) -> None:
        events = [TrajectoryEvent(action="edit", target="file.py")]
        result = detect_scope_drift(events, set())
        assert result is None

    def test_drift_metadata(self) -> None:
        events = [
            TrajectoryEvent(action="edit", target="unexpected/file.py"),
        ]
        result = detect_scope_drift(
            events, {"src/main.py"},
            drift_threshold=0.0,
        )
        assert result is not None
        assert "unexpected/file.py" in result.metadata["out_of_scope"]

    def test_events_without_target_ignored(self) -> None:
        events = [
            TrajectoryEvent(action="test"),  # No target
        ]
        result = detect_scope_drift(events, {"src/main.py"})
        assert result is None  # No targets to compare

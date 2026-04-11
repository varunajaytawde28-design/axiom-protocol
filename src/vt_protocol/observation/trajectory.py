"""Agent Trajectory Monitors — sequence analysis, loop detection, LTL-lite.

Observes agent behavior patterns to detect:
  1. Infinite loops (repeating the same actions)
  2. Scope creep (drifting from the original task)
  3. Thrashing (oscillating between states)
  4. Stalls (no progress for extended periods)

Uses a lightweight state machine approach inspired by Linear Temporal
Logic (LTL) to define and check trajectory properties.

Each agent action is recorded as a TrajectoryEvent. The monitor
analyzes the event sequence in real-time and raises alerts when
patterns match known anti-patterns.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Default thresholds
LOOP_DETECTION_WINDOW = 10  # Check last N events for loops
LOOP_REPEAT_THRESHOLD = 3  # Same action N times = loop
THRASH_DETECTION_WINDOW = 20  # Check for oscillation in last N events
THRASH_PAIR_THRESHOLD = 4  # Same A→B→A pair N times = thrashing
STALL_TIMEOUT_SECONDS = 300  # 5 minutes without progress = stall


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    LOOP_DETECTED = "loop_detected"
    THRASH_DETECTED = "thrash_detected"
    STALL_DETECTED = "stall_detected"
    SCOPE_DRIFT = "scope_drift"


@dataclass
class TrajectoryEvent:
    """A single event in an agent's trajectory."""

    action: str  # e.g. "file_edit", "llm_call", "test_run"
    target: str = ""  # e.g. file path, function name
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        """Action + target combo for pattern matching."""
        return f"{self.action}:{self.target}" if self.target else self.action


@dataclass
class TrajectoryAlert:
    """An alert raised by the trajectory monitor."""

    alert_type: AlertType
    severity: AlertSeverity
    message: str
    events: list[TrajectoryEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "event_count": len(self.events),
            "metadata": self.metadata,
        }


class TrajectoryMonitor:
    """Real-time agent trajectory monitor.

    Records events and checks for anti-patterns after each event.
    """

    def __init__(
        self,
        *,
        loop_window: int = LOOP_DETECTION_WINDOW,
        loop_threshold: int = LOOP_REPEAT_THRESHOLD,
        thrash_window: int = THRASH_DETECTION_WINDOW,
        thrash_threshold: int = THRASH_PAIR_THRESHOLD,
        stall_timeout: float = STALL_TIMEOUT_SECONDS,
    ) -> None:
        self._events: list[TrajectoryEvent] = []
        self._alerts: list[TrajectoryAlert] = []
        self._loop_window = loop_window
        self._loop_threshold = loop_threshold
        self._thrash_window = thrash_window
        self._thrash_threshold = thrash_threshold
        self._stall_timeout = stall_timeout

    @property
    def events(self) -> list[TrajectoryEvent]:
        return list(self._events)

    @property
    def alerts(self) -> list[TrajectoryAlert]:
        return list(self._alerts)

    @property
    def event_count(self) -> int:
        return len(self._events)

    def record(self, event: TrajectoryEvent) -> list[TrajectoryAlert]:
        """Record an event and check for anti-patterns.

        Returns any new alerts raised by this event.
        """
        self._events.append(event)
        new_alerts: list[TrajectoryAlert] = []

        # Check all detectors
        loop_alert = self._check_loop()
        if loop_alert:
            new_alerts.append(loop_alert)

        thrash_alert = self._check_thrashing()
        if thrash_alert:
            new_alerts.append(thrash_alert)

        stall_alert = self._check_stall()
        if stall_alert:
            new_alerts.append(stall_alert)

        self._alerts.extend(new_alerts)
        return new_alerts

    def _check_loop(self) -> TrajectoryAlert | None:
        """Detect repeated identical actions within the window."""
        if len(self._events) < self._loop_threshold:
            return None

        window = self._events[-self._loop_window:]
        sigs = [e.signature for e in window]
        counts = Counter(sigs)

        for sig, count in counts.most_common(1):
            if count >= self._loop_threshold:
                loop_events = [e for e in window if e.signature == sig]
                return TrajectoryAlert(
                    alert_type=AlertType.LOOP_DETECTED,
                    severity=AlertSeverity.WARNING,
                    message=f"Loop detected: '{sig}' repeated {count} times in last {len(window)} events",
                    events=loop_events,
                    metadata={"action": sig, "count": count},
                )

        return None

    def _check_thrashing(self) -> TrajectoryAlert | None:
        """Detect oscillation between two actions (A→B→A→B pattern)."""
        if len(self._events) < 4:
            return None

        window = self._events[-self._thrash_window:]
        sigs = [e.signature for e in window]

        # Count consecutive pairs
        pair_counts: Counter[str] = Counter()
        for i in range(len(sigs) - 1):
            pair = f"{sigs[i]}→{sigs[i + 1]}"
            pair_counts[pair] += 1

        # Check for A→B, B→A pairs both appearing frequently
        for pair, count in pair_counts.items():
            parts = pair.split("→")
            if len(parts) == 2:
                reverse = f"{parts[1]}→{parts[0]}"
                reverse_count = pair_counts.get(reverse, 0)
                if count >= self._thrash_threshold // 2 and reverse_count >= self._thrash_threshold // 2:
                    return TrajectoryAlert(
                        alert_type=AlertType.THRASH_DETECTED,
                        severity=AlertSeverity.WARNING,
                        message=(
                            f"Thrashing detected: '{parts[0]}' ↔ '{parts[1]}' "
                            f"oscillating ({count}+{reverse_count} transitions)"
                        ),
                        events=window,
                        metadata={
                            "pair": [parts[0], parts[1]],
                            "forward_count": count,
                            "reverse_count": reverse_count,
                        },
                    )

        return None

    def _check_stall(self) -> TrajectoryAlert | None:
        """Detect periods of no meaningful progress."""
        if len(self._events) < 2:
            return None

        last = self._events[-1]
        prev = self._events[-2]

        gap = (last.timestamp - prev.timestamp).total_seconds()
        if gap > self._stall_timeout:
            return TrajectoryAlert(
                alert_type=AlertType.STALL_DETECTED,
                severity=AlertSeverity.INFO,
                message=f"Stall detected: {gap:.0f}s gap between events",
                events=[prev, last],
                metadata={"gap_seconds": gap},
            )

        return None

    def reset(self) -> None:
        """Reset all events and alerts."""
        self._events.clear()
        self._alerts.clear()

    def summary(self) -> dict[str, Any]:
        """Return a summary of the trajectory."""
        action_counts = Counter(e.action for e in self._events)
        return {
            "total_events": len(self._events),
            "total_alerts": len(self._alerts),
            "action_counts": dict(action_counts),
            "alert_types": Counter(a.alert_type.value for a in self._alerts),
        }


def detect_scope_drift(
    events: list[TrajectoryEvent],
    original_targets: set[str],
    *,
    drift_threshold: float = 0.5,
) -> TrajectoryAlert | None:
    """Detect when agent targets drift from the original task scope.

    Compares the set of targets in recent events against the expected
    targets. If more than drift_threshold of recent targets are outside
    the original scope, raise an alert.
    """
    if not events or not original_targets:
        return None

    recent_targets = {e.target for e in events if e.target}
    if not recent_targets:
        return None

    out_of_scope = recent_targets - original_targets
    drift_ratio = len(out_of_scope) / len(recent_targets)

    if drift_ratio > drift_threshold:
        return TrajectoryAlert(
            alert_type=AlertType.SCOPE_DRIFT,
            severity=AlertSeverity.WARNING,
            message=(
                f"Scope drift: {len(out_of_scope)}/{len(recent_targets)} "
                f"targets ({drift_ratio:.0%}) outside original scope"
            ),
            metadata={
                "out_of_scope": sorted(out_of_scope),
                "drift_ratio": drift_ratio,
            },
        )

    return None

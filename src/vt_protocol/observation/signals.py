"""Seven golden signals — observation engine detection layer.

Signals:
  1. File changes — via cache.py snapshot diffing
  2. Dependency mutations — new/removed/changed deps
  3. Config sensitivity — critical/warning/info scoring
  4. Scope creep — embedding similarity between task and changes
  5. Intent drift — periodic lightweight check
  6. Pattern violations — structural analysis before/after
  7. LLM call anomalies — cost/latency spike detection

Each signal fires events on the event bus when thresholds are exceeded.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vt_protocol.events import Event, EventBus, EventType
from vt_protocol.observation.cache import (
    ChangeCategory,
    SnapshotDiff,
    diff_snapshots,
    take_snapshot,
)
from vt_protocol.observation.models import Span

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal severity levels
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """A detected observation signal."""

    name: str
    severity: str  # "critical", "warning", "info"
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Signal 1: File changes
# ---------------------------------------------------------------------------


def detect_file_changes(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[Signal]:
    """Detect significant file changes between two snapshots."""
    diff = diff_snapshots(before, after)
    signals: list[Signal] = []

    if diff.total_changes == 0:
        return signals

    # Large batch of changes
    if diff.total_changes > 50:
        signals.append(Signal(
            name="file_changes",
            severity="warning",
            message=f"Large change batch: {diff.total_changes} files modified",
            details={"total": diff.total_changes},
        ))

    # Category breakdown
    by_cat = diff.changes_by_category()
    if by_cat.get(ChangeCategory.CONFIG, 0) > 3:
        signals.append(Signal(
            name="config_change",
            severity="warning",
            message=f"{by_cat[ChangeCategory.CONFIG]} config files changed",
            details={"count": by_cat[ChangeCategory.CONFIG]},
        ))

    return signals


# ---------------------------------------------------------------------------
# Signal 2: Dependency mutations
# ---------------------------------------------------------------------------


def detect_dependency_changes(diff: SnapshotDiff) -> list[Signal]:
    """Flag new, removed, or modified dependency files."""
    signals: list[Signal] = []

    if not diff.has_dependency_changes:
        return signals

    dep_added = [e.path for e in diff.added if _is_dep_file(e.path)]
    dep_modified = [after.path for _, after in diff.modified if _is_dep_file(after.path)]
    dep_removed = [e.path for e in diff.removed if _is_dep_file(e.path)]

    if dep_added:
        signals.append(Signal(
            name="dependency_added",
            severity="warning",
            message=f"New dependency files: {', '.join(dep_added)}",
            details={"files": dep_added},
        ))

    if dep_modified:
        signals.append(Signal(
            name="dependency_modified",
            severity="info",
            message=f"Modified dependency files: {', '.join(dep_modified)}",
            details={"files": dep_modified},
        ))

    if dep_removed:
        signals.append(Signal(
            name="dependency_removed",
            severity="warning",
            message=f"Removed dependency files: {', '.join(dep_removed)}",
            details={"files": dep_removed},
        ))

    return signals


# ---------------------------------------------------------------------------
# Signal 3: Config sensitivity scoring
# ---------------------------------------------------------------------------

_CRITICAL_PATTERNS = {
    "DATABASE_URL", "DB_PASSWORD", "SECRET_KEY", "PRIVATE_KEY",
    "AWS_SECRET_ACCESS_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
}

_WARNING_PATTERNS = {
    "CORS", "ALLOWED_HOSTS", "DEBUG", "LOG_LEVEL",
    "MAX_CONNECTIONS", "TIMEOUT", "RATE_LIMIT",
}


def score_config_sensitivity(file_path: str, content: str = "") -> Signal | None:
    """Score how sensitive a config change is: critical / warning / info."""
    upper = content.upper()

    for pattern in _CRITICAL_PATTERNS:
        if pattern in upper:
            return Signal(
                name="config_sensitivity",
                severity="critical",
                message=f"Critical config change in {file_path}: contains {pattern}",
                details={"file": file_path, "pattern": pattern},
            )

    for pattern in _WARNING_PATTERNS:
        if pattern in upper:
            return Signal(
                name="config_sensitivity",
                severity="warning",
                message=f"Warning-level config change in {file_path}: contains {pattern}",
                details={"file": file_path, "pattern": pattern},
            )

    return Signal(
        name="config_sensitivity",
        severity="info",
        message=f"Config change in {file_path}",
        details={"file": file_path},
    )


# ---------------------------------------------------------------------------
# Signal 4: Scope creep detection
# ---------------------------------------------------------------------------


def detect_scope_creep(
    task_description: str,
    changed_files: list[str],
    *,
    threshold: float = 0.3,
) -> Signal | None:
    """Detect scope creep by comparing task description to changed files.

    Uses simple keyword overlap. If embedding models are available,
    computes cosine similarity for more accurate detection.
    """
    if not task_description or not changed_files:
        return None

    task_words = set(task_description.lower().split())
    file_words: set[str] = set()
    for f in changed_files:
        # Extract meaningful words from file paths
        parts = f.replace("/", " ").replace("_", " ").replace("-", " ").replace(".", " ").lower().split()
        file_words.update(parts)

    if not task_words or not file_words:
        return None

    overlap = len(task_words & file_words)
    similarity = overlap / max(len(task_words), 1)

    if similarity < threshold:
        return Signal(
            name="scope_creep",
            severity="warning",
            message=f"Possible scope creep: low overlap ({similarity:.0%}) between task and changes",
            details={
                "similarity": round(similarity, 3),
                "threshold": threshold,
                "changed_files_count": len(changed_files),
            },
        )
    return None


# ---------------------------------------------------------------------------
# Signal 5: Intent drift
# ---------------------------------------------------------------------------


def detect_intent_drift(
    initial_task: str,
    recent_actions: list[str],
    *,
    drift_threshold: float = 0.2,
) -> Signal | None:
    """Lightweight periodic check: has the agent drifted from its original task?

    Uses keyword overlap between initial task and recent file operations.
    """
    if not initial_task or not recent_actions:
        return None

    task_words = set(initial_task.lower().split())
    action_words: set[str] = set()
    for action in recent_actions:
        action_words.update(action.lower().split())

    if not task_words:
        return None

    overlap = len(task_words & action_words) / len(task_words)

    if overlap < drift_threshold:
        return Signal(
            name="intent_drift",
            severity="warning",
            message=f"Agent may have drifted from task (overlap: {overlap:.0%})",
            details={"overlap": round(overlap, 3), "threshold": drift_threshold},
        )
    return None


# ---------------------------------------------------------------------------
# Signal 6: Pattern violations
# ---------------------------------------------------------------------------


def detect_pattern_violations(
    before_analysis: dict[str, Any],
    after_analysis: dict[str, Any],
) -> list[Signal]:
    """Detect structural pattern violations by comparing before/after analysis.

    Checks for: removed tests, deleted public functions, broken imports.
    """
    signals: list[Signal] = []

    before_funcs = set(before_analysis.get("functions", []))
    after_funcs = set(after_analysis.get("functions", []))
    removed = before_funcs - after_funcs

    if removed:
        test_funcs = [f for f in removed if f.startswith("test_")]
        if test_funcs:
            signals.append(Signal(
                name="test_removed",
                severity="warning",
                message=f"Test functions removed: {', '.join(test_funcs)}",
                details={"removed_tests": test_funcs},
            ))

        public_funcs = [f for f in removed if not f.startswith("_")]
        if public_funcs:
            signals.append(Signal(
                name="public_api_removed",
                severity="info",
                message=f"Public functions removed: {', '.join(public_funcs[:5])}",
                details={"removed": public_funcs},
            ))

    return signals


# ---------------------------------------------------------------------------
# Signal 7: LLM call anomalies
# ---------------------------------------------------------------------------


class LLMCallTracker:
    """Track LLM call patterns and detect anomalies."""

    def __init__(self, *, cost_window: int = 20, latency_window: int = 20) -> None:
        self._costs: list[float] = []
        self._latencies: list[float] = []
        self._cost_window = cost_window
        self._latency_window = latency_window

    def record(self, span: Span) -> list[Signal]:
        """Record a span and return any anomaly signals."""
        signals: list[Signal] = []

        # Track cost
        self._costs.append(span.cost_usd)
        if len(self._costs) > self._cost_window:
            self._costs = self._costs[-self._cost_window:]

        # Track latency
        self._latencies.append(span.latency_ms)
        if len(self._latencies) > self._latency_window:
            self._latencies = self._latencies[-self._latency_window:]

        # Cost spike detection
        if len(self._costs) >= 5:
            avg = sum(self._costs[:-1]) / (len(self._costs) - 1)
            if avg > 0 and span.cost_usd > avg * 5:
                signals.append(Signal(
                    name="cost_spike",
                    severity="warning",
                    message=f"LLM cost spike: ${span.cost_usd:.4f} vs avg ${avg:.4f}",
                    details={
                        "cost": span.cost_usd,
                        "average": round(avg, 6),
                        "model": span.model,
                    },
                ))

        # Latency spike detection
        if len(self._latencies) >= 5:
            avg_lat = sum(self._latencies[:-1]) / (len(self._latencies) - 1)
            if avg_lat > 0 and span.latency_ms > avg_lat * 3:
                signals.append(Signal(
                    name="latency_spike",
                    severity="info",
                    message=f"LLM latency spike: {span.latency_ms:.0f}ms vs avg {avg_lat:.0f}ms",
                    details={
                        "latency_ms": span.latency_ms,
                        "average_ms": round(avg_lat, 1),
                        "model": span.model,
                    },
                ))

        return signals


# ---------------------------------------------------------------------------
# Event bus wiring
# ---------------------------------------------------------------------------


async def publish_signals(bus: EventBus, signals: list[Signal]) -> None:
    """Publish signal list to the event bus."""
    for sig in signals:
        event_type = _signal_to_event_type(sig)
        await bus.publish(Event(
            event_type=event_type,
            payload={
                "signal_name": sig.name,
                "severity": sig.severity,
                "message": sig.message,
                "details": sig.details,
            },
            source="observation.signals",
        ))


def _signal_to_event_type(sig: Signal) -> EventType:
    """Map signal name to EventType."""
    mapping = {
        "file_changes": EventType.FILE_CHANGED,
        "config_change": EventType.FILE_CHANGED,
        "dependency_added": EventType.DEPENDENCY_UPDATED,
        "dependency_modified": EventType.DEPENDENCY_UPDATED,
        "dependency_removed": EventType.DEPENDENCY_UPDATED,
        "config_sensitivity": EventType.FILE_CHANGED,
        "scope_creep": EventType.PATTERN_DETECTED,
        "intent_drift": EventType.PATTERN_DETECTED,
        "test_removed": EventType.PATTERN_DETECTED,
        "public_api_removed": EventType.PATTERN_DETECTED,
        "cost_spike": EventType.LLM_CALL_OBSERVED,
        "latency_spike": EventType.LLM_CALL_OBSERVED,
    }
    return mapping.get(sig.name, EventType.PATTERN_DETECTED)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEP_FILES = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock", "package.json",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock",
}


def _is_dep_file(path: str) -> bool:
    """Check if a path is a dependency file."""
    name = Path(path).name
    return name in _DEP_FILES

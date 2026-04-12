"""Auto-resolve low-risk contradictions.

For TENSION on non-critical dimensions with high confidence,
auto-resolve without human intervention.

From SPEC Sprint 21: "Auto-resolve low-risk contradictions."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from vt_protocol.decisions.models import (
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    Dimension,
)

logger = logging.getLogger(__name__)

# Dimensions that are never auto-resolved
DEFAULT_EXCLUDED_DIMENSIONS: set[str] = {
    Dimension.SECURITY.value,
    Dimension.AUTH.value,
}

# Maximum blast radius (files affected) for auto-resolution
MAX_BLAST_RADIUS = 3

# Minimum confidence for auto-resolution
MIN_CONFIDENCE = 0.85

# Hours within which a human can override
OVERRIDE_WINDOW_HOURS = 48


@dataclass
class AutoResolveConfig:
    """Configuration for auto-resolution."""

    enabled: bool = True
    max_severity: str = "tension"  # only TENSION, never CONTRADICTION
    excluded_dimensions: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDED_DIMENSIONS))
    min_confidence: float = MIN_CONFIDENCE
    max_blast_radius: int = MAX_BLAST_RADIUS
    override_window_hours: int = OVERRIDE_WINDOW_HOURS

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_severity": self.max_severity,
            "excluded_dimensions": sorted(self.excluded_dimensions),
            "min_confidence": self.min_confidence,
            "max_blast_radius": self.max_blast_radius,
            "override_window_hours": self.override_window_hours,
        }


@dataclass
class AutoResolveCandidate:
    """A contradiction evaluated for auto-resolution."""

    contradiction_id: str = ""
    decision_a_id: str = ""
    decision_b_id: str = ""
    verdict: str = ""
    dimensions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    winner_id: str = ""
    winner_title: str = ""
    loser_id: str = ""
    loser_title: str = ""
    reason: str = ""
    eligible: bool = False
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "winner_id": self.winner_id,
            "winner_title": self.winner_title,
            "loser_id": self.loser_id,
            "loser_title": self.loser_title,
            "eligible": self.eligible,
            "reason": self.reason,
            "rejection_reason": self.rejection_reason,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class AutoResolveResult:
    """Result of auto-resolution attempt."""

    resolved: bool = False
    candidate: AutoResolveCandidate | None = None
    audit_entry: dict[str, Any] = field(default_factory=dict)
    override_deadline: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "audit_entry": self.audit_entry,
            "override_deadline": self.override_deadline.isoformat() if self.override_deadline else None,
        }


@dataclass
class AutoResolveLog:
    """Audit log of auto-resolutions."""

    entries: list[AutoResolveResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def resolved_count(self) -> int:
        return sum(1 for e in self.entries if e.resolved)

    @property
    def rejected_count(self) -> int:
        return sum(1 for e in self.entries if not e.resolved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "resolved": self.resolved_count,
            "rejected": self.rejected_count,
            "entries": [e.to_dict() for e in self.entries],
        }


class AutoResolver:
    """Auto-resolves low-risk tensions based on configurable criteria."""

    def __init__(self, config: AutoResolveConfig | None = None) -> None:
        self._config = config or AutoResolveConfig()
        self._log = AutoResolveLog()

    @property
    def config(self) -> AutoResolveConfig:
        return self._config

    @property
    def log(self) -> AutoResolveLog:
        return self._log

    def evaluate(
        self,
        *,
        contradiction_id: str,
        verdict: ContradictionVerdict,
        dimensions: list[str],
        confidence: float,
        decision_a: Decision,
        decision_b: Decision,
        dependents_of_a: int = 0,
        dependents_of_b: int = 0,
        blast_radius: int = 0,
    ) -> AutoResolveCandidate:
        """Evaluate whether a contradiction can be auto-resolved."""
        candidate = AutoResolveCandidate(
            contradiction_id=contradiction_id,
            decision_a_id=str(decision_a.id),
            decision_b_id=str(decision_b.id),
            verdict=verdict.value,
            dimensions=dimensions,
            confidence=confidence,
        )

        if not self._config.enabled:
            candidate.rejection_reason = "Auto-resolve disabled"
            return candidate

        # Rule 1: Only TENSION, never CONTRADICTION
        if verdict != ContradictionVerdict.TENSION:
            candidate.rejection_reason = f"Verdict is {verdict.value}, not tension"
            return candidate

        # Rule 2: Excluded dimensions
        excluded_overlap = set(dimensions) & self._config.excluded_dimensions
        if excluded_overlap:
            candidate.rejection_reason = f"Excluded dimensions: {excluded_overlap}"
            return candidate

        # Rule 3: Confidence threshold
        if confidence < self._config.min_confidence:
            candidate.rejection_reason = f"Confidence {confidence:.2f} < {self._config.min_confidence}"
            return candidate

        # Rule 4: Blast radius
        if blast_radius > self._config.max_blast_radius:
            candidate.rejection_reason = f"Blast radius {blast_radius} > {self._config.max_blast_radius}"
            return candidate

        # Pick winner: higher confidence, then more recent
        winner, loser = self._pick_winner(decision_a, decision_b)

        # Rule 5: No dependents on loser
        loser_deps = dependents_of_b if str(loser.id) == str(decision_b.id) else dependents_of_a
        if loser_deps > 0:
            candidate.rejection_reason = f"Loser has {loser_deps} dependents"
            return candidate

        candidate.eligible = True
        candidate.winner_id = str(winner.id)
        candidate.winner_title = winner.title
        candidate.loser_id = str(loser.id)
        candidate.loser_title = loser.title
        candidate.reason = self._explain(winner, loser, confidence)

        return candidate

    def resolve(self, candidate: AutoResolveCandidate) -> AutoResolveResult:
        """Execute auto-resolution for an eligible candidate."""
        if not candidate.eligible:
            result = AutoResolveResult(resolved=False, candidate=candidate)
            self._log.entries.append(result)
            return result

        now = datetime.now(timezone.utc)
        from datetime import timedelta
        deadline = now + timedelta(hours=self._config.override_window_hours)

        audit_entry = {
            "event_type": "auto_resolution",
            "contradiction_id": candidate.contradiction_id,
            "winner_id": candidate.winner_id,
            "loser_id": candidate.loser_id,
            "reason": candidate.reason,
            "confidence": candidate.confidence,
            "dimensions": candidate.dimensions,
            "timestamp": now.isoformat(),
            "override_deadline": deadline.isoformat(),
        }

        result = AutoResolveResult(
            resolved=True,
            candidate=candidate,
            audit_entry=audit_entry,
            override_deadline=deadline,
        )
        self._log.entries.append(result)

        logger.info(
            "Auto-resolved %s: winner=%s, loser=%s, reason=%s",
            candidate.contradiction_id,
            candidate.winner_title,
            candidate.loser_title,
            candidate.reason,
        )
        return result

    def _pick_winner(self, a: Decision, b: Decision) -> tuple[Decision, Decision]:
        if a.confidence > b.confidence:
            return a, b
        if b.confidence > a.confidence:
            return b, a
        if a.created_at >= b.created_at:
            return a, b
        return b, a

    def _explain(self, winner: Decision, loser: Decision, confidence: float) -> str:
        parts: list[str] = []
        if winner.confidence > loser.confidence:
            parts.append(f"higher confidence ({winner.confidence:.2f} vs {loser.confidence:.2f})")
        if winner.created_at > loser.created_at:
            parts.append("more recent")
        return f"Auto-resolved: '{winner.title}' wins — {', '.join(parts) or 'equal, resolved by recency'}"

    def clear(self) -> None:
        self._log = AutoResolveLog()


def load_config_from_dict(data: dict[str, Any]) -> AutoResolveConfig:
    """Load auto-resolve config from governance.yaml dict."""
    return AutoResolveConfig(
        enabled=data.get("enabled", True),
        max_severity=data.get("max_severity", "tension"),
        excluded_dimensions=set(data.get("excluded_dimensions", DEFAULT_EXCLUDED_DIMENSIONS)),
        min_confidence=data.get("min_confidence", MIN_CONFIDENCE),
        max_blast_radius=data.get("max_blast_radius", MAX_BLAST_RADIUS),
        override_window_hours=data.get("override_window_hours", OVERRIDE_WINDOW_HOURS),
    )

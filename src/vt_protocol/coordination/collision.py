"""Decision collision detection.

Two agents making decisions on the same dimension within a short time
window (< 5 min) with no causal relationship → CONCURRENT_CONFLICT.

From SPEC Sprint 19: "Multi-agent coordination — collision.py."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Default collision window
COLLISION_WINDOW_SECONDS = 300  # 5 minutes


class CollisionType(str, Enum):
    """Types of decision collisions."""

    CONCURRENT_CONFLICT = "concurrent_conflict"
    SEQUENTIAL_OVERRIDE = "sequential_override"


@dataclass
class DecisionEvent:
    """A lightweight decision event for collision detection."""

    decision_id: str = ""
    agent_id: str = ""
    dimension: str = ""
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "agent_id": self.agent_id,
            "dimension": self.dimension,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
        }


@dataclass
class Collision:
    """A detected decision collision between agents."""

    collision_type: CollisionType
    event_a: DecisionEvent
    event_b: DecisionEvent
    dimension: str = ""
    time_gap_seconds: float = 0.0
    has_causal_link: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "collision_type": self.collision_type.value,
            "event_a": self.event_a.to_dict(),
            "event_b": self.event_b.to_dict(),
            "dimension": self.dimension,
            "time_gap_seconds": round(self.time_gap_seconds, 1),
            "has_causal_link": self.has_causal_link,
        }


class CollisionDetector:
    """Detects concurrent decision conflicts between agents.

    Maintains a sliding window of recent decision events and flags
    collisions when two agents decide on the same dimension within
    the window without a causal relationship.
    """

    def __init__(
        self,
        *,
        window_seconds: float = COLLISION_WINDOW_SECONDS,
        causal_pairs: set[tuple[str, str]] | None = None,
    ) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._events: list[DecisionEvent] = []
        self._collisions: list[Collision] = []
        # Set of (source_decision_id, target_decision_id) pairs with causal links
        self._causal_pairs = causal_pairs or set()

    @property
    def collision_count(self) -> int:
        return len(self._collisions)

    @property
    def collisions(self) -> list[Collision]:
        return list(self._collisions)

    def add_causal_pair(self, source_id: str, target_id: str) -> None:
        """Register a causal link between two decisions."""
        self._causal_pairs.add((source_id, target_id))
        self._causal_pairs.add((target_id, source_id))

    def record_decision(self, event: DecisionEvent) -> list[Collision]:
        """Record a decision event and check for collisions.

        Returns any new collisions detected by this event.
        """
        new_collisions: list[Collision] = []

        for existing in self._events:
            # Must be same dimension, different agents
            if existing.dimension != event.dimension:
                continue
            if existing.agent_id == event.agent_id:
                continue

            # Check time window
            gap = abs((event.timestamp - existing.timestamp).total_seconds())
            if gap > self._window.total_seconds():
                continue

            # Check causal link
            has_causal = (
                (existing.decision_id, event.decision_id) in self._causal_pairs
            )

            if not has_causal:
                collision = Collision(
                    collision_type=CollisionType.CONCURRENT_CONFLICT,
                    event_a=existing,
                    event_b=event,
                    dimension=event.dimension,
                    time_gap_seconds=gap,
                    has_causal_link=False,
                )
                new_collisions.append(collision)

        self._events.append(event)
        self._collisions.extend(new_collisions)
        return new_collisions

    def cleanup_old_events(self, *, before: datetime | None = None) -> int:
        """Remove events outside the window. Returns count removed."""
        cutoff = before or (datetime.now(timezone.utc) - self._window * 2)
        original = len(self._events)
        self._events = [e for e in self._events if e.timestamp > cutoff]
        return original - len(self._events)

    def get_collisions_for_dimension(self, dimension: str) -> list[Collision]:
        return [c for c in self._collisions if c.dimension == dimension]

    def clear(self) -> None:
        self._events.clear()
        self._collisions.clear()

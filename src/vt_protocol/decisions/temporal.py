"""Temporal decision graph — valid_from/valid_until and point-in-time queries.

Adds temporal edges to the decision graph so teams can ask "what was
the architecture at timestamp T?" and track how decisions evolve.

From SPEC Phase 3: "Temporal decision graph — valid_from, valid_until,
superseded_by edges, point-in-time queries."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from vt_protocol.decisions.models import Decision, DecisionStatus, Dimension

logger = logging.getLogger(__name__)


@dataclass
class TemporalDecision:
    """A decision with temporal validity bounds."""

    decision: Decision
    valid_from: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: datetime | None = None
    superseded_by: UUID | None = None

    @property
    def is_current(self) -> bool:
        """True if this decision is currently valid."""
        now = datetime.now(timezone.utc)
        if self.valid_until and now >= self.valid_until:
            return False
        return now >= self.valid_from

    def is_valid_at(self, timestamp: datetime) -> bool:
        """Check if this decision was valid at a specific point in time."""
        if timestamp < self.valid_from:
            return False
        if self.valid_until and timestamp >= self.valid_until:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": str(self.decision.id),
            "title": self.decision.title,
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "superseded_by": str(self.superseded_by) if self.superseded_by else None,
            "is_current": self.is_current,
            "dimensions": [d.value for d in self.decision.dimensions],
        }


@dataclass
class TemporalEdge:
    """Edge showing a supersession relationship with timestamps."""

    from_decision_id: UUID
    to_decision_id: UUID
    superseded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_decision_id": str(self.from_decision_id),
            "to_decision_id": str(self.to_decision_id),
            "superseded_at": self.superseded_at.isoformat(),
        }


@dataclass
class TimelineSnapshot:
    """Architecture state at a specific point in time."""

    timestamp: datetime
    active_decisions: list[TemporalDecision] = field(default_factory=list)
    dimensions_covered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "active_count": len(self.active_decisions),
            "active_decisions": [td.to_dict() for td in self.active_decisions],
            "dimensions_covered": self.dimensions_covered,
        }


class TemporalGraph:
    """Temporal decision graph supporting point-in-time queries.

    Wraps a list of decisions with temporal metadata, supporting queries
    like "what decisions were active at time T?" and "how has the
    database architecture changed?"
    """

    def __init__(self) -> None:
        self._decisions: list[TemporalDecision] = []
        self._edges: list[TemporalEdge] = []

    @property
    def decision_count(self) -> int:
        return len(self._decisions)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def add_decision(
        self,
        decision: Decision,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> TemporalDecision:
        """Add a decision with temporal bounds."""
        td = TemporalDecision(
            decision=decision,
            valid_from=valid_from or decision.created_at,
            valid_until=valid_until,
        )
        self._decisions.append(td)
        return td

    def supersede(
        self,
        old_decision_id: UUID,
        new_decision: Decision,
        *,
        superseded_at: datetime | None = None,
    ) -> TemporalDecision | None:
        """Supersede an old decision with a new one.

        Sets valid_until on the old decision and creates a temporal edge.
        Returns the new TemporalDecision, or None if old not found.
        """
        ts = superseded_at or datetime.now(timezone.utc)

        old_td = self._find_by_id(old_decision_id)
        if old_td is None:
            return None

        old_td.valid_until = ts
        old_td.superseded_by = new_decision.id

        new_td = self.add_decision(new_decision, valid_from=ts)
        new_td.decision.supersedes = old_decision_id

        self._edges.append(TemporalEdge(
            from_decision_id=old_decision_id,
            to_decision_id=new_decision.id,
            superseded_at=ts,
        ))

        return new_td

    def query_at(self, timestamp: datetime) -> TimelineSnapshot:
        """Get all decisions that were active at a specific timestamp."""
        active = [td for td in self._decisions if td.is_valid_at(timestamp)]
        dims: set[str] = set()
        for td in active:
            for d in td.decision.dimensions:
                dims.add(d.value)

        return TimelineSnapshot(
            timestamp=timestamp,
            active_decisions=active,
            dimensions_covered=sorted(dims),
        )

    def query_current(self) -> TimelineSnapshot:
        """Get currently active decisions."""
        return self.query_at(datetime.now(timezone.utc))

    def query_dimension_history(
        self,
        dimension: Dimension,
    ) -> list[TemporalDecision]:
        """Get the history of a specific dimension over time."""
        matching = [
            td for td in self._decisions
            if dimension in td.decision.dimensions
        ]
        return sorted(matching, key=lambda td: td.valid_from)

    def get_supersession_chain(self, decision_id: UUID) -> list[TemporalDecision]:
        """Get the full supersession chain starting from a decision.

        Returns [oldest → newest] supersession chain.
        """
        chain: list[TemporalDecision] = []
        current_id: UUID | None = decision_id

        # Walk backwards to find the start
        visited: set[str] = set()
        while current_id is not None:
            td = self._find_by_id(current_id)
            if td is None or str(current_id) in visited:
                break
            visited.add(str(current_id))
            chain.insert(0, td)
            current_id = td.decision.supersedes

        # Walk forward from the starting decision
        current_id = decision_id
        visited_forward: set[str] = set()
        while current_id is not None:
            td = self._find_by_id(current_id)
            if td is None or str(current_id) in visited_forward:
                break
            visited_forward.add(str(current_id))
            if td.superseded_by and str(td.superseded_by) not in visited:
                succ = self._find_by_id(td.superseded_by)
                if succ:
                    chain.append(succ)
                    visited.add(str(td.superseded_by))
                    current_id = td.superseded_by
                    continue
            break

        return chain

    def _find_by_id(self, decision_id: UUID) -> TemporalDecision | None:
        for td in self._decisions:
            if td.decision.id == decision_id:
                return td
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": [td.to_dict() for td in self._decisions],
            "edges": [e.to_dict() for e in self._edges],
            "total_decisions": self.decision_count,
            "total_edges": self.edge_count,
        }

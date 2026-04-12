"""Cross-agent causal intelligence.

When Agent A's tainted output feeds Agent B's input, record the causal
edge in the decision graph. Query: "which decisions in service B were
influenced by Agent A's choices?"

From SPEC Sprint 19: "Multi-agent coordination — coordinator.py."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


@dataclass
class CausalEdge:
    """A causal link between two agent decisions."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    source_agent_id: str = ""
    target_agent_id: str = ""
    source_decision_id: str = ""
    target_decision_id: str = ""
    taint_id: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_agent_id": self.source_agent_id,
            "target_agent_id": self.target_agent_id,
            "source_decision_id": self.source_decision_id,
            "target_decision_id": self.target_decision_id,
            "taint_id": self.taint_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CausalQuery:
    """Result of querying the causal graph."""

    agent_id: str = ""
    influenced_decisions: list[str] = field(default_factory=list)
    causal_edges: list[CausalEdge] = field(default_factory=list)

    @property
    def influence_count(self) -> int:
        return len(self.influenced_decisions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "influenced_decisions": self.influenced_decisions,
            "influence_count": self.influence_count,
            "causal_edges": [e.to_dict() for e in self.causal_edges],
        }


class CausalCoordinator:
    """Tracks causal relationships between agent decisions.

    Records edges when tainted output from one agent influences
    another agent's decision. Enables queries like "which decisions
    were influenced by Agent A?"
    """

    def __init__(self) -> None:
        self._edges: list[CausalEdge] = []

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    def record_causal_edge(
        self,
        *,
        source_agent_id: str,
        target_agent_id: str,
        source_decision_id: str,
        target_decision_id: str,
        taint_id: str = "",
    ) -> CausalEdge:
        """Record a causal edge between two agent decisions."""
        edge = CausalEdge(
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            source_decision_id=source_decision_id,
            target_decision_id=target_decision_id,
            taint_id=taint_id,
        )
        self._edges.append(edge)
        logger.debug(
            "Recorded causal edge: %s(%s) → %s(%s)",
            source_agent_id, source_decision_id,
            target_agent_id, target_decision_id,
        )
        return edge

    def query_influenced_by(self, agent_id: str) -> CausalQuery:
        """Find all decisions influenced by a specific agent."""
        relevant_edges = [e for e in self._edges if e.source_agent_id == agent_id]
        decision_ids = list({e.target_decision_id for e in relevant_edges})
        return CausalQuery(
            agent_id=agent_id,
            influenced_decisions=decision_ids,
            causal_edges=relevant_edges,
        )

    def query_influences_on(self, agent_id: str) -> CausalQuery:
        """Find all decisions that influenced a specific agent's decisions."""
        relevant_edges = [e for e in self._edges if e.target_agent_id == agent_id]
        decision_ids = list({e.source_decision_id for e in relevant_edges})
        return CausalQuery(
            agent_id=agent_id,
            influenced_decisions=decision_ids,
            causal_edges=relevant_edges,
        )

    def get_causal_chain(self, decision_id: str) -> list[CausalEdge]:
        """Trace the full causal chain for a decision.

        Returns all edges in the chain, following source_decision_id links.
        """
        chain: list[CausalEdge] = []
        visited: set[str] = set()
        queue = [decision_id]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for edge in self._edges:
                if edge.target_decision_id == current:
                    chain.append(edge)
                    queue.append(edge.source_decision_id)

        return chain

    def get_all_edges(self) -> list[CausalEdge]:
        return list(self._edges)

    def clear(self) -> None:
        self._edges.clear()

"""Automated negotiation broker for low-severity tensions.

For low-severity TENSION (not CONTRADICTION) on non-critical dimensions,
auto-resolve by picking the decision with higher confidence + more recent
timestamp. Escalate CONTRADICTION or critical dimensions to humans.

From SPEC Sprint 19: "Multi-agent coordination — negotiator.py."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from vt_protocol.decisions.models import (
    ContradictionVerdict,
    Dimension,
)

logger = logging.getLogger(__name__)

# Dimensions that require human review (never auto-resolved)
CRITICAL_DIMENSIONS: set[str] = {
    Dimension.AUTH.value,
    Dimension.SECURITY.value,
    Dimension.DEPLOYMENT.value,
}


class NegotiationOutcome(str, Enum):
    """Outcome of an automated negotiation."""

    AUTO_RESOLVED = "auto_resolved"
    ESCALATED = "escalated"
    DEFERRED = "deferred"


@dataclass
class NegotiationDecision:
    """A decision involved in negotiation."""

    decision_id: str = ""
    agent_id: str = ""
    title: str = ""
    confidence: float = 0.5
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "agent_id": self.agent_id,
            "title": self.title,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class NegotiationResult:
    """Result of negotiation between two decisions."""

    outcome: NegotiationOutcome
    winner: NegotiationDecision | None = None
    loser: NegotiationDecision | None = None
    reason: str = ""
    escalation_target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "winner": self.winner.to_dict() if self.winner else None,
            "loser": self.loser.to_dict() if self.loser else None,
            "reason": self.reason,
            "escalation_target": self.escalation_target,
        }


@dataclass
class NegotiationLog:
    """Audit log of all negotiations."""

    entries: list[NegotiationResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def auto_resolved_count(self) -> int:
        return sum(1 for e in self.entries if e.outcome == NegotiationOutcome.AUTO_RESOLVED)

    @property
    def escalated_count(self) -> int:
        return sum(1 for e in self.entries if e.outcome == NegotiationOutcome.ESCALATED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "auto_resolved": self.auto_resolved_count,
            "escalated": self.escalated_count,
            "entries": [e.to_dict() for e in self.entries],
        }


class NegotiationBroker:
    """Automated negotiation broker.

    Auto-resolves low-severity tensions by comparing confidence
    and recency. Escalates contradictions and critical dimension
    conflicts to human reviewers.
    """

    def __init__(
        self,
        *,
        critical_dimensions: set[str] | None = None,
    ) -> None:
        self._critical = critical_dimensions or CRITICAL_DIMENSIONS
        self._log = NegotiationLog()

    @property
    def log(self) -> NegotiationLog:
        return self._log

    def negotiate(
        self,
        decision_a: NegotiationDecision,
        decision_b: NegotiationDecision,
        *,
        verdict: ContradictionVerdict,
        dimensions: list[str] | None = None,
        escalation_target: str = "tech_lead",
    ) -> NegotiationResult:
        """Negotiate between two competing decisions.

        Rules:
        1. CONTRADICTION → always escalate
        2. Critical dimensions → always escalate
        3. TENSION on non-critical → auto-resolve
        4. COMPATIBLE → no action needed (deferred)
        """
        dims = set(dimensions or [])

        # Rule 1: Contradictions always escalate
        if verdict == ContradictionVerdict.CONTRADICTION:
            result = NegotiationResult(
                outcome=NegotiationOutcome.ESCALATED,
                reason="CONTRADICTION verdict requires human review",
                escalation_target=escalation_target,
            )
            self._log.entries.append(result)
            return result

        # Rule 2: Critical dimensions always escalate
        if dims & self._critical:
            critical_overlap = dims & self._critical
            result = NegotiationResult(
                outcome=NegotiationOutcome.ESCALATED,
                reason=f"Critical dimension(s) {critical_overlap} require human review",
                escalation_target=escalation_target,
            )
            self._log.entries.append(result)
            return result

        # Rule 3: TENSION on non-critical → auto-resolve
        if verdict == ContradictionVerdict.TENSION:
            winner, loser = self._pick_winner(decision_a, decision_b)
            result = NegotiationResult(
                outcome=NegotiationOutcome.AUTO_RESOLVED,
                winner=winner,
                loser=loser,
                reason=self._explain_choice(winner, loser),
            )
            self._log.entries.append(result)
            return result

        # Rule 4: COMPATIBLE → defer
        result = NegotiationResult(
            outcome=NegotiationOutcome.DEFERRED,
            reason="Decisions are compatible, no negotiation needed",
        )
        self._log.entries.append(result)
        return result

    def _pick_winner(
        self,
        a: NegotiationDecision,
        b: NegotiationDecision,
    ) -> tuple[NegotiationDecision, NegotiationDecision]:
        """Pick winner based on confidence + recency.

        Higher confidence wins. On tie, more recent wins.
        """
        if a.confidence > b.confidence:
            return a, b
        if b.confidence > a.confidence:
            return b, a
        # Tie: more recent wins
        if a.timestamp >= b.timestamp:
            return a, b
        return b, a

    def _explain_choice(
        self,
        winner: NegotiationDecision,
        loser: NegotiationDecision,
    ) -> str:
        """Generate explanation for auto-resolution."""
        parts: list[str] = []
        if winner.confidence > loser.confidence:
            parts.append(
                f"Higher confidence ({winner.confidence:.2f} vs {loser.confidence:.2f})"
            )
        if winner.timestamp > loser.timestamp:
            parts.append("More recent")
        if winner.confidence == loser.confidence:
            parts.append("Equal confidence, resolved by recency")
        return f"Auto-resolved: '{winner.title}' wins. {'; '.join(parts)}"

    def clear(self) -> None:
        self._log = NegotiationLog()

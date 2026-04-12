"""Tests for automated negotiation broker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.coordination.negotiator import (
    CRITICAL_DIMENSIONS,
    NegotiationBroker,
    NegotiationDecision,
    NegotiationLog,
    NegotiationOutcome,
    NegotiationResult,
)
from vt_protocol.decisions.models import ContradictionVerdict


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestNegotiationDecision:
    def test_to_dict(self) -> None:
        nd = NegotiationDecision(
            decision_id="d1",
            agent_id="agent_a",
            title="Use PostgreSQL",
            confidence=0.9,
        )
        d = nd.to_dict()
        assert d["decision_id"] == "d1"
        assert d["confidence"] == 0.9


class TestNegotiationResult:
    def test_to_dict(self) -> None:
        nr = NegotiationResult(
            outcome=NegotiationOutcome.AUTO_RESOLVED,
            reason="Higher confidence",
        )
        d = nr.to_dict()
        assert d["outcome"] == "auto_resolved"


class TestNegotiationLog:
    def test_empty(self) -> None:
        log = NegotiationLog()
        assert log.total == 0
        assert log.auto_resolved_count == 0
        assert log.escalated_count == 0

    def test_counts(self) -> None:
        log = NegotiationLog(entries=[
            NegotiationResult(outcome=NegotiationOutcome.AUTO_RESOLVED),
            NegotiationResult(outcome=NegotiationOutcome.AUTO_RESOLVED),
            NegotiationResult(outcome=NegotiationOutcome.ESCALATED),
        ])
        assert log.total == 3
        assert log.auto_resolved_count == 2
        assert log.escalated_count == 1


class TestNegotiationBroker:
    def test_contradiction_always_escalates(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="A", confidence=0.9)
        d_b = NegotiationDecision(decision_id="d2", title="B", confidence=0.5)
        result = broker.negotiate(
            d_a, d_b,
            verdict=ContradictionVerdict.CONTRADICTION,
        )
        assert result.outcome == NegotiationOutcome.ESCALATED
        assert "CONTRADICTION" in result.reason

    def test_critical_dimension_escalates(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="A", confidence=0.9)
        d_b = NegotiationDecision(decision_id="d2", title="B", confidence=0.5)
        result = broker.negotiate(
            d_a, d_b,
            verdict=ContradictionVerdict.TENSION,
            dimensions=["auth"],
        )
        assert result.outcome == NegotiationOutcome.ESCALATED
        assert "auth" in result.reason.lower()

    def test_tension_auto_resolves(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="Option A", confidence=0.9)
        d_b = NegotiationDecision(decision_id="d2", title="Option B", confidence=0.5)
        result = broker.negotiate(
            d_a, d_b,
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
        )
        assert result.outcome == NegotiationOutcome.AUTO_RESOLVED
        assert result.winner is not None
        assert result.winner.decision_id == "d1"  # Higher confidence

    def test_compatible_deferred(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="A")
        d_b = NegotiationDecision(decision_id="d2", title="B")
        result = broker.negotiate(
            d_a, d_b,
            verdict=ContradictionVerdict.COMPATIBLE,
        )
        assert result.outcome == NegotiationOutcome.DEFERRED

    def test_tie_resolved_by_recency(self) -> None:
        broker = NegotiationBroker()
        now = _now()
        d_a = NegotiationDecision(decision_id="d1", title="Older", confidence=0.8,
                                   timestamp=now - timedelta(hours=1))
        d_b = NegotiationDecision(decision_id="d2", title="Newer", confidence=0.8,
                                   timestamp=now)
        result = broker.negotiate(
            d_a, d_b,
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
        )
        assert result.outcome == NegotiationOutcome.AUTO_RESOLVED
        assert result.winner.decision_id == "d2"  # More recent

    def test_log_tracks_negotiations(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="A", confidence=0.9)
        d_b = NegotiationDecision(decision_id="d2", title="B", confidence=0.5)
        broker.negotiate(d_a, d_b, verdict=ContradictionVerdict.TENSION, dimensions=["database"])
        broker.negotiate(d_a, d_b, verdict=ContradictionVerdict.CONTRADICTION)
        assert broker.log.total == 2
        assert broker.log.auto_resolved_count == 1
        assert broker.log.escalated_count == 1

    def test_custom_critical_dimensions(self) -> None:
        broker = NegotiationBroker(critical_dimensions={"database"})
        d_a = NegotiationDecision(decision_id="d1", title="A", confidence=0.9)
        d_b = NegotiationDecision(decision_id="d2", title="B", confidence=0.5)
        result = broker.negotiate(
            d_a, d_b,
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
        )
        assert result.outcome == NegotiationOutcome.ESCALATED

    def test_escalation_target(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="A")
        d_b = NegotiationDecision(decision_id="d2", title="B")
        result = broker.negotiate(
            d_a, d_b,
            verdict=ContradictionVerdict.CONTRADICTION,
            escalation_target="ciso",
        )
        assert result.escalation_target == "ciso"

    def test_clear(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="A")
        d_b = NegotiationDecision(decision_id="d2", title="B")
        broker.negotiate(d_a, d_b, verdict=ContradictionVerdict.COMPATIBLE)
        broker.clear()
        assert broker.log.total == 0

    def test_default_critical_dimensions(self) -> None:
        assert "auth" in CRITICAL_DIMENSIONS
        assert "security" in CRITICAL_DIMENSIONS
        assert "deployment" in CRITICAL_DIMENSIONS

    def test_log_to_dict(self) -> None:
        broker = NegotiationBroker()
        d_a = NegotiationDecision(decision_id="d1", title="A", confidence=0.9)
        d_b = NegotiationDecision(decision_id="d2", title="B", confidence=0.5)
        broker.negotiate(d_a, d_b, verdict=ContradictionVerdict.TENSION, dimensions=["database"])
        d = broker.log.to_dict()
        assert d["total"] == 1
        assert d["auto_resolved"] == 1

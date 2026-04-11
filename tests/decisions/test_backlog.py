"""Tests for Snyk-style backlog trickle prioritization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from vt_protocol.decisions.backlog import (
    MAX_AGE_DAYS,
    PrioritizedContradiction,
    get_next_fix,
    prioritize_backlog,
)
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    Dimension,
    SourceType,
)


def _decision(
    title: str = "Test",
    *,
    dims: list[Dimension] | None = None,
) -> Decision:
    return Decision(
        title=title,
        content=f"Content for {title}",
        rationale="Good rationale",
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )


def _contradiction(
    d1: Decision | None = None,
    d2: Decision | None = None,
    *,
    verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
    confidence: float = 0.85,
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED,
    is_baseline: bool = False,
    detected_at: datetime | None = None,
    dims: list[Dimension] | None = None,
) -> Contradiction:
    d1 = d1 or _decision("A")
    d2 = d2 or _decision("B")
    return Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=verdict,
        reasoning="Conflict",
        evidence_a="A",
        evidence_b="B",
        confidence=confidence,
        status=status,
        is_baseline=is_baseline,
        detected_at=detected_at or datetime.now(timezone.utc),
        shared_dimensions=dims or [Dimension.DATABASE],
    )


class TestPrioritizeBacklog:
    def test_empty_list(self) -> None:
        assert prioritize_backlog([], []) == []

    def test_filters_resolved(self) -> None:
        c = _contradiction(status=ContradictionStatus.RESOLVED)
        result = prioritize_backlog([c], [])
        assert len(result) == 0

    def test_filters_baseline(self) -> None:
        c = _contradiction(is_baseline=True)
        result = prioritize_backlog([c], [])
        assert len(result) == 0

    def test_filters_compatible(self) -> None:
        c = _contradiction(verdict=ContradictionVerdict.COMPATIBLE)
        result = prioritize_backlog([c], [])
        assert len(result) == 0

    def test_includes_unresolved_contradiction(self) -> None:
        c = _contradiction()
        result = prioritize_backlog([c], [])
        assert len(result) == 1
        assert result[0].contradiction is c

    def test_includes_tension(self) -> None:
        c = _contradiction(verdict=ContradictionVerdict.TENSION)
        result = prioritize_backlog([c], [])
        assert len(result) == 1

    def test_higher_confidence_ranks_higher(self) -> None:
        c1 = _contradiction(confidence=0.9)
        c2 = _contradiction(confidence=0.5)
        result = prioritize_backlog([c1, c2], [])
        assert result[0].contradiction is c1

    def test_older_ranks_higher(self) -> None:
        now = datetime.now(timezone.utc)
        c_old = _contradiction(
            confidence=0.7,
            detected_at=now - timedelta(days=60),
        )
        c_new = _contradiction(
            confidence=0.7,
            detected_at=now - timedelta(days=1),
        )
        result = prioritize_backlog([c_new, c_old], [], now=now)
        assert result[0].contradiction is c_old

    def test_contradiction_outranks_tension(self) -> None:
        c1 = _contradiction(
            verdict=ContradictionVerdict.CONTRADICTION, confidence=0.7,
        )
        c2 = _contradiction(
            verdict=ContradictionVerdict.TENSION, confidence=0.7,
        )
        result = prioritize_backlog([c2, c1], [])
        assert result[0].contradiction is c1

    def test_impact_from_shared_decisions(self) -> None:
        d1 = _decision("A", dims=[Dimension.DATABASE])
        d2 = _decision("B", dims=[Dimension.DATABASE])
        d3 = _decision("C", dims=[Dimension.DATABASE])  # shares dimension
        d4 = _decision("D", dims=[Dimension.AUTH])  # different dimension

        c1 = _contradiction(d1, d2, dims=[Dimension.DATABASE])

        result = prioritize_backlog([c1], [d1, d2, d3, d4])
        assert result[0].impact_count == 1  # d3 shares DATABASE

    def test_scoring_breakdown_present(self) -> None:
        c = _contradiction()
        result = prioritize_backlog([c], [])
        breakdown = result[0].scoring_breakdown
        assert "confidence" in breakdown
        assert "impact" in breakdown
        assert "age" in breakdown
        assert "severity" in breakdown

    def test_age_days_computed(self) -> None:
        now = datetime.now(timezone.utc)
        c = _contradiction(detected_at=now - timedelta(days=10))
        result = prioritize_backlog([c], [], now=now)
        assert abs(result[0].age_days - 10.0) < 0.1


class TestGetNextFix:
    def test_returns_highest_priority(self) -> None:
        c1 = _contradiction(confidence=0.5)
        c2 = _contradiction(confidence=0.95)
        result = get_next_fix([c1, c2], [])
        assert result is not None
        assert result.contradiction is c2

    def test_returns_none_when_empty(self) -> None:
        assert get_next_fix([], []) is None

    def test_returns_none_when_all_resolved(self) -> None:
        c = _contradiction(status=ContradictionStatus.RESOLVED)
        assert get_next_fix([c], []) is None


class TestPrioritizedContradiction:
    def test_to_dict(self) -> None:
        c = _contradiction()
        pc = PrioritizedContradiction(
            contradiction=c,
            priority_score=0.75,
            impact_count=3,
            age_days=15.5,
            scoring_breakdown={"confidence": 0.25, "impact": 0.2},
        )
        d = pc.to_dict()
        assert d["priority_score"] == 0.75
        assert d["impact_count"] == 3
        assert d["age_days"] == 15.5
        assert d["verdict"] == "contradiction"

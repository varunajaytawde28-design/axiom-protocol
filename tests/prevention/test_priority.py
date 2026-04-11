"""Tests for priority scoring and tier assignment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.decisions.models import Decision, DecisionStatus, DecisionType, Dimension, SourceType
from vt_protocol.prevention.priority import (
    DIMENSION_GLOBS,
    ScoredDecision,
    assign_tiers,
    decisions_for_file,
    score_decision,
)


def _make_decision(
    title: str = "Test decision",
    decision_type: DecisionType = DecisionType.ARCHITECTURAL,
    dimensions: list[Dimension] | None = None,
    created_at: datetime | None = None,
    confidence: float = 0.9,
    **kwargs,
) -> Decision:
    return Decision(
        title=title,
        content="Test content for the decision, details about the choice.",
        made_by="test",
        project="test",
        decision_type=decision_type,
        dimensions=dimensions or [Dimension.DATABASE],
        source_type=SourceType.MANUAL,
        confidence=confidence,
        created_at=created_at or datetime.now(timezone.utc),
        **kwargs,
    )


class TestScoreDecision:
    def test_higher_severity_scores_higher(self) -> None:
        constraint = _make_decision(decision_type=DecisionType.CONSTRAINT)
        product = _make_decision(decision_type=DecisionType.PRODUCT)
        assert score_decision(constraint) > score_decision(product)

    def test_recent_scores_higher(self) -> None:
        now = datetime.now(timezone.utc)
        fresh = _make_decision(created_at=now)
        old = _make_decision(created_at=now - timedelta(days=365))
        assert score_decision(fresh) > score_decision(old)

    def test_violations_boost_score(self) -> None:
        d = _make_decision()
        base = score_decision(d, violation_count=0)
        boosted = score_decision(d, violation_count=10)
        assert boosted > base

    def test_higher_confidence_scores_higher(self) -> None:
        high = _make_decision(confidence=0.95)
        low = _make_decision(confidence=0.50)
        assert score_decision(high) > score_decision(low)

    def test_score_is_positive(self) -> None:
        d = _make_decision()
        assert score_decision(d) > 0


class TestAssignTiers:
    def test_top_decisions_are_always(self) -> None:
        decisions = [_make_decision(title=f"D{i}") for i in range(30)]
        scored = assign_tiers(decisions, always_count=5)
        always = [s for s in scored if s.tier == "always"]
        assert len(always) == 5

    def test_auto_tier_has_globs(self) -> None:
        decisions = [_make_decision(title=f"D{i}", dimensions=[Dimension.DATABASE]) for i in range(20)]
        scored = assign_tiers(decisions, always_count=3, auto_count=10)
        auto = [s for s in scored if s.tier == "auto"]
        for s in auto:
            assert s.globs  # Must have glob patterns

    def test_on_demand_is_remainder(self) -> None:
        decisions = [_make_decision(title=f"D{i}") for i in range(50)]
        scored = assign_tiers(decisions, always_count=5, auto_count=10)
        on_demand = [s for s in scored if s.tier == "on-demand"]
        assert len(on_demand) == 35

    def test_inactive_decisions_excluded(self) -> None:
        active = _make_decision(title="Active")
        superseded = _make_decision(title="Old", status=DecisionStatus.SUPERSEDED)
        scored = assign_tiers([active, superseded])
        assert len(scored) == 1
        assert scored[0].decision.title == "Active"

    def test_sorted_by_score_descending(self) -> None:
        decisions = [_make_decision(title=f"D{i}") for i in range(10)]
        scored = assign_tiers(decisions)
        scores = [s.score for s in scored]
        assert scores == sorted(scores, reverse=True)

    def test_empty_decisions(self) -> None:
        scored = assign_tiers([])
        assert scored == []


class TestDecisionsForFile:
    def test_always_included_for_any_file(self) -> None:
        decisions = [_make_decision(title=f"D{i}") for i in range(5)]
        scored = assign_tiers(decisions, always_count=3)
        result = decisions_for_file(scored, "src/random/file.py")
        always = [r for r in result if r.tier == "always"]
        assert len(always) == 3

    def test_auto_matched_by_glob(self) -> None:
        decisions = [_make_decision(title=f"D{i}", dimensions=[Dimension.DATABASE]) for i in range(20)]
        scored = assign_tiers(decisions, always_count=2, auto_count=10)
        result = decisions_for_file(scored, "src/models/user.py")
        # Should include always + auto-matched db decisions
        assert len(result) >= 2

    def test_on_demand_excluded(self) -> None:
        decisions = [_make_decision(title=f"D{i}") for i in range(50)]
        scored = assign_tiers(decisions, always_count=5, auto_count=5)
        result = decisions_for_file(scored, "src/something.py")
        assert all(r.tier != "on-demand" for r in result)


class TestDimensionGlobs:
    def test_all_dimensions_have_globs(self) -> None:
        for dim in Dimension:
            assert dim in DIMENSION_GLOBS, f"Missing globs for {dim.value}"

    def test_globs_are_non_empty(self) -> None:
        for dim, globs in DIMENSION_GLOBS.items():
            assert globs, f"Empty globs for {dim.value}"

"""Tests for auto-resolve low-risk contradictions."""

from __future__ import annotations

import pytest
from uuid import uuid4

from vt_protocol.decisions.auto_resolve import (
    AutoResolveCandidate,
    AutoResolveConfig,
    AutoResolveLog,
    AutoResolveResult,
    AutoResolver,
    DEFAULT_EXCLUDED_DIMENSIONS,
    MAX_BLAST_RADIUS,
    MIN_CONFIDENCE,
    OVERRIDE_WINDOW_HOURS,
    load_config_from_dict,
)
from vt_protocol.decisions.models import (
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    Dimension,
)


def _make_decision(
    title: str = "Test",
    confidence: float = 0.9,
    **kwargs,
) -> Decision:
    return Decision(
        title=title,
        content="Test decision content for auto-resolve.",
        made_by="agent-1",
        project="test-project",
        confidence=confidence,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# AutoResolveConfig
# ---------------------------------------------------------------------------


class TestAutoResolveConfig:
    def test_defaults(self):
        c = AutoResolveConfig()
        assert c.enabled is True
        assert c.max_severity == "tension"
        assert c.min_confidence == MIN_CONFIDENCE
        assert c.max_blast_radius == MAX_BLAST_RADIUS
        assert c.override_window_hours == OVERRIDE_WINDOW_HOURS

    def test_excluded_dimensions(self):
        c = AutoResolveConfig()
        assert Dimension.SECURITY.value in c.excluded_dimensions
        assert Dimension.AUTH.value in c.excluded_dimensions

    def test_to_dict(self):
        c = AutoResolveConfig()
        d = c.to_dict()
        assert d["enabled"] is True
        assert isinstance(d["excluded_dimensions"], list)


# ---------------------------------------------------------------------------
# AutoResolveCandidate
# ---------------------------------------------------------------------------


class TestAutoResolveCandidate:
    def test_defaults(self):
        c = AutoResolveCandidate()
        assert not c.eligible
        assert c.rejection_reason == ""

    def test_to_dict(self):
        c = AutoResolveCandidate(
            contradiction_id="c1",
            eligible=True,
            confidence=0.92,
        )
        d = c.to_dict()
        assert d["eligible"] is True
        assert d["confidence"] == 0.92


# ---------------------------------------------------------------------------
# AutoResolveLog
# ---------------------------------------------------------------------------


class TestAutoResolveLog:
    def test_empty(self):
        log = AutoResolveLog()
        assert log.total == 0
        assert log.resolved_count == 0
        assert log.rejected_count == 0

    def test_counts(self):
        log = AutoResolveLog(entries=[
            AutoResolveResult(resolved=True),
            AutoResolveResult(resolved=True),
            AutoResolveResult(resolved=False),
        ])
        assert log.total == 3
        assert log.resolved_count == 2
        assert log.rejected_count == 1


# ---------------------------------------------------------------------------
# AutoResolver — evaluate
# ---------------------------------------------------------------------------


class TestAutoResolverEvaluate:
    def test_disabled(self):
        resolver = AutoResolver(AutoResolveConfig(enabled=False))
        decision_a = _make_decision("A", 0.9)
        decision_b = _make_decision("B", 0.8)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
        )
        assert not candidate.eligible
        assert "disabled" in candidate.rejection_reason.lower()

    def test_reject_contradiction_verdict(self):
        resolver = AutoResolver()
        decision_a = _make_decision("A", 0.9)
        decision_b = _make_decision("B", 0.8)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.CONTRADICTION,
            dimensions=["database"],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
        )
        assert not candidate.eligible
        assert "contradiction" in candidate.rejection_reason.lower()

    def test_reject_excluded_dimension(self):
        resolver = AutoResolver()
        decision_a = _make_decision("A", 0.9)
        decision_b = _make_decision("B", 0.8)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=[Dimension.SECURITY.value],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
        )
        assert not candidate.eligible
        assert "excluded" in candidate.rejection_reason.lower()

    def test_reject_low_confidence(self):
        resolver = AutoResolver()
        decision_a = _make_decision("A", 0.9)
        decision_b = _make_decision("B", 0.8)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
            confidence=0.5,
            decision_a=decision_a,
            decision_b=decision_b,
        )
        assert not candidate.eligible
        assert "confidence" in candidate.rejection_reason.lower()

    def test_reject_high_blast_radius(self):
        resolver = AutoResolver()
        decision_a = _make_decision("A", 0.9)
        decision_b = _make_decision("B", 0.8)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
            blast_radius=10,
        )
        assert not candidate.eligible
        assert "blast radius" in candidate.rejection_reason.lower()

    def test_reject_loser_has_dependents(self):
        resolver = AutoResolver()
        decision_a = _make_decision("A", 0.9)
        decision_b = _make_decision("B", 0.7)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
            dependents_of_b=3,
        )
        assert not candidate.eligible
        assert "dependents" in candidate.rejection_reason.lower()

    def test_eligible_candidate(self):
        resolver = AutoResolver()
        decision_a = _make_decision("A", 0.9)
        decision_b = _make_decision("B", 0.7)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
        )
        assert candidate.eligible
        assert candidate.winner_title == "A"
        assert candidate.loser_title == "B"
        assert candidate.reason != ""

    def test_winner_by_confidence(self):
        resolver = AutoResolver()
        decision_a = _make_decision("A", 0.7)
        decision_b = _make_decision("B", 0.95)

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
        )
        assert candidate.eligible
        assert candidate.winner_title == "B"

    def test_winner_by_recency(self):
        from datetime import timedelta, timezone, datetime

        resolver = AutoResolver()
        old_time = datetime.now(timezone.utc) - timedelta(days=10)
        new_time = datetime.now(timezone.utc)

        decision_a = _make_decision("A", 0.85)
        decision_a.created_at = old_time
        decision_b = _make_decision("B", 0.85)
        decision_b.created_at = new_time

        candidate = resolver.evaluate(
            contradiction_id="c1",
            verdict=ContradictionVerdict.TENSION,
            dimensions=["database"],
            confidence=0.9,
            decision_a=decision_a,
            decision_b=decision_b,
        )
        assert candidate.eligible
        assert candidate.winner_title == "B"


# ---------------------------------------------------------------------------
# AutoResolver — resolve
# ---------------------------------------------------------------------------


class TestAutoResolverResolve:
    def test_resolve_eligible(self):
        resolver = AutoResolver()
        candidate = AutoResolveCandidate(
            contradiction_id="c1",
            eligible=True,
            winner_id="w1",
            loser_id="l1",
            reason="test reason",
            confidence=0.9,
            dimensions=["database"],
        )
        result = resolver.resolve(candidate)
        assert result.resolved
        assert result.override_deadline is not None
        assert result.audit_entry["event_type"] == "auto_resolution"

    def test_resolve_ineligible(self):
        resolver = AutoResolver()
        candidate = AutoResolveCandidate(eligible=False)
        result = resolver.resolve(candidate)
        assert not result.resolved

    def test_resolve_logs_entry(self):
        resolver = AutoResolver()
        candidate = AutoResolveCandidate(eligible=True, contradiction_id="c1", confidence=0.9)
        resolver.resolve(candidate)
        assert resolver.log.total == 1
        assert resolver.log.resolved_count == 1

    def test_override_deadline_48_hours(self):
        from datetime import timedelta
        resolver = AutoResolver()
        candidate = AutoResolveCandidate(eligible=True, contradiction_id="c1", confidence=0.9)
        result = resolver.resolve(candidate)
        # Should be ~48 hours from now
        assert result.override_deadline is not None

    def test_clear(self):
        resolver = AutoResolver()
        candidate = AutoResolveCandidate(eligible=True, confidence=0.9)
        resolver.resolve(candidate)
        assert resolver.log.total == 1
        resolver.clear()
        assert resolver.log.total == 0


# ---------------------------------------------------------------------------
# load_config_from_dict
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_load_defaults(self):
        config = load_config_from_dict({})
        assert config.enabled is True
        assert config.min_confidence == MIN_CONFIDENCE

    def test_load_custom(self):
        config = load_config_from_dict({
            "enabled": False,
            "min_confidence": 0.95,
            "max_blast_radius": 5,
        })
        assert config.enabled is False
        assert config.min_confidence == 0.95
        assert config.max_blast_radius == 5

    def test_load_excluded_dimensions(self):
        config = load_config_from_dict({
            "excluded_dimensions": ["security", "auth", "deployment"],
        })
        assert "deployment" in config.excluded_dimensions


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_defaults(self):
        assert MIN_CONFIDENCE == 0.85
        assert MAX_BLAST_RADIUS == 3
        assert OVERRIDE_WINDOW_HOURS == 48
        assert Dimension.SECURITY.value in DEFAULT_EXCLUDED_DIMENSIONS

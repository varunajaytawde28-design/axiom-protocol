"""Tests for dismissal learning and threshold auto-tuning."""

from __future__ import annotations

import pytest

from vt_protocol.decisions.dismissal_learning import (
    DISMISSAL_RATE_THRESHOLD,
    MIN_SAMPLES,
    DimensionStats,
    DismissalRecord,
    ThresholdRecommendation,
    TuningReport,
    compute_dimension_stats,
    generate_threshold_recommendations,
    generate_tuning_report,
)


# ---------------------------------------------------------------------------
# DismissalRecord
# ---------------------------------------------------------------------------


class TestDismissalRecord:
    def test_default_fields(self) -> None:
        r = DismissalRecord()
        assert r.timestamp is not None
        assert r.confidence == 0.0


# ---------------------------------------------------------------------------
# DimensionStats
# ---------------------------------------------------------------------------


class TestDimensionStats:
    def test_dismissal_rate_none(self) -> None:
        s = DimensionStats(dimension="database", total_contradictions=0)
        assert s.dismissal_rate == 0.0

    def test_dismissal_rate_half(self) -> None:
        s = DimensionStats(
            dimension="database",
            total_contradictions=10,
            total_dismissals=5,
        )
        assert s.dismissal_rate == 0.5

    def test_needs_tuning_true(self) -> None:
        s = DimensionStats(
            dimension="database",
            total_contradictions=10,
            total_dismissals=5,  # 50% > 30%, 5 >= MIN_SAMPLES
        )
        assert s.needs_tuning is True

    def test_needs_tuning_false_low_rate(self) -> None:
        s = DimensionStats(
            dimension="database",
            total_contradictions=10,
            total_dismissals=2,  # 20% < 30%
        )
        assert s.needs_tuning is False

    def test_needs_tuning_false_low_samples(self) -> None:
        s = DimensionStats(
            dimension="database",
            total_contradictions=4,
            total_dismissals=4,  # 100% rate but only 4 samples < MIN_SAMPLES
        )
        assert s.needs_tuning is False

    def test_to_dict(self) -> None:
        s = DimensionStats(
            dimension="auth",
            total_contradictions=20,
            total_dismissals=8,
        )
        d = s.to_dict()
        assert d["dimension"] == "auth"
        assert d["dismissal_rate"] == 0.4
        assert d["needs_tuning"] is True


# ---------------------------------------------------------------------------
# compute_dimension_stats
# ---------------------------------------------------------------------------


class TestComputeDimensionStats:
    def test_empty(self) -> None:
        stats = compute_dimension_stats([], {})
        assert stats == {}

    def test_single_dimension(self) -> None:
        dismissals = [
            DismissalRecord(dimension="database", confidence=0.7, nli_score=0.4),
            DismissalRecord(dimension="database", confidence=0.6, nli_score=0.35),
        ]
        stats = compute_dimension_stats(dismissals, {"database": 10})
        assert "database" in stats
        assert stats["database"].total_dismissals == 2
        assert stats["database"].total_contradictions == 10
        assert stats["database"].avg_dismissed_confidence == pytest.approx(0.65, abs=0.01)

    def test_multiple_dimensions(self) -> None:
        dismissals = [
            DismissalRecord(dimension="database", confidence=0.7),
            DismissalRecord(dimension="auth", confidence=0.8),
            DismissalRecord(dimension="auth", confidence=0.6),
        ]
        stats = compute_dimension_stats(
            dismissals,
            {"database": 5, "auth": 10},
        )
        assert stats["database"].total_dismissals == 1
        assert stats["auth"].total_dismissals == 2

    def test_includes_zero_dismissal_dimensions(self) -> None:
        stats = compute_dimension_stats([], {"caching": 5})
        assert "caching" in stats
        assert stats["caching"].total_dismissals == 0
        assert stats["caching"].total_contradictions == 5

    def test_avg_nli_score(self) -> None:
        dismissals = [
            DismissalRecord(dimension="database", nli_score=0.4),
            DismissalRecord(dimension="database", nli_score=0.6),
        ]
        stats = compute_dimension_stats(dismissals, {"database": 10})
        assert stats["database"].avg_dismissed_nli_score == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# generate_threshold_recommendations
# ---------------------------------------------------------------------------


class TestGenerateRecommendations:
    def test_no_recommendations_for_low_dismissal(self) -> None:
        stats = {
            "database": DimensionStats(
                dimension="database",
                total_contradictions=10,
                total_dismissals=1,
            )
        }
        recs = generate_threshold_recommendations(stats)
        assert len(recs) == 0

    def test_recommends_for_high_dismissal(self) -> None:
        stats = {
            "database": DimensionStats(
                dimension="database",
                total_contradictions=10,
                total_dismissals=6,  # 60% > 30%, 6 >= 5
                avg_dismissed_nli_score=0.35,
            )
        }
        recs = generate_threshold_recommendations(stats)
        assert len(recs) == 1
        assert recs[0].dimension == "database"
        assert recs[0].recommended_threshold > 0.3  # Higher than current

    def test_recommended_threshold_based_on_nli(self) -> None:
        stats = {
            "auth": DimensionStats(
                dimension="auth",
                total_contradictions=20,
                total_dismissals=10,
                avg_dismissed_nli_score=0.5,
            )
        }
        recs = generate_threshold_recommendations(stats, current_nli_threshold=0.3)
        assert recs[0].recommended_threshold == pytest.approx(0.6, abs=0.01)

    def test_cap_at_0_8(self) -> None:
        stats = {
            "auth": DimensionStats(
                dimension="auth",
                total_contradictions=20,
                total_dismissals=10,
                avg_dismissed_nli_score=0.9,
            )
        }
        recs = generate_threshold_recommendations(stats)
        assert recs[0].recommended_threshold <= 0.8

    def test_sorted_by_dismissal_rate(self) -> None:
        stats = {
            "database": DimensionStats(
                dimension="database", total_contradictions=10,
                total_dismissals=5, avg_dismissed_nli_score=0.4,
            ),
            "auth": DimensionStats(
                dimension="auth", total_contradictions=10,
                total_dismissals=8, avg_dismissed_nli_score=0.4,
            ),
        }
        recs = generate_threshold_recommendations(stats)
        assert recs[0].dimension == "auth"  # Higher rate first

    def test_min_samples_required(self) -> None:
        stats = {
            "database": DimensionStats(
                dimension="database",
                total_contradictions=8,
                total_dismissals=4,  # 50% rate but < MIN_SAMPLES
            )
        }
        recs = generate_threshold_recommendations(stats)
        assert len(recs) == 0


# ---------------------------------------------------------------------------
# ThresholdRecommendation
# ---------------------------------------------------------------------------


class TestThresholdRecommendation:
    def test_to_dict(self) -> None:
        r = ThresholdRecommendation(
            dimension="database",
            current_threshold=0.3,
            recommended_threshold=0.45,
            reason="High dismissal rate",
            dismissal_rate=0.6,
            sample_count=12,
        )
        d = r.to_dict()
        assert d["dimension"] == "database"
        assert d["recommended_threshold"] == 0.45
        assert d["sample_count"] == 12


# ---------------------------------------------------------------------------
# generate_tuning_report
# ---------------------------------------------------------------------------


class TestGenerateTuningReport:
    def test_empty_report(self) -> None:
        report = generate_tuning_report([], {})
        assert report.total_dismissals == 0
        assert report.recommendations == []

    def test_report_with_data(self) -> None:
        dismissals = [
            DismissalRecord(dimension="database", confidence=0.7, nli_score=0.4)
            for _ in range(6)
        ]
        report = generate_tuning_report(
            dismissals,
            {"database": 10},
        )
        assert report.total_dismissals == 6
        assert report.total_contradictions == 10
        assert report.overall_dismissal_rate == 0.6
        assert len(report.recommendations) == 1

    def test_report_to_dict(self) -> None:
        report = generate_tuning_report([], {"database": 5})
        d = report.to_dict()
        assert "dimension_stats" in d
        assert "recommendations" in d
        assert "total_dismissals" in d

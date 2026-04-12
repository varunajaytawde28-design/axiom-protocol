"""Tests for cross-company intelligence — insights."""

from __future__ import annotations

import pytest

from vt_protocol.intelligence.aggregator import (
    AggregationResult,
    Aggregator,
    DimensionAggregate,
    MIN_ORGS_FOR_AGGREGATION,
    PatternStatistic,
)
from vt_protocol.intelligence.insights import (
    HIGH_ADOPTION_THRESHOLD,
    HIGH_CONTRADICTION_RATE,
    Insight,
    InsightReport,
    generate_insights,
)


def _make_aggregation(
    *,
    total_orgs: int = 10,
    k_met: bool = True,
    patterns: list[PatternStatistic] | None = None,
    dim_aggs: list[DimensionAggregate] | None = None,
) -> AggregationResult:
    return AggregationResult(
        total_orgs=total_orgs,
        total_decisions=100,
        k_anonymity_met=k_met,
        top_patterns=patterns or [],
        dimension_aggregates=dim_aggs or [],
    )


# ---------------------------------------------------------------------------
# Insight
# ---------------------------------------------------------------------------


class TestInsight:
    def test_defaults(self):
        i = Insight()
        assert i.severity == "info"

    def test_to_dict(self):
        i = Insight(insight_type="adoption_gap", title="Test")
        d = i.to_dict()
        assert d["insight_type"] == "adoption_gap"


# ---------------------------------------------------------------------------
# InsightReport
# ---------------------------------------------------------------------------


class TestInsightReport:
    def test_empty(self):
        r = InsightReport()
        assert r.critical_count == 0
        assert r.warning_count == 0

    def test_counts(self):
        r = InsightReport(insights=[
            Insight(severity="critical"),
            Insight(severity="warning"),
            Insight(severity="warning"),
            Insight(severity="info"),
        ])
        assert r.critical_count == 1
        assert r.warning_count == 2

    def test_by_type(self):
        r = InsightReport(insights=[
            Insight(insight_type="adoption_gap"),
            Insight(insight_type="trending"),
            Insight(insight_type="adoption_gap"),
        ])
        assert len(r.by_type("adoption_gap")) == 2

    def test_by_severity(self):
        r = InsightReport(insights=[
            Insight(severity="critical"),
            Insight(severity="info"),
        ])
        assert len(r.by_severity("critical")) == 1

    def test_to_dict(self):
        r = InsightReport(total_orgs_analyzed=10)
        d = r.to_dict()
        assert d["total_orgs_analyzed"] == 10


# ---------------------------------------------------------------------------
# generate_insights
# ---------------------------------------------------------------------------


class TestGenerateInsights:
    def test_k_anonymity_not_met(self):
        agg = _make_aggregation(k_met=False, total_orgs=2)
        report = generate_insights(agg)
        assert len(report.insights) == 1
        assert "insufficient" in report.insights[0].title.lower()

    def test_adoption_gap(self):
        patterns = [
            PatternStatistic(
                pattern_name="technical",
                dimension="database",
                adoption_rate=0.8,
                avg_confidence=0.9,
                org_count=8,
            ),
        ]
        agg = _make_aggregation(patterns=patterns)
        report = generate_insights(agg, org_dimensions=["auth"])  # missing database
        adoption_gaps = report.by_type("adoption_gap")
        assert len(adoption_gaps) >= 1

    def test_no_adoption_gap_when_covered(self):
        patterns = [
            PatternStatistic(
                pattern_name="technical",
                dimension="database",
                adoption_rate=0.8,
            ),
        ]
        agg = _make_aggregation(patterns=patterns)
        report = generate_insights(agg, org_dimensions=["database"])
        adoption_gaps = report.by_type("adoption_gap")
        assert len(adoption_gaps) == 0

    def test_contradiction_hotspot(self):
        dim_aggs = [
            DimensionAggregate(
                dimension="database",
                contradiction_rate=0.4,
                orgs_using=8,
            ),
        ]
        agg = _make_aggregation(dim_aggs=dim_aggs)
        report = generate_insights(agg)
        hotspots = report.by_type("contradiction_hotspot")
        assert len(hotspots) == 1
        assert hotspots[0].severity == "critical"

    def test_trending_pattern(self):
        patterns = [
            PatternStatistic(
                pattern_name="technical",
                dimension="database",
                adoption_rate=0.8,
                avg_confidence=0.9,
            ),
        ]
        agg = _make_aggregation(patterns=patterns)
        report = generate_insights(agg)
        trending = report.by_type("trending")
        assert len(trending) >= 1

    def test_coverage_recommendation(self):
        dim_aggs = [
            DimensionAggregate(
                dimension="logging",
                orgs_using=8,
                total_decisions=40,
            ),
        ]
        agg = _make_aggregation(dim_aggs=dim_aggs)
        report = generate_insights(agg, org_dimensions=["database"])
        recs = report.by_type("recommendation")
        assert len(recs) >= 1

    def test_no_coverage_rec_when_no_org_dims(self):
        dim_aggs = [
            DimensionAggregate(dimension="logging", orgs_using=8),
        ]
        agg = _make_aggregation(dim_aggs=dim_aggs)
        report = generate_insights(agg)
        recs = report.by_type("recommendation")
        assert len(recs) == 0

    def test_report_metadata(self):
        agg = _make_aggregation(total_orgs=10)
        report = generate_insights(agg)
        assert report.total_orgs_analyzed == 10
        assert report.total_decisions_analyzed == 100

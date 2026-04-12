"""Tests for cross-company intelligence — aggregator."""

from __future__ import annotations

import pytest

from vt_protocol.intelligence.aggregator import (
    MIN_ORGS_FOR_AGGREGATION,
    AggregationResult,
    Aggregator,
    DimensionAggregate,
    PatternStatistic,
)


def _org_data(dims: list[str], n_decisions: int = 5) -> dict:
    return {
        "decisions": [
            {
                "dimensions": dims,
                "confidence": 0.8,
                "decision_type": "technical",
            }
            for _ in range(n_decisions)
        ],
    }


# ---------------------------------------------------------------------------
# PatternStatistic
# ---------------------------------------------------------------------------


class TestPatternStatistic:
    def test_to_dict(self):
        p = PatternStatistic(pattern_name="test", adoption_rate=0.75)
        d = p.to_dict()
        assert d["adoption_rate"] == 0.75


# ---------------------------------------------------------------------------
# DimensionAggregate
# ---------------------------------------------------------------------------


class TestDimensionAggregate:
    def test_to_dict(self):
        da = DimensionAggregate(dimension="database", total_decisions=10)
        d = da.to_dict()
        assert d["dimension"] == "database"
        assert d["total_decisions"] == 10


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class TestAggregator:
    def test_empty(self):
        agg = Aggregator()
        assert agg.org_count == 0
        assert not agg.k_anonymity_met

    def test_add_org_data(self):
        agg = Aggregator()
        agg.add_org_data("org-1", _org_data(["database"]))
        assert agg.org_count == 1

    def test_remove_org_data(self):
        agg = Aggregator()
        agg.add_org_data("org-1", _org_data(["database"]))
        assert agg.remove_org_data("org-1") is True
        assert agg.org_count == 0

    def test_remove_nonexistent(self):
        agg = Aggregator()
        assert agg.remove_org_data("nonexistent") is False

    def test_k_anonymity_not_met(self):
        agg = Aggregator()
        for i in range(3):
            agg.add_org_data(f"org-{i}", _org_data(["database"]))
        result = agg.aggregate()
        assert not result.k_anonymity_met
        assert result.total_decisions == 0  # no aggregation when k-anon not met

    def test_k_anonymity_met(self):
        agg = Aggregator()
        for i in range(MIN_ORGS_FOR_AGGREGATION):
            agg.add_org_data(f"org-{i}", _org_data(["database"]))
        assert agg.k_anonymity_met

    def test_aggregate_basic(self):
        agg = Aggregator()
        for i in range(MIN_ORGS_FOR_AGGREGATION):
            agg.add_org_data(f"org-{i}", _org_data(["database"], 3))
        result = agg.aggregate()
        assert result.k_anonymity_met
        assert result.total_decisions == MIN_ORGS_FOR_AGGREGATION * 3
        assert result.total_orgs == MIN_ORGS_FOR_AGGREGATION

    def test_dimension_aggregates(self):
        agg = Aggregator()
        for i in range(MIN_ORGS_FOR_AGGREGATION):
            agg.add_org_data(f"org-{i}", _org_data(["database", "auth"]))
        result = agg.aggregate()
        dims = {da.dimension for da in result.dimension_aggregates}
        assert "database" in dims
        assert "auth" in dims

    def test_top_patterns(self):
        agg = Aggregator()
        for i in range(MIN_ORGS_FOR_AGGREGATION):
            agg.add_org_data(f"org-{i}", _org_data(["database"]))
        result = agg.aggregate()
        assert len(result.top_patterns) > 0

    def test_pattern_adoption_rate(self):
        agg = Aggregator()
        for i in range(MIN_ORGS_FOR_AGGREGATION):
            agg.add_org_data(f"org-{i}", _org_data(["database"]))
        result = agg.aggregate()
        for p in result.top_patterns:
            assert 0.0 <= p.adoption_rate <= 1.0

    def test_multiple_dimensions(self):
        agg = Aggregator()
        for i in range(MIN_ORGS_FOR_AGGREGATION):
            dims = ["database"] if i % 2 == 0 else ["auth"]
            agg.add_org_data(f"org-{i}", _org_data(dims))
        result = agg.aggregate()
        assert len(result.dimension_aggregates) >= 2

    def test_clear(self):
        agg = Aggregator()
        agg.add_org_data("org-1", _org_data(["database"]))
        agg.clear()
        assert agg.org_count == 0

    def test_custom_min_orgs(self):
        agg = Aggregator(min_orgs=2)
        agg.add_org_data("org-1", _org_data(["database"]))
        agg.add_org_data("org-2", _org_data(["database"]))
        assert agg.k_anonymity_met
        result = agg.aggregate()
        assert result.k_anonymity_met

    def test_to_dict(self):
        agg = Aggregator()
        for i in range(MIN_ORGS_FOR_AGGREGATION):
            agg.add_org_data(f"org-{i}", _org_data(["database"]))
        result = agg.aggregate()
        d = result.to_dict()
        assert "total_orgs" in d
        assert "k_anonymity_met" in d

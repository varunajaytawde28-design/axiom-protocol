"""Cross-company architectural intelligence — insights.

Generate actionable recommendations from aggregated governance data.
Dashboard-ready insights: adoption gaps, contradiction hotspots,
trending patterns.

From SPEC Sprint 23: "Cross-company architectural intelligence — insights."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from vt_protocol.intelligence.aggregator import AggregationResult, PatternStatistic

logger = logging.getLogger(__name__)

# Thresholds for generating insights
HIGH_ADOPTION_THRESHOLD = 0.7  # 70% of orgs use this
LOW_ADOPTION_THRESHOLD = 0.2   # below 20% is uncommon
HIGH_CONTRADICTION_RATE = 0.3  # 30%+ contradiction rate is a hotspot


@dataclass
class Insight:
    """A single actionable insight from aggregated data."""

    insight_type: str = ""  # "adoption_gap", "contradiction_hotspot", "trending", "recommendation"
    severity: str = "info"  # "info", "warning", "critical"
    title: str = ""
    description: str = ""
    dimension: str = ""
    metric_value: float = 0.0
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "insight_type": self.insight_type,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "dimension": self.dimension,
            "metric_value": round(self.metric_value, 4),
            "recommendation": self.recommendation,
        }


@dataclass
class InsightReport:
    """Collection of insights from aggregated governance data."""

    insights: list[Insight] = field(default_factory=list)
    total_orgs_analyzed: int = 0
    total_decisions_analyzed: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.insights if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.insights if i.severity == "warning")

    def by_type(self, insight_type: str) -> list[Insight]:
        return [i for i in self.insights if i.insight_type == insight_type]

    def by_severity(self, severity: str) -> list[Insight]:
        return [i for i in self.insights if i.severity == severity]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_orgs_analyzed": self.total_orgs_analyzed,
            "total_decisions_analyzed": self.total_decisions_analyzed,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "total_insights": len(self.insights),
            "insights": [i.to_dict() for i in self.insights],
        }


def generate_insights(
    aggregation: AggregationResult,
    *,
    org_dimensions: list[str] | None = None,
) -> InsightReport:
    """Generate insights from aggregated governance data.

    Args:
        aggregation: The aggregated result from the Aggregator.
        org_dimensions: Optional list of the requesting org's active dimensions.
                       Used to generate adoption gap insights.
    """
    report = InsightReport(
        total_orgs_analyzed=aggregation.total_orgs,
        total_decisions_analyzed=aggregation.total_decisions,
    )

    if not aggregation.k_anonymity_met:
        report.insights.append(Insight(
            insight_type="warning",
            severity="info",
            title="Insufficient data for insights",
            description=f"Only {aggregation.total_orgs} organizations — need more for reliable insights.",
            recommendation="Collect data from more organizations before drawing conclusions.",
        ))
        return report

    # 1. Adoption gap insights
    _generate_adoption_insights(report, aggregation, org_dimensions or [])

    # 2. Contradiction hotspot insights
    _generate_contradiction_insights(report, aggregation)

    # 3. Trending pattern insights
    _generate_trending_insights(report, aggregation)

    # 4. Dimension coverage insights
    _generate_coverage_insights(report, aggregation, org_dimensions or [])

    return report


def _generate_adoption_insights(
    report: InsightReport,
    aggregation: AggregationResult,
    org_dimensions: list[str],
) -> None:
    """Find patterns widely adopted elsewhere but missing from the org."""
    for pattern in aggregation.top_patterns:
        if pattern.adoption_rate >= HIGH_ADOPTION_THRESHOLD:
            if org_dimensions and pattern.dimension not in org_dimensions:
                report.insights.append(Insight(
                    insight_type="adoption_gap",
                    severity="warning",
                    title=f"High adoption pattern missing: {pattern.dimension}",
                    description=(
                        f"{pattern.adoption_rate:.0%} of organizations use "
                        f"'{pattern.pattern_name}' for '{pattern.dimension}', "
                        f"but it's not in your governance config."
                    ),
                    dimension=pattern.dimension,
                    metric_value=pattern.adoption_rate,
                    recommendation=f"Consider adding '{pattern.dimension}' governance rules.",
                ))


def _generate_contradiction_insights(
    report: InsightReport,
    aggregation: AggregationResult,
) -> None:
    """Flag dimensions with high contradiction rates."""
    for dim_agg in aggregation.dimension_aggregates:
        if dim_agg.contradiction_rate >= HIGH_CONTRADICTION_RATE:
            report.insights.append(Insight(
                insight_type="contradiction_hotspot",
                severity="critical",
                title=f"Contradiction hotspot: {dim_agg.dimension}",
                description=(
                    f"The '{dim_agg.dimension}' dimension has a "
                    f"{dim_agg.contradiction_rate:.0%} contradiction rate across "
                    f"{dim_agg.orgs_using} organizations."
                ),
                dimension=dim_agg.dimension,
                metric_value=dim_agg.contradiction_rate,
                recommendation=(
                    f"Review '{dim_agg.dimension}' decisions for clarity. "
                    f"Consider adding guardrails or stricter templates."
                ),
            ))


def _generate_trending_insights(
    report: InsightReport,
    aggregation: AggregationResult,
) -> None:
    """Identify trending patterns (high adoption + high confidence)."""
    for pattern in aggregation.top_patterns[:5]:
        if pattern.adoption_rate >= HIGH_ADOPTION_THRESHOLD and pattern.avg_confidence >= 0.8:
            report.insights.append(Insight(
                insight_type="trending",
                severity="info",
                title=f"Trending pattern: {pattern.pattern_name} in {pattern.dimension}",
                description=(
                    f"'{pattern.pattern_name}' for '{pattern.dimension}' is used by "
                    f"{pattern.adoption_rate:.0%} of orgs with {pattern.avg_confidence:.0%} avg confidence."
                ),
                dimension=pattern.dimension,
                metric_value=pattern.adoption_rate,
                recommendation=f"This is a well-established pattern worth considering.",
            ))


def _generate_coverage_insights(
    report: InsightReport,
    aggregation: AggregationResult,
    org_dimensions: list[str],
) -> None:
    """Check dimension coverage compared to industry norms."""
    if not org_dimensions:
        return

    # Find dimensions common in industry but missing from org
    industry_dims = {
        da.dimension for da in aggregation.dimension_aggregates
        if da.orgs_using >= aggregation.total_orgs * 0.5
    }
    org_dim_set = set(org_dimensions)
    missing = industry_dims - org_dim_set

    for dim in sorted(missing):
        report.insights.append(Insight(
            insight_type="recommendation",
            severity="info",
            title=f"Missing common dimension: {dim}",
            description=(
                f"Most organizations govern the '{dim}' dimension, "
                f"but it's missing from your configuration."
            ),
            dimension=dim,
            recommendation=f"Add governance rules for '{dim}'.",
        ))

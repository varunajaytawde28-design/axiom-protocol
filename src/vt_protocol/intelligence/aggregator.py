"""Cross-company architectural intelligence — aggregator.

Aggregate anonymized governance data from multiple organizations
to derive industry-wide architectural patterns and statistics.

From SPEC Sprint 23: "Cross-company architectural intelligence — aggregator."
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Minimum orgs required for any aggregation (k-anonymity)
MIN_ORGS_FOR_AGGREGATION = 5


@dataclass
class PatternStatistic:
    """A statistical observation about an architectural pattern."""

    pattern_name: str = ""
    dimension: str = ""
    occurrence_count: int = 0
    org_count: int = 0  # number of distinct orgs using this pattern
    adoption_rate: float = 0.0  # fraction of orgs using this
    avg_confidence: float = 0.0
    contradiction_rate: float = 0.0  # how often it leads to contradictions

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_name": self.pattern_name,
            "dimension": self.dimension,
            "occurrence_count": self.occurrence_count,
            "org_count": self.org_count,
            "adoption_rate": round(self.adoption_rate, 4),
            "avg_confidence": round(self.avg_confidence, 4),
            "contradiction_rate": round(self.contradiction_rate, 4),
        }


@dataclass
class DimensionAggregate:
    """Aggregated statistics for a single dimension."""

    dimension: str = ""
    total_decisions: int = 0
    orgs_using: int = 0
    avg_decisions_per_org: float = 0.0
    contradiction_rate: float = 0.0
    top_patterns: list[PatternStatistic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "total_decisions": self.total_decisions,
            "orgs_using": self.orgs_using,
            "avg_decisions_per_org": round(self.avg_decisions_per_org, 2),
            "contradiction_rate": round(self.contradiction_rate, 4),
            "top_patterns": [p.to_dict() for p in self.top_patterns],
        }


@dataclass
class AggregationResult:
    """Result of aggregating governance data across organizations."""

    total_orgs: int = 0
    total_decisions: int = 0
    dimension_aggregates: list[DimensionAggregate] = field(default_factory=list)
    top_patterns: list[PatternStatistic] = field(default_factory=list)
    k_anonymity_met: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_orgs": self.total_orgs,
            "total_decisions": self.total_decisions,
            "k_anonymity_met": self.k_anonymity_met,
            "dimension_aggregates": [d.to_dict() for d in self.dimension_aggregates],
            "top_patterns": [p.to_dict() for p in self.top_patterns],
        }


class Aggregator:
    """Aggregate anonymized governance data from multiple organizations."""

    def __init__(self, *, min_orgs: int = MIN_ORGS_FOR_AGGREGATION) -> None:
        self._min_orgs = min_orgs
        self._org_data: dict[str, dict[str, Any]] = {}

    @property
    def org_count(self) -> int:
        return len(self._org_data)

    @property
    def k_anonymity_met(self) -> bool:
        return self.org_count >= self._min_orgs

    def add_org_data(self, org_id: str, data: dict[str, Any]) -> None:
        """Add anonymized governance data from an organization."""
        self._org_data[org_id] = data

    def remove_org_data(self, org_id: str) -> bool:
        """Remove an org's data."""
        if org_id in self._org_data:
            del self._org_data[org_id]
            return True
        return False

    def aggregate(self) -> AggregationResult:
        """Aggregate all org data into statistics."""
        result = AggregationResult(total_orgs=self.org_count)
        result.k_anonymity_met = self.k_anonymity_met

        if not self.k_anonymity_met:
            logger.warning(
                "K-anonymity not met: %d orgs < %d minimum",
                self.org_count, self._min_orgs,
            )
            return result

        # Collect all decisions
        all_decisions: list[dict[str, Any]] = []
        org_decisions: dict[str, list[dict[str, Any]]] = {}

        for org_id, data in self._org_data.items():
            decisions = data.get("decisions", [])
            all_decisions.extend(decisions)
            org_decisions[org_id] = decisions

        result.total_decisions = len(all_decisions)

        # Aggregate by dimension
        dim_stats = self._aggregate_dimensions(all_decisions, org_decisions)
        result.dimension_aggregates = dim_stats

        # Top patterns across all dimensions
        result.top_patterns = self._extract_top_patterns(all_decisions, org_decisions)

        return result

    def _aggregate_dimensions(
        self,
        all_decisions: list[dict[str, Any]],
        org_decisions: dict[str, list[dict[str, Any]]],
    ) -> list[DimensionAggregate]:
        """Compute per-dimension statistics."""
        # Count decisions per dimension
        dim_counts: dict[str, int] = {}
        dim_orgs: dict[str, set[str]] = {}
        dim_confidences: dict[str, list[float]] = {}

        for org_id, decisions in org_decisions.items():
            for d in decisions:
                for dim in d.get("dimensions", []):
                    dim_counts[dim] = dim_counts.get(dim, 0) + 1
                    dim_orgs.setdefault(dim, set()).add(org_id)
                    conf = d.get("confidence", 0.0)
                    dim_confidences.setdefault(dim, []).append(conf)

        aggregates: list[DimensionAggregate] = []
        for dim in sorted(dim_counts.keys()):
            orgs_using = len(dim_orgs.get(dim, set()))
            total = dim_counts[dim]
            avg_per_org = total / max(orgs_using, 1)

            aggregates.append(DimensionAggregate(
                dimension=dim,
                total_decisions=total,
                orgs_using=orgs_using,
                avg_decisions_per_org=avg_per_org,
            ))

        return aggregates

    def _extract_top_patterns(
        self,
        all_decisions: list[dict[str, Any]],
        org_decisions: dict[str, list[dict[str, Any]]],
    ) -> list[PatternStatistic]:
        """Extract most common architectural patterns."""
        # Group by decision_type + first dimension
        pattern_counts: dict[str, int] = {}
        pattern_orgs: dict[str, set[str]] = {}
        pattern_confidences: dict[str, list[float]] = {}

        for org_id, decisions in org_decisions.items():
            for d in decisions:
                dtype = d.get("decision_type", "unknown")
                dims = d.get("dimensions", [])
                dim = dims[0] if dims else "general"
                pattern_key = f"{dtype}:{dim}"

                pattern_counts[pattern_key] = pattern_counts.get(pattern_key, 0) + 1
                pattern_orgs.setdefault(pattern_key, set()).add(org_id)
                pattern_confidences.setdefault(pattern_key, []).append(
                    d.get("confidence", 0.0)
                )

        total_orgs = len(org_decisions)
        patterns: list[PatternStatistic] = []
        for key in sorted(pattern_counts, key=lambda k: pattern_counts[k], reverse=True):
            parts = key.split(":", 1)
            orgs = len(pattern_orgs.get(key, set()))
            confs = pattern_confidences.get(key, [])
            avg_conf = sum(confs) / max(len(confs), 1)

            patterns.append(PatternStatistic(
                pattern_name=parts[0],
                dimension=parts[1] if len(parts) > 1 else "",
                occurrence_count=pattern_counts[key],
                org_count=orgs,
                adoption_rate=orgs / max(total_orgs, 1),
                avg_confidence=avg_conf,
            ))

        return patterns[:20]  # top 20

    def clear(self) -> None:
        """Clear all org data."""
        self._org_data.clear()

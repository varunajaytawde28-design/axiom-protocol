"""Learn from dismissals — auto-tune thresholds per dimension.

When users dismiss contradictions as false positives, the system learns:
  - Which dimensions produce the most false positives
  - What confidence levels are unreliable
  - Which decision pairs are frequently dismissed

Over time, this adjusts NLI thresholds per dimension so the system
becomes more precise for each project's specific patterns.

Algorithm:
  1. Track dismissals per dimension with confidence levels
  2. Compute dismissal rate per dimension
  3. If dismissal rate > threshold, recommend raising NLI cutoff for that dimension
  4. Generate "threshold tuning report" for the tech lead
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# If dismissal rate exceeds this, recommend threshold adjustment
DISMISSAL_RATE_THRESHOLD = 0.3

# Minimum samples needed before making recommendations
MIN_SAMPLES = 5


@dataclass
class DismissalRecord:
    """A recorded dismissal of a contradiction as false positive."""

    contradiction_id: str = ""
    dimension: str = ""
    confidence: float = 0.0
    nli_score: float = 0.0
    dismissed_by: str = ""
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    reason: str = ""


@dataclass
class DimensionStats:
    """Aggregated dismissal statistics for a single dimension."""

    dimension: str
    total_contradictions: int = 0
    total_dismissals: int = 0
    avg_dismissed_confidence: float = 0.0
    avg_dismissed_nli_score: float = 0.0

    @property
    def dismissal_rate(self) -> float:
        if self.total_contradictions == 0:
            return 0.0
        return self.total_dismissals / self.total_contradictions

    @property
    def needs_tuning(self) -> bool:
        return (
            self.total_dismissals >= MIN_SAMPLES
            and self.dismissal_rate > DISMISSAL_RATE_THRESHOLD
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "total_contradictions": self.total_contradictions,
            "total_dismissals": self.total_dismissals,
            "dismissal_rate": round(self.dismissal_rate, 4),
            "avg_dismissed_confidence": round(self.avg_dismissed_confidence, 4),
            "avg_dismissed_nli_score": round(self.avg_dismissed_nli_score, 4),
            "needs_tuning": self.needs_tuning,
        }


@dataclass
class ThresholdRecommendation:
    """Recommended threshold adjustment for a dimension."""

    dimension: str
    current_threshold: float
    recommended_threshold: float
    reason: str
    dismissal_rate: float
    sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "current_threshold": round(self.current_threshold, 4),
            "recommended_threshold": round(self.recommended_threshold, 4),
            "reason": self.reason,
            "dismissal_rate": round(self.dismissal_rate, 4),
            "sample_count": self.sample_count,
        }


def compute_dimension_stats(
    dismissals: list[DismissalRecord],
    total_per_dimension: dict[str, int],
) -> dict[str, DimensionStats]:
    """Compute dismissal statistics per dimension.

    Args:
        dismissals: List of dismissal records
        total_per_dimension: Total contradictions per dimension (including non-dismissed)

    Returns: Dict mapping dimension → stats
    """
    dim_dismissals: dict[str, list[DismissalRecord]] = defaultdict(list)
    for d in dismissals:
        dim_dismissals[d.dimension].append(d)

    stats: dict[str, DimensionStats] = {}
    for dim, records in dim_dismissals.items():
        confidences = [r.confidence for r in records]
        nli_scores = [r.nli_score for r in records if r.nli_score > 0]

        stats[dim] = DimensionStats(
            dimension=dim,
            total_contradictions=total_per_dimension.get(dim, len(records)),
            total_dismissals=len(records),
            avg_dismissed_confidence=sum(confidences) / len(confidences) if confidences else 0.0,
            avg_dismissed_nli_score=sum(nli_scores) / len(nli_scores) if nli_scores else 0.0,
        )

    # Include dimensions with contradictions but no dismissals
    for dim, total in total_per_dimension.items():
        if dim not in stats:
            stats[dim] = DimensionStats(
                dimension=dim,
                total_contradictions=total,
            )

    return stats


def generate_threshold_recommendations(
    stats: dict[str, DimensionStats],
    *,
    current_nli_threshold: float = 0.3,
    min_samples: int = MIN_SAMPLES,
) -> list[ThresholdRecommendation]:
    """Generate NLI threshold adjustment recommendations.

    For dimensions with high dismissal rates:
    - Recommend raising the NLI threshold to filter more aggressively
    - The recommended threshold is based on avg dismissed NLI score
    """
    recommendations: list[ThresholdRecommendation] = []

    for dim, dim_stats in stats.items():
        if dim_stats.total_dismissals < min_samples:
            continue

        if dim_stats.dismissal_rate <= DISMISSAL_RATE_THRESHOLD:
            continue

        # Recommend threshold slightly above average dismissed NLI score
        if dim_stats.avg_dismissed_nli_score > 0:
            recommended = min(
                0.8,  # Never go above 0.8
                max(
                    current_nli_threshold,
                    dim_stats.avg_dismissed_nli_score + 0.1,
                ),
            )
        else:
            # Fallback: bump by 0.1
            recommended = min(0.8, current_nli_threshold + 0.1)

        recommendations.append(ThresholdRecommendation(
            dimension=dim,
            current_threshold=current_nli_threshold,
            recommended_threshold=recommended,
            reason=(
                f"Dimension '{dim}' has {dim_stats.dismissal_rate:.0%} "
                f"dismissal rate ({dim_stats.total_dismissals}/{dim_stats.total_contradictions}). "
                f"Consider raising NLI threshold to reduce false positives."
            ),
            dismissal_rate=dim_stats.dismissal_rate,
            sample_count=dim_stats.total_dismissals,
        ))

    return sorted(recommendations, key=lambda r: r.dismissal_rate, reverse=True)


@dataclass
class TuningReport:
    """Complete threshold tuning report for the tech lead."""

    dimension_stats: dict[str, DimensionStats] = field(default_factory=dict)
    recommendations: list[ThresholdRecommendation] = field(default_factory=list)
    total_dismissals: int = 0
    total_contradictions: int = 0
    overall_dismissal_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_stats": {
                k: v.to_dict() for k, v in self.dimension_stats.items()
            },
            "recommendations": [r.to_dict() for r in self.recommendations],
            "total_dismissals": self.total_dismissals,
            "total_contradictions": self.total_contradictions,
            "overall_dismissal_rate": round(self.overall_dismissal_rate, 4),
        }


def generate_tuning_report(
    dismissals: list[DismissalRecord],
    total_per_dimension: dict[str, int],
    *,
    current_nli_threshold: float = 0.3,
) -> TuningReport:
    """Generate a complete threshold tuning report."""
    stats = compute_dimension_stats(dismissals, total_per_dimension)
    recommendations = generate_threshold_recommendations(
        stats, current_nli_threshold=current_nli_threshold,
    )

    total_d = len(dismissals)
    total_c = sum(total_per_dimension.values()) or 1

    return TuningReport(
        dimension_stats=stats,
        recommendations=recommendations,
        total_dismissals=total_d,
        total_contradictions=total_c,
        overall_dismissal_rate=total_d / total_c,
    )

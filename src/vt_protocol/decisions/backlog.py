"""Snyk-style backlog trickle — prioritized contradiction queue.

Instead of presenting all contradictions at once (big-bang remediation),
we surface ONE auto-fix PR per sprint targeting the highest-priority
resolvable contradiction.

Priority scoring considers:
  - Confidence (higher = more certain it's real)
  - Impact (number of related decisions/dimensions affected)
  - Age (older unresolved contradictions rank higher)
  - Severity (contradictions > tensions)

The trickle queue feeds into:
  - Dashboard "Next to fix" widget
  - CLI `vt next` command
  - GitHub Action for auto-fix PRs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
)

logger = logging.getLogger(__name__)

# Scoring weights
WEIGHT_CONFIDENCE = 0.3
WEIGHT_IMPACT = 0.25
WEIGHT_AGE = 0.25
WEIGHT_SEVERITY = 0.2

# Maximum age in days for age scoring (older than this caps at 1.0)
MAX_AGE_DAYS = 90


@dataclass
class PrioritizedContradiction:
    """A contradiction with its computed priority score."""

    contradiction: Contradiction
    priority_score: float
    impact_count: int = 0
    age_days: float = 0.0
    scoring_breakdown: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": str(self.contradiction.id),
            "decision_a_title": self.contradiction.decision_a_title,
            "decision_b_title": self.contradiction.decision_b_title,
            "verdict": self.contradiction.verdict.value,
            "confidence": self.contradiction.confidence,
            "priority_score": round(self.priority_score, 4),
            "impact_count": self.impact_count,
            "age_days": round(self.age_days, 1),
            "scoring_breakdown": {
                k: round(v, 4) for k, v in self.scoring_breakdown.items()
            },
        }


def prioritize_backlog(
    contradictions: list[Contradiction],
    decisions: list[Decision],
    *,
    now: datetime | None = None,
) -> list[PrioritizedContradiction]:
    """Sort contradictions by priority for trickle processing.

    Only includes actionable contradictions (unresolved, non-baseline,
    genuine contradictions or tensions).

    Returns sorted list (highest priority first).
    """
    now = now or datetime.now(timezone.utc)

    actionable = [
        c for c in contradictions
        if c.status == ContradictionStatus.UNRESOLVED
        and not c.is_baseline
        and c.verdict != ContradictionVerdict.COMPATIBLE
    ]

    scored: list[PrioritizedContradiction] = []
    for c in actionable:
        impact = _compute_impact(c, decisions)
        age = _compute_age(c, now)
        severity = _severity_score(c)

        breakdown = {
            "confidence": c.confidence * WEIGHT_CONFIDENCE,
            "impact": impact * WEIGHT_IMPACT,
            "age": age * WEIGHT_AGE,
            "severity": severity * WEIGHT_SEVERITY,
        }
        total = sum(breakdown.values())

        age_days = (now - c.detected_at).total_seconds() / 86400

        scored.append(PrioritizedContradiction(
            contradiction=c,
            priority_score=total,
            impact_count=_count_affected(c, decisions),
            age_days=age_days,
            scoring_breakdown=breakdown,
        ))

    scored.sort(key=lambda p: p.priority_score, reverse=True)
    return scored


def get_next_fix(
    contradictions: list[Contradiction],
    decisions: list[Decision],
) -> PrioritizedContradiction | None:
    """Get the single highest-priority contradiction to fix next.

    This is the 'trickle' — one fix per sprint, not big-bang.
    """
    backlog = prioritize_backlog(contradictions, decisions)
    return backlog[0] if backlog else None


def _compute_impact(c: Contradiction, decisions: list[Decision]) -> float:
    """Impact score based on how many decisions are affected.

    Looks at shared dimensions between the contradicting decisions
    and counts how many other active decisions share those dimensions.
    """
    affected = _count_affected(c, decisions)
    # Normalize: 5+ affected = max impact
    return min(1.0, affected / 5)


def _count_affected(c: Contradiction, decisions: list[Decision]) -> int:
    """Count decisions sharing dimensions with the contradiction pair."""
    dims = set(c.shared_dimensions)
    if not dims:
        return 0

    count = 0
    pair_ids = {c.decision_a_id, c.decision_b_id}
    for d in decisions:
        if d.id in pair_ids or not d.valid:
            continue
        if set(d.dimensions) & dims:
            count += 1
    return count


def _compute_age(c: Contradiction, now: datetime) -> float:
    """Age score — older unresolved contradictions rank higher."""
    age_seconds = (now - c.detected_at).total_seconds()
    age_days = age_seconds / 86400
    return min(1.0, age_days / MAX_AGE_DAYS)


def _severity_score(c: Contradiction) -> float:
    """Severity score based on verdict type."""
    if c.verdict == ContradictionVerdict.CONTRADICTION:
        return 1.0
    if c.verdict == ContradictionVerdict.TENSION:
        return 0.5
    return 0.0

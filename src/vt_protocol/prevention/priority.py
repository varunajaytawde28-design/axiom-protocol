"""Priority scoring for decision → rule selection.

Scores each decision to determine which rules make it into each tier of
the three-tier context injection system:
  - Always: top 15-20 rules, always injected
  - Auto-attached: glob-matched rules, activated by file patterns
  - On-demand: available via MCP query only

Score = violation_frequency × severity × recency × file_relevance

From SPEC: "Priority scoring: violation frequency × severity × recency ×
file relevance. Respect ~100-150 instruction budget."
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from vt_protocol.decisions.models import Decision, DecisionStatus, DecisionType, Dimension


# Severity weights by decision type
_SEVERITY: dict[DecisionType, float] = {
    DecisionType.CONSTRAINT: 1.0,
    DecisionType.ARCHITECTURAL: 0.9,
    DecisionType.TECHNICAL: 0.7,
    DecisionType.PRODUCT: 0.5,
}

# Dimension → glob patterns for auto-attach matching
DIMENSION_GLOBS: dict[Dimension, list[str]] = {
    Dimension.DATABASE: ["**/models/**", "**/db/**", "**/migration*/**", "**/schema*"],
    Dimension.AUTH: ["**/auth/**", "**/login*", "**/session*", "**/jwt*"],
    Dimension.CACHING: ["**/cache/**", "**/redis*"],
    Dimension.API_STYLE: ["**/api/**", "**/routes/**", "**/endpoints/**", "**/views/**"],
    Dimension.DEPLOYMENT: ["**/deploy/**", "**/infra/**", "Dockerfile*", "docker-compose*",
                           "**/k8s/**", "*.tf"],
    Dimension.CONCURRENCY: ["**/workers/**", "**/tasks/**", "**/async*"],
    Dimension.LOGGING: ["**/logging*", "**/monitor*", "**/metrics*"],
    Dimension.TESTING: ["**/test*/**", "**/spec/**", "**/fixtures/**"],
    Dimension.ERROR_HANDLING: ["**/errors/**", "**/exceptions*"],
    Dimension.STATE_MANAGEMENT: ["**/store/**", "**/state/**"],
    Dimension.MESSAGING: ["**/events/**", "**/queue*", "**/pubsub*"],
    Dimension.SECURITY: ["**/security/**", "**/encrypt*", "**/secrets*"],
}


@dataclass
class ScoredDecision:
    """A decision with its computed priority score and tier assignment."""

    decision: Decision
    score: float
    tier: str  # "always" | "auto" | "on-demand"
    globs: list[str] = field(default_factory=list)


def score_decision(
    decision: Decision,
    *,
    violation_count: int = 0,
    now: datetime | None = None,
) -> float:
    """Compute priority score for a single decision.

    score = severity × recency × (1 + log(1 + violations)) × confidence
    """
    now = now or datetime.now(timezone.utc)

    severity = _SEVERITY.get(decision.decision_type, 0.5)

    age_days = max(1, (now - decision.created_at).total_seconds() / 86400)
    recency = 1.0 / (1.0 + math.log(age_days))

    violation_boost = 1.0 + math.log(1 + violation_count)

    return severity * recency * violation_boost * decision.confidence


def assign_tiers(
    decisions: list[Decision],
    *,
    always_count: int = 15,
    auto_count: int = 50,
    violation_counts: dict[str, int] | None = None,
) -> list[ScoredDecision]:
    """Score all decisions and assign to tiers.

    Args:
        decisions: Active decisions to score.
        always_count: Max decisions in the "always" tier.
        auto_count: Max decisions in the "auto" tier.
        violation_counts: Map of decision title → violation count.

    Returns:
        List of ScoredDecision sorted by score descending.
    """
    violation_counts = violation_counts or {}
    scored: list[ScoredDecision] = []

    for d in decisions:
        if d.status != DecisionStatus.ACTIVE:
            continue
        s = score_decision(d, violation_count=violation_counts.get(d.title, 0))

        # Collect glob patterns from dimensions
        globs: list[str] = []
        for dim in d.dimensions:
            globs.extend(DIMENSION_GLOBS.get(dim, []))

        scored.append(ScoredDecision(decision=d, score=s, tier="on-demand", globs=globs))

    scored.sort(key=lambda x: x.score, reverse=True)

    for i, sd in enumerate(scored):
        if i < always_count:
            sd.tier = "always"
        elif i < always_count + auto_count and sd.globs:
            sd.tier = "auto"
        # else: remains "on-demand"

    return scored


def decisions_for_file(scored: list[ScoredDecision], file_path: str) -> list[ScoredDecision]:
    """Return decisions relevant to a specific file path.

    Includes all "always" tier plus "auto" tier decisions whose globs match.
    """
    from fnmatch import fnmatch

    result: list[ScoredDecision] = []
    for sd in scored:
        if sd.tier == "always":
            result.append(sd)
        elif sd.tier == "auto":
            if any(fnmatch(file_path, g) for g in sd.globs):
                result.append(sd)
    return result

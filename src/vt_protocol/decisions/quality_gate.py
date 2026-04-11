"""Architecture quality gates — binary pass/fail checks.

Two conditions for Phase 2:
  1. No new unresolved contradictions (above baseline)
  2. All new decisions have required metadata (title, dimensions, rationale)

Callable from CLI (`smm gate`) and CI pipelines.
Exit code 0 = pass, 1 = fail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class GateViolation:
    """A single quality gate violation."""

    rule: str
    severity: str  # "error" or "warning"
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
    """Result of running all quality gate checks."""

    passed: bool
    violations: list[GateViolation] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def errors(self) -> list[GateViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[GateViolation]:
        return [v for v in self.violations if v.severity == "warning"]


def run_quality_gate(
    decisions: list[Decision],
    contradictions: list[Contradiction],
    *,
    baseline_contradiction_ids: set[str] | None = None,
    require_rationale: bool = True,
    require_dimensions: bool = True,
) -> GateResult:
    """Run all quality gate checks and return pass/fail result.

    Args:
        decisions: All decisions in the project.
        contradictions: All contradictions in the project.
        baseline_contradiction_ids: IDs of contradictions frozen at adoption.
        require_rationale: Whether new decisions must have rationale.
        require_dimensions: Whether new decisions must have dimensions.

    Returns:
        GateResult with pass/fail and violation details.
    """
    violations: list[GateViolation] = []
    checks_run = 0
    checks_passed = 0

    # Check 1: No new unresolved contradictions
    checks_run += 1
    new_violations = check_no_new_contradictions(
        contradictions,
        baseline_ids=baseline_contradiction_ids,
    )
    if new_violations:
        violations.extend(new_violations)
    else:
        checks_passed += 1

    # Check 2: All new decisions have required metadata
    checks_run += 1
    metadata_violations = check_decision_metadata(
        decisions,
        require_rationale=require_rationale,
        require_dimensions=require_dimensions,
    )
    if metadata_violations:
        violations.extend(metadata_violations)
    else:
        checks_passed += 1

    passed = not any(v.severity == "error" for v in violations)

    return GateResult(
        passed=passed,
        violations=violations,
        checks_run=checks_run,
        checks_passed=checks_passed,
    )


def check_no_new_contradictions(
    contradictions: list[Contradiction],
    *,
    baseline_ids: set[str] | None = None,
) -> list[GateViolation]:
    """Gate check: no new unresolved contradictions above baseline.

    Baseline contradictions (frozen at adoption via freeze-on-adopt)
    are excluded. Only NEW actionable contradictions fail the gate.
    """
    baseline = baseline_ids or set()
    violations: list[GateViolation] = []

    for c in contradictions:
        # Skip baseline contradictions
        if str(c.id) in baseline or c.is_baseline:
            continue

        # Only fail on actionable (unresolved + contradiction verdict)
        if c.is_actionable:
            violations.append(GateViolation(
                rule="no-new-contradictions",
                severity="error",
                message=(
                    f"Unresolved contradiction: {c.decision_a_title} "
                    f"vs {c.decision_b_title} (confidence: {c.confidence:.0%})"
                ),
                details={
                    "contradiction_id": str(c.id),
                    "decision_a_id": str(c.decision_a_id),
                    "decision_b_id": str(c.decision_b_id),
                    "verdict": c.verdict.value,
                    "confidence": c.confidence,
                },
            ))

    return violations


def check_decision_metadata(
    decisions: list[Decision],
    *,
    require_rationale: bool = True,
    require_dimensions: bool = True,
) -> list[GateViolation]:
    """Gate check: all active decisions have required metadata.

    Required:
      - title (always, enforced by model)
      - dimensions (at least one)
      - rationale (non-empty)
    """
    violations: list[GateViolation] = []

    for d in decisions:
        if d.status != DecisionStatus.ACTIVE:
            continue

        if require_dimensions and not d.dimensions:
            violations.append(GateViolation(
                rule="decision-metadata",
                severity="error",
                message=f"Decision '{d.title}' has no dimensions tagged",
                details={
                    "decision_id": str(d.id),
                    "field": "dimensions",
                },
            ))

        if require_rationale and not d.rationale.strip():
            violations.append(GateViolation(
                rule="decision-metadata",
                severity="warning",
                message=f"Decision '{d.title}' has no rationale",
                details={
                    "decision_id": str(d.id),
                    "field": "rationale",
                },
            ))

    return violations

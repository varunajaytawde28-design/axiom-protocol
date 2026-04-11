"""Tests for architecture quality gates."""

from __future__ import annotations

from uuid import uuid4

import pytest

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.decisions.quality_gate import (
    GateResult,
    GateViolation,
    check_decision_metadata,
    check_no_new_contradictions,
    run_quality_gate,
)


def _decision(
    title: str = "Test Decision",
    *,
    dims: list[Dimension] | None = None,
    rationale: str = "Good rationale",
    status: DecisionStatus = DecisionStatus.ACTIVE,
) -> Decision:
    return Decision(
        title=title,
        content="Content for testing",
        rationale=rationale,
        decision_type=DecisionType.TECHNICAL,
        dimensions=dims if dims is not None else [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
        status=status,
    )


def _contradiction(
    *,
    is_baseline: bool = False,
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED,
    verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
    confidence: float = 0.85,
) -> Contradiction:
    return Contradiction(
        decision_a_id=uuid4(),
        decision_b_id=uuid4(),
        decision_a_title="Decision A",
        decision_b_title="Decision B",
        verdict=verdict,
        reasoning="They conflict",
        evidence_a="A says X",
        evidence_b="B says Y",
        confidence=confidence,
        status=status,
        is_baseline=is_baseline,
    )


class TestCheckNoNewContradictions:
    def test_no_contradictions(self) -> None:
        violations = check_no_new_contradictions([])
        assert violations == []

    def test_baseline_excluded(self) -> None:
        c = _contradiction(is_baseline=True)
        violations = check_no_new_contradictions([c])
        assert violations == []

    def test_resolved_excluded(self) -> None:
        c = _contradiction(status=ContradictionStatus.RESOLVED)
        violations = check_no_new_contradictions([c])
        assert violations == []

    def test_tension_excluded(self) -> None:
        c = _contradiction(verdict=ContradictionVerdict.TENSION)
        violations = check_no_new_contradictions([c])
        assert violations == []

    def test_actionable_fails(self) -> None:
        c = _contradiction()
        violations = check_no_new_contradictions([c])
        assert len(violations) == 1
        assert violations[0].rule == "no-new-contradictions"
        assert violations[0].severity == "error"

    def test_baseline_ids_excluded(self) -> None:
        c = _contradiction()
        violations = check_no_new_contradictions([c], baseline_ids={str(c.id)})
        assert violations == []

    def test_multiple_actionable(self) -> None:
        c1 = _contradiction()
        c2 = _contradiction()
        violations = check_no_new_contradictions([c1, c2])
        assert len(violations) == 2


class TestCheckDecisionMetadata:
    def test_valid_decision(self) -> None:
        d = _decision()
        violations = check_decision_metadata([d])
        assert violations == []

    def test_missing_dimensions(self) -> None:
        d = _decision(dims=[])
        violations = check_decision_metadata([d])
        assert len(violations) == 1
        assert violations[0].rule == "decision-metadata"
        assert violations[0].severity == "error"

    def test_missing_rationale(self) -> None:
        d = _decision(rationale="")
        violations = check_decision_metadata([d])
        assert len(violations) == 1
        assert violations[0].severity == "warning"

    def test_inactive_skipped(self) -> None:
        d = _decision(dims=[], status=DecisionStatus.SUPERSEDED)
        violations = check_decision_metadata([d])
        assert violations == []

    def test_can_disable_rationale_check(self) -> None:
        d = _decision(rationale="")
        violations = check_decision_metadata([d], require_rationale=False)
        assert violations == []

    def test_can_disable_dimensions_check(self) -> None:
        d = _decision(dims=[])
        violations = check_decision_metadata([d], require_dimensions=False)
        assert violations == []


class TestRunQualityGate:
    def test_all_pass(self) -> None:
        result = run_quality_gate(
            [_decision()],
            [],
        )
        assert result.passed is True
        assert result.checks_run == 2
        assert result.checks_passed == 2
        assert result.violations == []

    def test_contradiction_fails(self) -> None:
        result = run_quality_gate(
            [_decision()],
            [_contradiction()],
        )
        assert result.passed is False
        assert len(result.errors) == 1

    def test_metadata_warning_does_not_fail(self) -> None:
        result = run_quality_gate(
            [_decision(rationale="")],
            [],
        )
        # Warnings don't cause failure, only errors
        assert result.passed is True
        assert len(result.warnings) == 1

    def test_missing_dimensions_fails(self) -> None:
        result = run_quality_gate(
            [_decision(dims=[])],
            [],
        )
        assert result.passed is False
        assert len(result.errors) == 1

    def test_combined_failures(self) -> None:
        result = run_quality_gate(
            [_decision(dims=[])],
            [_contradiction()],
        )
        assert result.passed is False
        assert len(result.errors) == 2

    def test_baseline_contradiction_passes(self) -> None:
        result = run_quality_gate(
            [_decision()],
            [_contradiction(is_baseline=True)],
        )
        assert result.passed is True

    def test_gate_result_properties(self) -> None:
        result = run_quality_gate(
            [_decision(dims=[], rationale="")],
            [_contradiction()],
        )
        assert len(result.errors) >= 1
        assert len(result.warnings) >= 0
        assert result.checks_run == 2

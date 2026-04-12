"""Gemini Compliance: Lyapunov Drift Bound Recovery.

Tests the BehavioralContract system's Lyapunov-inspired drift scoring.
Validates that agents can recover from drift by returning to expected
actions, and that hard-stop thresholds trigger on sustained deviation.
"""

from __future__ import annotations

import math

import pytest

from vt_protocol.analysis.behavioral_contracts import (
    CODING_AGENT_CONTRACT,
    DRIFT_HARD_STOP,
    DRIFT_WARNING,
    EXPECTED_ACTIONS,
    REVIEW_AGENT_CONTRACT,
    BehavioralContract,
    ContractClause,
    ContractEvaluation,
    ContractStatus,
    ContractViolation,
    ViolationSeverity,
    compute_drift_score,
    evaluate_agent,
    evaluate_contract,
)
from vt_protocol.observation.trajectory import TrajectoryEvent

pytestmark = pytest.mark.compliance


class TestLyapunovDriftBound:
    """Lyapunov-inspired drift scoring and recovery."""

    def test_zero_drift_for_expected_actions(self):
        """All expected actions → drift = 0."""
        events = [
            TrajectoryEvent(action="load_context"),
            TrajectoryEvent(action="read_file", target="src/main.py"),
            TrajectoryEvent(action="file_edit", target="src/main.py"),
            TrajectoryEvent(action="test_run"),
            TrajectoryEvent(action="record_decision"),
        ]
        score = compute_drift_score(events, EXPECTED_ACTIONS["coding"])
        assert score == 0.0

    def test_full_deviation_near_one(self):
        """All unexpected actions → drift near 1.0."""
        events = [
            TrajectoryEvent(action="unknown_action_1"),
            TrajectoryEvent(action="unknown_action_2"),
            TrajectoryEvent(action="unknown_action_3"),
            TrajectoryEvent(action="unknown_action_4"),
            TrajectoryEvent(action="unknown_action_5"),
            TrajectoryEvent(action="unknown_action_6"),
            TrajectoryEvent(action="unknown_action_7"),
            TrajectoryEvent(action="unknown_action_8"),
            TrajectoryEvent(action="unknown_action_9"),
            TrajectoryEvent(action="unknown_action_10"),
        ]
        score = compute_drift_score(events, EXPECTED_ACTIONS["coding"])
        assert score >= DRIFT_HARD_STOP
        assert score <= 1.0

    def test_sigmoid_scaling_small_deviation_tolerated(self):
        """1 out of 10 unexpected → drift is small (sigmoid dampens)."""
        events = [TrajectoryEvent(action="read_file")] * 9 + [
            TrajectoryEvent(action="unexpected_thing"),
        ]
        score = compute_drift_score(events, EXPECTED_ACTIONS["coding"])
        # 10% deviation with sigmoid: 1 - exp(-3 * 0.1) ≈ 0.259
        assert score < DRIFT_WARNING

    def test_drift_recovery_scenario(self):
        """Agent drifts then returns to expected actions — score should drop."""
        # Phase 1: high drift (all unexpected)
        bad_events = [TrajectoryEvent(action=f"rogue_{i}") for i in range(10)]
        bad_score = compute_drift_score(bad_events, EXPECTED_ACTIONS["coding"])
        assert bad_score >= DRIFT_HARD_STOP

        # Phase 2: recovery (mix — 8 expected + 2 remaining rogue)
        recovery_events = (
            [TrajectoryEvent(action="read_file")] * 8
            + [TrajectoryEvent(action="rogue_0"), TrajectoryEvent(action="rogue_1")]
        )
        recovery_score = compute_drift_score(recovery_events, EXPECTED_ACTIONS["coding"])
        assert recovery_score < bad_score
        assert recovery_score < DRIFT_HARD_STOP

    def test_empty_events_zero_drift(self):
        """No events → drift = 0."""
        score = compute_drift_score([], EXPECTED_ACTIONS["coding"])
        assert score == 0.0

    def test_warning_threshold_boundary(self):
        """Drift right at DRIFT_WARNING boundary."""
        # We need ~10% deviation for sigmoid to cross 0.3
        # 1 - exp(-3 * r) = 0.3 → exp(-3r) = 0.7 → r ≈ 0.119
        # So ~12% unexpected actions should be near the boundary
        events = [TrajectoryEvent(action="read_file")] * 88 + [
            TrajectoryEvent(action=f"rogue_{i}") for i in range(12)
        ]
        score = compute_drift_score(events, EXPECTED_ACTIONS["coding"])
        # Score should be near the warning boundary
        assert abs(score - DRIFT_WARNING) < 0.1

    def test_hard_stop_threshold(self):
        """50% deviation should exceed hard stop."""
        events = [TrajectoryEvent(action="read_file")] * 5 + [
            TrajectoryEvent(action=f"rogue_{i}") for i in range(5)
        ]
        score = compute_drift_score(events, EXPECTED_ACTIONS["coding"])
        # 50% deviation: 1 - exp(-3 * 0.5) ≈ 0.777
        assert score >= DRIFT_HARD_STOP


class TestBehavioralContractEvaluation:
    """Evaluate contracts against trajectories."""

    def test_coding_contract_satisfied(self):
        """Valid coding trajectory satisfies all contract clauses."""
        events = [
            TrajectoryEvent(action="load_context"),
            TrajectoryEvent(action="read_file", target="src/app.py"),
            TrajectoryEvent(action="file_edit", target="src/app.py"),
            TrajectoryEvent(action="record_decision"),
            TrajectoryEvent(action="test_run"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.preconditions_met
        assert evaluation.postconditions_met
        assert evaluation.invariants_met
        assert evaluation.drift_score < DRIFT_WARNING

    def test_coding_contract_precondition_failed(self):
        """No load_context → precondition violated."""
        events = [
            TrajectoryEvent(action="file_edit", target="src/app.py"),
            TrajectoryEvent(action="test_run"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert not evaluation.preconditions_met
        assert any(v.clause_name == "loaded_context" for v in evaluation.violations)

    def test_coding_contract_postcondition_failed(self):
        """File edits without recording decisions → postcondition violated."""
        events = [
            TrajectoryEvent(action="load_context"),
            TrajectoryEvent(action="file_edit", target="src/app.py"),
            TrajectoryEvent(action="file_edit", target="src/models.py"),
            # No record_decision
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.preconditions_met
        assert not evaluation.postconditions_met

    def test_coding_contract_scope_invariant_violated(self):
        """Editing files outside task scope → invariant violated."""
        events = [
            TrajectoryEvent(
                action="task_start",
                metadata={"scope": ["src/auth.py"]},
            ),
            TrajectoryEvent(action="load_context"),
            TrajectoryEvent(action="file_edit", target="src/auth.py"),
            TrajectoryEvent(action="file_edit", target="src/billing.py"),  # Out of scope!
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert not evaluation.invariants_met
        assert any(v.clause_name == "scope_respected" for v in evaluation.violations)

    def test_review_contract_satisfied(self):
        """Valid review trajectory satisfies contract."""
        events = [
            TrajectoryEvent(action="load_context"),
            TrajectoryEvent(action="read_file", target="src/app.py"),
            TrajectoryEvent(action="comment"),
            TrajectoryEvent(action="approve"),
        ]
        evaluation = evaluate_contract(events, REVIEW_AGENT_CONTRACT)
        assert evaluation.preconditions_met
        assert evaluation.drift_score < DRIFT_WARNING

    def test_evaluate_agent_unknown_type(self):
        """Unknown agent type returns empty evaluation."""
        events = [TrajectoryEvent(action="something")]
        evaluation = evaluate_agent(events, "unknown_agent_type")
        assert evaluation.agent_type == "unknown_agent_type"
        assert evaluation.is_satisfied

    def test_drift_violation_added_at_warning(self):
        """Drift above WARNING adds a violation to the evaluation."""
        # 40% deviation → 1 - exp(-1.2) ≈ 0.70 → hard stop
        events = [TrajectoryEvent(action="read_file")] * 6 + [
            TrajectoryEvent(action=f"rogue_{i}") for i in range(4)
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        drift_violations = [
            v for v in evaluation.violations
            if v.clause_type == "drift"
        ]
        assert len(drift_violations) >= 1

    def test_drift_hard_stop_severity(self):
        """Drift above HARD_STOP → severity = HARD_STOP."""
        events = [TrajectoryEvent(action=f"rogue_{i}") for i in range(20)]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.severity == ViolationSeverity.HARD_STOP
        assert evaluation.drift_score >= DRIFT_HARD_STOP

    def test_evaluation_to_dict(self):
        """ContractEvaluation serializes to dict correctly."""
        events = [
            TrajectoryEvent(action="load_context"),
            TrajectoryEvent(action="read_file"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        d = evaluation.to_dict()
        assert "agent_type" in d
        assert "drift_score" in d
        assert "violations" in d
        assert isinstance(d["violations"], list)

    def test_custom_expected_actions(self):
        """Custom expected actions override defaults."""
        events = [
            TrajectoryEvent(action="load_context"),
            TrajectoryEvent(action="custom_action_1"),
            TrajectoryEvent(action="custom_action_2"),
        ]
        custom = {"load_context", "custom_action_1", "custom_action_2"}
        evaluation = evaluate_contract(
            events, CODING_AGENT_CONTRACT, expected_actions=custom
        )
        assert evaluation.drift_score == 0.0

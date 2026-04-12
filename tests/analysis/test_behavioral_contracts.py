"""Tests for Agent Behavioral Contracts (Bhardwaj 2026)."""

from __future__ import annotations

import pytest

from vt_protocol.analysis.behavioral_contracts import (
    CODING_AGENT_CONTRACT,
    DEFAULT_CONTRACTS,
    DRIFT_HARD_STOP,
    DRIFT_WARNING,
    EXPECTED_ACTIONS,
    BehavioralContract,
    ContractClause,
    ContractEvaluation,
    ContractViolation,
    ViolationSeverity,
    compute_drift_score,
    evaluate_agent,
    evaluate_contract,
)
from vt_protocol.observation.trajectory import TrajectoryEvent


def _event(action: str, target: str = "", **metadata) -> TrajectoryEvent:
    return TrajectoryEvent(action=action, target=target, metadata=metadata)


class TestContractClause:
    def test_evaluate_true(self) -> None:
        clause = ContractClause(name="test", check_fn=lambda events: True)
        assert clause.evaluate([]) is True

    def test_evaluate_false(self) -> None:
        clause = ContractClause(name="test", check_fn=lambda events: False)
        assert clause.evaluate([]) is False

    def test_no_check_fn(self) -> None:
        clause = ContractClause(name="test")
        assert clause.evaluate([]) is True


class TestContractViolation:
    def test_to_dict(self) -> None:
        v = ContractViolation(
            clause_name="test_clause",
            clause_type="invariant",
            agent_type="coding",
            message="violated",
            severity=ViolationSeverity.ERROR,
        )
        d = v.to_dict()
        assert d["severity"] == "error"
        assert d["clause_type"] == "invariant"


class TestBehavioralContract:
    def test_to_dict(self) -> None:
        contract = BehavioralContract(
            agent_type="coding",
            preconditions=[ContractClause(name="pre1")],
            postconditions=[ContractClause(name="post1"), ContractClause(name="post2")],
            invariants=[ContractClause(name="inv1")],
        )
        d = contract.to_dict()
        assert d["agent_type"] == "coding"
        assert d["precondition_count"] == 1
        assert d["postcondition_count"] == 2
        assert d["invariant_count"] == 1


class TestContractEvaluation:
    def test_satisfied(self) -> None:
        ev = ContractEvaluation(agent_type="coding")
        assert ev.is_satisfied is True

    def test_not_satisfied(self) -> None:
        ev = ContractEvaluation(
            agent_type="coding",
            violations=[ContractViolation(clause_name="test")],
        )
        assert ev.is_satisfied is False

    def test_severity_from_drift(self) -> None:
        ev = ContractEvaluation(agent_type="coding", drift_score=0.8)
        assert ev.severity == ViolationSeverity.HARD_STOP

    def test_to_dict(self) -> None:
        ev = ContractEvaluation(agent_type="coding", drift_score=0.1)
        d = ev.to_dict()
        assert d["agent_type"] == "coding"
        assert d["drift_score"] == 0.1


class TestComputeDriftScore:
    def test_no_events(self) -> None:
        assert compute_drift_score([], set()) == 0.0

    def test_all_expected(self) -> None:
        events = [_event("read_file"), _event("file_edit")]
        score = compute_drift_score(events, {"read_file", "file_edit"})
        assert score == 0.0

    def test_all_unexpected(self) -> None:
        events = [_event("random_action")] * 10
        score = compute_drift_score(events, {"expected"})
        assert score > DRIFT_HARD_STOP

    def test_mixed(self) -> None:
        events = [_event("read_file"), _event("unexpected"), _event("file_edit")]
        score = compute_drift_score(events, {"read_file", "file_edit"})
        assert 0.0 < score < 1.0

    def test_monotonic_with_deviations(self) -> None:
        expected = {"good"}
        events_low = [_event("good")] * 8 + [_event("bad")] * 2
        events_high = [_event("good")] * 2 + [_event("bad")] * 8
        score_low = compute_drift_score(events_low, expected)
        score_high = compute_drift_score(events_high, expected)
        assert score_low < score_high


class TestEvaluateContract:
    def test_coding_agent_satisfied(self) -> None:
        events = [
            _event("load_context"),
            _event("read_file", "src/main.py"),
            _event("file_edit", "src/main.py"),
            _event("record_decision", "src/main.py"),
            _event("test_run"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.preconditions_met is True
        assert evaluation.postconditions_met is True

    def test_coding_agent_no_context(self) -> None:
        events = [
            _event("file_edit", "src/main.py"),
            _event("test_run"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.preconditions_met is False
        assert any(v.clause_name == "loaded_context" for v in evaluation.violations)

    def test_coding_agent_no_decision(self) -> None:
        events = [
            _event("load_context"),
            _event("file_edit", "src/main.py"),
            _event("test_run"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.postconditions_met is False
        assert any(v.clause_name == "decisions_recorded" for v in evaluation.violations)

    def test_scope_invariant_violated(self) -> None:
        events = [
            TrajectoryEvent(action="load_context", metadata={"scope": ["src/main.py"]}),
            TrajectoryEvent(action="task_start", metadata={"scope": ["src/main.py"]}),
            TrajectoryEvent(action="file_edit", target="totally/different/file.py"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.invariants_met is False

    def test_scope_invariant_no_scope_defined(self) -> None:
        events = [
            _event("load_context"),
            _event("file_edit", "any_file.py"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        # No scope defined = invariant trivially holds
        assert evaluation.invariants_met is True

    def test_drift_score_computed(self) -> None:
        events = [
            _event("load_context"),
            _event("read_file"),
            _event("file_edit"),
        ]
        evaluation = evaluate_contract(events, CODING_AGENT_CONTRACT)
        assert evaluation.drift_score >= 0.0

    def test_high_drift_triggers_warning(self) -> None:
        events = [_event("unexpected_action")] * 20
        events.insert(0, _event("load_context"))  # satisfy precondition
        evaluation = evaluate_contract(
            events,
            BehavioralContract(
                agent_type="test",
                preconditions=[ContractClause(name="pre", check_fn=lambda _: True)],
            ),
            expected_actions={"expected"},
        )
        assert evaluation.drift_score > DRIFT_WARNING
        assert any(v.clause_type == "drift" for v in evaluation.violations)

    def test_custom_expected_actions(self) -> None:
        events = [_event("custom_action")] * 5
        evaluation = evaluate_contract(
            events,
            BehavioralContract(agent_type="custom"),
            expected_actions={"custom_action"},
        )
        assert evaluation.drift_score == 0.0


class TestEvaluateAgent:
    def test_coding_agent(self) -> None:
        events = [
            _event("load_context"),
            _event("read_file"),
            _event("file_edit", "f.py"),
            _event("record_decision"),
        ]
        evaluation = evaluate_agent(events, "coding")
        assert evaluation.agent_type == "coding"

    def test_review_agent(self) -> None:
        events = [
            _event("load_context"),
            _event("read_file"),
            _event("comment"),
        ]
        evaluation = evaluate_agent(events, "review")
        assert evaluation.agent_type == "review"

    def test_unknown_agent_type(self) -> None:
        evaluation = evaluate_agent([], "unknown_type")
        assert evaluation.agent_type == "unknown_type"
        assert evaluation.is_satisfied is True

    def test_default_contracts_exist(self) -> None:
        assert "coding" in DEFAULT_CONTRACTS
        assert "review" in DEFAULT_CONTRACTS

    def test_expected_actions_defined(self) -> None:
        assert "coding" in EXPECTED_ACTIONS
        assert "review" in EXPECTED_ACTIONS
        assert "load_context" in EXPECTED_ACTIONS["coding"]

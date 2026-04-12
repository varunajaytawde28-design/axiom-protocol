"""Agent Behavioral Contracts (Bhardwaj 2026).

Define preconditions, postconditions, and invariants per agent type.
Evaluate contracts against trajectory data. Lyapunov-inspired drift
score tracks cumulative deviation from expected behavior.

From SPEC Sprint 20: "Agent Behavioral Contracts."
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from vt_protocol.observation.trajectory import TrajectoryEvent

logger = logging.getLogger(__name__)

# Drift thresholds
DRIFT_WARNING = 0.3
DRIFT_HARD_STOP = 0.7


class ContractStatus(str, Enum):
    """Status of a contract evaluation."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    SKIPPED = "skipped"  # Precondition not met, contract doesn't apply


class ViolationSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"
    HARD_STOP = "hard_stop"


@dataclass
class ContractClause:
    """A single clause (precondition, postcondition, or invariant)."""

    name: str = ""
    description: str = ""
    check_fn: Callable[[list[TrajectoryEvent]], bool] | None = None

    def evaluate(self, events: list[TrajectoryEvent]) -> bool:
        if self.check_fn is None:
            return True
        return self.check_fn(events)


@dataclass
class ContractViolation:
    """A detected contract violation."""

    clause_name: str = ""
    clause_type: str = ""  # precondition, postcondition, invariant
    agent_type: str = ""
    message: str = ""
    severity: ViolationSeverity = ViolationSeverity.WARNING
    drift_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause_name": self.clause_name,
            "clause_type": self.clause_type,
            "agent_type": self.agent_type,
            "message": self.message,
            "severity": self.severity.value,
            "drift_score": round(self.drift_score, 4),
        }


@dataclass
class BehavioralContract:
    """A complete behavioral contract for an agent type."""

    agent_type: str = ""
    preconditions: list[ContractClause] = field(default_factory=list)
    postconditions: list[ContractClause] = field(default_factory=list)
    invariants: list[ContractClause] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "precondition_count": len(self.preconditions),
            "postcondition_count": len(self.postconditions),
            "invariant_count": len(self.invariants),
        }


@dataclass
class ContractEvaluation:
    """Result of evaluating a behavioral contract."""

    agent_type: str = ""
    violations: list[ContractViolation] = field(default_factory=list)
    preconditions_met: bool = True
    postconditions_met: bool = True
    invariants_met: bool = True
    drift_score: float = 0.0

    @property
    def is_satisfied(self) -> bool:
        return len(self.violations) == 0

    @property
    def severity(self) -> ViolationSeverity:
        if self.drift_score >= DRIFT_HARD_STOP:
            return ViolationSeverity.HARD_STOP
        if self.drift_score >= DRIFT_WARNING:
            return ViolationSeverity.WARNING
        if any(v.severity == ViolationSeverity.HARD_STOP for v in self.violations):
            return ViolationSeverity.HARD_STOP
        if any(v.severity == ViolationSeverity.ERROR for v in self.violations):
            return ViolationSeverity.ERROR
        if self.violations:
            return ViolationSeverity.WARNING
        return ViolationSeverity.WARNING  # safe default

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "is_satisfied": self.is_satisfied,
            "violation_count": len(self.violations),
            "preconditions_met": self.preconditions_met,
            "postconditions_met": self.postconditions_met,
            "invariants_met": self.invariants_met,
            "drift_score": round(self.drift_score, 4),
            "severity": self.severity.value,
            "violations": [v.to_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# Drift score computation (Lyapunov-inspired)
# ---------------------------------------------------------------------------


def compute_drift_score(
    events: list[TrajectoryEvent],
    expected_actions: set[str],
) -> float:
    """Compute cumulative deviation from expected behavior.

    Lyapunov-inspired: each event that deviates from expected behavior
    adds to the drift score. Score is normalized to [0, 1].

    Warning at 0.3, hard stop at 0.7.
    """
    if not events:
        return 0.0

    deviations = 0
    for event in events:
        if event.action not in expected_actions:
            deviations += 1

    # Normalize using sigmoid-like function
    ratio = deviations / len(events)
    # Apply non-linear scaling: small deviations are tolerated,
    # large deviations escalate rapidly
    drift = 1.0 - math.exp(-3.0 * ratio)
    return min(1.0, max(0.0, drift))


# ---------------------------------------------------------------------------
# Default contracts
# ---------------------------------------------------------------------------


def _check_loaded_context(events: list[TrajectoryEvent]) -> bool:
    """Precondition: agent loaded project context."""
    return any(e.action in ("load_context", "get_project_context") for e in events)


def _check_decisions_recorded(events: list[TrajectoryEvent]) -> bool:
    """Postcondition: all modified files have decisions recorded."""
    file_edits = {e.target for e in events if e.action in ("file_edit", "write_file")}
    decisions = {e.target for e in events if e.action in ("record_decision", "decision")}
    # At least one decision for any file edits
    if file_edits and not decisions:
        return False
    return True


def _check_scope_invariant(events: list[TrajectoryEvent]) -> bool:
    """Invariant: never modify files outside task scope."""
    task_targets = set()
    for e in events:
        if e.action in ("task_start", "load_context"):
            scope = e.metadata.get("scope", [])
            if isinstance(scope, list):
                task_targets.update(scope)

    if not task_targets:
        return True  # No scope defined = invariant trivially holds

    for e in events:
        if e.action in ("file_edit", "write_file") and e.target:
            if e.target not in task_targets:
                return False
    return True


CODING_AGENT_CONTRACT = BehavioralContract(
    agent_type="coding",
    preconditions=[
        ContractClause(
            name="loaded_context",
            description="Agent loaded project context before making changes",
            check_fn=_check_loaded_context,
        ),
    ],
    postconditions=[
        ContractClause(
            name="decisions_recorded",
            description="All modified files have decisions recorded",
            check_fn=_check_decisions_recorded,
        ),
    ],
    invariants=[
        ContractClause(
            name="scope_respected",
            description="Never modify files outside task scope",
            check_fn=_check_scope_invariant,
        ),
    ],
)

REVIEW_AGENT_CONTRACT = BehavioralContract(
    agent_type="review",
    preconditions=[
        ContractClause(
            name="loaded_context",
            description="Agent loaded project context",
            check_fn=_check_loaded_context,
        ),
    ],
    postconditions=[],
    invariants=[],
)

DEFAULT_CONTRACTS: dict[str, BehavioralContract] = {
    "coding": CODING_AGENT_CONTRACT,
    "review": REVIEW_AGENT_CONTRACT,
}

# Expected actions per agent type (for drift scoring)
EXPECTED_ACTIONS: dict[str, set[str]] = {
    "coding": {
        "load_context", "get_project_context", "read_file", "file_edit",
        "write_file", "test_run", "record_decision", "decision",
        "task_start", "task_end",
    },
    "review": {
        "load_context", "get_project_context", "read_file", "comment",
        "approve", "request_changes", "task_start", "task_end",
    },
}


# ---------------------------------------------------------------------------
# Contract evaluation
# ---------------------------------------------------------------------------


def evaluate_contract(
    events: list[TrajectoryEvent],
    contract: BehavioralContract,
    *,
    expected_actions: set[str] | None = None,
) -> ContractEvaluation:
    """Evaluate a behavioral contract against a trajectory.

    Checks preconditions, then postconditions (if pre met), then invariants.
    Computes Lyapunov drift score from event actions.
    """
    evaluation = ContractEvaluation(agent_type=contract.agent_type)

    # Check preconditions
    for clause in contract.preconditions:
        if not clause.evaluate(events):
            evaluation.preconditions_met = False
            evaluation.violations.append(ContractViolation(
                clause_name=clause.name,
                clause_type="precondition",
                agent_type=contract.agent_type,
                message=f"Precondition failed: {clause.description}",
                severity=ViolationSeverity.ERROR,
            ))

    # Check postconditions (only if preconditions are met)
    if evaluation.preconditions_met:
        for clause in contract.postconditions:
            if not clause.evaluate(events):
                evaluation.postconditions_met = False
                evaluation.violations.append(ContractViolation(
                    clause_name=clause.name,
                    clause_type="postcondition",
                    agent_type=contract.agent_type,
                    message=f"Postcondition failed: {clause.description}",
                    severity=ViolationSeverity.WARNING,
                ))

    # Check invariants (always checked)
    for clause in contract.invariants:
        if not clause.evaluate(events):
            evaluation.invariants_met = False
            evaluation.violations.append(ContractViolation(
                clause_name=clause.name,
                clause_type="invariant",
                agent_type=contract.agent_type,
                message=f"Invariant violated: {clause.description}",
                severity=ViolationSeverity.ERROR,
            ))

    # Compute drift score
    expected = expected_actions or EXPECTED_ACTIONS.get(contract.agent_type, set())
    evaluation.drift_score = compute_drift_score(events, expected)

    # Add drift violation if above threshold
    if evaluation.drift_score >= DRIFT_HARD_STOP:
        evaluation.violations.append(ContractViolation(
            clause_name="drift_limit",
            clause_type="drift",
            agent_type=contract.agent_type,
            message=f"Drift score {evaluation.drift_score:.2f} exceeds hard stop threshold {DRIFT_HARD_STOP}",
            severity=ViolationSeverity.HARD_STOP,
            drift_score=evaluation.drift_score,
        ))
    elif evaluation.drift_score >= DRIFT_WARNING:
        evaluation.violations.append(ContractViolation(
            clause_name="drift_warning",
            clause_type="drift",
            agent_type=contract.agent_type,
            message=f"Drift score {evaluation.drift_score:.2f} exceeds warning threshold {DRIFT_WARNING}",
            severity=ViolationSeverity.WARNING,
            drift_score=evaluation.drift_score,
        ))

    # Set drift score on all violations for context
    for v in evaluation.violations:
        if v.drift_score == 0.0:
            v.drift_score = evaluation.drift_score

    return evaluation


def evaluate_agent(
    events: list[TrajectoryEvent],
    agent_type: str,
) -> ContractEvaluation:
    """Evaluate an agent's trajectory against its default contract."""
    contract = DEFAULT_CONTRACTS.get(agent_type)
    if contract is None:
        return ContractEvaluation(agent_type=agent_type)
    return evaluate_contract(events, contract)

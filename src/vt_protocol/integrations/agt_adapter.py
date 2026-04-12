"""Microsoft AGT (Agent Governance Toolkit) integration adapter.

Implements a PolicyProviderInterface so VT Protocol can be plugged into
Microsoft's AGT framework as an architectural governance policy provider.

From SPEC Sprint 14: "Microsoft AGT integration — PolicyProviderInterface adapter."
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionVerdict,
    Decision,
    Dimension,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AGT PolicyProviderInterface (matches Microsoft's interface contract)
# ---------------------------------------------------------------------------


class PolicyProviderInterface(ABC):
    """Abstract base matching Microsoft AGT PolicyProvider contract.

    Implementations must provide:
    - evaluate_action: Check if an action is allowed
    - get_policies: List active policies
    - get_policy: Get a specific policy by ID
    """

    @abstractmethod
    def evaluate_action(
        self,
        agent_id: str,
        action: str,
        context: dict[str, Any],
    ) -> PolicyEvaluation:
        ...

    @abstractmethod
    def get_policies(self) -> list[PolicyRecord]:
        ...

    @abstractmethod
    def get_policy(self, policy_id: str) -> PolicyRecord | None:
        ...


@dataclass
class PolicyEvaluation:
    """Result of evaluating an agent action against policies."""

    allowed: bool
    policy_id: str = ""
    reason: str = ""
    constraints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "policy_id": self.policy_id,
            "reason": self.reason,
            "constraints": self.constraints,
            "metadata": self.metadata,
        }


@dataclass
class PolicyRecord:
    """A policy record compatible with AGT's policy format."""

    policy_id: str
    name: str
    description: str = ""
    dimensions: list[str] = field(default_factory=list)
    severity: str = "medium"  # low, medium, high, critical
    active: bool = True
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "description": self.description,
            "dimensions": self.dimensions,
            "severity": self.severity,
            "active": self.active,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# VT Protocol AGT Adapter
# ---------------------------------------------------------------------------


class VTProtocolPolicyProvider(PolicyProviderInterface):
    """VT Protocol adapter for Microsoft AGT.

    Translates VT Protocol decisions and contradictions into AGT policy
    evaluations. Decisions become policies, contradictions become violations.
    """

    def __init__(
        self,
        decisions: list[Decision] | None = None,
        contradictions: list[Contradiction] | None = None,
    ) -> None:
        self._decisions = decisions or []
        self._contradictions = contradictions or []

    def set_decisions(self, decisions: list[Decision]) -> None:
        self._decisions = decisions

    def set_contradictions(self, contradictions: list[Contradiction]) -> None:
        self._contradictions = contradictions

    def evaluate_action(
        self,
        agent_id: str,
        action: str,
        context: dict[str, Any],
    ) -> PolicyEvaluation:
        """Evaluate an agent action against VT Protocol decisions.

        Context should include:
        - file_path: str — the file being modified
        - dimensions: list[str] — relevant dimensions
        - change_type: str — type of change (add, modify, delete)
        """
        dimensions = context.get("dimensions", [])
        file_path = context.get("file_path", "")

        # Find active contradictions in the relevant dimensions
        blocking = []
        for c in self._contradictions:
            if c.status.value != "unresolved":
                continue
            if c.verdict != ContradictionVerdict.CONTRADICTION:
                continue
            shared = [d.value for d in c.shared_dimensions]
            if any(d in dimensions for d in shared):
                blocking.append(c)

        if blocking:
            c = blocking[0]
            return PolicyEvaluation(
                allowed=False,
                policy_id=str(c.id),
                reason=f"Unresolved contradiction in shared dimensions: {c.reasoning[:100]}",
                constraints=[d.value for d in c.shared_dimensions],
                metadata={
                    "contradiction_id": str(c.id),
                    "decision_a": c.decision_a_title,
                    "decision_b": c.decision_b_title,
                    "agent_id": agent_id,
                    "file_path": file_path,
                },
            )

        # Find relevant decisions as constraints
        relevant_constraints = []
        for d in self._decisions:
            if not d.valid:
                continue
            d_dims = [dim.value for dim in d.dimensions]
            if any(dim in dimensions for dim in d_dims):
                relevant_constraints.append(d.title)

        return PolicyEvaluation(
            allowed=True,
            reason="No blocking contradictions",
            constraints=relevant_constraints[:5],
            metadata={"agent_id": agent_id, "file_path": file_path},
        )

    def get_policies(self) -> list[PolicyRecord]:
        """Convert active decisions to AGT policy records."""
        policies = []
        for d in self._decisions:
            if not d.valid:
                continue
            policies.append(PolicyRecord(
                policy_id=str(d.id),
                name=d.title,
                description=d.content[:200],
                dimensions=[dim.value for dim in d.dimensions],
                severity=_decision_severity(d),
                metadata={
                    "source_type": d.source_type.value,
                    "confidence": d.confidence,
                    "decision_type": d.decision_type.value,
                },
            ))
        return policies

    def get_policy(self, policy_id: str) -> PolicyRecord | None:
        """Look up a specific policy by decision ID."""
        for d in self._decisions:
            if str(d.id) == policy_id:
                return PolicyRecord(
                    policy_id=str(d.id),
                    name=d.title,
                    description=d.content[:200],
                    dimensions=[dim.value for dim in d.dimensions],
                    severity=_decision_severity(d),
                )
        return None


def _decision_severity(d: Decision) -> str:
    """Map decision type to AGT severity level."""
    from vt_protocol.decisions.models import DecisionType
    severity_map = {
        DecisionType.CONSTRAINT: "critical",
        DecisionType.ARCHITECTURAL: "high",
        DecisionType.TECHNICAL: "medium",
        DecisionType.PRODUCT: "low",
    }
    return severity_map.get(d.decision_type, "medium")

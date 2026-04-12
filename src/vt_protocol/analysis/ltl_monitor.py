"""LTL runtime monitors — Dwyer pattern enforcement.

Implements three temporal logic patterns evaluated against agent
trajectory event logs:

1. Response (globally): □(decision → ◇review)
2. Precedence (globally): ¬data_access W auth
3. Absence (between Q and R): □(flagged ∧ ¬resolved → ¬deploy W resolved)

From SPEC Sprint 18: "LTL runtime monitors."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from vt_protocol.observation.trajectory import TrajectoryEvent

logger = logging.getLogger(__name__)


class PatternType(str, Enum):
    """Dwyer temporal property patterns."""

    RESPONSE = "response"  # □(P → ◇Q)
    PRECEDENCE = "precedence"  # ¬Q W P (P must precede Q)
    ABSENCE = "absence"  # □(scope → ¬P W end_scope)


class MonitorStatus(str, Enum):
    """Current status of a monitor."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    PENDING = "pending"  # Waiting for eventual response


@dataclass
class LTLViolation:
    """A detected temporal property violation."""

    pattern: PatternType
    property_name: str
    message: str
    trigger_event: TrajectoryEvent | None = None
    events_before: list[TrajectoryEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.value,
            "property_name": self.property_name,
            "message": self.message,
            "trigger_action": self.trigger_event.action if self.trigger_event else None,
        }


@dataclass
class MonitorResult:
    """Result of evaluating all monitors against a trace."""

    violations: list[LTLViolation] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    satisfied: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.violations) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation_count": len(self.violations),
            "pending_count": len(self.pending),
            "satisfied_count": len(self.satisfied),
            "violations": [v.to_dict() for v in self.violations],
            "pending": self.pending,
            "satisfied": self.satisfied,
            "is_clean": self.is_clean,
        }


# ---------------------------------------------------------------------------
# Pattern 1: Response (globally) — □(P → ◇Q)
# "Every decision must be reviewed before implementation"
# ---------------------------------------------------------------------------


def check_response(
    events: list[TrajectoryEvent],
    *,
    trigger_action: str,
    response_action: str,
    property_name: str = "response",
) -> tuple[MonitorStatus, LTLViolation | None]:
    """Check □(trigger → ◇response): every trigger must eventually get a response.

    Scans the event trace. For each trigger_action occurrence, checks
    that response_action occurs somewhere after it.
    """
    pending_triggers: list[TrajectoryEvent] = []

    for event in events:
        if event.action == trigger_action:
            pending_triggers.append(event)
        elif event.action == response_action:
            # Clears all pending triggers (response satisfies all waiting triggers)
            pending_triggers.clear()

    if pending_triggers:
        # Still have unresponded triggers — violation
        oldest = pending_triggers[0]
        return MonitorStatus.VIOLATED, LTLViolation(
            pattern=PatternType.RESPONSE,
            property_name=property_name,
            message=(
                f"'{trigger_action}' at index requires '{response_action}' "
                f"but none found — {len(pending_triggers)} unresolved"
            ),
            trigger_event=oldest,
        )

    return MonitorStatus.SATISFIED, None


# ---------------------------------------------------------------------------
# Pattern 2: Precedence (globally) — ¬Q W P
# "Authentication must precede data access"
# ---------------------------------------------------------------------------


def check_precedence(
    events: list[TrajectoryEvent],
    *,
    required_first: str,
    guarded_action: str,
    property_name: str = "precedence",
) -> tuple[MonitorStatus, LTLViolation | None]:
    """Check ¬guarded W required_first: guarded_action must not occur before required_first.

    Returns VIOLATED if guarded_action appears before any required_first.
    """
    seen_required = False

    for event in events:
        if event.action == required_first:
            seen_required = True
        elif event.action == guarded_action and not seen_required:
            return MonitorStatus.VIOLATED, LTLViolation(
                pattern=PatternType.PRECEDENCE,
                property_name=property_name,
                message=(
                    f"'{guarded_action}' occurred before '{required_first}' — "
                    f"precedence violated"
                ),
                trigger_event=event,
            )

    return MonitorStatus.SATISFIED, None


# ---------------------------------------------------------------------------
# Pattern 3: Absence (between Q and R)
# "No deployment while breaking change unresolved"
# □(flagged ∧ ¬resolved → ¬deploy W resolved)
# ---------------------------------------------------------------------------


def check_absence(
    events: list[TrajectoryEvent],
    *,
    scope_start: str,
    scope_end: str,
    forbidden_action: str,
    property_name: str = "absence",
) -> tuple[MonitorStatus, LTLViolation | None]:
    """Check absence of forbidden_action between scope_start and scope_end.

    Once scope_start fires, forbidden_action must not occur until scope_end.
    """
    in_scope = False

    for event in events:
        if event.action == scope_start:
            in_scope = True
        elif event.action == scope_end:
            in_scope = False
        elif event.action == forbidden_action and in_scope:
            return MonitorStatus.VIOLATED, LTLViolation(
                pattern=PatternType.ABSENCE,
                property_name=property_name,
                message=(
                    f"'{forbidden_action}' occurred between '{scope_start}' "
                    f"and '{scope_end}' — absence property violated"
                ),
                trigger_event=event,
            )

    return MonitorStatus.SATISFIED, None


# ---------------------------------------------------------------------------
# Composite monitor
# ---------------------------------------------------------------------------


@dataclass
class PropertySpec:
    """Specification for a temporal property to monitor."""

    name: str
    pattern: PatternType
    params: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pattern": self.pattern.value,
            "params": self.params,
        }


# Default governance properties
DEFAULT_PROPERTIES: list[PropertySpec] = [
    PropertySpec(
        name="decision_reviewed",
        pattern=PatternType.RESPONSE,
        params={"trigger_action": "decision", "response_action": "review"},
    ),
    PropertySpec(
        name="auth_before_data",
        pattern=PatternType.PRECEDENCE,
        params={"required_first": "auth", "guarded_action": "data_access"},
    ),
    PropertySpec(
        name="no_deploy_while_flagged",
        pattern=PatternType.ABSENCE,
        params={"scope_start": "flag_breaking", "scope_end": "resolve_breaking", "forbidden_action": "deploy"},
    ),
]


def evaluate_properties(
    events: list[TrajectoryEvent],
    properties: list[PropertySpec] | None = None,
) -> MonitorResult:
    """Evaluate all temporal properties against an event trace."""
    props = properties or DEFAULT_PROPERTIES
    result = MonitorResult()

    for prop in props:
        if prop.pattern == PatternType.RESPONSE:
            status, violation = check_response(
                events,
                trigger_action=prop.params.get("trigger_action", ""),
                response_action=prop.params.get("response_action", ""),
                property_name=prop.name,
            )
        elif prop.pattern == PatternType.PRECEDENCE:
            status, violation = check_precedence(
                events,
                required_first=prop.params.get("required_first", ""),
                guarded_action=prop.params.get("guarded_action", ""),
                property_name=prop.name,
            )
        elif prop.pattern == PatternType.ABSENCE:
            status, violation = check_absence(
                events,
                scope_start=prop.params.get("scope_start", ""),
                scope_end=prop.params.get("scope_end", ""),
                forbidden_action=prop.params.get("forbidden_action", ""),
                property_name=prop.name,
            )
        else:
            continue

        if status == MonitorStatus.VIOLATED and violation:
            result.violations.append(violation)
        elif status == MonitorStatus.PENDING:
            result.pending.append(prop.name)
        else:
            result.satisfied.append(prop.name)

    return result

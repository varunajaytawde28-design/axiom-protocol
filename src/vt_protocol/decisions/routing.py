"""Role-based routing engine — dimension-to-role mapping.

Routes contradictions to the right person based on which architectural
dimensions are involved. Configurable via governance.yaml.

Default routing:
  - database, caching, concurrency, state_management → tech_lead
  - auth, security → tech_lead + ciso
  - api_style, messaging → tech_lead + qa
  - deployment, logging, testing, error_handling → tech_lead
  - product-type decisions → pm

From SPEC Phase 2: "contradictions auto-route based on dimension"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from vt_protocol.decisions.models import (
    Contradiction,
    Decision,
    Dimension,
)

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """Organizational roles for routing."""

    TECH_LEAD = "tech_lead"
    CISO = "ciso"
    QA = "qa"
    PM = "pm"
    ARCHITECT = "architect"
    DEVOPS = "devops"


# Default dimension-to-role mapping
DEFAULT_ROUTING: dict[str, list[str]] = {
    Dimension.DATABASE.value: [Role.TECH_LEAD.value],
    Dimension.AUTH.value: [Role.TECH_LEAD.value, Role.CISO.value],
    Dimension.CACHING.value: [Role.TECH_LEAD.value],
    Dimension.API_STYLE.value: [Role.TECH_LEAD.value, Role.QA.value],
    Dimension.DEPLOYMENT.value: [Role.TECH_LEAD.value, Role.DEVOPS.value],
    Dimension.CONCURRENCY.value: [Role.TECH_LEAD.value],
    Dimension.LOGGING.value: [Role.TECH_LEAD.value],
    Dimension.TESTING.value: [Role.TECH_LEAD.value, Role.QA.value],
    Dimension.ERROR_HANDLING.value: [Role.TECH_LEAD.value],
    Dimension.STATE_MANAGEMENT.value: [Role.TECH_LEAD.value],
    Dimension.MESSAGING.value: [Role.TECH_LEAD.value, Role.QA.value],
    Dimension.SECURITY.value: [Role.TECH_LEAD.value, Role.CISO.value],
}


@dataclass
class RoutingRule:
    """A single dimension-to-role mapping."""

    dimension: str
    roles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "roles": self.roles}


@dataclass
class RoutingConfig:
    """Complete routing configuration for a project."""

    rules: list[RoutingRule] = field(default_factory=list)
    _index: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._index = {}
        for rule in self.rules:
            self._index[rule.dimension] = rule.roles

    def roles_for_dimension(self, dimension: str) -> list[str]:
        """Get roles assigned to a dimension."""
        return self._index.get(dimension, [])

    def dimensions_for_role(self, role: str) -> list[str]:
        """Get all dimensions a role is responsible for."""
        return [dim for dim, roles in self._index.items() if role in roles]

    def to_dict(self) -> dict[str, Any]:
        return {"rules": [r.to_dict() for r in self.rules]}


@dataclass
class RoutingResult:
    """Result of routing a contradiction to roles."""

    contradiction_id: str
    assigned_roles: list[str]
    dimensions_involved: list[str]
    requires_dual_auth: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "assigned_roles": self.assigned_roles,
            "dimensions_involved": self.dimensions_involved,
            "requires_dual_auth": self.requires_dual_auth,
        }


def load_default_routing() -> RoutingConfig:
    """Load the default dimension-to-role routing config."""
    rules = [
        RoutingRule(dimension=dim, roles=list(roles))
        for dim, roles in DEFAULT_ROUTING.items()
    ]
    return RoutingConfig(rules=rules)


def load_routing_from_dict(data: dict[str, Any]) -> RoutingConfig:
    """Load routing config from a dictionary (governance.yaml routing section)."""
    rules = []
    routing_data = data.get("routing", data)
    if isinstance(routing_data, dict):
        for dim, roles in routing_data.items():
            if isinstance(roles, list):
                rules.append(RoutingRule(dimension=dim, roles=roles))
            elif isinstance(roles, str):
                rules.append(RoutingRule(dimension=dim, roles=[roles]))
    elif isinstance(routing_data, list):
        for item in routing_data:
            if isinstance(item, dict):
                rules.append(RoutingRule(
                    dimension=item.get("dimension", ""),
                    roles=item.get("roles", []),
                ))
    return RoutingConfig(rules=rules)


def route_contradiction(
    contradiction: Contradiction,
    config: RoutingConfig | None = None,
) -> RoutingResult:
    """Route a contradiction to the appropriate roles.

    Collects roles from all shared dimensions and deduplicates. If more
    than one distinct role is assigned, marks as requiring dual authorization.
    """
    if config is None:
        config = load_default_routing()

    dimensions = [d.value for d in contradiction.shared_dimensions]
    roles_set: set[str] = set()
    for dim in dimensions:
        roles_set.update(config.roles_for_dimension(dim))

    # Fallback: if no roles matched, assign to tech_lead
    if not roles_set:
        roles_set.add(Role.TECH_LEAD.value)

    assigned = sorted(roles_set)
    requires_dual = len(assigned) >= 2

    return RoutingResult(
        contradiction_id=str(contradiction.id),
        assigned_roles=assigned,
        dimensions_involved=dimensions,
        requires_dual_auth=requires_dual,
    )


def route_contradictions(
    contradictions: list[Contradiction],
    config: RoutingConfig | None = None,
) -> list[RoutingResult]:
    """Route multiple contradictions to roles."""
    if config is None:
        config = load_default_routing()
    return [route_contradiction(c, config) for c in contradictions]

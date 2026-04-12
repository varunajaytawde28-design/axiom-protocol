"""Tests for role-based routing engine."""

from __future__ import annotations

import pytest

from vt_protocol.decisions.routing import (
    DEFAULT_ROUTING,
    Role,
    RoutingConfig,
    RoutingResult,
    RoutingRule,
    load_default_routing,
    load_routing_from_dict,
    route_contradiction,
    route_contradictions,
)
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contradiction(
    shared_dimensions: list[Dimension] | None = None,
    **kwargs,
) -> Contradiction:
    defaults = dict(
        decision_a_id="00000000-0000-0000-0000-000000000001",
        decision_b_id="00000000-0000-0000-0000-000000000002",
        decision_a_title="Decision A",
        decision_b_title="Decision B",
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="They conflict.",
        evidence_a="A says X.",
        evidence_b="B says Y.",
        shared_dimensions=shared_dimensions or [Dimension.DATABASE],
        confidence=0.9,
    )
    defaults.update(kwargs)
    return Contradiction(**defaults)


# ---------------------------------------------------------------------------
# Role enum
# ---------------------------------------------------------------------------


class TestRole:
    def test_values(self) -> None:
        assert Role.TECH_LEAD.value == "tech_lead"
        assert Role.CISO.value == "ciso"
        assert Role.QA.value == "qa"
        assert Role.PM.value == "pm"


# ---------------------------------------------------------------------------
# RoutingRule
# ---------------------------------------------------------------------------


class TestRoutingRule:
    def test_to_dict(self) -> None:
        rule = RoutingRule(dimension="database", roles=["tech_lead"])
        d = rule.to_dict()
        assert d["dimension"] == "database"
        assert d["roles"] == ["tech_lead"]


# ---------------------------------------------------------------------------
# RoutingConfig
# ---------------------------------------------------------------------------


class TestRoutingConfig:
    def test_roles_for_dimension(self) -> None:
        config = RoutingConfig(rules=[
            RoutingRule(dimension="auth", roles=["tech_lead", "ciso"]),
        ])
        assert config.roles_for_dimension("auth") == ["tech_lead", "ciso"]

    def test_roles_for_unknown_dimension(self) -> None:
        config = RoutingConfig(rules=[])
        assert config.roles_for_dimension("unknown") == []

    def test_dimensions_for_role(self) -> None:
        config = RoutingConfig(rules=[
            RoutingRule(dimension="auth", roles=["tech_lead", "ciso"]),
            RoutingRule(dimension="database", roles=["tech_lead"]),
            RoutingRule(dimension="security", roles=["ciso"]),
        ])
        assert set(config.dimensions_for_role("ciso")) == {"auth", "security"}
        assert set(config.dimensions_for_role("tech_lead")) == {"auth", "database"}

    def test_to_dict(self) -> None:
        config = RoutingConfig(rules=[
            RoutingRule(dimension="auth", roles=["tech_lead"]),
        ])
        d = config.to_dict()
        assert len(d["rules"]) == 1


# ---------------------------------------------------------------------------
# load_default_routing
# ---------------------------------------------------------------------------


class TestLoadDefaultRouting:
    def test_loads_all_dimensions(self) -> None:
        config = load_default_routing()
        dims = {r.dimension for r in config.rules}
        # All 12 dimensions should be covered
        for d in Dimension:
            assert d.value in dims

    def test_auth_routes_to_ciso(self) -> None:
        config = load_default_routing()
        roles = config.roles_for_dimension(Dimension.AUTH.value)
        assert Role.CISO.value in roles
        assert Role.TECH_LEAD.value in roles

    def test_security_routes_to_ciso(self) -> None:
        config = load_default_routing()
        roles = config.roles_for_dimension(Dimension.SECURITY.value)
        assert Role.CISO.value in roles

    def test_api_routes_to_qa(self) -> None:
        config = load_default_routing()
        roles = config.roles_for_dimension(Dimension.API_STYLE.value)
        assert Role.QA.value in roles

    def test_deployment_routes_to_devops(self) -> None:
        config = load_default_routing()
        roles = config.roles_for_dimension(Dimension.DEPLOYMENT.value)
        assert Role.DEVOPS.value in roles


# ---------------------------------------------------------------------------
# load_routing_from_dict
# ---------------------------------------------------------------------------


class TestLoadRoutingFromDict:
    def test_dict_format(self) -> None:
        data = {
            "routing": {
                "database": ["tech_lead"],
                "auth": ["tech_lead", "ciso"],
            }
        }
        config = load_routing_from_dict(data)
        assert config.roles_for_dimension("database") == ["tech_lead"]
        assert config.roles_for_dimension("auth") == ["tech_lead", "ciso"]

    def test_string_role_value(self) -> None:
        data = {"routing": {"database": "tech_lead"}}
        config = load_routing_from_dict(data)
        assert config.roles_for_dimension("database") == ["tech_lead"]

    def test_list_format(self) -> None:
        data = {
            "routing": [
                {"dimension": "database", "roles": ["tech_lead"]},
            ]
        }
        config = load_routing_from_dict(data)
        assert config.roles_for_dimension("database") == ["tech_lead"]

    def test_flat_dict(self) -> None:
        data = {"database": ["tech_lead"]}
        config = load_routing_from_dict(data)
        assert config.roles_for_dimension("database") == ["tech_lead"]


# ---------------------------------------------------------------------------
# route_contradiction
# ---------------------------------------------------------------------------


class TestRouteContradiction:
    def test_database_routes_to_tech_lead(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.DATABASE])
        result = route_contradiction(c)
        assert Role.TECH_LEAD.value in result.assigned_roles

    def test_auth_routes_to_dual(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.AUTH])
        result = route_contradiction(c)
        assert Role.TECH_LEAD.value in result.assigned_roles
        assert Role.CISO.value in result.assigned_roles
        assert result.requires_dual_auth is True

    def test_security_routes_to_dual(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.SECURITY])
        result = route_contradiction(c)
        assert result.requires_dual_auth is True

    def test_single_dimension_single_role(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.CACHING])
        result = route_contradiction(c)
        assert result.requires_dual_auth is False

    def test_multiple_dimensions_union_roles(self) -> None:
        c = _make_contradiction(
            shared_dimensions=[Dimension.DATABASE, Dimension.AUTH]
        )
        result = route_contradiction(c)
        assert Role.TECH_LEAD.value in result.assigned_roles
        assert Role.CISO.value in result.assigned_roles

    def test_fallback_to_tech_lead(self) -> None:
        c = _make_contradiction(shared_dimensions=[])
        # Empty routing config
        config = RoutingConfig(rules=[])
        result = route_contradiction(c, config)
        assert Role.TECH_LEAD.value in result.assigned_roles

    def test_custom_config(self) -> None:
        config = RoutingConfig(rules=[
            RoutingRule(dimension="database", roles=["architect"]),
        ])
        c = _make_contradiction(shared_dimensions=[Dimension.DATABASE])
        result = route_contradiction(c, config)
        assert "architect" in result.assigned_roles

    def test_result_to_dict(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.AUTH])
        result = route_contradiction(c)
        d = result.to_dict()
        assert "contradiction_id" in d
        assert "assigned_roles" in d
        assert "requires_dual_auth" in d

    def test_dimensions_in_result(self) -> None:
        c = _make_contradiction(shared_dimensions=[Dimension.AUTH, Dimension.SECURITY])
        result = route_contradiction(c)
        assert Dimension.AUTH.value in result.dimensions_involved
        assert Dimension.SECURITY.value in result.dimensions_involved


# ---------------------------------------------------------------------------
# route_contradictions (batch)
# ---------------------------------------------------------------------------


class TestRouteContradictions:
    def test_batch_routing(self) -> None:
        contradictions = [
            _make_contradiction(shared_dimensions=[Dimension.DATABASE]),
            _make_contradiction(shared_dimensions=[Dimension.AUTH]),
        ]
        results = route_contradictions(contradictions)
        assert len(results) == 2

    def test_empty_list(self) -> None:
        assert route_contradictions([]) == []

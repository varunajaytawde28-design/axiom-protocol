"""Tests for agent registry."""

from __future__ import annotations

import pytest

from vt_protocol.decisions.agent_registry import (
    AgentRegistration,
    AgentRegistry,
    AgentType,
    DEFAULT_PERMISSIONS,
    Permission,
    PermissionCheck,
)


# ---------------------------------------------------------------------------
# AgentRegistration
# ---------------------------------------------------------------------------


class TestAgentRegistration:
    def test_defaults(self) -> None:
        a = AgentRegistration(name="test-agent")
        assert a.name == "test-agent"
        assert a.active is True
        assert a.version == "0.0.0"

    def test_has_permission(self) -> None:
        a = AgentRegistration(
            name="test",
            permissions=[Permission.READ_DECISIONS.value, Permission.WRITE_DECISIONS.value],
        )
        assert a.has_permission(Permission.READ_DECISIONS.value) is True
        assert a.has_permission(Permission.DEPLOY.value) is False

    def test_to_dict(self) -> None:
        a = AgentRegistration(name="test", agent_type=AgentType.CODING.value)
        d = a.to_dict()
        assert d["name"] == "test"
        assert d["agent_type"] == "coding"
        assert "registered_at" in d


# ---------------------------------------------------------------------------
# PermissionCheck
# ---------------------------------------------------------------------------


class TestPermissionCheck:
    def test_allowed(self) -> None:
        pc = PermissionCheck(agent_id="a1", permission="read", allowed=True)
        assert pc.allowed is True
        assert pc.to_dict()["allowed"] is True

    def test_denied(self) -> None:
        pc = PermissionCheck(agent_id="a1", permission="deploy", allowed=False, reason="Denied")
        assert pc.allowed is False
        assert pc.reason == "Denied"


# ---------------------------------------------------------------------------
# AgentRegistry — registration
# ---------------------------------------------------------------------------


class TestRegistryRegistration:
    def test_register_agent(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("claude-code", AgentType.CODING.value, owner="devteam")
        assert agent.name == "claude-code"
        assert agent.agent_type == AgentType.CODING.value
        assert reg.agent_count == 1

    def test_register_with_custom_id(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", agent_id="custom-123")
        assert agent.agent_id == "custom-123"

    def test_default_permissions_by_type(self) -> None:
        reg = AgentRegistry()
        coding = reg.register("code-agent", AgentType.CODING.value)
        assert Permission.MODIFY_CODE.value in coding.permissions
        assert Permission.READ_DECISIONS.value in coding.permissions

        review = reg.register("review-agent", AgentType.REVIEW.value)
        assert Permission.READ_DECISIONS.value in review.permissions
        assert Permission.MODIFY_CODE.value not in review.permissions

    def test_custom_permissions_override(self) -> None:
        reg = AgentRegistry()
        agent = reg.register(
            "restricted",
            permissions=[Permission.READ_DECISIONS.value],
        )
        assert agent.permissions == [Permission.READ_DECISIONS.value]

    def test_register_with_metadata(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", metadata={"model": "gpt-4"})
        assert agent.metadata["model"] == "gpt-4"

    def test_register_with_capabilities(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", capabilities=["code_gen", "review"])
        assert agent.capabilities == ["code_gen", "review"]


# ---------------------------------------------------------------------------
# AgentRegistry — lookup
# ---------------------------------------------------------------------------


class TestRegistryLookup:
    def test_get_agent(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", agent_id="abc")
        found = reg.get("abc")
        assert found is not None
        assert found.name == "test"

    def test_get_unknown(self) -> None:
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_agents(self) -> None:
        reg = AgentRegistry()
        reg.register("a1", AgentType.CODING.value)
        reg.register("a2", AgentType.REVIEW.value)
        agents = reg.list_agents()
        assert len(agents) == 2

    def test_list_by_type(self) -> None:
        reg = AgentRegistry()
        reg.register("a1", AgentType.CODING.value)
        reg.register("a2", AgentType.REVIEW.value)
        coding = reg.list_agents(agent_type=AgentType.CODING.value)
        assert len(coding) == 1
        assert coding[0].name == "a1"

    def test_active_agents(self) -> None:
        reg = AgentRegistry()
        a1 = reg.register("a1")
        a2 = reg.register("a2")
        reg.deactivate(a1.agent_id)
        assert len(reg.active_agents) == 1


# ---------------------------------------------------------------------------
# AgentRegistry — deactivation
# ---------------------------------------------------------------------------


class TestRegistryDeactivation:
    def test_deactivate(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test")
        assert reg.deactivate(agent.agent_id) is True
        assert reg.get(agent.agent_id).active is False

    def test_deactivate_unknown(self) -> None:
        reg = AgentRegistry()
        assert reg.deactivate("nonexistent") is False

    def test_deactivated_filtered_from_list(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test")
        reg.deactivate(agent.agent_id)
        assert len(reg.list_agents(active_only=True)) == 0
        assert len(reg.list_agents(active_only=False)) == 1


# ---------------------------------------------------------------------------
# AgentRegistry — permission checking
# ---------------------------------------------------------------------------


class TestRegistryPermissions:
    def test_allowed_permission(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", permissions=[Permission.READ_DECISIONS.value])
        result = reg.check_permission(agent.agent_id, Permission.READ_DECISIONS.value)
        assert result.allowed is True

    def test_denied_permission(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", permissions=[Permission.READ_DECISIONS.value])
        result = reg.check_permission(agent.agent_id, Permission.DEPLOY.value)
        assert result.allowed is False
        assert "not granted" in result.reason

    def test_unregistered_agent(self) -> None:
        reg = AgentRegistry()
        result = reg.check_permission("unknown", Permission.READ_DECISIONS.value)
        assert result.allowed is False
        assert "not registered" in result.reason

    def test_deactivated_agent(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", permissions=[Permission.READ_DECISIONS.value])
        reg.deactivate(agent.agent_id)
        result = reg.check_permission(agent.agent_id, Permission.READ_DECISIONS.value)
        assert result.allowed is False
        assert "deactivated" in result.reason

    def test_update_permissions(self) -> None:
        reg = AgentRegistry()
        agent = reg.register("test", permissions=[])
        reg.update_permissions(agent.agent_id, [Permission.DEPLOY.value])
        result = reg.check_permission(agent.agent_id, Permission.DEPLOY.value)
        assert result.allowed is True

    def test_update_unknown_returns_false(self) -> None:
        reg = AgentRegistry()
        assert reg.update_permissions("unknown", []) is False


# ---------------------------------------------------------------------------
# AgentRegistry — to_dict
# ---------------------------------------------------------------------------


class TestRegistryToDict:
    def test_to_dict(self) -> None:
        reg = AgentRegistry()
        reg.register("a1")
        reg.register("a2")
        d = reg.to_dict()
        assert d["total"] == 2
        assert d["active"] == 2
        assert len(d["agents"]) == 2


# ---------------------------------------------------------------------------
# DEFAULT_PERMISSIONS
# ---------------------------------------------------------------------------


class TestDefaultPermissions:
    def test_all_agent_types_have_defaults(self) -> None:
        for at in AgentType:
            assert at.value in DEFAULT_PERMISSIONS

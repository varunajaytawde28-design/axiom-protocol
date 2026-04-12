"""Tests for open agent identity specification."""

from __future__ import annotations

import pytest

from vt_protocol.identity.spec import (
    AgentIdentity,
    AgentRegistry,
    AgentType,
    CapabilityType,
    TrustLevel,
    TRUST_CAPABILITIES,
)


# ---------------------------------------------------------------------------
# AgentType enum
# ---------------------------------------------------------------------------


class TestAgentType:
    def test_values(self):
        assert AgentType.CODING.value == "coding"
        assert AgentType.REVIEW.value == "review"
        assert AgentType.HUMAN.value == "human"


# ---------------------------------------------------------------------------
# TrustLevel enum
# ---------------------------------------------------------------------------


class TestTrustLevel:
    def test_values(self):
        assert TrustLevel.UNTRUSTED.value == "untrusted"
        assert TrustLevel.PRIVILEGED.value == "privileged"


# ---------------------------------------------------------------------------
# TRUST_CAPABILITIES
# ---------------------------------------------------------------------------


class TestTrustCapabilities:
    def test_untrusted_minimal(self):
        caps = TRUST_CAPABILITIES[TrustLevel.UNTRUSTED]
        assert CapabilityType.READ_DECISIONS in caps
        assert CapabilityType.WRITE_DECISIONS not in caps

    def test_privileged_all(self):
        caps = TRUST_CAPABILITIES[TrustLevel.PRIVILEGED]
        assert caps == set(CapabilityType)

    def test_trust_levels_ordered(self):
        for level in TrustLevel:
            assert level in TRUST_CAPABILITIES


# ---------------------------------------------------------------------------
# AgentIdentity
# ---------------------------------------------------------------------------


class TestAgentIdentity:
    def test_default_id(self):
        a = AgentIdentity(name="test")
        assert len(a.agent_id) == 16

    def test_allowed_capabilities_from_trust(self):
        a = AgentIdentity(
            name="test",
            trust_level=TrustLevel.BASIC,
        )
        caps = a.allowed_capabilities
        assert CapabilityType.READ_DECISIONS.value in caps
        assert CapabilityType.WRITE_DECISIONS.value in caps

    def test_custom_capabilities_added(self):
        a = AgentIdentity(
            name="test",
            trust_level=TrustLevel.UNTRUSTED,
            capabilities=["custom_action"],
        )
        assert "custom_action" in a.allowed_capabilities

    def test_has_capability(self):
        a = AgentIdentity(
            name="test",
            trust_level=TrustLevel.TRUSTED,
        )
        assert a.has_capability(CapabilityType.EXECUTE_CODE.value)
        assert not a.has_capability(CapabilityType.MODIFY_GOVERNANCE.value)

    def test_to_dict(self):
        a = AgentIdentity(
            name="test-agent",
            agent_type=AgentType.CODING,
            organization="acme",
        )
        d = a.to_dict()
        assert d["name"] == "test-agent"
        assert d["agent_type"] == "coding"
        assert d["organization"] == "acme"

    def test_public_key_truncated_in_dict(self):
        a = AgentIdentity(
            name="test",
            public_key="a" * 100,
        )
        d = a.to_dict()
        assert d["public_key"].endswith("...")
        assert len(d["public_key"]) < 100


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


class TestAgentRegistry:
    def test_empty(self):
        r = AgentRegistry()
        assert r.count == 0

    def test_register_and_get(self):
        r = AgentRegistry()
        a = AgentIdentity(agent_id="a1", name="Agent 1")
        r.register(a)
        assert r.count == 1
        assert r.get("a1") is not None

    def test_get_not_found(self):
        r = AgentRegistry()
        assert r.get("nonexistent") is None

    def test_find_by_type(self):
        r = AgentRegistry()
        r.register(AgentIdentity(agent_id="a1", name="Coder", agent_type=AgentType.CODING))
        r.register(AgentIdentity(agent_id="a2", name="Reviewer", agent_type=AgentType.REVIEW))
        r.register(AgentIdentity(agent_id="a3", name="Coder2", agent_type=AgentType.CODING))

        coders = r.find_by_type(AgentType.CODING)
        assert len(coders) == 2

    def test_find_by_org(self):
        r = AgentRegistry()
        r.register(AgentIdentity(agent_id="a1", name="A", organization="acme"))
        r.register(AgentIdentity(agent_id="a2", name="B", organization="other"))

        acme = r.find_by_org("acme")
        assert len(acme) == 1

    def test_find_by_capability(self):
        r = AgentRegistry()
        r.register(AgentIdentity(
            agent_id="a1", name="Admin",
            trust_level=TrustLevel.PRIVILEGED,
        ))
        r.register(AgentIdentity(
            agent_id="a2", name="Basic",
            trust_level=TrustLevel.BASIC,
        ))

        admins = r.find_by_capability(CapabilityType.MANAGE_AGENTS.value)
        assert len(admins) == 1

    def test_remove(self):
        r = AgentRegistry()
        r.register(AgentIdentity(agent_id="a1", name="A"))
        assert r.remove("a1") is True
        assert r.count == 0

    def test_remove_not_found(self):
        r = AgentRegistry()
        assert r.remove("nonexistent") is False

    def test_to_dict(self):
        r = AgentRegistry()
        r.register(AgentIdentity(agent_id="a1", name="A"))
        d = r.to_dict()
        assert d["count"] == 1
        assert "a1" in d["agents"]

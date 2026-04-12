"""Tests for agent identity validator."""

from __future__ import annotations

import pytest

from vt_protocol.identity.spec import (
    AgentIdentity,
    AgentType,
    CapabilityType,
    TrustLevel,
)
from vt_protocol.identity.validator import (
    CapabilityCheckResult,
    IdentityValidationResult,
    IdentityValidator,
    ValidationError,
)


# ---------------------------------------------------------------------------
# IdentityValidator — validate_identity
# ---------------------------------------------------------------------------


class TestValidateIdentity:
    def test_valid_identity(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test-agent")
        result = v.validate_identity(a)
        assert result.valid is True
        assert len(result.errors) == 0

    def test_missing_name(self):
        v = IdentityValidator()
        a = AgentIdentity(name="")
        result = v.validate_identity(a)
        assert result.valid is False
        assert any(e.field == "name" for e in result.errors)

    def test_missing_agent_id(self):
        v = IdentityValidator()
        a = AgentIdentity(agent_id="", name="test")
        result = v.validate_identity(a)
        assert result.valid is False
        assert any(e.field == "agent_id" for e in result.errors)

    def test_invalid_version_warning(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test", version="not-semver")
        result = v.validate_identity(a)
        assert result.valid is True  # warning, not error
        assert any(w.field == "version" for w in result.warnings)

    def test_valid_semver(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test", version="2.1.0")
        result = v.validate_identity(a)
        assert not any(w.field == "version" for w in result.warnings)

    def test_capability_exceeds_trust_warning(self):
        v = IdentityValidator()
        a = AgentIdentity(
            name="test",
            trust_level=TrustLevel.UNTRUSTED,
            capabilities=[CapabilityType.EXECUTE_CODE.value],
        )
        result = v.validate_identity(a)
        assert any(
            w.field == "capabilities" and "exceeds" in w.message
            for w in result.warnings
        )

    def test_non_standard_capability_warning(self):
        v = IdentityValidator()
        a = AgentIdentity(
            name="test",
            capabilities=["custom_nonstandard"],
        )
        result = v.validate_identity(a)
        assert any(
            w.field == "capabilities" and "non-standard" in w.message.lower()
            for w in result.warnings
        )

    def test_public_key_requires_org(self):
        v = IdentityValidator()
        a = AgentIdentity(
            name="test",
            public_key="ssh-rsa AAAA...",
            organization="",
        )
        result = v.validate_identity(a)
        assert result.valid is False
        assert any(e.field == "organization" for e in result.errors)

    def test_public_key_with_org_valid(self):
        v = IdentityValidator()
        a = AgentIdentity(
            name="test",
            public_key="ssh-rsa AAAA...",
            organization="acme",
        )
        result = v.validate_identity(a)
        assert result.valid is True


# ---------------------------------------------------------------------------
# IdentityValidator — check_capability
# ---------------------------------------------------------------------------


class TestCheckCapability:
    def test_allowed(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test", trust_level=TrustLevel.TRUSTED)
        result = v.check_capability(a, CapabilityType.EXECUTE_CODE.value)
        assert result.allowed is True

    def test_not_allowed(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test", trust_level=TrustLevel.UNTRUSTED)
        result = v.check_capability(a, CapabilityType.EXECUTE_CODE.value)
        assert result.allowed is False

    def test_result_fields(self):
        v = IdentityValidator()
        a = AgentIdentity(agent_id="a1", name="test", trust_level=TrustLevel.BASIC)
        result = v.check_capability(a, CapabilityType.READ_DECISIONS.value)
        assert result.agent_id == "a1"
        assert result.trust_level == "basic"


# ---------------------------------------------------------------------------
# IdentityValidator — check_operation
# ---------------------------------------------------------------------------


class TestCheckOperation:
    def test_read_operation(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test", trust_level=TrustLevel.BASIC)
        result = v.check_operation(a, "read")
        assert result.allowed is True

    def test_execute_operation(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test", trust_level=TrustLevel.UNTRUSTED)
        result = v.check_operation(a, "execute")
        assert result.allowed is False

    def test_unknown_operation(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test", trust_level=TrustLevel.BASIC)
        result = v.check_operation(a, "unknown_op")
        assert result.allowed is False


# ---------------------------------------------------------------------------
# Policy-based checks
# ---------------------------------------------------------------------------


class TestPolicyChecks:
    def test_min_trust_level_policy(self):
        v = IdentityValidator()
        v.set_policy({"min_trust_level": "verified"})
        a = AgentIdentity(name="test", trust_level=TrustLevel.BASIC)
        result = v.validate_identity(a)
        assert result.valid is False
        assert not result.trust_level_appropriate

    def test_min_trust_level_met(self):
        v = IdentityValidator()
        v.set_policy({"min_trust_level": "basic"})
        a = AgentIdentity(name="test", trust_level=TrustLevel.VERIFIED)
        result = v.validate_identity(a)
        assert result.valid is True

    def test_required_organization_policy(self):
        v = IdentityValidator()
        v.set_policy({"required_organization": "acme"})
        a = AgentIdentity(name="test", organization="other")
        result = v.validate_identity(a)
        assert result.valid is False

    def test_blocked_agent_type_policy(self):
        v = IdentityValidator()
        v.set_policy({"blocked_agent_types": ["scan"]})
        a = AgentIdentity(name="test", agent_type=AgentType.SCAN)
        result = v.validate_identity(a)
        assert result.valid is False

    def test_no_policy(self):
        v = IdentityValidator()
        a = AgentIdentity(name="test")
        result = v.validate_identity(a)
        assert result.valid is True

"""Tests for cross-organization agent federation."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from vt_protocol.identity.spec import AgentIdentity, AgentType, TrustLevel
from vt_protocol.identity.federation import (
    Federation,
    FederationMember,
    FederationResult,
    FederationToken,
)


def _make_member(org_id: str = "org-1", org_name: str = "Acme") -> FederationMember:
    return FederationMember(
        org_id=org_id,
        org_name=org_name,
        public_key="ssh-rsa AAAA" + "B" * 50,
    )


def _make_agent(org: str = "org-1") -> AgentIdentity:
    return AgentIdentity(
        name="test-agent",
        organization=org,
        agent_type=AgentType.CODING,
        trust_level=TrustLevel.VERIFIED,
    )


# ---------------------------------------------------------------------------
# FederationMember
# ---------------------------------------------------------------------------


class TestFederationMember:
    def test_defaults(self):
        m = FederationMember()
        assert m.active is True

    def test_to_dict(self):
        m = _make_member()
        d = m.to_dict()
        assert d["org_id"] == "org-1"
        assert d["public_key"].endswith("...")


# ---------------------------------------------------------------------------
# FederationToken
# ---------------------------------------------------------------------------


class TestFederationToken:
    def test_not_expired_by_default(self):
        t = FederationToken()
        assert not t.is_expired

    def test_expired(self):
        t = FederationToken(
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert t.is_expired

    def test_to_dict(self):
        t = FederationToken(token_id="t1", agent_id="a1")
        d = t.to_dict()
        assert d["token_id"] == "t1"


# ---------------------------------------------------------------------------
# Federation — member management
# ---------------------------------------------------------------------------


class TestFederationMembers:
    def test_register_member(self):
        fed = Federation(org_id="home", org_name="Home Org")
        member = _make_member()
        assert fed.register_member(member) is True
        assert fed.member_count == 1

    def test_register_requires_public_key(self):
        fed = Federation(org_id="home", org_name="Home Org")
        member = FederationMember(org_id="org-1", org_name="Acme", public_key="")
        assert fed.register_member(member) is False

    def test_register_requires_org_id(self):
        fed = Federation(org_id="home", org_name="Home Org")
        member = FederationMember(org_id="", public_key="abc")
        assert fed.register_member(member) is False

    def test_get_member(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        assert fed.get_member("org-1") is not None
        assert fed.get_member("nonexistent") is None

    def test_remove_member(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        assert fed.remove_member("org-1") is True
        member = fed.get_member("org-1")
        assert member.active is False

    def test_active_members(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        fed.register_member(_make_member("org-2"))
        fed.remove_member("org-1")
        assert len(fed.active_members) == 1


# ---------------------------------------------------------------------------
# Federation — token management
# ---------------------------------------------------------------------------


class TestFederationTokens:
    def test_issue_token(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        agent = _make_agent("org-1")
        result = fed.issue_token(agent)
        assert result.success is True
        assert result.token is not None
        assert result.token.agent_id == agent.agent_id

    def test_issue_token_non_member(self):
        fed = Federation(org_id="home", org_name="Home Org")
        agent = _make_agent("unknown-org")
        result = fed.issue_token(agent)
        assert result.success is False
        assert "not a federation member" in result.message

    def test_issue_token_inactive_member(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        fed.remove_member("org-1")
        agent = _make_agent("org-1")
        result = fed.issue_token(agent)
        assert result.success is False
        assert "inactive" in result.message.lower()

    def test_verify_token(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        agent = _make_agent("org-1")
        issue_result = fed.issue_token(agent)
        verify_result = fed.verify_token(issue_result.token.token_id)
        assert verify_result.success is True

    def test_verify_nonexistent_token(self):
        fed = Federation(org_id="home", org_name="Home Org")
        result = fed.verify_token("nonexistent")
        assert result.success is False

    def test_revoke_token(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        agent = _make_agent("org-1")
        issue_result = fed.issue_token(agent)
        token_id = issue_result.token.token_id

        assert fed.revoke_token(token_id) is True
        verify_result = fed.verify_token(token_id)
        assert verify_result.success is False

    def test_revoke_nonexistent(self):
        fed = Federation(org_id="home", org_name="Home Org")
        assert fed.revoke_token("nonexistent") is False

    def test_token_claims(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        agent = _make_agent("org-1")
        result = fed.issue_token(agent)
        claims = result.token.claims
        assert claims["trust_level"] == "verified"
        assert claims["agent_type"] == "coding"

    def test_list_tokens(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        a1 = _make_agent("org-1")
        a2 = _make_agent("org-1")
        fed.issue_token(a1)
        fed.issue_token(a2)
        tokens = fed.list_tokens()
        assert len(tokens) == 2

    def test_list_tokens_by_org(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        fed.register_member(_make_member("org-2", "Org 2"))
        fed.issue_token(_make_agent("org-1"))
        fed.issue_token(_make_agent("org-2"))
        tokens = fed.list_tokens(org_id="org-1")
        assert len(tokens) == 1

    def test_member_removal_invalidates_tokens(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        agent = _make_agent("org-1")
        issue_result = fed.issue_token(agent)
        token_id = issue_result.token.token_id

        fed.remove_member("org-1")
        verify_result = fed.verify_token(token_id)
        assert verify_result.success is False


# ---------------------------------------------------------------------------
# Federation — to_dict
# ---------------------------------------------------------------------------


class TestFederationToDict:
    def test_to_dict(self):
        fed = Federation(org_id="home", org_name="Home Org")
        fed.register_member(_make_member("org-1"))
        d = fed.to_dict()
        assert d["org_id"] == "home"
        assert d["member_count"] == 1

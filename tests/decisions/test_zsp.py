"""Tests for Zero Standing Privileges — capability tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.decisions.zsp import (
    DEFAULT_TTL_SECONDS,
    CapabilityToken,
    TokenIssuer,
    TokenValidation,
)


# ---------------------------------------------------------------------------
# CapabilityToken
# ---------------------------------------------------------------------------


class TestCapabilityToken:
    def test_defaults(self) -> None:
        token = CapabilityToken(agent_id="agent-1", permissions=["read"])
        assert token.agent_id == "agent-1"
        assert token.permissions == ["read"]
        assert token.revoked is False

    def test_is_valid_when_fresh(self) -> None:
        token = CapabilityToken(agent_id="a1", permissions=["read"])
        assert token.is_valid is True
        assert token.is_expired is False

    def test_is_expired_after_ttl(self) -> None:
        token = CapabilityToken(
            agent_id="a1",
            permissions=["read"],
            issued_at=datetime.now(timezone.utc) - timedelta(seconds=600),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=300),
        )
        assert token.is_expired is True
        assert token.is_valid is False

    def test_remaining_seconds(self) -> None:
        token = CapabilityToken(
            agent_id="a1",
            permissions=["read"],
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=120),
        )
        assert token.remaining_seconds > 100

    def test_remaining_seconds_expired(self) -> None:
        token = CapabilityToken(
            agent_id="a1",
            permissions=["read"],
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        assert token.remaining_seconds == 0.0

    def test_revoked_not_valid(self) -> None:
        token = CapabilityToken(agent_id="a1", permissions=["read"], revoked=True)
        assert token.is_valid is False

    def test_canonical_bytes_deterministic(self) -> None:
        token = CapabilityToken(agent_id="a1", permissions=["read", "write"])
        assert token.canonical_bytes() == token.canonical_bytes()

    def test_content_hash(self) -> None:
        token = CapabilityToken(agent_id="a1", permissions=["read"])
        assert len(token.content_hash()) == 64  # SHA-256 hex

    def test_to_dict(self) -> None:
        token = CapabilityToken(agent_id="a1", permissions=["read"], scope="auth files")
        d = token.to_dict()
        assert d["agent_id"] == "a1"
        assert d["scope"] == "auth files"
        assert "is_valid" in d
        assert "remaining_seconds" in d


# ---------------------------------------------------------------------------
# TokenValidation
# ---------------------------------------------------------------------------


class TestTokenValidation:
    def test_valid(self) -> None:
        v = TokenValidation(token_id="t1", valid=True, permissions=["read"])
        assert v.valid is True

    def test_invalid(self) -> None:
        v = TokenValidation(token_id="t1", valid=False, reason="Expired")
        assert v.valid is False

    def test_to_dict(self) -> None:
        v = TokenValidation(token_id="t1", valid=True, permissions=["read"])
        d = v.to_dict()
        assert d["token_id"] == "t1"
        assert d["valid"] is True


# ---------------------------------------------------------------------------
# TokenIssuer — issue
# ---------------------------------------------------------------------------


class TestTokenIssuerIssue:
    def test_issue_token(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("agent-1", ["read_decisions"], scope="task-42")
        assert token.agent_id == "agent-1"
        assert token.permissions == ["read_decisions"]
        assert token.scope == "task-42"
        assert token.is_valid is True

    def test_issue_custom_ttl(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("a1", ["read"], ttl_seconds=60)
        assert token.remaining_seconds <= 60
        assert token.remaining_seconds > 50

    def test_active_count(self) -> None:
        issuer = TokenIssuer()
        issuer.issue("a1", ["read"])
        issuer.issue("a2", ["write"])
        assert issuer.active_count == 2

    def test_issue_with_signing_key(self) -> None:
        try:
            from nacl.signing import SigningKey
            key = SigningKey.generate()
            issuer = TokenIssuer(signing_key=key)
            token = issuer.issue("a1", ["read"])
            assert len(token.signature) == 64  # Ed25519 signature
        except ImportError:
            pytest.skip("PyNaCl not installed")


# ---------------------------------------------------------------------------
# TokenIssuer — validate
# ---------------------------------------------------------------------------


class TestTokenIssuerValidate:
    def test_validate_valid_token(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("a1", ["read"])
        result = issuer.validate(token.token_id)
        assert result.valid is True
        assert result.permissions == ["read"]

    def test_validate_unknown_token(self) -> None:
        issuer = TokenIssuer()
        result = issuer.validate("nonexistent")
        assert result.valid is False
        assert "not found" in result.reason

    def test_validate_expired_token(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("a1", ["read"], ttl_seconds=0)
        # Token expires immediately
        result = issuer.validate(token.token_id)
        assert result.valid is False
        assert "expired" in result.reason

    def test_validate_revoked_token(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("a1", ["read"])
        issuer.revoke(token.token_id)
        result = issuer.validate(token.token_id)
        assert result.valid is False
        assert "revoked" in result.reason


# ---------------------------------------------------------------------------
# TokenIssuer — check_permission
# ---------------------------------------------------------------------------


class TestTokenIssuerCheckPermission:
    def test_has_permission(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("a1", ["read_decisions", "write_decisions"])
        result = issuer.check_permission(token.token_id, "read_decisions")
        assert result.valid is True

    def test_missing_permission(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("a1", ["read_decisions"])
        result = issuer.check_permission(token.token_id, "deploy")
        assert result.valid is False
        assert "not in token scope" in result.reason

    def test_invalid_token_permission_check(self) -> None:
        issuer = TokenIssuer()
        result = issuer.check_permission("bad", "read")
        assert result.valid is False


# ---------------------------------------------------------------------------
# TokenIssuer — revocation
# ---------------------------------------------------------------------------


class TestTokenIssuerRevocation:
    def test_revoke(self) -> None:
        issuer = TokenIssuer()
        token = issuer.issue("a1", ["read"])
        assert issuer.revoke(token.token_id) is True
        assert issuer.active_count == 0

    def test_revoke_unknown(self) -> None:
        issuer = TokenIssuer()
        assert issuer.revoke("unknown") is False

    def test_revoke_all_for_agent(self) -> None:
        issuer = TokenIssuer()
        issuer.issue("a1", ["read"])
        issuer.issue("a1", ["write"])
        issuer.issue("a2", ["read"])
        count = issuer.revoke_all_for_agent("a1")
        assert count == 2
        assert issuer.active_count == 1


# ---------------------------------------------------------------------------
# TokenIssuer — cleanup
# ---------------------------------------------------------------------------


class TestTokenIssuerCleanup:
    def test_cleanup_expired(self) -> None:
        issuer = TokenIssuer()
        # Issue a token that expires immediately
        issuer.issue("a1", ["read"], ttl_seconds=0)
        # Issue a valid token
        issuer.issue("a2", ["read"], ttl_seconds=300)
        cleaned = issuer.cleanup_expired()
        assert cleaned == 1
        assert issuer.active_count == 1

    def test_cleanup_nothing(self) -> None:
        issuer = TokenIssuer()
        issuer.issue("a1", ["read"])
        assert issuer.cleanup_expired() == 0

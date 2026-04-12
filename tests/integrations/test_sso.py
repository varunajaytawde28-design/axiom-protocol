"""Tests for SSO/SCIM integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.integrations.sso import (
    AuthProvider,
    OIDCTokenClaims,
    SAMLAssertion,
    SCIMUser,
    SSOConfig,
    User,
    UserStatus,
    UserStore,
)


# ---------------------------------------------------------------------------
# SSOConfig
# ---------------------------------------------------------------------------


class TestSSOConfig:
    def test_defaults(self) -> None:
        config = SSOConfig()
        assert config.provider == "local"
        assert config.scim_enabled is False

    def test_to_dict(self) -> None:
        config = SSOConfig(
            provider="saml",
            entity_id="https://idp.example.com",
            sso_url="https://idp.example.com/sso",
            certificate="MIID...",
        )
        d = config.to_dict()
        assert d["provider"] == "saml"
        assert d["has_certificate"] is True


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class TestUser:
    def test_is_active(self) -> None:
        u = User(email="test@example.com")
        assert u.is_active is True

    def test_inactive_user(self) -> None:
        u = User(email="test@example.com", status=UserStatus.INACTIVE.value)
        assert u.is_active is False

    def test_to_dict(self) -> None:
        u = User(email="test@example.com", display_name="Test", roles=["admin"])
        d = u.to_dict()
        assert d["email"] == "test@example.com"
        assert d["roles"] == ["admin"]
        assert d["is_active"] is True


# ---------------------------------------------------------------------------
# SCIMUser
# ---------------------------------------------------------------------------


class TestSCIMUser:
    def test_primary_email(self) -> None:
        u = SCIMUser(
            user_name="jdoe",
            emails=[
                {"value": "secondary@example.com", "primary": False},
                {"value": "primary@example.com", "primary": True},
            ],
        )
        assert u.primary_email == "primary@example.com"

    def test_primary_email_fallback(self) -> None:
        u = SCIMUser(
            user_name="jdoe",
            emails=[{"value": "only@example.com"}],
        )
        assert u.primary_email == "only@example.com"

    def test_no_emails(self) -> None:
        u = SCIMUser(user_name="jdoe")
        assert u.primary_email == ""

    def test_to_dict(self) -> None:
        u = SCIMUser(user_name="jdoe", display_name="John Doe")
        d = u.to_dict()
        assert d["userName"] == "jdoe"
        assert d["displayName"] == "John Doe"


# ---------------------------------------------------------------------------
# SAMLAssertion
# ---------------------------------------------------------------------------


class TestSAMLAssertion:
    def test_valid(self) -> None:
        a = SAMLAssertion(
            subject="user123",
            issuer="https://idp.example.com",
            email="user@example.com",
            not_before=datetime.now(timezone.utc) - timedelta(hours=1),
            not_after=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert a.is_valid is True

    def test_missing_subject(self) -> None:
        a = SAMLAssertion(issuer="https://idp.example.com")
        assert a.is_valid is False

    def test_expired(self) -> None:
        a = SAMLAssertion(
            subject="user",
            issuer="idp",
            not_after=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert a.is_valid is False

    def test_not_yet_valid(self) -> None:
        a = SAMLAssertion(
            subject="user",
            issuer="idp",
            not_before=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        assert a.is_valid is False

    def test_to_dict(self) -> None:
        a = SAMLAssertion(subject="user", issuer="idp", roles=["admin"])
        d = a.to_dict()
        assert d["subject"] == "user"
        assert d["roles"] == ["admin"]


# ---------------------------------------------------------------------------
# OIDCTokenClaims
# ---------------------------------------------------------------------------


class TestOIDCTokenClaims:
    def test_not_expired(self) -> None:
        future = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        claims = OIDCTokenClaims(sub="user", iss="idp", exp=future)
        assert claims.is_expired is False

    def test_expired(self) -> None:
        past = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        claims = OIDCTokenClaims(sub="user", iss="idp", exp=past)
        assert claims.is_expired is True

    def test_zero_exp_is_expired(self) -> None:
        claims = OIDCTokenClaims(sub="user", iss="idp")
        assert claims.is_expired is True

    def test_to_dict(self) -> None:
        claims = OIDCTokenClaims(sub="user", iss="idp", email="u@x.com")
        d = claims.to_dict()
        assert d["sub"] == "user"
        assert d["email"] == "u@x.com"


# ---------------------------------------------------------------------------
# UserStore
# ---------------------------------------------------------------------------


class TestUserStore:
    def test_create_user(self) -> None:
        store = UserStore()
        user = store.create_user("test@example.com", "Test User")
        assert store.count == 1
        assert user.email == "test@example.com"

    def test_get_by_id(self) -> None:
        store = UserStore()
        user = store.create_user("test@example.com")
        found = store.get_by_id(user.user_id)
        assert found is not None
        assert found.email == "test@example.com"

    def test_get_by_email(self) -> None:
        store = UserStore()
        store.create_user("test@example.com")
        found = store.get_by_email("test@example.com")
        assert found is not None

    def test_get_by_external_id(self) -> None:
        store = UserStore()
        store.create_user("test@example.com", external_id="ext-123")
        found = store.get_by_external_id("ext-123")
        assert found is not None

    def test_update_user(self) -> None:
        store = UserStore()
        user = store.create_user("test@example.com")
        updated = store.update_user(user.user_id, display_name="Updated Name")
        assert updated is not None
        assert updated.display_name == "Updated Name"

    def test_update_nonexistent(self) -> None:
        store = UserStore()
        assert store.update_user("bad-id", display_name="X") is None

    def test_deactivate_user(self) -> None:
        store = UserStore()
        user = store.create_user("test@example.com")
        assert store.deactivate_user(user.user_id) is True
        assert store.get_by_id(user.user_id).is_active is False

    def test_list_active_only(self) -> None:
        store = UserStore()
        u1 = store.create_user("a@x.com")
        u2 = store.create_user("b@x.com")
        store.deactivate_user(u1.user_id)
        active = store.list_users(active_only=True)
        assert len(active) == 1

    def test_list_by_provider(self) -> None:
        store = UserStore()
        store.create_user("a@x.com", provider="saml")
        store.create_user("b@x.com", provider="oidc")
        saml_users = store.list_users(provider="saml")
        assert len(saml_users) == 1


class TestSCIMProvisioning:
    def test_provision_new_user(self) -> None:
        store = UserStore()
        scim = SCIMUser(
            user_name="jdoe",
            display_name="John Doe",
            emails=[{"value": "jdoe@example.com", "primary": True}],
            external_id="ext-456",
        )
        user = store.provision_from_scim(scim)
        assert user.email == "jdoe@example.com"
        assert user.display_name == "John Doe"
        assert store.count == 1

    def test_provision_updates_existing(self) -> None:
        store = UserStore()
        store.create_user("jdoe@example.com", "Old Name", external_id="ext-456")
        scim = SCIMUser(
            user_name="jdoe",
            display_name="New Name",
            emails=[{"value": "jdoe@example.com", "primary": True}],
            external_id="ext-456",
        )
        user = store.provision_from_scim(scim)
        assert user.display_name == "New Name"
        assert store.count == 1  # Updated, not duplicated

    def test_provision_deactivate(self) -> None:
        store = UserStore()
        store.create_user("jdoe@example.com", external_id="ext-789")
        scim = SCIMUser(
            user_name="jdoe",
            emails=[{"value": "jdoe@example.com", "primary": True}],
            external_id="ext-789",
            active=False,
        )
        user = store.provision_from_scim(scim)
        assert user.status == UserStatus.INACTIVE.value


class TestSAMLProvisioning:
    def test_provision_new_user(self) -> None:
        store = UserStore()
        assertion = SAMLAssertion(
            subject="user123",
            issuer="idp",
            email="saml@example.com",
            display_name="SAML User",
            roles=["admin"],
        )
        user = store.provision_from_saml(assertion)
        assert user.email == "saml@example.com"
        assert user.provider == "saml"
        assert "admin" in user.roles

    def test_provision_updates_existing(self) -> None:
        store = UserStore()
        store.create_user("saml@example.com", "Old Name")
        assertion = SAMLAssertion(
            subject="user123",
            issuer="idp",
            email="saml@example.com",
            display_name="Updated Name",
            roles=["tech_lead"],
        )
        user = store.provision_from_saml(assertion)
        assert user.display_name == "Updated Name"
        assert user.roles == ["tech_lead"]
        assert user.last_login is not None

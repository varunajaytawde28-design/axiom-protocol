"""SSO/SCIM integration — SAML 2.0, OIDC, and SCIM 2.0 user provisioning.

Provides the data models and validation logic for enterprise SSO.
Actual IdP communication requires deployment-specific HTTP clients.

From SPEC Phase 3 Enterprise Features: "SSO (SAML/OIDC) + SCIM provisioning."
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class AuthProvider(str, Enum):
    """Supported authentication providers."""

    SAML = "saml"
    OIDC = "oidc"
    LOCAL = "local"


class UserStatus(str, Enum):
    """User account status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"


@dataclass
class SSOConfig:
    """SSO configuration for a tenant."""

    provider: str = AuthProvider.LOCAL.value
    entity_id: str = ""
    sso_url: str = ""
    certificate: str = ""
    # OIDC specific
    client_id: str = ""
    client_secret: str = ""
    issuer: str = ""
    # SCIM
    scim_enabled: bool = False
    scim_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "entity_id": self.entity_id,
            "sso_url": self.sso_url,
            "has_certificate": bool(self.certificate),
            "client_id": self.client_id,
            "issuer": self.issuer,
            "scim_enabled": self.scim_enabled,
        }


@dataclass
class User:
    """A user in the system (provisioned via SCIM or manual)."""

    user_id: str = field(default_factory=lambda: uuid4().hex[:16])
    email: str = ""
    display_name: str = ""
    roles: list[str] = field(default_factory=list)
    status: str = UserStatus.ACTIVE.value
    provider: str = AuthProvider.LOCAL.value
    external_id: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_login: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "roles": self.roles,
            "status": self.status,
            "provider": self.provider,
            "external_id": self.external_id,
            "created_at": self.created_at.isoformat(),
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "is_active": self.is_active,
        }


@dataclass
class SCIMUser:
    """SCIM 2.0 user representation for provisioning."""

    schemas: list[str] = field(
        default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:User"]
    )
    user_name: str = ""
    display_name: str = ""
    emails: list[dict[str, Any]] = field(default_factory=list)
    active: bool = True
    external_id: str = ""
    groups: list[str] = field(default_factory=list)

    @property
    def primary_email(self) -> str:
        for e in self.emails:
            if e.get("primary"):
                return e.get("value", "")
        return self.emails[0].get("value", "") if self.emails else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemas": self.schemas,
            "userName": self.user_name,
            "displayName": self.display_name,
            "emails": self.emails,
            "active": self.active,
            "externalId": self.external_id,
            "groups": self.groups,
        }


@dataclass
class SAMLAssertion:
    """Parsed SAML 2.0 assertion attributes."""

    subject: str = ""
    issuer: str = ""
    email: str = ""
    display_name: str = ""
    roles: list[str] = field(default_factory=list)
    session_index: str = ""
    not_before: datetime | None = None
    not_after: datetime | None = None

    @property
    def is_valid(self) -> bool:
        if not self.subject or not self.issuer:
            return False
        now = datetime.now(timezone.utc)
        if self.not_before and now < self.not_before:
            return False
        if self.not_after and now > self.not_after:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "email": self.email,
            "display_name": self.display_name,
            "roles": self.roles,
            "is_valid": self.is_valid,
        }


@dataclass
class OIDCTokenClaims:
    """Parsed OIDC ID token claims."""

    sub: str = ""
    iss: str = ""
    email: str = ""
    name: str = ""
    roles: list[str] = field(default_factory=list)
    exp: int = 0
    iat: int = 0

    @property
    def is_expired(self) -> bool:
        if self.exp == 0:
            return True
        now = datetime.now(timezone.utc).timestamp()
        return now > self.exp

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "iss": self.iss,
            "email": self.email,
            "name": self.name,
            "roles": self.roles,
            "is_expired": self.is_expired,
        }


class UserStore:
    """In-memory user store for SCIM provisioning."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    @property
    def count(self) -> int:
        return len(self._users)

    def create_user(
        self,
        email: str,
        display_name: str = "",
        *,
        roles: list[str] | None = None,
        provider: str = AuthProvider.LOCAL.value,
        external_id: str = "",
    ) -> User:
        """Create a new user."""
        user = User(
            email=email,
            display_name=display_name or email.split("@")[0],
            roles=roles or [],
            provider=provider,
            external_id=external_id,
        )
        self._users[user.user_id] = user
        return user

    def get_by_id(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_by_email(self, email: str) -> User | None:
        for u in self._users.values():
            if u.email == email:
                return u
        return None

    def get_by_external_id(self, external_id: str) -> User | None:
        for u in self._users.values():
            if u.external_id == external_id:
                return u
        return None

    def update_user(self, user_id: str, **kwargs: Any) -> User | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        return user

    def deactivate_user(self, user_id: str) -> bool:
        user = self._users.get(user_id)
        if user:
            user.status = UserStatus.INACTIVE.value
            return True
        return False

    def list_users(
        self,
        *,
        active_only: bool = True,
        provider: str | None = None,
    ) -> list[User]:
        users = list(self._users.values())
        if active_only:
            users = [u for u in users if u.is_active]
        if provider:
            users = [u for u in users if u.provider == provider]
        return users

    def provision_from_scim(self, scim_user: SCIMUser) -> User:
        """Provision or update a user from SCIM data."""
        existing = self.get_by_external_id(scim_user.external_id) if scim_user.external_id else None
        if existing is None:
            existing = self.get_by_email(scim_user.primary_email)

        if existing:
            existing.display_name = scim_user.display_name or existing.display_name
            existing.status = UserStatus.ACTIVE.value if scim_user.active else UserStatus.INACTIVE.value
            return existing

        return self.create_user(
            email=scim_user.primary_email,
            display_name=scim_user.display_name,
            provider=AuthProvider.OIDC.value,
            external_id=scim_user.external_id,
        )

    def provision_from_saml(self, assertion: SAMLAssertion) -> User:
        """Provision or update a user from a SAML assertion."""
        existing = self.get_by_email(assertion.email) if assertion.email else None

        if existing:
            existing.display_name = assertion.display_name or existing.display_name
            existing.roles = assertion.roles or existing.roles
            existing.last_login = datetime.now(timezone.utc)
            return existing

        return self.create_user(
            email=assertion.email,
            display_name=assertion.display_name,
            roles=assertion.roles,
            provider=AuthProvider.SAML.value,
            external_id=assertion.subject,
        )

"""Cross-organization agent federation.

Enable agents from different organizations to establish trust,
verify identities via public keys, and share governance data
within federated trust boundaries.

From SPEC Sprint 24: "Open agent identity specification — federation."
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from vt_protocol.identity.spec import AgentIdentity, TrustLevel

logger = logging.getLogger(__name__)


@dataclass
class FederationMember:
    """An organization participating in the federation."""

    org_id: str = ""
    org_name: str = ""
    public_key: str = ""
    trust_level: TrustLevel = TrustLevel.BASIC
    agents: list[str] = field(default_factory=list)  # agent IDs
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "org_name": self.org_name,
            "public_key": self.public_key[:20] + "..." if len(self.public_key) > 20 else self.public_key,
            "trust_level": self.trust_level.value,
            "agent_count": len(self.agents),
            "joined_at": self.joined_at.isoformat(),
            "active": self.active,
        }


@dataclass
class FederationToken:
    """A signed token for cross-org agent authentication."""

    token_id: str = ""
    agent_id: str = ""
    org_id: str = ""
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    signature: str = ""
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "agent_id": self.agent_id,
            "org_id": self.org_id,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_expired": self.is_expired,
            "claims": self.claims,
        }


@dataclass
class FederationResult:
    """Result of a federation operation."""

    success: bool = False
    message: str = ""
    token: FederationToken | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "token": self.token.to_dict() if self.token else None,
        }


class Federation:
    """Manage cross-organization agent federation."""

    def __init__(self, *, org_id: str, org_name: str, signing_key: str = "") -> None:
        self._org_id = org_id
        self._org_name = org_name
        self._signing_key = signing_key or hashlib.sha256(org_id.encode()).hexdigest()
        self._members: dict[str, FederationMember] = {}
        self._tokens: dict[str, FederationToken] = {}

    @property
    def org_id(self) -> str:
        return self._org_id

    @property
    def member_count(self) -> int:
        return len(self._members)

    @property
    def active_members(self) -> list[FederationMember]:
        return [m for m in self._members.values() if m.active]

    def register_member(self, member: FederationMember) -> bool:
        """Register an organization as a federation member."""
        if not member.org_id or not member.public_key:
            return False
        self._members[member.org_id] = member
        logger.info("Registered federation member: %s", member.org_name)
        return True

    def remove_member(self, org_id: str) -> bool:
        """Remove a member from the federation."""
        if org_id in self._members:
            self._members[org_id].active = False
            # Invalidate their tokens
            for token in self._tokens.values():
                if token.org_id == org_id:
                    token.expires_at = datetime.now(timezone.utc)
            return True
        return False

    def get_member(self, org_id: str) -> FederationMember | None:
        return self._members.get(org_id)

    def issue_token(
        self,
        agent: AgentIdentity,
        *,
        ttl_hours: int = 24,
    ) -> FederationResult:
        """Issue a federation token for an agent."""
        # Verify agent's org is a member
        if agent.organization not in self._members:
            return FederationResult(
                success=False,
                message=f"Organization '{agent.organization}' is not a federation member",
            )

        member = self._members[agent.organization]
        if not member.active:
            return FederationResult(
                success=False,
                message=f"Organization '{agent.organization}' is inactive",
            )

        # Create token
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        expires = now + timedelta(hours=ttl_hours)

        token_data = f"{agent.agent_id}:{agent.organization}:{now.isoformat()}"
        signature = hmac.new(
            self._signing_key.encode(),
            token_data.encode(),
            hashlib.sha256,
        ).hexdigest()

        token = FederationToken(
            token_id=hashlib.sha256(token_data.encode()).hexdigest()[:16],
            agent_id=agent.agent_id,
            org_id=agent.organization,
            issued_at=now,
            expires_at=expires,
            signature=signature,
            claims={
                "trust_level": agent.trust_level.value,
                "capabilities": sorted(agent.allowed_capabilities),
                "agent_type": agent.agent_type.value,
            },
        )

        self._tokens[token.token_id] = token

        return FederationResult(
            success=True,
            message=f"Token issued for agent '{agent.name}'",
            token=token,
        )

    def verify_token(self, token_id: str) -> FederationResult:
        """Verify a federation token."""
        token = self._tokens.get(token_id)
        if token is None:
            return FederationResult(
                success=False, message="Token not found",
            )

        if token.is_expired:
            return FederationResult(
                success=False, message="Token has expired",
            )

        # Verify org is still active
        member = self._members.get(token.org_id)
        if member is None or not member.active:
            return FederationResult(
                success=False,
                message=f"Organization '{token.org_id}' is no longer active",
            )

        return FederationResult(
            success=True,
            message="Token is valid",
            token=token,
        )

    def revoke_token(self, token_id: str) -> bool:
        """Revoke a federation token."""
        if token_id in self._tokens:
            self._tokens[token_id].expires_at = datetime.now(timezone.utc)
            return True
        return False

    def list_tokens(self, *, org_id: str = "", active_only: bool = True) -> list[FederationToken]:
        """List federation tokens."""
        tokens = list(self._tokens.values())
        if org_id:
            tokens = [t for t in tokens if t.org_id == org_id]
        if active_only:
            tokens = [t for t in tokens if not t.is_expired]
        return tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self._org_id,
            "org_name": self._org_name,
            "member_count": self.member_count,
            "active_members": [m.to_dict() for m in self.active_members],
            "total_tokens": len(self._tokens),
            "active_tokens": len([t for t in self._tokens.values() if not t.is_expired]),
        }

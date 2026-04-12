"""Zero Standing Privileges — short-lived capability tokens for agents.

Issues dynamic, time-bound identity tokens scoped to current task.
Tokens are Ed25519 signed with a 5-minute default TTL.

From SPEC Phase 3: "Zero Standing Privileges for agents — issue dynamic,
time-bound identity tokens scoped to current task, not static credentials.
Active permission enforcement, not just tracking."
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# Default token TTL
DEFAULT_TTL_SECONDS = 300  # 5 minutes


@dataclass
class CapabilityToken:
    """A short-lived capability token for an agent.

    Contains the agent ID, scoped permissions, expiry time, and
    a signature for verification.
    """

    token_id: str = field(default_factory=lambda: uuid4().hex[:16])
    agent_id: str = ""
    permissions: list[str] = field(default_factory=list)
    scope: str = ""
    issued_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_TTL_SECONDS)
    )
    signature: bytes = b""
    revoked: bool = False

    @property
    def is_expired(self) -> bool:
        """Check if the token has expired."""
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if the token is currently valid (not expired, not revoked)."""
        return not self.is_expired and not self.revoked

    @property
    def remaining_seconds(self) -> float:
        """Seconds remaining until expiry."""
        delta = self.expires_at - datetime.now(timezone.utc)
        return max(0.0, delta.total_seconds())

    def canonical_bytes(self) -> bytes:
        """Canonical representation for signing/verification."""
        data = json.dumps({
            "token_id": self.token_id,
            "agent_id": self.agent_id,
            "permissions": sorted(self.permissions),
            "scope": self.scope,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }, sort_keys=True, separators=(",", ":"))
        return data.encode("utf-8")

    def content_hash(self) -> str:
        """SHA-256 hash of canonical content."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "agent_id": self.agent_id,
            "permissions": self.permissions,
            "scope": self.scope,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "is_valid": self.is_valid,
            "remaining_seconds": round(self.remaining_seconds, 1),
            "revoked": self.revoked,
            "signature_hex": self.signature.hex() if self.signature else "",
        }


@dataclass
class TokenValidation:
    """Result of validating a capability token."""

    token_id: str
    valid: bool
    reason: str = ""
    permissions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "valid": self.valid,
            "reason": self.reason,
            "permissions": self.permissions,
        }


class TokenIssuer:
    """Issues and validates short-lived capability tokens.

    Optionally signs tokens with Ed25519 when a signing key is provided.
    """

    def __init__(self, signing_key: Any = None) -> None:
        self._signing_key = signing_key
        self._active_tokens: dict[str, CapabilityToken] = {}
        self._revoked: set[str] = set()

    @property
    def active_count(self) -> int:
        """Count of non-expired, non-revoked tokens."""
        return sum(1 for t in self._active_tokens.values() if t.is_valid)

    def issue(
        self,
        agent_id: str,
        permissions: list[str],
        *,
        scope: str = "",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> CapabilityToken:
        """Issue a new capability token.

        Args:
            agent_id: The agent this token is for.
            permissions: Scoped permissions for this token.
            scope: Description of what the token is scoped to.
            ttl_seconds: Time-to-live in seconds (default 5 minutes).
        """
        now = datetime.now(timezone.utc)
        token = CapabilityToken(
            agent_id=agent_id,
            permissions=permissions,
            scope=scope,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

        # Sign if we have a key
        if self._signing_key is not None:
            try:
                signed = self._signing_key.sign(token.canonical_bytes())
                token.signature = signed.signature
            except Exception:
                logger.warning("Failed to sign token %s", token.token_id)

        self._active_tokens[token.token_id] = token
        return token

    def validate(self, token_id: str) -> TokenValidation:
        """Validate a capability token."""
        if token_id in self._revoked:
            return TokenValidation(
                token_id=token_id,
                valid=False,
                reason="Token has been revoked",
            )

        token = self._active_tokens.get(token_id)
        if token is None:
            return TokenValidation(
                token_id=token_id,
                valid=False,
                reason="Token not found",
            )

        if token.is_expired:
            return TokenValidation(
                token_id=token_id,
                valid=False,
                reason="Token has expired",
            )

        if token.revoked:
            return TokenValidation(
                token_id=token_id,
                valid=False,
                reason="Token has been revoked",
            )

        return TokenValidation(
            token_id=token_id,
            valid=True,
            permissions=token.permissions,
        )

    def check_permission(
        self,
        token_id: str,
        permission: str,
    ) -> TokenValidation:
        """Validate a token AND check if it grants a specific permission."""
        validation = self.validate(token_id)
        if not validation.valid:
            return validation

        token = self._active_tokens[token_id]
        if permission not in token.permissions:
            return TokenValidation(
                token_id=token_id,
                valid=False,
                reason=f"Permission '{permission}' not in token scope",
                permissions=token.permissions,
            )

        return validation

    def revoke(self, token_id: str) -> bool:
        """Revoke a token. Returns True if found."""
        token = self._active_tokens.get(token_id)
        if token:
            token.revoked = True
            self._revoked.add(token_id)
            return True
        return False

    def revoke_all_for_agent(self, agent_id: str) -> int:
        """Revoke all tokens for an agent. Returns count revoked."""
        count = 0
        for token in self._active_tokens.values():
            if token.agent_id == agent_id and not token.revoked:
                token.revoked = True
                self._revoked.add(token.token_id)
                count += 1
        return count

    def cleanup_expired(self) -> int:
        """Remove expired tokens from the store. Returns count cleaned."""
        expired = [
            tid for tid, t in self._active_tokens.items()
            if t.is_expired
        ]
        for tid in expired:
            del self._active_tokens[tid]
        return len(expired)

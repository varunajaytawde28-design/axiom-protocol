"""Dual-authorization sign-off for contradiction resolution.

When PM and tech lead (or any two roles) disagree on resolution, both
must sign off. Approvals are stored immutably in the Merkle tree audit log.

From SPEC Phase 2: "when PM and tech lead disagree on resolution,
require BOTH to sign off, store disagreement + resolution immutably
in Merkle tree audit log."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
)

logger = logging.getLogger(__name__)


@dataclass
class Approval:
    """A single role's approval of a contradiction resolution."""

    id: str = field(default_factory=lambda: uuid4().hex[:16])
    role: str = ""
    approver: str = ""
    approved_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    rationale: str = ""
    dissenting: bool = False
    dissent_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "approver": self.approver,
            "approved_at": self.approved_at.isoformat(),
            "rationale": self.rationale,
            "dissenting": self.dissenting,
            "dissent_reason": self.dissent_reason,
        }


@dataclass
class DualAuthRequest:
    """A request requiring dual authorization for resolution."""

    id: str = field(default_factory=lambda: uuid4().hex[:16])
    contradiction_id: str = ""
    required_roles: list[str] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    resolution_action: str = ""
    resolution_rationale: str = ""
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def approved_roles(self) -> set[str]:
        """Roles that have already approved."""
        return {a.role for a in self.approvals}

    @property
    def pending_roles(self) -> list[str]:
        """Roles that still need to approve."""
        approved = self.approved_roles
        return [r for r in self.required_roles if r not in approved]

    @property
    def is_fully_approved(self) -> bool:
        """True if all required roles have approved."""
        return len(self.pending_roles) == 0

    @property
    def has_dissent(self) -> bool:
        """True if any approval is dissenting."""
        return any(a.dissenting for a in self.approvals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "contradiction_id": self.contradiction_id,
            "required_roles": self.required_roles,
            "approvals": [a.to_dict() for a in self.approvals],
            "approved_roles": sorted(self.approved_roles),
            "pending_roles": self.pending_roles,
            "is_fully_approved": self.is_fully_approved,
            "has_dissent": self.has_dissent,
            "resolution_action": self.resolution_action,
            "resolution_rationale": self.resolution_rationale,
            "created_at": self.created_at.isoformat(),
        }


def create_dual_auth_request(
    contradiction_id: str,
    required_roles: list[str],
    resolution_action: str = "",
    resolution_rationale: str = "",
) -> DualAuthRequest:
    """Create a new dual-authorization request."""
    if len(required_roles) < 2:
        raise ValueError("Dual authorization requires at least 2 roles")
    return DualAuthRequest(
        contradiction_id=contradiction_id,
        required_roles=required_roles,
        resolution_action=resolution_action,
        resolution_rationale=resolution_rationale,
    )


def add_approval(
    request: DualAuthRequest,
    role: str,
    approver: str,
    *,
    rationale: str = "",
    dissenting: bool = False,
    dissent_reason: str = "",
) -> Approval:
    """Add an approval to a dual-auth request.

    Returns the created Approval. Raises ValueError if the role is not
    required or has already approved.
    """
    if role not in request.required_roles:
        raise ValueError(f"Role '{role}' is not required for this authorization")
    if role in request.approved_roles:
        raise ValueError(f"Role '{role}' has already approved")

    approval = Approval(
        role=role,
        approver=approver,
        rationale=rationale,
        dissenting=dissenting,
        dissent_reason=dissent_reason,
    )
    request.approvals.append(approval)
    return approval


def build_approval_audit_entry(
    request: DualAuthRequest,
    approval: Approval,
) -> AuditEntry:
    """Build an audit entry for a dual-auth approval.

    Stored in the Merkle tree for immutable record of sign-off.
    """
    return AuditEntry(
        event_type=AuditEventType.CONTRADICTION_RESOLVED,
        actor=approval.approver,
        payload={
            "dual_auth_id": request.id,
            "contradiction_id": request.contradiction_id,
            "role": approval.role,
            "dissenting": approval.dissenting,
            "dissent_reason": approval.dissent_reason,
            "rationale": approval.rationale,
            "resolution_action": request.resolution_action,
            "all_approvals": [a.to_dict() for a in request.approvals],
            "fully_approved": request.is_fully_approved,
        },
    )


def build_completion_audit_entry(
    request: DualAuthRequest,
) -> AuditEntry | None:
    """Build an audit entry when dual auth is fully complete.

    Returns None if not yet fully approved.
    """
    if not request.is_fully_approved:
        return None

    return AuditEntry(
        event_type=AuditEventType.CONTRADICTION_RESOLVED,
        actor="system",
        payload={
            "dual_auth_id": request.id,
            "contradiction_id": request.contradiction_id,
            "resolution_action": request.resolution_action,
            "resolution_rationale": request.resolution_rationale,
            "approvals": [a.to_dict() for a in request.approvals],
            "has_dissent": request.has_dissent,
            "status": "dual_auth_complete",
        },
    )

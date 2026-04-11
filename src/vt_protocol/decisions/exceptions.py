"""Four-tier exception handling for contradiction waivers.

Tiers (from least to most privileged):
  1. Auto-waiver    — permanent, logged, for low-confidence contradictions
  2. Standard       — 30-day window, single approval required
  3. Elevated       — 90-day window, dual approval required
  4. Break-glass    — 24-72 hour emergency override, mandatory review after

Recurring exceptions (same pair, same dimension, 3+ times) generate a
"rule needs updating" report — signals that the governance rule is wrong.

Each exception is tracked with:
  - Who approved it
  - When it expires
  - Why it was granted
  - The contradiction it applies to
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class ExceptionTier(str, Enum):
    """Four-tier exception model."""

    AUTO_WAIVER = "auto_waiver"  # Permanent, logged
    STANDARD = "standard"  # 30-day, single approval
    ELEVATED = "elevated"  # 90-day, dual approval
    BREAK_GLASS = "break_glass"  # 24-72h emergency


# Duration limits per tier
TIER_DURATIONS: dict[ExceptionTier, timedelta | None] = {
    ExceptionTier.AUTO_WAIVER: None,  # Permanent
    ExceptionTier.STANDARD: timedelta(days=30),
    ExceptionTier.ELEVATED: timedelta(days=90),
    ExceptionTier.BREAK_GLASS: timedelta(hours=72),
}

# Required approvals per tier
TIER_APPROVALS: dict[ExceptionTier, int] = {
    ExceptionTier.AUTO_WAIVER: 0,
    ExceptionTier.STANDARD: 1,
    ExceptionTier.ELEVATED: 2,
    ExceptionTier.BREAK_GLASS: 1,  # But mandatory review after
}

# Threshold for recurring exception detection
RECURRING_THRESHOLD = 3


@dataclass
class ExceptionRecord:
    """A recorded exception (waiver) for a contradiction."""

    id: str = field(default_factory=lambda: uuid4().hex[:16])
    contradiction_id: str = ""
    tier: ExceptionTier = ExceptionTier.STANDARD
    reason: str = ""
    approved_by: list[str] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    expires_at: datetime | None = None
    requires_review: bool = False
    reviewed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Whether this exception is currently in effect."""
        if self.expires_at is None:
            return True  # Permanent (auto-waiver)
        return datetime.now(timezone.utc) < self.expires_at

    @property
    def is_expired(self) -> bool:
        """Whether this exception has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_fully_approved(self) -> bool:
        """Whether all required approvals have been obtained."""
        required = TIER_APPROVALS[self.tier]
        return len(self.approved_by) >= required

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "contradiction_id": self.contradiction_id,
            "tier": self.tier.value,
            "reason": self.reason,
            "approved_by": self.approved_by,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
            "is_expired": self.is_expired,
            "is_fully_approved": self.is_fully_approved,
            "requires_review": self.requires_review,
            "reviewed": self.reviewed,
        }


@dataclass
class RecurringExceptionReport:
    """Report for contradictions with recurring exceptions."""

    contradiction_id: str
    dimension: str
    exception_count: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "dimension": self.dimension,
            "exception_count": self.exception_count,
            "message": self.message,
        }


def create_exception(
    contradiction_id: str,
    tier: ExceptionTier,
    *,
    reason: str = "",
    approved_by: list[str] | None = None,
    now: datetime | None = None,
) -> ExceptionRecord:
    """Create a new exception record with proper duration and approval tracking."""
    now = now or datetime.now(timezone.utc)
    duration = TIER_DURATIONS[tier]
    expires_at = (now + duration) if duration else None

    record = ExceptionRecord(
        contradiction_id=contradiction_id,
        tier=tier,
        reason=reason,
        approved_by=approved_by or [],
        created_at=now,
        expires_at=expires_at,
        requires_review=(tier == ExceptionTier.BREAK_GLASS),
    )

    return record


def validate_exception(record: ExceptionRecord) -> list[str]:
    """Validate an exception record. Returns list of validation errors."""
    errors: list[str] = []

    if not record.contradiction_id:
        errors.append("Missing contradiction_id")

    if not record.reason:
        errors.append("Missing reason")

    required_approvals = TIER_APPROVALS[record.tier]
    if len(record.approved_by) < required_approvals:
        errors.append(
            f"Tier '{record.tier.value}' requires {required_approvals} "
            f"approval(s), got {len(record.approved_by)}"
        )

    if record.tier == ExceptionTier.AUTO_WAIVER and record.expires_at is not None:
        errors.append("Auto-waivers should not have an expiry date")

    return errors


def check_auto_waiver_eligible(
    contradiction_confidence: float,
    *,
    threshold: float = 0.5,
) -> bool:
    """Check if a contradiction is eligible for automatic waiver.

    Low-confidence contradictions (below threshold) can be auto-waived
    since they're likely false positives.
    """
    return contradiction_confidence < threshold


def detect_recurring_exceptions(
    exceptions: list[ExceptionRecord],
    *,
    threshold: int = RECURRING_THRESHOLD,
) -> list[RecurringExceptionReport]:
    """Find contradictions with recurring exceptions.

    When the same contradiction gets 3+ exceptions, it signals that
    the governance rule itself needs updating rather than granting
    yet another exception.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for exc in exceptions:
        counts[exc.contradiction_id] += 1

    reports: list[RecurringExceptionReport] = []
    for cid, count in counts.items():
        if count >= threshold:
            reports.append(RecurringExceptionReport(
                contradiction_id=cid,
                dimension="",  # Caller should enrich with actual dimension
                exception_count=count,
                message=(
                    f"Contradiction {cid[:8]} has been excepted {count} times. "
                    f"Consider updating the governance rule instead."
                ),
            ))

    return reports


def get_active_exceptions(
    exceptions: list[ExceptionRecord],
) -> list[ExceptionRecord]:
    """Filter to only active (non-expired, fully-approved) exceptions."""
    return [
        e for e in exceptions
        if e.is_active and e.is_fully_approved
    ]


def get_pending_reviews(
    exceptions: list[ExceptionRecord],
) -> list[ExceptionRecord]:
    """Find break-glass exceptions awaiting mandatory review."""
    return [
        e for e in exceptions
        if e.requires_review and not e.reviewed
    ]

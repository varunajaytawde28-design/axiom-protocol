"""Tests for four-tier exception handling model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.decisions.exceptions import (
    RECURRING_THRESHOLD,
    TIER_APPROVALS,
    TIER_DURATIONS,
    ExceptionRecord,
    ExceptionTier,
    RecurringExceptionReport,
    check_auto_waiver_eligible,
    create_exception,
    detect_recurring_exceptions,
    get_active_exceptions,
    get_pending_reviews,
    validate_exception,
)


# ---------------------------------------------------------------------------
# ExceptionRecord properties
# ---------------------------------------------------------------------------


class TestExceptionRecord:
    def test_auto_waiver_is_active(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.AUTO_WAIVER,
            expires_at=None,
        )
        assert r.is_active is True
        assert r.is_expired is False

    def test_standard_active_before_expiry(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        assert r.is_active is True
        assert r.is_expired is False

    def test_standard_expired(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        assert r.is_active is False
        assert r.is_expired is True

    def test_fully_approved_auto_waiver(self) -> None:
        # Auto-waiver needs 0 approvals
        r = ExceptionRecord(tier=ExceptionTier.AUTO_WAIVER)
        assert r.is_fully_approved is True

    def test_fully_approved_standard(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            approved_by=["alice"],
        )
        assert r.is_fully_approved is True

    def test_not_fully_approved_standard(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            approved_by=[],
        )
        assert r.is_fully_approved is False

    def test_fully_approved_elevated(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.ELEVATED,
            approved_by=["alice", "bob"],
        )
        assert r.is_fully_approved is True

    def test_not_fully_approved_elevated(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.ELEVATED,
            approved_by=["alice"],  # Needs 2
        )
        assert r.is_fully_approved is False

    def test_to_dict(self) -> None:
        r = ExceptionRecord(
            contradiction_id="c1",
            tier=ExceptionTier.STANDARD,
            reason="Known issue",
            approved_by=["alice"],
        )
        d = r.to_dict()
        assert d["tier"] == "standard"
        assert d["reason"] == "Known issue"
        assert d["approved_by"] == ["alice"]
        assert "is_active" in d
        assert "is_expired" in d

    def test_default_id_generated(self) -> None:
        r = ExceptionRecord()
        assert r.id  # Non-empty


# ---------------------------------------------------------------------------
# create_exception
# ---------------------------------------------------------------------------


class TestCreateException:
    def test_auto_waiver(self) -> None:
        r = create_exception("c1", ExceptionTier.AUTO_WAIVER, reason="Low confidence")
        assert r.tier == ExceptionTier.AUTO_WAIVER
        assert r.expires_at is None
        assert r.requires_review is False

    def test_standard(self) -> None:
        now = datetime.now(timezone.utc)
        r = create_exception(
            "c1", ExceptionTier.STANDARD,
            reason="Sprint priority",
            approved_by=["alice"],
            now=now,
        )
        assert r.tier == ExceptionTier.STANDARD
        assert r.expires_at is not None
        assert (r.expires_at - now).days == 30
        assert r.approved_by == ["alice"]

    def test_elevated(self) -> None:
        now = datetime.now(timezone.utc)
        r = create_exception(
            "c1", ExceptionTier.ELEVATED,
            reason="Architecture review needed",
            approved_by=["alice", "bob"],
            now=now,
        )
        assert (r.expires_at - now).days == 90  # type: ignore[operator]

    def test_break_glass(self) -> None:
        r = create_exception("c1", ExceptionTier.BREAK_GLASS, reason="Emergency deploy")
        assert r.requires_review is True
        assert r.expires_at is not None
        # 72 hours
        delta = r.expires_at - r.created_at
        assert delta.total_seconds() == pytest.approx(72 * 3600, abs=1)

    def test_reason_stored(self) -> None:
        r = create_exception("c1", ExceptionTier.STANDARD, reason="Known trade-off")
        assert r.reason == "Known trade-off"


# ---------------------------------------------------------------------------
# validate_exception
# ---------------------------------------------------------------------------


class TestValidateException:
    def test_valid_standard(self) -> None:
        r = create_exception(
            "c1", ExceptionTier.STANDARD,
            reason="Good reason",
            approved_by=["alice"],
        )
        assert validate_exception(r) == []

    def test_missing_contradiction_id(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            reason="Reason",
            approved_by=["alice"],
        )
        errors = validate_exception(r)
        assert any("contradiction_id" in e for e in errors)

    def test_missing_reason(self) -> None:
        r = ExceptionRecord(
            contradiction_id="c1",
            tier=ExceptionTier.STANDARD,
            approved_by=["alice"],
        )
        errors = validate_exception(r)
        assert any("reason" in e.lower() for e in errors)

    def test_insufficient_approvals_standard(self) -> None:
        r = ExceptionRecord(
            contradiction_id="c1",
            tier=ExceptionTier.STANDARD,
            reason="Reason",
            approved_by=[],
        )
        errors = validate_exception(r)
        assert any("approval" in e for e in errors)

    def test_insufficient_approvals_elevated(self) -> None:
        r = ExceptionRecord(
            contradiction_id="c1",
            tier=ExceptionTier.ELEVATED,
            reason="Reason",
            approved_by=["alice"],  # Needs 2
        )
        errors = validate_exception(r)
        assert any("2" in e for e in errors)

    def test_auto_waiver_with_expiry(self) -> None:
        r = ExceptionRecord(
            contradiction_id="c1",
            tier=ExceptionTier.AUTO_WAIVER,
            reason="Low confidence",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        errors = validate_exception(r)
        assert any("expiry" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# check_auto_waiver_eligible
# ---------------------------------------------------------------------------


class TestAutoWaiverEligible:
    def test_low_confidence_eligible(self) -> None:
        assert check_auto_waiver_eligible(0.3) is True

    def test_high_confidence_not_eligible(self) -> None:
        assert check_auto_waiver_eligible(0.8) is False

    def test_at_threshold_not_eligible(self) -> None:
        assert check_auto_waiver_eligible(0.5) is False

    def test_custom_threshold(self) -> None:
        assert check_auto_waiver_eligible(0.6, threshold=0.7) is True


# ---------------------------------------------------------------------------
# detect_recurring_exceptions
# ---------------------------------------------------------------------------


class TestDetectRecurring:
    def test_no_recurring(self) -> None:
        exceptions = [
            ExceptionRecord(contradiction_id="c1"),
            ExceptionRecord(contradiction_id="c2"),
        ]
        reports = detect_recurring_exceptions(exceptions)
        assert reports == []

    def test_detects_recurring(self) -> None:
        exceptions = [
            ExceptionRecord(contradiction_id="c1") for _ in range(3)
        ]
        reports = detect_recurring_exceptions(exceptions)
        assert len(reports) == 1
        assert reports[0].contradiction_id == "c1"
        assert reports[0].exception_count == 3

    def test_custom_threshold(self) -> None:
        exceptions = [
            ExceptionRecord(contradiction_id="c1") for _ in range(2)
        ]
        reports = detect_recurring_exceptions(exceptions, threshold=2)
        assert len(reports) == 1

    def test_multiple_recurring(self) -> None:
        exceptions = [
            ExceptionRecord(contradiction_id="c1") for _ in range(3)
        ] + [
            ExceptionRecord(contradiction_id="c2") for _ in range(4)
        ]
        reports = detect_recurring_exceptions(exceptions)
        assert len(reports) == 2

    def test_report_message(self) -> None:
        exceptions = [
            ExceptionRecord(contradiction_id="c1") for _ in range(3)
        ]
        reports = detect_recurring_exceptions(exceptions)
        assert "updating" in reports[0].message.lower()


# ---------------------------------------------------------------------------
# get_active_exceptions
# ---------------------------------------------------------------------------


class TestGetActiveExceptions:
    def test_filters_expired(self) -> None:
        active = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            approved_by=["alice"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        expired = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            approved_by=["alice"],
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        result = get_active_exceptions([active, expired])
        assert len(result) == 1
        assert result[0] is active

    def test_filters_unapproved(self) -> None:
        approved = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            approved_by=["alice"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        unapproved = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            approved_by=[],
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        result = get_active_exceptions([approved, unapproved])
        assert len(result) == 1

    def test_auto_waiver_always_active(self) -> None:
        r = ExceptionRecord(tier=ExceptionTier.AUTO_WAIVER)
        result = get_active_exceptions([r])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_pending_reviews
# ---------------------------------------------------------------------------


class TestGetPendingReviews:
    def test_finds_pending_break_glass(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.BREAK_GLASS,
            requires_review=True,
            reviewed=False,
        )
        result = get_pending_reviews([r])
        assert len(result) == 1

    def test_excludes_reviewed(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.BREAK_GLASS,
            requires_review=True,
            reviewed=True,
        )
        result = get_pending_reviews([r])
        assert len(result) == 0

    def test_excludes_non_break_glass(self) -> None:
        r = ExceptionRecord(
            tier=ExceptionTier.STANDARD,
            requires_review=False,
        )
        result = get_pending_reviews([r])
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tier configuration
# ---------------------------------------------------------------------------


class TestTierConfig:
    def test_auto_waiver_no_duration(self) -> None:
        assert TIER_DURATIONS[ExceptionTier.AUTO_WAIVER] is None

    def test_standard_30_days(self) -> None:
        assert TIER_DURATIONS[ExceptionTier.STANDARD] == timedelta(days=30)

    def test_elevated_90_days(self) -> None:
        assert TIER_DURATIONS[ExceptionTier.ELEVATED] == timedelta(days=90)

    def test_break_glass_72_hours(self) -> None:
        assert TIER_DURATIONS[ExceptionTier.BREAK_GLASS] == timedelta(hours=72)

    def test_approval_counts(self) -> None:
        assert TIER_APPROVALS[ExceptionTier.AUTO_WAIVER] == 0
        assert TIER_APPROVALS[ExceptionTier.STANDARD] == 1
        assert TIER_APPROVALS[ExceptionTier.ELEVATED] == 2
        assert TIER_APPROVALS[ExceptionTier.BREAK_GLASS] == 1

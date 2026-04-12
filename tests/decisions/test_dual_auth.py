"""Tests for dual-authorization sign-off."""

from __future__ import annotations

import pytest

from vt_protocol.decisions.dual_auth import (
    Approval,
    DualAuthRequest,
    add_approval,
    build_approval_audit_entry,
    build_completion_audit_entry,
    create_dual_auth_request,
)
from vt_protocol.decisions.models import AuditEventType


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class TestApproval:
    def test_defaults(self) -> None:
        a = Approval(role="tech_lead", approver="alice")
        assert a.role == "tech_lead"
        assert a.approver == "alice"
        assert a.dissenting is False

    def test_to_dict(self) -> None:
        a = Approval(
            role="ciso",
            approver="bob",
            rationale="Looks good",
            dissenting=True,
            dissent_reason="Needs more review",
        )
        d = a.to_dict()
        assert d["role"] == "ciso"
        assert d["dissenting"] is True
        assert d["dissent_reason"] == "Needs more review"
        assert "approved_at" in d


# ---------------------------------------------------------------------------
# DualAuthRequest
# ---------------------------------------------------------------------------


class TestDualAuthRequest:
    def test_defaults(self) -> None:
        req = DualAuthRequest(
            contradiction_id="c-123",
            required_roles=["tech_lead", "ciso"],
        )
        assert req.contradiction_id == "c-123"
        assert req.required_roles == ["tech_lead", "ciso"]
        assert req.approvals == []

    def test_approved_roles_empty(self) -> None:
        req = DualAuthRequest(required_roles=["tech_lead", "ciso"])
        assert req.approved_roles == set()

    def test_pending_roles_all(self) -> None:
        req = DualAuthRequest(required_roles=["tech_lead", "ciso"])
        assert req.pending_roles == ["tech_lead", "ciso"]

    def test_is_fully_approved_false(self) -> None:
        req = DualAuthRequest(required_roles=["tech_lead", "ciso"])
        assert req.is_fully_approved is False

    def test_is_fully_approved_after_all_sign(self) -> None:
        req = DualAuthRequest(required_roles=["tech_lead", "ciso"])
        req.approvals = [
            Approval(role="tech_lead", approver="alice"),
            Approval(role="ciso", approver="bob"),
        ]
        assert req.is_fully_approved is True
        assert req.pending_roles == []

    def test_has_dissent(self) -> None:
        req = DualAuthRequest(required_roles=["tech_lead", "ciso"])
        req.approvals = [
            Approval(role="tech_lead", approver="alice", dissenting=True, dissent_reason="Disagree"),
            Approval(role="ciso", approver="bob"),
        ]
        assert req.has_dissent is True

    def test_no_dissent(self) -> None:
        req = DualAuthRequest(required_roles=["tech_lead", "ciso"])
        req.approvals = [
            Approval(role="tech_lead", approver="alice"),
            Approval(role="ciso", approver="bob"),
        ]
        assert req.has_dissent is False

    def test_to_dict(self) -> None:
        req = DualAuthRequest(
            contradiction_id="c-123",
            required_roles=["tech_lead", "ciso"],
            resolution_action="pick_a",
            resolution_rationale="A is better",
        )
        d = req.to_dict()
        assert d["contradiction_id"] == "c-123"
        assert d["required_roles"] == ["tech_lead", "ciso"]
        assert d["is_fully_approved"] is False
        assert d["resolution_action"] == "pick_a"

    def test_partial_approval(self) -> None:
        req = DualAuthRequest(required_roles=["tech_lead", "ciso"])
        req.approvals = [Approval(role="tech_lead", approver="alice")]
        assert req.approved_roles == {"tech_lead"}
        assert req.pending_roles == ["ciso"]
        assert req.is_fully_approved is False


# ---------------------------------------------------------------------------
# create_dual_auth_request
# ---------------------------------------------------------------------------


class TestCreateDualAuthRequest:
    def test_creates_request(self) -> None:
        req = create_dual_auth_request(
            contradiction_id="c-456",
            required_roles=["tech_lead", "pm"],
            resolution_action="update_decision",
        )
        assert req.contradiction_id == "c-456"
        assert req.required_roles == ["tech_lead", "pm"]

    def test_requires_two_roles(self) -> None:
        with pytest.raises(ValueError, match="at least 2 roles"):
            create_dual_auth_request(
                contradiction_id="c-789",
                required_roles=["tech_lead"],
            )

    def test_three_roles_allowed(self) -> None:
        req = create_dual_auth_request(
            contradiction_id="c-100",
            required_roles=["tech_lead", "ciso", "pm"],
        )
        assert len(req.required_roles) == 3


# ---------------------------------------------------------------------------
# add_approval
# ---------------------------------------------------------------------------


class TestAddApproval:
    def test_adds_approval(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        approval = add_approval(req, "tech_lead", "alice", rationale="LGTM")
        assert approval.role == "tech_lead"
        assert approval.approver == "alice"
        assert len(req.approvals) == 1

    def test_dissenting_approval(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        approval = add_approval(
            req, "ciso", "bob",
            dissenting=True,
            dissent_reason="Not the right approach",
        )
        assert approval.dissenting is True
        assert approval.dissent_reason == "Not the right approach"

    def test_role_not_required_raises(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        with pytest.raises(ValueError, match="not required"):
            add_approval(req, "qa", "charlie")

    def test_duplicate_role_raises(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        add_approval(req, "tech_lead", "alice")
        with pytest.raises(ValueError, match="already approved"):
            add_approval(req, "tech_lead", "alice2")

    def test_full_approval_flow(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        add_approval(req, "tech_lead", "alice")
        assert not req.is_fully_approved
        add_approval(req, "ciso", "bob")
        assert req.is_fully_approved


# ---------------------------------------------------------------------------
# build_approval_audit_entry
# ---------------------------------------------------------------------------


class TestBuildApprovalAuditEntry:
    def test_builds_entry(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        approval = add_approval(req, "tech_lead", "alice")
        entry = build_approval_audit_entry(req, approval)

        assert entry.event_type == AuditEventType.CONTRADICTION_RESOLVED
        assert entry.actor == "alice"
        assert entry.payload["role"] == "tech_lead"
        assert entry.payload["contradiction_id"] == "c-1"

    def test_dissenting_in_payload(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        approval = add_approval(req, "ciso", "bob", dissenting=True, dissent_reason="Nope")
        entry = build_approval_audit_entry(req, approval)
        assert entry.payload["dissenting"] is True
        assert entry.payload["dissent_reason"] == "Nope"

    def test_includes_all_approvals(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        add_approval(req, "tech_lead", "alice")
        a2 = add_approval(req, "ciso", "bob")
        entry = build_approval_audit_entry(req, a2)
        assert len(entry.payload["all_approvals"]) == 2
        assert entry.payload["fully_approved"] is True


# ---------------------------------------------------------------------------
# build_completion_audit_entry
# ---------------------------------------------------------------------------


class TestBuildCompletionAuditEntry:
    def test_returns_none_when_incomplete(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        add_approval(req, "tech_lead", "alice")
        assert build_completion_audit_entry(req) is None

    def test_builds_when_complete(self) -> None:
        req = create_dual_auth_request(
            "c-1", ["tech_lead", "ciso"],
            resolution_action="pick_a",
            resolution_rationale="A is correct",
        )
        add_approval(req, "tech_lead", "alice")
        add_approval(req, "ciso", "bob")
        entry = build_completion_audit_entry(req)

        assert entry is not None
        assert entry.actor == "system"
        assert entry.payload["status"] == "dual_auth_complete"
        assert entry.payload["resolution_action"] == "pick_a"
        assert len(entry.payload["approvals"]) == 2

    def test_records_dissent_status(self) -> None:
        req = create_dual_auth_request("c-1", ["tech_lead", "ciso"])
        add_approval(req, "tech_lead", "alice", dissenting=True, dissent_reason="Bad idea")
        add_approval(req, "ciso", "bob")
        entry = build_completion_audit_entry(req)
        assert entry is not None
        assert entry.payload["has_dissent"] is True

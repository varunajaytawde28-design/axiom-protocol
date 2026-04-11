"""Tests for resolution path suggestions and application."""

from __future__ import annotations

from uuid import uuid4

import pytest

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    Dimension,
    SourceType,
)
from vt_protocol.decisions.resolution import (
    ResolutionPath,
    ResolutionType,
    apply_resolution,
    suggest_resolution_paths,
)


def _decision(title: str = "Test", *, dims: list[Dimension] | None = None) -> Decision:
    return Decision(
        title=title,
        content=f"Content for {title}",
        rationale="Good rationale",
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )


def _contradiction(
    d1: Decision | None = None,
    d2: Decision | None = None,
    *,
    verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
) -> Contradiction:
    d1 = d1 or _decision("Decision A")
    d2 = d2 or _decision("Decision B")
    return Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=verdict,
        reasoning="They conflict",
        evidence_a="A says X",
        evidence_b="B says Y",
        confidence=0.85,
    )


# ---------------------------------------------------------------------------
# ResolutionPath
# ---------------------------------------------------------------------------


class TestResolutionPath:
    def test_to_dict(self) -> None:
        p = ResolutionPath(
            action="pick_a",
            label="Keep PostgreSQL",
            description="Supersede MongoDB",
            impact="high",
            details={"winner_id": "abc"},
        )
        d = p.to_dict()
        assert d["action"] == "pick_a"
        assert d["label"] == "Keep PostgreSQL"
        assert d["impact"] == "high"
        assert d["details"]["winner_id"] == "abc"


# ---------------------------------------------------------------------------
# suggest_resolution_paths
# ---------------------------------------------------------------------------


class TestSuggestResolutionPaths:
    def test_contradiction_gives_three_paths(self) -> None:
        d1 = _decision("Use PostgreSQL")
        d2 = _decision("Use MongoDB")
        c = _contradiction(d1, d2, verdict=ContradictionVerdict.CONTRADICTION)

        paths = suggest_resolution_paths(c, d1, d2)
        assert len(paths) == 3
        actions = {p.action for p in paths}
        assert ResolutionType.PICK_A in actions
        assert ResolutionType.PICK_B in actions
        assert ResolutionType.ACCEPT_EXCEPTION in actions

    def test_tension_gives_three_paths(self) -> None:
        d1 = _decision("Use REST")
        d2 = _decision("Use GraphQL")
        c = _contradiction(d1, d2, verdict=ContradictionVerdict.TENSION)

        paths = suggest_resolution_paths(c, d1, d2)
        assert len(paths) == 3
        actions = {p.action for p in paths}
        assert ResolutionType.ACCEPT_EXCEPTION in actions
        assert ResolutionType.UPDATE_DECISION in actions
        assert ResolutionType.DEFER in actions

    def test_compatible_gives_dismiss(self) -> None:
        c = _contradiction(verdict=ContradictionVerdict.COMPATIBLE)
        paths = suggest_resolution_paths(c)
        assert len(paths) == 1
        assert paths[0].action == ResolutionType.DISMISS

    def test_contradiction_path_labels_include_titles(self) -> None:
        d1 = _decision("Use PostgreSQL")
        d2 = _decision("Use MongoDB")
        c = _contradiction(d1, d2)

        paths = suggest_resolution_paths(c, d1, d2)
        pick_a = next(p for p in paths if p.action == ResolutionType.PICK_A)
        assert "PostgreSQL" in pick_a.label

    def test_contradiction_path_details_have_ids(self) -> None:
        d1 = _decision("Use PostgreSQL")
        d2 = _decision("Use MongoDB")
        c = _contradiction(d1, d2)

        paths = suggest_resolution_paths(c, d1, d2)
        pick_a = next(p for p in paths if p.action == ResolutionType.PICK_A)
        assert pick_a.details["winner_id"] == str(d1.id)
        assert pick_a.details["loser_id"] == str(d2.id)

    def test_works_without_decisions(self) -> None:
        c = _contradiction()
        paths = suggest_resolution_paths(c)
        assert len(paths) == 3  # Falls back to contradiction.decision_a_title


# ---------------------------------------------------------------------------
# apply_resolution
# ---------------------------------------------------------------------------


class TestApplyResolution:
    def test_pick_a(self) -> None:
        d1 = _decision("Use PostgreSQL")
        d2 = _decision("Use MongoDB")
        c = _contradiction(d1, d2)

        result = apply_resolution(c, ResolutionType.PICK_A, rationale="PostgreSQL is better")
        assert result["status"] == "resolved"
        assert result["winner_id"] == str(d1.id)
        assert c.status == ContradictionStatus.RESOLVED
        assert c.resolved_by == "dashboard-user"
        assert "PostgreSQL" in c.resolution_note

    def test_pick_b(self) -> None:
        d1 = _decision("Use PostgreSQL")
        d2 = _decision("Use MongoDB")
        c = _contradiction(d1, d2)

        result = apply_resolution(c, ResolutionType.PICK_B)
        assert result["status"] == "resolved"
        assert result["winner_id"] == str(d2.id)

    def test_pick_a_supersedes_loser(self) -> None:
        d1 = _decision("Use PostgreSQL")
        d2 = _decision("Use MongoDB")
        c = _contradiction(d1, d2)

        result = apply_resolution(c, ResolutionType.PICK_A, decisions=[d1, d2])
        assert d2.valid is False
        assert len(result["changes"]) == 2  # resolved + superseded

    def test_accept_exception(self) -> None:
        c = _contradiction()
        result = apply_resolution(c, ResolutionType.ACCEPT_EXCEPTION)
        assert result["status"] == "ignored"
        assert c.status == ContradictionStatus.IGNORED
        assert "exception" in c.resolution_note.lower()

    def test_defer(self) -> None:
        c = _contradiction()
        result = apply_resolution(c, ResolutionType.DEFER)
        assert result["status"] == "deferred"
        assert c.status == ContradictionStatus.DEFERRED

    def test_dismiss(self) -> None:
        c = _contradiction()
        result = apply_resolution(c, ResolutionType.DISMISS)
        assert result["status"] == "resolved"
        assert "false positive" in c.resolution_note.lower()

    def test_update_decision(self) -> None:
        c = _contradiction()
        result = apply_resolution(c, ResolutionType.UPDATE_DECISION)
        assert result["status"] == "deferred"
        assert result["needs_new_decision"] is True

    def test_unknown_action(self) -> None:
        c = _contradiction()
        result = apply_resolution(c, "unknown_action")
        assert result["status"] == "error"

    def test_custom_actor(self) -> None:
        c = _contradiction()
        apply_resolution(c, ResolutionType.ACCEPT_EXCEPTION, actor="alice@example.com")
        assert c.resolved_by == "alice@example.com"

    def test_rationale_recorded(self) -> None:
        c = _contradiction()
        apply_resolution(c, ResolutionType.ACCEPT_EXCEPTION, rationale="Known trade-off")
        assert "Known trade-off" in c.resolution_note

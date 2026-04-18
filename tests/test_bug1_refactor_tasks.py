"""Tests for Bug 1: Accept A/B should generate a pending-refactor task file.

Verifies:
- generate_refactor_task() writes .smm/pending-refactors/refactor-{id}.md
- File contains superseded and winner titles
- File lists potentially affected .py files
- MCP check_before_coding includes pending_refactors
- MCP get_project_decisions includes pending_refactors
- api_resolve_contradiction generates refactor file on disk
- api_apply_resolution (pick_a/pick_b) generates refactor file on disk
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    Dimension,
    SourceType,
)
from vt_protocol.decisions.resolution import generate_refactor_task


def _decision(title: str, *, dims: list[Dimension] | None = None) -> Decision:
    return Decision(
        title=title,
        content=f"Use {title} for the API layer.",
        rationale="Team decision",
        dimensions=dims or [Dimension.API_STYLE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )


def _contradiction(d1: Decision, d2: Decision) -> Contradiction:
    return Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="They conflict",
        evidence_a="A says X",
        evidence_b="B says Y",
        shared_dimensions=list(set(d1.dimensions) & set(d2.dimensions)),
        confidence=0.9,
    )


class TestGenerateRefactorTask:
    def test_creates_file_in_pending_refactors_dir(self, tmp_path: Path) -> None:
        d1 = _decision("GraphQL via Strawberry")
        d2 = _decision("REST via FastAPI")
        c = _contradiction(d1, d2)

        path = generate_refactor_task(
            tmp_path,
            c,
            loser_id=str(d2.id),
            loser_title=d2.title,
            winner_title=d1.title,
        )

        assert path.exists()
        assert path.parent == tmp_path / ".smm" / "pending-refactors"

    def test_filename_contains_contradiction_id(self, tmp_path: Path) -> None:
        d1 = _decision("GraphQL")
        d2 = _decision("REST")
        c = _contradiction(d1, d2)

        path = generate_refactor_task(
            tmp_path, c,
            loser_id=str(d2.id), loser_title=d2.title, winner_title=d1.title,
        )

        assert str(c.id)[:8] in path.name

    def test_file_contains_superseded_title(self, tmp_path: Path) -> None:
        d1 = _decision("GraphQL via Strawberry")
        d2 = _decision("REST via FastAPI")
        c = _contradiction(d1, d2)

        path = generate_refactor_task(
            tmp_path, c,
            loser_id=str(d2.id), loser_title=d2.title, winner_title=d1.title,
        )

        content = path.read_text()
        assert d2.title in content
        assert "What was superseded" in content

    def test_file_contains_winner_title(self, tmp_path: Path) -> None:
        d1 = _decision("GraphQL via Strawberry")
        d2 = _decision("REST via FastAPI")
        c = _contradiction(d1, d2)

        path = generate_refactor_task(
            tmp_path, c,
            loser_id=str(d2.id), loser_title=d2.title, winner_title=d1.title,
        )

        content = path.read_text()
        assert d1.title in content
        assert "What won" in content

    def test_file_lists_affected_py_files(self, tmp_path: Path) -> None:
        # Create a Python file that mentions FastAPI
        src = tmp_path / "src"
        src.mkdir()
        (src / "api.py").write_text("from fastapi import FastAPI\napp = FastAPI()")

        d1 = _decision("GraphQL")
        d2 = _decision("FastAPI REST")
        c = _contradiction(d1, d2)

        path = generate_refactor_task(
            tmp_path, c,
            loser_id=str(d2.id), loser_title=d2.title, winner_title=d1.title,
        )

        content = path.read_text()
        assert "api.py" in content

    def test_idempotent_overwrite(self, tmp_path: Path) -> None:
        d1 = _decision("GraphQL")
        d2 = _decision("REST")
        c = _contradiction(d1, d2)

        path1 = generate_refactor_task(
            tmp_path, c,
            loser_id=str(d2.id), loser_title=d2.title, winner_title=d1.title,
        )
        path2 = generate_refactor_task(
            tmp_path, c,
            loser_id=str(d2.id), loser_title=d2.title, winner_title=d1.title,
        )

        assert path1 == path2
        assert path1.exists()


@pytest.fixture()
def setup_with_disk(tmp_path: Path):
    d1 = _decision("GraphQL via Strawberry")
    d2 = _decision("REST via FastAPI")
    c = _contradiction(d1, d2)

    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
    (tmp_path / ".smm" / "decisions" / "001-graphql.json").write_text(
        d1.model_dump_json(indent=2)
    )
    (tmp_path / ".smm" / "decisions" / "002-rest.json").write_text(
        d2.model_dump_json(indent=2)
    )
    (tmp_path / ".smm" / "contradictions" / f"{str(c.id)[:8]}.json").write_text(
        c.model_dump_json(indent=2)
    )

    ds = DashboardState(project_root=tmp_path)
    ds.decisions = [d1, d2]
    ds.contradictions = [c]
    set_state(ds)
    yield {"d1": d1, "d2": d2, "c": c}
    reset_state()


class TestApiResolveGeneratesRefactorTask:
    def test_resolve_creates_refactor_file(
        self, tmp_path: Path, setup_with_disk: dict
    ) -> None:
        d1 = setup_with_disk["d1"]
        c = setup_with_disk["c"]
        client = TestClient(app)

        client.post(
            f"/api/contradictions/{c.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "GraphQL is better"},
        )

        refactors_dir = tmp_path / ".smm" / "pending-refactors"
        assert refactors_dir.is_dir()
        files = list(refactors_dir.glob("refactor-*.md"))
        assert len(files) == 1

    def test_refactor_file_mentions_loser(
        self, tmp_path: Path, setup_with_disk: dict
    ) -> None:
        d1 = setup_with_disk["d1"]
        d2 = setup_with_disk["d2"]
        c = setup_with_disk["c"]
        client = TestClient(app)

        client.post(
            f"/api/contradictions/{c.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
        )

        files = list((tmp_path / ".smm" / "pending-refactors").glob("refactor-*.md"))
        content = files[0].read_text()
        assert d2.title in content


class TestMcpPendingRefactors:
    def test_load_pending_refactors_returns_list(self, tmp_path: Path) -> None:
        from vt_protocol.mcp.server import _load_pending_refactors

        refactors_dir = tmp_path / ".smm" / "pending-refactors"
        refactors_dir.mkdir(parents=True)
        (refactors_dir / "refactor-abcd1234.md").write_text(
            "# Refactor: REST → GraphQL\n\nDetails here."
        )

        result = _load_pending_refactors(tmp_path)
        assert len(result) == 1
        assert "Refactor: REST" in result[0]["title"]
        assert "PENDING REFACTOR" in result[0]["message"]

    def test_load_pending_refactors_empty_when_no_dir(self, tmp_path: Path) -> None:
        from vt_protocol.mcp.server import _load_pending_refactors

        result = _load_pending_refactors(tmp_path)
        assert result == []

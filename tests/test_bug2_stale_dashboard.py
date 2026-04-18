"""Tests for Bug 2: Dashboard shows contradictions on first load without restart.

Verifies:
- /api/contradictions re-reads disk on every call
- /api/home re-reads disk on every call
- Contradiction written after startup appears immediately
- Resolved contradiction disappears from unresolved list immediately
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    Dimension,
    SourceType,
)


def _decision(title: str) -> Decision:
    return Decision(
        title=title,
        content="Content",
        rationale="Rationale",
        dimensions=[Dimension.API_STYLE],
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
        evidence_a="A",
        evidence_b="B",
        shared_dimensions=[Dimension.API_STYLE],
        confidence=0.9,
    )


@pytest.fixture()
def empty_state(tmp_path: Path):
    """State loaded at startup with NO contradictions in memory."""
    (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
    ds = DashboardState(project_root=tmp_path)
    ds.decisions = []
    ds.contradictions = []  # empty at startup
    set_state(ds)
    yield tmp_path
    reset_state()


class TestApiContradictionsReloadsFromDisk:
    def test_new_contradiction_appears_without_restart(
        self, tmp_path: Path, empty_state: Path
    ) -> None:
        """Write a contradiction to disk AFTER startup — should appear on next request."""
        client = TestClient(app)

        # Verify empty at first
        resp = client.get("/api/contradictions")
        assert resp.json()["total"] == 0

        # Write contradiction to disk (simulates vt check running)
        d1 = _decision("GraphQL")
        d2 = _decision("REST")
        c = _contradiction(d1, d2)
        contradictions_dir = tmp_path / ".smm" / "contradictions"
        (contradictions_dir / f"{str(c.id)[:8]}.json").write_text(
            c.model_dump_json(indent=2)
        )

        # Next request should pick it up WITHOUT restart
        resp2 = client.get("/api/contradictions")
        assert resp2.json()["total"] == 1

    def test_resolved_contradiction_excluded_from_default_list(
        self, tmp_path: Path, empty_state: Path
    ) -> None:
        """Resolving a contradiction on disk should make it disappear from unresolved list."""
        client = TestClient(app)

        d1 = _decision("GraphQL")
        d2 = _decision("REST")
        c = _contradiction(d1, d2)
        contradictions_dir = tmp_path / ".smm" / "contradictions"
        (contradictions_dir / f"{str(c.id)[:8]}.json").write_text(
            c.model_dump_json(indent=2)
        )

        # Appears as unresolved
        assert client.get("/api/contradictions").json()["total"] == 1

        # Mark it as resolved on disk
        c.status = ContradictionStatus.RESOLVED
        (contradictions_dir / f"{str(c.id)[:8]}.json").write_text(
            c.model_dump_json(indent=2)
        )

        # Should now return 0 unresolved
        assert client.get("/api/contradictions").json()["total"] == 0


class TestApiHomeReloadsFromDisk:
    def test_home_open_contradictions_reflects_disk(
        self, tmp_path: Path, empty_state: Path
    ) -> None:
        client = TestClient(app)

        assert client.get("/api/home").json()["health"]["open_contradictions"] == 0

        d1 = _decision("PostgreSQL")
        d2 = _decision("MongoDB")
        c = _contradiction(d1, d2)
        contradictions_dir = tmp_path / ".smm" / "contradictions"
        (contradictions_dir / f"{str(c.id)[:8]}.json").write_text(
            c.model_dump_json(indent=2)
        )

        resp = client.get("/api/home").json()
        assert resp["health"]["open_contradictions"] == 1

    def test_home_triage_list_reflects_disk(
        self, tmp_path: Path, empty_state: Path
    ) -> None:
        client = TestClient(app)

        d1 = _decision("Celery")
        d2 = _decision("RQ")
        c = _contradiction(d1, d2)
        (tmp_path / ".smm" / "contradictions" / f"{str(c.id)[:8]}.json").write_text(
            c.model_dump_json(indent=2)
        )

        resp = client.get("/api/home").json()
        assert resp["triage"]["total"] == 1
        titles = [x["decision_a_title"] for x in resp["triage"]["contradictions"]]
        assert "Celery" in titles or "RQ" in titles

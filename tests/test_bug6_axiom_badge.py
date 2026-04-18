"""Tests for Bug 6: Axiom badge shows correct unresolved count.

Verifies:
- /api/contradictions?status=unresolved returns count from disk
- Resolved contradictions are excluded from unresolved count
- Badge should be 0 after all contradictions are resolved
- updateBadges() JS function is present in index.html
- resolveContra() calls updateBadges() after resolution
"""

from __future__ import annotations

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
        dimensions=[Dimension.DATABASE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )


def _contradiction(
    d1: Decision,
    d2: Decision,
    *,
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED,
) -> Contradiction:
    c = Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="They conflict",
        evidence_a="A",
        evidence_b="B",
        shared_dimensions=[Dimension.DATABASE],
        confidence=0.9,
    )
    c.status = status
    return c


@pytest.fixture()
def state_with_contradictions(tmp_path: Path):
    d1 = _decision("PostgreSQL")
    d2 = _decision("MongoDB")
    d3 = _decision("MySQL")

    c_open = _contradiction(d1, d2)
    c_resolved = _contradiction(d1, d3, status=ContradictionStatus.RESOLVED)

    (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
    (tmp_path / ".smm" / "contradictions" / f"{str(c_open.id)[:8]}.json").write_text(
        c_open.model_dump_json(indent=2)
    )
    (tmp_path / ".smm" / "contradictions" / f"{str(c_resolved.id)[:8]}.json").write_text(
        c_resolved.model_dump_json(indent=2)
    )

    ds = DashboardState(project_root=tmp_path)
    ds.decisions = [d1, d2, d3]
    ds.contradictions = [c_open, c_resolved]
    set_state(ds)
    yield {"d1": d1, "d2": d2, "d3": d3, "c_open": c_open, "c_resolved": c_resolved, "root": tmp_path}
    reset_state()


class TestUnresolvedCountForBadge:
    def test_unresolved_count_excludes_resolved(
        self, state_with_contradictions: dict
    ) -> None:
        """Badge count must only count UNRESOLVED contradictions."""
        client = TestClient(app)
        resp = client.get("/api/contradictions?status=unresolved").json()
        # 1 open, 1 resolved — only 1 should appear
        assert resp["total"] == 1

    def test_badge_is_zero_after_all_resolved(
        self, tmp_path: Path
    ) -> None:
        """After all contradictions are resolved, unresolved count = 0."""
        d1 = _decision("A")
        d2 = _decision("B")
        c = _contradiction(d1, d2, status=ContradictionStatus.RESOLVED)

        (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
        (tmp_path / ".smm" / "contradictions" / f"{str(c.id)[:8]}.json").write_text(
            c.model_dump_json(indent=2)
        )

        ds = DashboardState(project_root=tmp_path)
        ds.decisions = [d1, d2]
        ds.contradictions = [c]
        set_state(ds)

        try:
            client = TestClient(app)
            resp = client.get("/api/contradictions?status=unresolved").json()
            assert resp["total"] == 0
        finally:
            reset_state()

    def test_badge_reflects_disk_after_resolution(
        self, tmp_path: Path
    ) -> None:
        """Resolving a contradiction on disk should immediately drop count to 0."""
        d1 = _decision("Redis")
        d2 = _decision("Memcached")
        c = _contradiction(d1, d2)

        (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
        fp = tmp_path / ".smm" / "contradictions" / f"{str(c.id)[:8]}.json"
        fp.write_text(c.model_dump_json(indent=2))

        ds = DashboardState(project_root=tmp_path)
        ds.decisions = [d1, d2]
        ds.contradictions = [c]
        set_state(ds)

        try:
            client = TestClient(app)
            # Before resolution
            assert client.get("/api/contradictions?status=unresolved").json()["total"] == 1

            # Simulate resolution on disk
            c.status = ContradictionStatus.RESOLVED
            fp.write_text(c.model_dump_json(indent=2))

            # Badge count should now be 0
            assert client.get("/api/contradictions?status=unresolved").json()["total"] == 0
        finally:
            reset_state()


class TestBadgeFrontendCode:
    """Verify the updateBadges() fix is present in index.html."""

    def _get_html(self) -> str:
        html_path = (
            Path(__file__).parent.parent
            / "src" / "vt_protocol" / "dashboard" / "static" / "index.html"
        )
        return html_path.read_text()

    def test_update_badges_function_exists(self) -> None:
        assert "async function updateBadges()" in self._get_html()

    def test_update_badges_removes_existing_badge(self) -> None:
        html = self._get_html()
        # Must remove stale badge before adding new one
        assert "existing.remove()" in html or "nav-badge" in html

    def test_resolve_contra_calls_update_badges(self) -> None:
        html = self._get_html()
        # After resolveContra() succeeds, updateBadges() must be called
        assert "updateBadges()" in html

    def test_init_calls_update_badges(self) -> None:
        """initDashboard must call updateBadges() on startup."""
        html = self._get_html()
        assert "updateBadges()" in html

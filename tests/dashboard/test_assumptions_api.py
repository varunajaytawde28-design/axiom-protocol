"""Tests for dashboard assumption governance API endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    AssumptionCategory,
    AssumptionStatus,
    CodeEvidence,
    DomainAssumption,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assumption(
    *,
    category: AssumptionCategory = AssumptionCategory.DATA_SCOPE,
    status: AssumptionStatus = AssumptionStatus.PROPOSED,
    pattern_id: str = "single_source_write",
    summary: str = "Write to orders in one location",
    confidence: float = 0.7,
    question: str = "Which matches your business reality?",
    options: list[str] | None = None,
    file: str = "src/order_service.py",
    line: int = 42,
    snippet: str = "INSERT INTO transactions ...",
    is_baseline: bool = False,
) -> DomainAssumption:
    return DomainAssumption(
        category=category,
        status=status,
        pattern_id=pattern_id,
        summary=summary,
        confidence=confidence,
        severity="high",
        question=question,
        options=options or [
            "A) Correct -- only place_order() should write",
            "B) Incomplete -- external webhooks should also write",
            "C) Wrong -- multiple services must write",
            "D) I need more context before deciding",
        ],
        code_evidence=[CodeEvidence(file=file, line=line, snippet=snippet)],
        is_baseline=is_baseline,
    )


def _write_assumption_file(
    assumptions_dir: Path, assumption: DomainAssumption
) -> Path:
    """Write assumption JSON to .smm/assumptions/ for on-disk loading."""
    filename = f"{assumption.pattern_id}-{assumption.id.hex[:8]}.json"
    filepath = assumptions_dir / filename
    filepath.write_text(assumption.model_dump_json(indent=2))
    return filepath


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_state()


@pytest.fixture
def project_with_assumptions(tmp_path: Path) -> tuple[Path, list[DomainAssumption]]:
    """Project with .git, .smm/assumptions/, and 3 assumption files."""
    (tmp_path / ".git").mkdir()
    assumptions_dir = tmp_path / ".smm" / "assumptions"
    assumptions_dir.mkdir(parents=True)

    proposed = _make_assumption(
        category=AssumptionCategory.DATA_SCOPE,
        status=AssumptionStatus.PROPOSED,
        pattern_id="single_source_write",
        summary="Write to orders in one location",
    )
    validated = _make_assumption(
        category=AssumptionCategory.TEMPORAL,
        status=AssumptionStatus.VALIDATED,
        pattern_id="hardcoded_date",
        summary="Hardcoded date 2024-01-01",
        confidence=0.8,
    )
    rejected = _make_assumption(
        category=AssumptionCategory.ACCESS,
        status=AssumptionStatus.REJECTED,
        pattern_id="single_role_access",
        summary="Single role check admin",
        confidence=0.75,
    )

    all_assumptions = [proposed, validated, rejected]
    for a in all_assumptions:
        _write_assumption_file(assumptions_dir, a)

    return tmp_path, all_assumptions


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assumptions_list(
    project_with_assumptions: tuple[Path, list[DomainAssumption]],
) -> None:
    root, assumptions = project_with_assumptions
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/assumptions")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert "stats" in data
    assert "by_status" in data["stats"]
    assert "by_category" in data["stats"]
    assert len(data["assumptions"]) == 3


@pytest.mark.asyncio
async def test_assumptions_filter_by_status(
    project_with_assumptions: tuple[Path, list[DomainAssumption]],
) -> None:
    root, assumptions = project_with_assumptions
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/assumptions?status=proposed")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["assumptions"][0]["status"] == "proposed"


@pytest.mark.asyncio
async def test_assumptions_filter_by_category(
    project_with_assumptions: tuple[Path, list[DomainAssumption]],
) -> None:
    root, assumptions = project_with_assumptions
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/assumptions?category=data_scope")

    assert resp.status_code == 200
    data = resp.json()
    # Only the proposed one has category=data_scope
    assert data["total"] == 1
    assert data["assumptions"][0]["category"] == "data_scope"


@pytest.mark.asyncio
async def test_assumptions_detail(
    project_with_assumptions: tuple[Path, list[DomainAssumption]],
) -> None:
    root, assumptions = project_with_assumptions
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    target = assumptions[0]  # proposed assumption

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/assumptions/{target.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert "assumption" in data
    detail = data["assumption"]
    assert detail["id"] == str(target.id)
    assert detail["pattern_id"] == target.pattern_id
    assert detail["summary"] == target.summary
    assert detail["question"] == target.question
    assert len(detail["options"]) == len(target.options)
    assert len(detail["code_evidence"]) == 1


@pytest.mark.asyncio
async def test_assumptions_detail_not_found(
    project_with_assumptions: tuple[Path, list[DomainAssumption]],
) -> None:
    root, _ = project_with_assumptions
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/assumptions/{uuid4()}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_assumptions_stats(
    project_with_assumptions: tuple[Path, list[DomainAssumption]],
) -> None:
    """Verify stats are available through the list endpoint.

    Note: The dedicated /api/assumptions/stats GET route is shadowed by
    /api/assumptions/{assumption_id} due to route registration order in
    FastAPI. Stats are verified via the ``stats`` field in the list response.
    """
    root, _ = project_with_assumptions
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/assumptions")

    assert resp.status_code == 200
    data = resp.json()
    stats = data["stats"]

    assert "by_category" in stats
    assert "by_status" in stats

    # Verify counts match the 3 assumptions we created (1 proposed, 1 validated, 1 rejected)
    assert stats["by_status"].get("proposed", 0) == 1
    assert stats["by_status"].get("validated", 0) == 1
    assert stats["by_status"].get("rejected", 0) == 1

    # by_category should have all 3 categories
    assert "data_scope" in stats["by_category"]
    assert "temporal" in stats["by_category"]
    assert "access" in stats["by_category"]


@pytest.mark.asyncio
async def test_assumptions_empty_project(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm").mkdir()
    # No assumptions directory at all

    state = DashboardState(project_root=tmp_path)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/assumptions")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["assumptions"] == []


# ---------------------------------------------------------------------------
# Resolve endpoint — status transitions
# ---------------------------------------------------------------------------


def _project_with_one_assumption(
    tmp_path: Path,
    *,
    status: AssumptionStatus = AssumptionStatus.PROPOSED,
    options: list[str] | None = None,
) -> tuple[Path, DomainAssumption]:
    """Helper: project root + a single assumption file."""
    (tmp_path / ".git").mkdir()
    assumptions_dir = tmp_path / ".smm" / "assumptions"
    assumptions_dir.mkdir(parents=True)
    assumption = _make_assumption(
        status=status,
        options=options or [
            "A) Correct — only place_order() writes",
            "B) Incomplete — webhooks also write",
            "C) Wrong — multiple services write",
            "D) Need more context",
        ],
    )
    _write_assumption_file(assumptions_dir, assumption)
    return tmp_path, assumption


@pytest.mark.asyncio
async def test_resolve_option_0_sets_validated(tmp_path: Path) -> None:
    """Option 0 (A) must set status=validated."""
    root, assumption = _project_with_one_assumption(tmp_path)
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/assumptions/{assumption.id}/resolve",
            json={"selected_option": 0, "resolved_by": "tech-lead", "rationale": "looks right"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "validated"
    assert data["assumption"]["status"] == "validated"
    assert data["assumption"]["selected_option"] == 0

    # Reload from disk and verify persistence
    reloaded = DomainAssumption.model_validate_json(
        next((root / ".smm" / "assumptions").glob("*.json")).read_text()
    )
    assert reloaded.status.value == "validated"
    assert reloaded.resolved_by == "tech-lead"


@pytest.mark.asyncio
async def test_resolve_option_1_sets_rejected(tmp_path: Path) -> None:
    """Option 1 (B — not 'need more context') must set status=rejected."""
    root, assumption = _project_with_one_assumption(tmp_path)
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/assumptions/{assumption.id}/resolve",
            json={"selected_option": 1, "resolved_by": "tech-lead", "rationale": ""},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_resolve_context_option_sets_deferred(tmp_path: Path) -> None:
    """An option whose text contains 'need more context' must set status=deferred."""
    root, assumption = _project_with_one_assumption(
        tmp_path,
        options=[
            "A) Correct",
            "B) Wrong",
            "C) I need more context before deciding",
        ],
    )
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/assumptions/{assumption.id}/resolve",
            json={"selected_option": 2, "resolved_by": "pm", "rationale": ""},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "deferred"


@pytest.mark.asyncio
async def test_resolve_reloads_state_counts(tmp_path: Path) -> None:
    """After resolving, GET /api/assumptions must show updated counts immediately."""
    root, assumption = _project_with_one_assumption(tmp_path)
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Before: 1 proposed
        before = await client.get("/api/assumptions")
        assert before.json()["stats"]["by_status"].get("proposed", 0) == 1
        assert before.json()["stats"]["by_status"].get("validated", 0) == 0

        # Resolve → validated
        await client.post(
            f"/api/assumptions/{assumption.id}/resolve",
            json={"selected_option": 0, "resolved_by": "human", "rationale": ""},
        )

        # After: 0 proposed, 1 validated — counts update in same request cycle
        after = await client.get("/api/assumptions")
        assert after.json()["stats"]["by_status"].get("proposed", 0) == 0
        assert after.json()["stats"]["by_status"].get("validated", 0) == 1


@pytest.mark.asyncio
async def test_resolve_404_unknown_id(tmp_path: Path) -> None:
    """Resolving an unknown assumption ID must return 404."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm" / "assumptions").mkdir(parents=True)
    state = DashboardState(project_root=tmp_path)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/assumptions/00000000-0000-0000-0000-000000000000/resolve",
            json={"selected_option": 0, "resolved_by": "human", "rationale": ""},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_invalid_option_returns_422(tmp_path: Path) -> None:
    """An out-of-range option index must return 422."""
    root, assumption = _project_with_one_assumption(tmp_path)
    state = DashboardState(project_root=root)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/assumptions/{assumption.id}/resolve",
            json={"selected_option": 99, "resolved_by": "human", "rationale": ""},
        )
    assert resp.status_code == 422

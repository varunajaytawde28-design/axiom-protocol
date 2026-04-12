"""Tests for redesigned dashboard API endpoints.

Tests the new consolidated endpoints: /api/home, /api/specs, /api/contracts, /api/persona.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_decision(title: str = "Test Decision", **kwargs) -> Decision:
    defaults = {
        "title": title,
        "content": f"Content for {title}",
        "rationale": "Good reason",
        "status": DecisionStatus.ACTIVE,
        "decision_type": DecisionType.ARCHITECTURAL,
        "dimensions": [Dimension.DATABASE],
        "made_by": "claude-backend",
        "project": "test",
        "source_type": SourceType.AGENT,
    }
    defaults.update(kwargs)
    return Decision(**defaults)


def _make_contradiction(d1: Decision, d2: Decision, **kwargs) -> Contradiction:
    defaults = {
        "decision_a_id": d1.id,
        "decision_b_id": d2.id,
        "decision_a_title": d1.title,
        "decision_b_title": d2.title,
        "verdict": ContradictionVerdict.CONTRADICTION,
        "reasoning": "They conflict",
        "evidence_a": "Evidence from A",
        "evidence_b": "Evidence from B",
        "shared_dimensions": [Dimension.DATABASE],
        "confidence": 0.9,
        "status": ContradictionStatus.UNRESOLVED,
    }
    defaults.update(kwargs)
    return Contradiction(**defaults)


@pytest.fixture()
def project_with_data(tmp_path: Path):
    """Create a project with decisions, contradictions, and observation data."""
    smm = tmp_path / ".smm"
    smm.mkdir()

    # Decisions
    dec_dir = smm / "decisions"
    dec_dir.mkdir()
    d1 = _make_decision("Use PostgreSQL", dimensions=[Dimension.DATABASE])
    d2 = _make_decision("Use SQLite", dimensions=[Dimension.DATABASE])
    d3 = _make_decision("REST API", dimensions=[Dimension.API_STYLE], status=DecisionStatus.ACTIVE)

    for d in [d1, d2, d3]:
        (dec_dir / f"{d.id}.json").write_text(d.model_dump_json())

    # Contradictions
    contra_dir = smm / "contradictions"
    contra_dir.mkdir()
    c1 = _make_contradiction(d1, d2)
    (contra_dir / f"{c1.id}.json").write_text(c1.model_dump_json())

    # Observation data
    obs_dir = smm / "observation"
    obs_dir.mkdir()

    now = datetime.now(timezone.utc).isoformat()
    spans = [
        {
            "span_id": uuid4().hex[:16],
            "timestamp": now,
            "agent_id": "claude-backend",
            "model": "claude-sonnet-4-20250514",
            "provider": "anthropic",
            "tokens_in": 500,
            "tokens_out": 200,
            "cost_usd": 0.01,
            "latency_ms": 1200,
        }
    ]
    (obs_dir / "spans.json").write_text(json.dumps(spans))

    # Trajectory alerts for drift
    trajectory = [
        {"agent_id": "cursor-frontend", "drift_score": 0.68, "alert_type": "drift_warning"}
    ]
    (obs_dir / "trajectory.json").write_text(json.dumps(trajectory))

    # Assumptions
    assumptions_dir = smm / "assumptions"
    assumptions_dir.mkdir()

    assumption = {
        "id": uuid4().hex,
        "category": "data_scope",
        "status": "proposed",
        "pattern_id": "single_source_write",
        "summary": "Write to transactions found in one location",
        "code_evidence": [{"file": "order_service.py", "line": 5, "snippet": "INSERT INTO transactions"}],
        "confidence": 0.7,
        "severity": "high",
        "question": "How is the transactions table written to?",
        "options": ["Only here", "Multiple places", "External ETL", "I need more context"],
        "detected_at": now,
        "is_baseline": False,
        "detected_by": "vt-scanner",
    }
    (assumptions_dir / f"{assumption['pattern_id']}-{assumption['id'][:8]}.json").write_text(
        json.dumps(assumption)
    )

    state = DashboardState(project_root=tmp_path)
    state.load()
    set_state(state)
    yield {"tmp_path": tmp_path, "decisions": [d1, d2, d3], "contradiction": c1}
    reset_state()


@pytest.fixture()
def empty_project(tmp_path: Path):
    """Create an empty project."""
    (tmp_path / ".smm").mkdir()
    state = DashboardState(project_root=tmp_path)
    state.load()
    set_state(state)
    yield tmp_path
    reset_state()


@pytest.fixture()
def project_with_specs(tmp_path: Path):
    """Create a project with spec files."""
    smm = tmp_path / ".smm"
    smm.mkdir()

    # Decisions
    dec_dir = smm / "decisions"
    dec_dir.mkdir()
    d1 = _make_decision("Implement user authentication", content="Use JWT tokens for auth")
    d2 = _make_decision("Add rate limiting", content="Rate limit API endpoints at 100 req/s")
    for d in [d1, d2]:
        (dec_dir / f"{d.id}.json").write_text(d.model_dump_json())

    # Specs
    specs_dir = smm / "specs"
    specs_dir.mkdir()
    spec = {
        "id": "spec-001",
        "title": "Auth PRD",
        "raw_text": "# Authentication\\n1. The system must support JWT authentication\\n2. Rate limiting must be applied\\n3. Audit logging is required",
        "requirements": [
            {"id": "req-001", "text": "The system must support JWT authentication", "section": "Authentication", "index": 0},
            {"id": "req-002", "text": "Rate limiting must be applied", "section": "Authentication", "index": 1},
            {"id": "req-003", "text": "Audit logging is required", "section": "Authentication", "index": 2},
        ],
    }
    (specs_dir / "auth.json").write_text(json.dumps(spec))

    state = DashboardState(project_root=tmp_path)
    state.load()
    set_state(state)
    yield {"tmp_path": tmp_path, "decisions": [d1, d2]}
    reset_state()


# ---------------------------------------------------------------------------
# Home endpoint tests
# ---------------------------------------------------------------------------


class TestHomeEndpoint:
    """GET /api/home -- aggregated dashboard data."""

    @pytest.mark.anyio
    async def test_home_with_data(self, project_with_data):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/home")
        assert resp.status_code == 200
        data = resp.json()

        # Health section
        assert "health" in data
        h = data["health"]
        assert "coherence_score" in h
        assert "status" in h
        assert h["total_decisions"] == 3
        assert h["active_decisions"] >= 2
        assert h["open_contradictions"] >= 1

    @pytest.mark.anyio
    async def test_home_has_drift(self, project_with_data):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/home")
        data = resp.json()
        drift = data["health"]["highest_drift"]
        assert drift["agent"] == "cursor-frontend"
        assert drift["score"] == 0.68

    @pytest.mark.anyio
    async def test_home_has_triage(self, project_with_data):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/home")
        data = resp.json()
        assert "triage" in data
        assert data["triage"]["total"] >= 1
        assert len(data["triage"]["contradictions"]) >= 1

    @pytest.mark.anyio
    async def test_home_has_signals(self, project_with_data):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/home")
        data = resp.json()
        assert "signals" in data
        assert isinstance(data["signals"], list)

    @pytest.mark.anyio
    async def test_home_empty_project(self, empty_project):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/home")
        assert resp.status_code == 200
        data = resp.json()
        assert data["health"]["total_decisions"] == 0
        assert data["health"]["coherence_score"] == 1.0
        assert data["health"]["status"] == "healthy"
        assert data["triage"]["total"] == 0

    @pytest.mark.anyio
    async def test_home_pending_assumptions(self, project_with_data):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/home")
        data = resp.json()
        assert data["health"]["pending_assumptions"] >= 0


# ---------------------------------------------------------------------------
# Specs endpoint tests
# ---------------------------------------------------------------------------


class TestSpecsEndpoint:
    """GET /api/specs -- living specifications."""

    @pytest.mark.anyio
    async def test_specs_with_data(self, project_with_specs):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/specs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_specs"] == 1
        assert data["total_requirements"] == 3
        assert "coverage_percent" in data
        assert len(data["specs"]) == 1

    @pytest.mark.anyio
    async def test_specs_empty(self, empty_project):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/specs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_specs"] == 0
        assert data["total_requirements"] == 0
        assert data["coverage_percent"] == 0

    @pytest.mark.anyio
    async def test_specs_coverage_reports(self, project_with_specs):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/specs")
        data = resp.json()
        assert len(data["coverage_reports"]) == 1
        report = data["coverage_reports"][0]
        assert "total" in report
        assert "coverage_percent" in report
        assert "coverages" in report


# ---------------------------------------------------------------------------
# Contracts endpoint tests
# ---------------------------------------------------------------------------


class TestContractsEndpoint:
    """GET /api/contracts -- API contract analysis."""

    @pytest.mark.anyio
    async def test_contracts_empty(self, empty_project):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/contracts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_endpoints"] == 0
        assert data["consistency_score"] == 1.0
        assert data["violations"] == []

    @pytest.mark.anyio
    async def test_contracts_with_routes(self, tmp_path: Path):
        """Create a project with Python files that have route decorators."""
        smm = tmp_path / ".smm"
        smm.mkdir()
        (smm / "decisions").mkdir()

        # Create a fake FastAPI source file
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        api_file = src_dir / "api.py"
        api_file.write_text('''
from fastapi import FastAPI
app = FastAPI()

@app.get("/users")
def list_users():
    return []

@app.post("/users")
def create_user():
    return {}

@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {}
''')

        state = DashboardState(project_root=tmp_path)
        state.load()
        set_state(state)
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/contracts")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_endpoints"] >= 3
            assert data["consistency_score"] >= 0.0
            assert isinstance(data["services"], list)
        finally:
            reset_state()

    @pytest.mark.anyio
    async def test_contracts_structure(self, tmp_path: Path):
        """Verify response structure."""
        smm = tmp_path / ".smm"
        smm.mkdir()
        (smm / "decisions").mkdir()

        state = DashboardState(project_root=tmp_path)
        state.load()
        set_state(state)
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/contracts")
            data = resp.json()
            assert "total_endpoints" in data
            assert "violation_count" in data
            assert "consistency_score" in data
            assert "services" in data
            assert "violations" in data
        finally:
            reset_state()


# ---------------------------------------------------------------------------
# Persona endpoint tests
# ---------------------------------------------------------------------------


class TestPersonaEndpoint:
    """GET /api/persona -- persona routing."""

    @pytest.mark.anyio
    async def test_persona_default(self, empty_project):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/persona")
        assert resp.status_code == 200
        data = resp.json()
        assert "persona" in data
        assert "routing" in data
        assert "available_personas" in data
        assert data["persona"] == "tech-lead"

    @pytest.mark.anyio
    async def test_persona_routing_structure(self, empty_project):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/persona")
        data = resp.json()
        routing = data["routing"]
        assert "landing" in routing
        assert "sidebar_order" in routing
        assert len(routing["sidebar_order"]) == 5

    @pytest.mark.anyio
    async def test_persona_available_list(self, empty_project):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/persona")
        data = resp.json()
        assert set(data["available_personas"]) == {"tech-lead", "ciso", "pm", "qa"}

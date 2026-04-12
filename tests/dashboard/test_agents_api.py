"""Tests for dashboard /api/agents endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from vt_protocol.config import save_governance_config
from vt_protocol.dashboard.app import DashboardState, app, set_state, reset_state
from vt_protocol.decisions.models import (
    AgentConfig,
    Decision,
    DecisionType,
    Dimension,
    GovernanceConfig,
    SourceType,
)


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_state()


@pytest.fixture
def project_with_agents(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "audit").mkdir(parents=True)

    cfg = GovernanceConfig(
        agents={
            "claude": True,
            "claude-backend": AgentConfig(
                type="claude-code",
                role="backend",
                display_name="Claude Backend",
                allowed_paths=["src/**", "tests/**"],
                blocked_paths=[".env"],
                allowed_dimensions=["database", "api-style"],
                restricted_dimensions=["security"],
                context_level="relevant",
            ),
            "cursor-frontend": AgentConfig(
                type="cursor",
                role="frontend",
                display_name="Cursor Frontend",
                allowed_paths=["ui/**", "components/**"],
                blocked_paths=[".env", "api/**"],
                allowed_dimensions=["state-management"],
                restricted_dimensions=["database"],
                context_level="minimal",
            ),
        }
    )
    save_governance_config(tmp_path, cfg)

    # Write a decision made by claude-backend
    d = Decision(
        title="Use PostgreSQL",
        content="Chose PostgreSQL for primary database.",
        rationale="Best fit for our schema",
        dimensions=[Dimension.DATABASE],
        made_by="claude-backend",
        project="test",
        source_type=SourceType.AGENT,
    )
    (tmp_path / ".smm" / "decisions" / "001-db.json").write_text(
        d.model_dump_json(indent=2)
    )

    return tmp_path


@pytest.mark.asyncio
async def test_list_agents(project_with_agents: Path) -> None:
    state = DashboardState(project_root=project_with_agents)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        names = [a["name"] for a in data["agents"]]
        assert "claude" in names
        assert "claude-backend" in names
        assert "cursor-frontend" in names


@pytest.mark.asyncio
async def test_agent_detail(project_with_agents: Path) -> None:
    state = DashboardState(project_root=project_with_agents)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/claude-backend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "claude-backend"
        assert data["role"] == "backend"
        assert data["type"] == "claude-code"
        assert "src/**" in data["allowed_paths"]
        assert len(data["recent_decisions"]) >= 1


@pytest.mark.asyncio
async def test_agent_not_found(project_with_agents: Path) -> None:
    state = DashboardState(project_root=project_with_agents)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/nonexistent")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_simple_agent_detail(project_with_agents: Path) -> None:
    state = DashboardState(project_root=project_with_agents)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/claude")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "simple"


@pytest.mark.asyncio
async def test_agent_activity_stats(project_with_agents: Path) -> None:
    state = DashboardState(project_root=project_with_agents)
    state.load()
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents")
        data = resp.json()
        backend = next(a for a in data["agents"] if a["name"] == "claude-backend")
        assert backend["activity"]["decisions_made"] >= 1


@pytest.mark.asyncio
async def test_agents_empty_project(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm").mkdir()
    state = DashboardState(project_root=tmp_path)
    set_state(state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 0

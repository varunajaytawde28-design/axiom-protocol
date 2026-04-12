"""Tests for MCP server agent access enforcement."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vt_protocol.config import save_governance_config
from vt_protocol.decisions.models import (
    AgentConfig,
    Decision,
    DecisionType,
    Dimension,
    GovernanceConfig,
    SourceType,
)
from vt_protocol.mcp.server import (
    _check_dimension_access,
    _check_path_access,
    _filter_decisions_by_context,
    check_before_coding,
    get_project_decisions,
    report_decision,
    validate_change,
)


@pytest.fixture
def backend_agent() -> AgentConfig:
    return AgentConfig(
        type="claude-code",
        role="backend",
        allowed_paths=["src/**", "api/**", "tests/**"],
        blocked_paths=[".env", ".env.*", "secrets/**", "terraform/**"],
        allowed_dimensions=["database", "api-style", "caching", "testing"],
        restricted_dimensions=["security", "auth"],
        context_level="relevant",
        session_ttl_minutes=60,
    )


@pytest.fixture
def frontend_agent() -> AgentConfig:
    return AgentConfig(
        type="cursor",
        role="frontend",
        allowed_paths=["ui/**", "components/**", "pages/**"],
        blocked_paths=[".env", "api/**", "services/**"],
        allowed_dimensions=["state-management", "testing"],
        restricted_dimensions=["security", "database"],
        context_level="minimal",
    )


# ---------------------------------------------------------------------------
# Path access checks
# ---------------------------------------------------------------------------


class TestPathAccess:
    def test_allowed_path(self, backend_agent: AgentConfig) -> None:
        result = _check_path_access(backend_agent, "src/main.py")
        assert result["allowed"] is True

    def test_blocked_path(self, backend_agent: AgentConfig) -> None:
        result = _check_path_access(backend_agent, ".env")
        assert result["allowed"] is False
        assert ".env" in result["reason"]

    def test_blocked_glob(self, backend_agent: AgentConfig) -> None:
        result = _check_path_access(backend_agent, "secrets/api_key.txt")
        assert result["allowed"] is False

    def test_not_in_allowed(self, backend_agent: AgentConfig) -> None:
        result = _check_path_access(backend_agent, "frontend/app.jsx")
        assert result["allowed"] is False

    def test_empty_path(self, backend_agent: AgentConfig) -> None:
        result = _check_path_access(backend_agent, "")
        assert result["allowed"] is True

    def test_no_allowed_paths_means_all_allowed(self) -> None:
        agent = AgentConfig(blocked_paths=[".env"])
        result = _check_path_access(agent, "anything/goes.py")
        assert result["allowed"] is True

    def test_blocked_takes_priority(self) -> None:
        agent = AgentConfig(
            allowed_paths=["**"],
            blocked_paths=["secrets/**"],
        )
        result = _check_path_access(agent, "secrets/key.pem")
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# Dimension access checks
# ---------------------------------------------------------------------------


class TestDimensionAccess:
    def test_allowed_dimension(self, backend_agent: AgentConfig) -> None:
        result = _check_dimension_access(backend_agent, ["database"])
        assert result["allowed"] is True

    def test_restricted_dimension(self, backend_agent: AgentConfig) -> None:
        result = _check_dimension_access(backend_agent, ["security"])
        assert result["allowed"] is False
        assert "security" in result["restricted"]

    def test_mixed_allowed_and_restricted(self, backend_agent: AgentConfig) -> None:
        result = _check_dimension_access(backend_agent, ["database", "security"])
        assert result["allowed"] is False
        assert "security" in result["restricted"]

    def test_empty_dimensions(self, backend_agent: AgentConfig) -> None:
        result = _check_dimension_access(backend_agent, [])
        assert result["allowed"] is True

    def test_no_allowed_dimensions_means_all(self) -> None:
        agent = AgentConfig(restricted_dimensions=["security"])
        result = _check_dimension_access(agent, ["database"])
        assert result["allowed"] is True

    def test_denied_not_in_allowed(self, backend_agent: AgentConfig) -> None:
        result = _check_dimension_access(backend_agent, ["deployment"])
        assert result["allowed"] is False


# ---------------------------------------------------------------------------
# Context-level filtering
# ---------------------------------------------------------------------------


class TestContextFiltering:
    def _make_decisions(self) -> list[Decision]:
        return [
            Decision(
                title=f"Decision {i}",
                content=f"Content for decision {i}",
                rationale="test",
                dimensions=[Dimension.DATABASE if i % 2 == 0 else Dimension.SECURITY],
                made_by="test",
                project="test",
                source_type=SourceType.MANUAL,
            )
            for i in range(20)
        ]

    def test_full_context(self) -> None:
        agent = AgentConfig(context_level="full")
        decisions = self._make_decisions()
        filtered = _filter_decisions_by_context(decisions, agent)
        assert len(filtered) == 20

    def test_relevant_context(self) -> None:
        agent = AgentConfig(context_level="relevant")
        decisions = self._make_decisions()
        filtered = _filter_decisions_by_context(decisions, agent)
        assert len(filtered) <= 10

    def test_minimal_context(self) -> None:
        agent = AgentConfig(context_level="minimal")
        decisions = self._make_decisions()
        filtered = _filter_decisions_by_context(decisions, agent)
        assert len(filtered) <= 5

    def test_dimension_filtering(self) -> None:
        agent = AgentConfig(
            allowed_dimensions=["database"],
            context_level="full",
        )
        decisions = self._make_decisions()
        filtered = _filter_decisions_by_context(decisions, agent)
        # Only database dimension decisions should pass
        for d in filtered:
            assert Dimension.DATABASE in d.dimensions or not d.dimensions


# ---------------------------------------------------------------------------
# MCP tool integration with agent enforcement
# ---------------------------------------------------------------------------


class TestMCPAgentEnforcement:
    @pytest.fixture
    def project_with_agent(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".smm" / "decisions").mkdir(parents=True)
        cfg = GovernanceConfig(
            agents={
                "test-backend": AgentConfig(
                    type="claude-code",
                    role="backend",
                    allowed_paths=["src/**"],
                    blocked_paths=[".env", "secrets/**"],
                    allowed_dimensions=["database"],
                    restricted_dimensions=["security"],
                    context_level="relevant",
                ),
            }
        )
        save_governance_config(tmp_path, cfg)
        return tmp_path

    def test_check_before_coding_blocked_path(self, project_with_agent: Path) -> None:
        with patch("vt_protocol.mcp.server._load_config") as mock_cfg:
            from vt_protocol.config import load_governance_config
            mock_cfg.return_value = load_governance_config(project_with_agent)
            with patch("vt_protocol.mcp.server._detect_project", return_value="test"):
                result = json.loads(check_before_coding(".env", project="test", agent_id="test-backend"))
                assert result.get("access_denied") is True

    def test_check_before_coding_allowed_path(self, project_with_agent: Path) -> None:
        with patch("vt_protocol.mcp.server._load_config") as mock_cfg:
            from vt_protocol.config import load_governance_config
            mock_cfg.return_value = load_governance_config(project_with_agent)
            with patch("vt_protocol.mcp.server._detect_project", return_value="test"):
                result = json.loads(check_before_coding("src/main.py", project="test", agent_id="test-backend"))
                assert "access_denied" not in result or result["access_denied"] is False

    def test_report_decision_restricted_dimension(self, project_with_agent: Path) -> None:
        with patch("vt_protocol.mcp.server._load_config") as mock_cfg:
            from vt_protocol.config import load_governance_config
            mock_cfg.return_value = load_governance_config(project_with_agent)
            with patch("vt_protocol.mcp.server._detect_project", return_value="test"):
                result = json.loads(report_decision(
                    title="Security Fix",
                    content="Update auth middleware",
                    dimensions=["security"],
                    agent_id="test-backend",
                    project="test",
                ))
                assert result["status"] == "proposed"
                assert "restricted" in result["note"].lower() or "approval" in result["note"].lower()

    def test_validate_change_blocked_path(self, project_with_agent: Path) -> None:
        with patch("vt_protocol.mcp.server._load_config") as mock_cfg:
            from vt_protocol.config import load_governance_config
            mock_cfg.return_value = load_governance_config(project_with_agent)
            with patch("vt_protocol.mcp.server._detect_project", return_value="test"):
                result = json.loads(validate_change(
                    diff="+ SECRET=abc",
                    file_path=".env",
                    agent_id="test-backend",
                    project="test",
                ))
                assert result.get("access_denied") is True

    def test_no_agent_id_no_enforcement(self) -> None:
        """Without agent_id, no access checks are applied."""
        result = json.loads(check_before_coding("anything.py", project="test"))
        assert "access_denied" not in result

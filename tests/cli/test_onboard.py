"""Tests for vt onboard CLI command and agent onboarding wizard."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.config import load_governance_config, save_governance_config
from vt_protocol.decisions.models import AgentConfig, GovernanceConfig


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "generated").mkdir(parents=True)
    (tmp_path / ".smm" / "audit").mkdir(parents=True)
    (tmp_path / ".smm" / "cache").mkdir(parents=True)
    # Write governance.yaml with an onboarded agent
    cfg = GovernanceConfig(
        agents={
            "claude": True,
            "claude-backend": AgentConfig(
                type="claude-code",
                role="backend",
                display_name="Claude Backend",
                allowed_paths=["src/**", "tests/**"],
                blocked_paths=[".env", "secrets/**"],
                allowed_dimensions=["database", "api-style"],
                restricted_dimensions=["security"],
                context_level="relevant",
                session_ttl_minutes=60,
            ),
        }
    )
    save_governance_config(tmp_path, cfg)
    return tmp_path


# ---------------------------------------------------------------------------
# vt onboard --list
# ---------------------------------------------------------------------------


class TestOnboardList:
    def test_list_agents(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["onboard", "--list", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "claude" in result.output
        assert "claude-backend" in result.output
        assert "backend" in result.output

    def test_list_empty(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".smm").mkdir()
        (tmp_path / "governance.yaml").write_text(
            "agents:\n  claude: true\n"
        )
        result = runner.invoke(main, ["onboard", "--list", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "claude" in result.output


# ---------------------------------------------------------------------------
# vt onboard --remove
# ---------------------------------------------------------------------------


class TestOnboardRemove:
    def test_remove_existing(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["onboard", "--remove", "claude-backend", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "Removed" in result.output
        cfg = load_governance_config(project_dir)
        assert "claude-backend" not in cfg.agents

    def test_remove_nonexistent(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["onboard", "--remove", "nonexistent", "--path", str(project_dir)])
        assert result.exit_code == 1
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# vt onboard --edit
# ---------------------------------------------------------------------------


class TestOnboardEdit:
    def test_edit_existing(self, runner: CliRunner, project_dir: Path) -> None:
        # Simulate user input for the wizard
        result = runner.invoke(
            main,
            ["onboard", "--edit", "claude-backend", "--path", str(project_dir)],
            input="claude-code\nbackend\nrelevant\n60\ny\n",
        )
        assert result.exit_code == 0
        assert "Updated" in result.output

    def test_edit_nonexistent(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["onboard", "--edit", "ghost", "--path", str(project_dir)])
        assert result.exit_code == 1
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# vt onboard (interactive)
# ---------------------------------------------------------------------------


class TestOnboardInteractive:
    def test_no_agents_configured(self, runner: CliRunner, project_dir: Path) -> None:
        # User declines to configure agents
        result = runner.invoke(
            main,
            ["onboard", "--path", str(project_dir)],
            input="n\n",
        )
        assert result.exit_code == 0
        assert "No agents configured" in result.output

    def test_add_one_agent(self, runner: CliRunner, project_dir: Path) -> None:
        # User configures one agent then finishes
        result = runner.invoke(
            main,
            ["onboard", "--path", str(project_dir)],
            input="y\nnew-agent\nclaude-code\nfull-stack\nfull\n60\ny\n\n",
        )
        assert result.exit_code == 0
        assert "new-agent" in result.output
        cfg = load_governance_config(project_dir)
        assert "new-agent" in cfg.agents


# ---------------------------------------------------------------------------
# vt config llm
# ---------------------------------------------------------------------------


class TestConfigLlm:
    def test_display_current(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["config", "llm", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "Provider:" in result.output

    def test_set_provider_openai(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["config", "llm", "--provider", "openai", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "openai" in result.output
        cfg = load_governance_config(project_dir)
        assert cfg.model.provider == "openai"

    def test_set_provider_none(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["config", "llm", "--provider", "none", "--path", str(project_dir)])
        assert result.exit_code == 0
        cfg = load_governance_config(project_dir)
        assert cfg.model.provider == "none"

    def test_set_provider_ollama(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["config", "llm", "--provider", "ollama", "--path", str(project_dir)])
        assert result.exit_code == 0
        cfg = load_governance_config(project_dir)
        assert cfg.model.provider == "ollama"

    def test_set_model(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["config", "llm", "--model", "gpt-4o", "--path", str(project_dir)])
        assert result.exit_code == 0
        cfg = load_governance_config(project_dir)
        assert cfg.model.model == "gpt-4o"

    def test_test_connection_none(self, runner: CliRunner, project_dir: Path) -> None:
        # Set to none first
        runner.invoke(main, ["config", "llm", "--provider", "none", "--path", str(project_dir)])
        result = runner.invoke(main, ["config", "llm", "--test", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "NLI-only" in result.output

    @patch("vt_protocol.decisions.llm_providers.test_ollama_connection")
    def test_test_connection_ollama(self, mock_test, runner: CliRunner, project_dir: Path) -> None:
        mock_test.return_value = {"connected": True, "models": ["llama3:8b"], "error": None}
        runner.invoke(main, ["config", "llm", "--provider", "ollama", "--path", str(project_dir)])
        result = runner.invoke(main, ["config", "llm", "--test", "--path", str(project_dir)])
        assert result.exit_code == 0

    def test_no_project(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["config", "llm", "--path", str(tmp_path)])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# AgentConfig model
# ---------------------------------------------------------------------------


class TestAgentConfig:
    def test_defaults(self):
        ac = AgentConfig()
        assert ac.type == "claude-code"
        assert ac.role == "full-stack"
        assert ac.context_level == "full"
        assert ac.session_ttl_minutes == 60
        assert ac.block_on_contradiction is True

    def test_custom_config(self):
        ac = AgentConfig(
            type="cursor",
            role="frontend",
            allowed_paths=["ui/**"],
            blocked_paths=[".env"],
            allowed_dimensions=["state-management"],
            restricted_dimensions=["database"],
            context_level="minimal",
            session_ttl_minutes=30,
            block_on_contradiction=False,
        )
        assert ac.type == "cursor"
        assert ac.role == "frontend"
        assert ac.context_level == "minimal"
        assert ac.session_ttl_minutes == 30

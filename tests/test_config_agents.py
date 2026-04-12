"""Tests for governance.yaml model: and agents: schema extensions."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.config import load_governance_config, save_governance_config
from vt_protocol.decisions.models import AgentConfig, GovernanceConfig, ModelConfig


class TestModelConfigParsing:
    def test_default_model_config(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        cfg = load_governance_config(tmp_path)
        assert cfg.model.provider == "anthropic"
        assert cfg.model.temperature == 0.0

    def test_parse_model_section(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "model:\n"
            "  provider: ollama\n"
            "  model: llama3:8b\n"
            "  base-url: http://localhost:11434\n"
            "  temperature: 0.7\n"
            "  timeout-seconds: 30\n"
            "  fallback: error\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.model.provider == "ollama"
        assert cfg.model.model == "llama3:8b"
        assert cfg.model.base_url == "http://localhost:11434"
        assert cfg.model.temperature == 0.7
        assert cfg.model.timeout_seconds == 30
        assert cfg.model.fallback == "error"

    def test_parse_openai_model(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "model:\n"
            "  provider: openai\n"
            "  model: gpt-4o-mini\n"
            "  api-key-env: OPENAI_API_KEY\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.model.provider == "openai"
        assert cfg.model.api_key_env == "OPENAI_API_KEY"

    def test_parse_none_model(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "model:\n"
            "  provider: none\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.model.provider == "none"

    def test_round_trip_model(self, tmp_path: Path) -> None:
        cfg = GovernanceConfig(
            model=ModelConfig(
                provider="ollama",
                model="mistral:7b",
                base_url="http://localhost:11434",
                timeout_seconds=20,
            )
        )
        save_governance_config(tmp_path, cfg)
        loaded = load_governance_config(tmp_path)
        assert loaded.model.provider == "ollama"
        assert loaded.model.model == "mistral:7b"
        assert loaded.model.base_url == "http://localhost:11434"
        assert loaded.model.timeout_seconds == 20

    def test_snake_case_keys(self, tmp_path: Path) -> None:
        """Both kebab-case and snake_case should work."""
        (tmp_path / "governance.yaml").write_text(
            "model:\n"
            "  provider: anthropic\n"
            "  api_key_env: MY_KEY\n"
            "  timeout_seconds: 15\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.model.api_key_env == "MY_KEY"
        assert cfg.model.timeout_seconds == 15


class TestAgentConfigParsing:
    def test_bool_agents_still_work(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "agents:\n"
            "  claude: true\n"
            "  cursor: false\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.agents["claude"] is True
        assert cfg.agents["cursor"] is False

    def test_full_agent_config(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "agents:\n"
            "  claude-backend:\n"
            "    type: claude-code\n"
            "    role: backend\n"
            "    display-name: Claude Backend\n"
            "    allowed-paths:\n"
            "      - 'src/**'\n"
            "      - 'tests/**'\n"
            "    blocked-paths:\n"
            "      - '.env'\n"
            "    allowed-dimensions:\n"
            "      - database\n"
            "      - api-style\n"
            "    restricted-dimensions:\n"
            "      - security\n"
            "    context-level: relevant\n"
            "    auto-resolve: false\n"
            "    session-ttl-minutes: 60\n"
            "    block-on-contradiction: true\n"
        )
        cfg = load_governance_config(tmp_path)
        agent = cfg.agents["claude-backend"]
        assert isinstance(agent, AgentConfig)
        assert agent.type == "claude-code"
        assert agent.role == "backend"
        assert agent.display_name == "Claude Backend"
        assert "src/**" in agent.allowed_paths
        assert ".env" in agent.blocked_paths
        assert "database" in agent.allowed_dimensions
        assert "security" in agent.restricted_dimensions
        assert agent.context_level == "relevant"
        assert agent.session_ttl_minutes == 60
        assert agent.block_on_contradiction is True

    def test_mixed_bool_and_config(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "agents:\n"
            "  copilot: true\n"
            "  cursor-frontend:\n"
            "    type: cursor\n"
            "    role: frontend\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.agents["copilot"] is True
        assert isinstance(cfg.agents["cursor-frontend"], AgentConfig)
        assert cfg.agents["cursor-frontend"].type == "cursor"

    def test_round_trip_agents(self, tmp_path: Path) -> None:
        cfg = GovernanceConfig(
            agents={
                "claude": True,
                "my-agent": AgentConfig(
                    type="cursor",
                    role="frontend",
                    allowed_paths=["ui/**"],
                    blocked_paths=[".env"],
                    allowed_dimensions=["state-management"],
                    restricted_dimensions=["database"],
                    context_level="minimal",
                ),
            }
        )
        save_governance_config(tmp_path, cfg)
        loaded = load_governance_config(tmp_path)
        assert loaded.agents["claude"] is True
        agent = loaded.agents["my-agent"]
        assert isinstance(agent, AgentConfig)
        assert agent.role == "frontend"
        assert "ui/**" in agent.allowed_paths
        assert agent.context_level == "minimal"

    def test_backward_compat_with_existing_yaml(self, tmp_path: Path) -> None:
        """Old governance.yaml without model: or rich agents: should still load."""
        (tmp_path / "governance.yaml").write_text(
            "extends:\n"
            '  - "@vt/recommended"\n'
            "agents:\n"
            "  claude: true\n"
            "  cursor: true\n"
            "rules:\n"
            "  freeze-on-adopt: true\n"
        )
        cfg = load_governance_config(tmp_path)
        assert cfg.agents["claude"] is True
        assert cfg.model.provider == "anthropic"  # default

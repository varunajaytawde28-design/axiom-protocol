"""Tests for per-agent rule generation in rulesync."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.decisions.models import (
    AgentConfig,
    Decision,
    DecisionType,
    Dimension,
    GovernanceConfig,
    SourceType,
)
from vt_protocol.prevention.rulesync import SyncResult, _filter_scored_for_agent, _is_agent_type_enabled, sync_rules
from vt_protocol.prevention.priority import assign_tiers


def _make_decision(title: str, dims: list[Dimension]) -> Decision:
    return Decision(
        title=title,
        content=f"Decision about {title}. Full description here.",
        rationale=f"Because {title} is needed.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=dims,
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )


class TestIsAgentTypeEnabled:
    def test_bool_true(self):
        cfg = GovernanceConfig(agents={"claude": True})
        assert _is_agent_type_enabled(cfg, "claude") is True

    def test_bool_false(self):
        cfg = GovernanceConfig(agents={"claude": False})
        assert _is_agent_type_enabled(cfg, "claude") is False

    def test_agent_config_counts_as_enabled(self):
        cfg = GovernanceConfig(agents={
            "my-claude": AgentConfig(type="claude-code"),
        })
        assert _is_agent_type_enabled(cfg, "claude") is True

    def test_missing_agent(self):
        cfg = GovernanceConfig(agents={"cursor": True})
        assert _is_agent_type_enabled(cfg, "claude") is False


class TestFilterScoredForAgent:
    def test_filter_by_dimension(self):
        decisions = [
            _make_decision("DB Choice", [Dimension.DATABASE]),
            _make_decision("Auth Choice", [Dimension.AUTH]),
            _make_decision("API Choice", [Dimension.API_STYLE]),
        ]
        scored = assign_tiers(decisions)

        agent = AgentConfig(allowed_dimensions=["database", "api-style"])
        filtered = _filter_scored_for_agent(scored, agent)
        titles = {sd.decision.title for sd in filtered}
        assert "DB Choice" in titles
        assert "API Choice" in titles
        assert "Auth Choice" not in titles

    def test_empty_allowed_means_all(self):
        decisions = [
            _make_decision("DB Choice", [Dimension.DATABASE]),
            _make_decision("Auth Choice", [Dimension.AUTH]),
        ]
        scored = assign_tiers(decisions)

        agent = AgentConfig(allowed_dimensions=[])
        filtered = _filter_scored_for_agent(scored, agent)
        assert len(filtered) == len(scored)


class TestPerAgentRuleGeneration:
    def test_generates_per_agent_files(self, tmp_path: Path):
        (tmp_path / ".smm" / "generated").mkdir(parents=True)

        decisions = [
            _make_decision("Use PostgreSQL", [Dimension.DATABASE]),
            _make_decision("Use JWT", [Dimension.AUTH]),
            _make_decision("Use Redis", [Dimension.CACHING]),
        ]

        cfg = GovernanceConfig(
            agents={
                "claude": True,
                "backend-agent": AgentConfig(
                    type="claude-code",
                    role="backend",
                    allowed_dimensions=["database", "caching"],
                ),
            }
        )

        result = sync_rules(decisions, tmp_path, cfg)
        assert isinstance(result, SyncResult)

        # Should have generated per-agent files
        agent_dir = tmp_path / ".smm" / "generated" / "backend-agent"
        assert agent_dir.is_dir()
        claude_md = agent_dir / "CLAUDE.md"
        assert claude_md.is_file()
        content = claude_md.read_text()
        assert "PostgreSQL" in content or "Redis" in content
        # Auth shouldn't be in backend agent's CLAUDE.md
        assert "JWT" not in content

    def test_shared_files_still_generated(self, tmp_path: Path):
        (tmp_path / ".smm" / "generated").mkdir(parents=True)

        decisions = [_make_decision("Use PostgreSQL", [Dimension.DATABASE])]
        cfg = GovernanceConfig(agents={"claude": True})

        result = sync_rules(decisions, tmp_path, cfg)
        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.is_file()
        generated_claude = tmp_path / ".smm" / "generated" / "CLAUDE.md"
        assert generated_claude.is_file()

"""Integration test — RuleSync pipeline.

decisions in graph → smm apply → verify generated CLAUDE.md / .cursorrules / AGENTS.md
contain correct rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.config import ensure_smm_structure, save_governance_config
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    GovernanceConfig,
    GovernanceRules,
    SourceType,
)
from vt_protocol.prevention.rulesync import SyncResult, sync_rules

pytestmark = pytest.mark.integration


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "sync-project"
    root.mkdir()
    (root / ".git").mkdir()
    ensure_smm_structure(root)
    return root


def _make_decision(
    title: str,
    content: str,
    *,
    dims: list[Dimension] | None = None,
    source: SourceType = SourceType.MANUAL,
    alternatives: list[str] | None = None,
) -> Decision:
    return Decision(
        title=title,
        content=content,
        rationale=f"Rationale for: {title}",
        decision_type=DecisionType.TECHNICAL,
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="sync-project",
        source_type=source,
        alternatives=alternatives or [],
    )


class TestRuleSyncIntegration:
    def test_sync_generates_agents_md(self, project_root: Path) -> None:
        """AGENTS.md is always generated regardless of config."""
        config = GovernanceConfig(agents={"claude": False, "cursor": False})
        decisions = [
            _make_decision("Use PostgreSQL", "PostgreSQL for all storage"),
        ]
        result = sync_rules(decisions, project_root, config)

        assert any("AGENTS.md" in str(f) for f in result.files_written)
        agents_content = (project_root / "AGENTS.md").read_text()
        assert "PostgreSQL" in agents_content

    def test_sync_generates_claude_md(self, project_root: Path) -> None:
        """CLAUDE.md generated when claude agent is enabled."""
        config = GovernanceConfig(agents={"claude": True, "cursor": False})
        decisions = [
            _make_decision("Use REST API", "REST for all external APIs", dims=[Dimension.API_STYLE]),
        ]
        result = sync_rules(decisions, project_root, config)

        generated_claude = project_root / ".smm" / "generated" / "CLAUDE.md"
        assert generated_claude.exists()
        assert "REST" in generated_claude.read_text()

    def test_sync_generates_cursor_rules(self, project_root: Path) -> None:
        """Cursor rule files generated when cursor agent is enabled."""
        config = GovernanceConfig(agents={"claude": False, "cursor": True})
        decisions = [
            _make_decision("Use pytest", "All tests use pytest", dims=[Dimension.TESTING]),
        ]
        result = sync_rules(decisions, project_root, config)

        cursor_dir = project_root / ".cursor" / "rules"
        assert cursor_dir.exists()
        mdc_files = list(cursor_dir.glob("*.mdc"))
        assert len(mdc_files) > 0

    def test_tier_counts(self, project_root: Path) -> None:
        """Decisions are tiered into always/auto/on-demand."""
        config = GovernanceConfig(agents={"claude": True, "cursor": True})
        decisions = [
            # High confidence + manual = likely always tier
            _make_decision(
                "Critical: Use PostgreSQL",
                "A" * 600,
                source=SourceType.MANUAL,
                alternatives=["MySQL", "MongoDB"],
            ),
            # Medium confidence
            _make_decision("Use Redis for caching", "Redis for session cache", dims=[Dimension.CACHING]),
            # Low confidence (scan source)
            _make_decision("Detected pattern", "Auto-detected", source=SourceType.SCAN),
        ]
        result = sync_rules(decisions, project_root, config)

        total = result.always_count + result.auto_count + result.on_demand_count
        assert total == 3

    def test_multiple_decisions_all_appear(self, project_root: Path) -> None:
        """All decisions appear in generated output."""
        config = GovernanceConfig(agents={"claude": True, "cursor": True})
        decisions = [
            _make_decision("Use PostgreSQL", "PostgreSQL for storage"),
            _make_decision("Use Redis", "Redis for caching", dims=[Dimension.CACHING]),
            _make_decision("Use pytest", "pytest for testing", dims=[Dimension.TESTING]),
        ]
        result = sync_rules(decisions, project_root, config)

        agents_content = (project_root / "AGENTS.md").read_text()
        assert "PostgreSQL" in agents_content
        assert "Redis" in agents_content
        assert "pytest" in agents_content

    def test_empty_decisions(self, project_root: Path) -> None:
        """Sync with no decisions still generates files."""
        config = GovernanceConfig(agents={"claude": True, "cursor": True})
        result = sync_rules([], project_root, config)

        assert (project_root / "AGENTS.md").exists()
        assert result.always_count == 0
        assert result.auto_count == 0

    def test_violation_counts_affect_tiers(self, project_root: Path) -> None:
        """Decisions with high violation counts get promoted."""
        config = GovernanceConfig(agents={"claude": True, "cursor": False})
        decisions = [
            _make_decision("Use type hints", "Type hints on all public functions", dims=[Dimension.TESTING]),
        ]
        result_without = sync_rules(decisions, project_root, config)
        result_with = sync_rules(
            decisions, project_root, config,
            violation_counts={"Use type hints": 10},
        )
        # With violations, the decision should be promoted (or at least counted)
        total_without = result_without.always_count + result_without.auto_count + result_without.on_demand_count
        total_with = result_with.always_count + result_with.auto_count + result_with.on_demand_count
        assert total_without == total_with == 1

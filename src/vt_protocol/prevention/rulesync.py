"""RuleSync — orchestrate agent file generation across all providers.

Reads the decision graph and governance config, scores decisions by
priority, and generates output files for each enabled agent provider.

From SPEC: "Auto-generate CLAUDE.md, .cursor/rules/*.mdc, AGENTS.md
from decision graph via three-tier context injection."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from vt_protocol.decisions.models import Decision, GovernanceConfig
from vt_protocol.prevention.priority import ScoredDecision, assign_tiers
from vt_protocol.prevention.providers.agents_md import generate_agents_md
from vt_protocol.prevention.providers.claude import generate_claude_md, generate_claude_rules
from vt_protocol.prevention.providers.cursor import generate_cursor_rules

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a RuleSync run."""

    files_written: list[Path] = field(default_factory=list)
    always_count: int = 0
    auto_count: int = 0
    on_demand_count: int = 0


def sync_rules(
    decisions: list[Decision],
    project_root: Path,
    config: GovernanceConfig,
    *,
    violation_counts: dict[str, int] | None = None,
) -> SyncResult:
    """Run the full rule sync pipeline.

    1. Score and tier all active decisions
    2. For each enabled agent provider, generate output files
    3. Write to .smm/generated/ and agent-specific locations

    Args:
        decisions: Active decisions from the graph.
        project_root: Project root directory.
        config: Governance configuration.
        violation_counts: Optional map of decision title → violation count.

    Returns:
        SyncResult with files written and tier counts.
    """
    result = SyncResult()

    scored = assign_tiers(decisions, violation_counts=violation_counts)
    result.always_count = sum(1 for s in scored if s.tier == "always")
    result.auto_count = sum(1 for s in scored if s.tier == "auto")
    result.on_demand_count = sum(1 for s in scored if s.tier == "on-demand")

    generated_dir = project_root / ".smm" / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)

    project_name = project_root.name

    # AGENTS.md — always generated
    agents_content = generate_agents_md(scored, project_name=project_name)
    agents_path = generated_dir / "AGENTS.md"
    agents_path.write_text(agents_content)
    result.files_written.append(agents_path)

    # Also write to project root
    root_agents = project_root / "AGENTS.md"
    root_agents.write_text(agents_content)
    result.files_written.append(root_agents)
    logger.info("Generated AGENTS.md")

    # Claude provider
    if config.agents.get("claude", False):
        claude_content = generate_claude_md(scored, project_name=project_name)
        claude_path = generated_dir / "CLAUDE.md"
        claude_path.write_text(claude_content)
        result.files_written.append(claude_path)

        # .claude/rules/ files
        claude_rules_dir = project_root / ".claude" / "rules"
        rule_paths = generate_claude_rules(scored, claude_rules_dir)
        result.files_written.extend(rule_paths)
        logger.info("Generated CLAUDE.md + %d rule files", len(rule_paths))

    # Cursor provider
    if config.agents.get("cursor", False):
        cursor_rules_dir = project_root / ".cursor" / "rules"
        rule_paths = generate_cursor_rules(scored, cursor_rules_dir)
        result.files_written.extend(rule_paths)
        logger.info("Generated %d Cursor rule files", len(rule_paths))

    return result

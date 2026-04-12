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

from vt_protocol.decisions.models import AgentConfig, Decision, Dimension, GovernanceConfig
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

    # Determine which agent types are enabled (any agent with type=claude-code or bool claude=true)
    has_claude = _is_agent_type_enabled(config, "claude")
    has_cursor = _is_agent_type_enabled(config, "cursor")

    # Claude provider — shared rules
    if has_claude:
        claude_content = generate_claude_md(scored, project_name=project_name)
        claude_path = generated_dir / "CLAUDE.md"
        claude_path.write_text(claude_content)
        result.files_written.append(claude_path)

        # .claude/rules/ files
        claude_rules_dir = project_root / ".claude" / "rules"
        rule_paths = generate_claude_rules(scored, claude_rules_dir)
        result.files_written.extend(rule_paths)
        logger.info("Generated CLAUDE.md + %d rule files", len(rule_paths))

    # Cursor provider — shared rules
    if has_cursor:
        cursor_rules_dir = project_root / ".cursor" / "rules"
        rule_paths = generate_cursor_rules(scored, cursor_rules_dir)
        result.files_written.extend(rule_paths)
        logger.info("Generated %d Cursor rule files", len(rule_paths))

    # Per-agent generation for onboarded agents with AgentConfig
    for agent_name, agent_val in config.agents.items():
        if not isinstance(agent_val, AgentConfig):
            continue

        agent_dir = generated_dir / agent_name
        agent_dir.mkdir(parents=True, exist_ok=True)

        # Filter decisions by agent's allowed dimensions
        filtered = _filter_scored_for_agent(scored, agent_val)

        agent_type = agent_val.type.lower()
        if agent_type in ("claude-code", "claude"):
            content = generate_claude_md(filtered, project_name=f"{project_name} ({agent_val.display_name or agent_name})")
            path = agent_dir / "CLAUDE.md"
            path.write_text(content)
            result.files_written.append(path)
        elif agent_type == "cursor":
            rule_dir = agent_dir / ".cursor" / "rules"
            paths = generate_cursor_rules(filtered, rule_dir)
            result.files_written.extend(paths)

    return result


def _is_agent_type_enabled(config: GovernanceConfig, agent_key: str) -> bool:
    """Check if an agent type is enabled — handles bool and AgentConfig."""
    val = config.agents.get(agent_key)
    if val is None:
        # Check if any AgentConfig has this as its type
        for v in config.agents.values():
            if isinstance(v, AgentConfig) and v.type.lower().startswith(agent_key):
                return True
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, AgentConfig):
        return True
    return bool(val)


def _filter_scored_for_agent(
    scored: list[ScoredDecision],
    agent: AgentConfig,
) -> list[ScoredDecision]:
    """Filter scored decisions to those within an agent's allowed dimensions."""
    if not agent.allowed_dimensions:
        return scored  # No filter means all dimensions

    allowed = set(agent.allowed_dimensions)
    filtered = []
    for sd in scored:
        decision_dims = {d.value for d in sd.decision.dimensions}
        if decision_dims & allowed:
            filtered.append(sd)
    return filtered

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

    # Write to project root — append governance section if AGENTS.md exists
    root_agents = project_root / "AGENTS.md"
    _write_root_agent_file(root_agents, agents_content)
    result.files_written.append(root_agents)
    logger.info("Generated AGENTS.md")

    # Determine which agent types are enabled (any agent with type=claude-code or bool claude=true)
    has_claude = _is_agent_type_enabled(config, "claude")
    has_cursor = _is_agent_type_enabled(config, "cursor")

    # Load validated/rejected assumptions for rule injection
    try:
        from vt_protocol.analysis.assumption_pipeline import load_assumptions
        from vt_protocol.prevention.providers.claude import append_assumption_rules

        all_assumptions = load_assumptions(project_root)
        resolved_assumptions = [
            a for a in all_assumptions
            if a.status.value in ("validated", "rejected")
        ]
    except Exception:
        resolved_assumptions = []

    # Claude provider — shared rules
    if has_claude:
        claude_content = generate_claude_md(scored, project_name=project_name)
        # Inject domain assumption rules
        if resolved_assumptions:
            claude_content = append_assumption_rules(claude_content, resolved_assumptions)
        claude_path = generated_dir / "CLAUDE.md"
        claude_path.write_text(claude_content)
        result.files_written.append(claude_path)

        # Write to project root — append governance section if CLAUDE.md exists
        root_claude = project_root / "CLAUDE.md"
        _write_root_claude_md(root_claude, claude_content)
        result.files_written.append(root_claude)

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


_GOVERNANCE_START = "<!-- BEGIN VT PROTOCOL GOVERNANCE -->"
_GOVERNANCE_END = "<!-- END VT PROTOCOL GOVERNANCE -->"


def _write_root_claude_md(path: Path, governance_content: str) -> None:
    """Write governance rules to root CLAUDE.md.

    If the file already exists, replace the governance section (between markers)
    or append it at the end. If it doesn't exist, write with markers.

    Content is ALWAYS written inside BEGIN/END VT PROTOCOL GOVERNANCE markers
    to support idempotent re-runs without duplication.
    """
    wrapped = f"{_GOVERNANCE_START}\n{governance_content}\n{_GOVERNANCE_END}\n"

    if path.is_file():
        existing = path.read_text()
        if _GOVERNANCE_START in existing:
            # Replace existing governance section
            start = existing.index(_GOVERNANCE_START)
            end = existing.index(_GOVERNANCE_END) + len(_GOVERNANCE_END)
            before = existing[:start].rstrip()
            after = existing[end:].lstrip("\n")
            prefix = before + "\n\n" if before else ""
            path.write_text(prefix + wrapped + after)
        else:
            # Append governance section
            path.write_text(existing.rstrip() + "\n\n" + wrapped)
    else:
        path.write_text(wrapped)


def _write_root_agent_file(path: Path, governance_content: str) -> None:
    """Write governance rules to root AGENTS.md.

    Same append/replace logic as CLAUDE.md.
    """
    wrapped = f"{_GOVERNANCE_START}\n{governance_content}\n{_GOVERNANCE_END}\n"

    if path.is_file():
        existing = path.read_text()
        if _GOVERNANCE_START in existing:
            start = existing.index(_GOVERNANCE_START)
            end = existing.index(_GOVERNANCE_END) + len(_GOVERNANCE_END)
            before = existing[:start].rstrip()
            after = existing[end:].lstrip("\n")
            prefix = before + "\n\n" if before else ""
            path.write_text(prefix + wrapped + after)
        else:
            path.write_text(existing.rstrip() + "\n\n" + wrapped)
    else:
        path.write_text(wrapped)

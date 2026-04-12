"""MCP server — 5 tools for AI agent governance.

Exposes the decision engine to AI agents via the Model Context Protocol.
Uses FastMCP for tool registration. Each tool delivers a complete answer
in one call, earning its 550-1400 token context budget.

From SPEC: "5 MCP tools max — check_before_coding, validate_change,
get_project_decisions, report_decision, get_resolution."

Tool behavior:
- check_before_coding: Returns architectural constraints relevant to a file
- validate_change: Checks a proposed change against the decision graph
- get_project_decisions: Lists active decisions, optionally filtered by dimension
- report_decision: Records a new architectural decision
- get_resolution: Fetches a previous contradiction resolution
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from uuid import UUID

from fastmcp import FastMCP

from vt_protocol.config import find_project_root, load_governance_config
from vt_protocol.decisions.models import (
    AgentConfig,
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    Session,
    SourceType,
)

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "VT Protocol",
    instructions="AI agent governance — decision graph, contradiction detection",
)

# ---------------------------------------------------------------------------
# Session state (per-server-instance)
# ---------------------------------------------------------------------------

_sessions: dict[str, Session] = {}


def _get_or_create_session(project: str, agent_id: str | None = None) -> Session:
    """Get existing session for project or create a new one."""
    for s in _sessions.values():
        if s.project == project and s.completed_at is None:
            return s
    session = Session(project=project, agent_id=agent_id)
    _sessions[session.session_id] = session
    return session


def _get_graph_client():  # type: ignore[no-untyped-def]
    """Lazy import to avoid hard DB dependency at module load."""
    from vt_protocol.decisions.graph_client import get_graph_client
    return get_graph_client()


# ---------------------------------------------------------------------------
# Tool 1: check_before_coding
# ---------------------------------------------------------------------------


@mcp.tool()
def check_before_coding(
    file_path: str,
    project: str = "",
    task_description: str = "",
    agent_id: str = "",
) -> str:
    """Get architectural constraints relevant to a file before coding.

    Returns decisions that apply to the area of code you're about to modify.
    Call this BEFORE making changes to understand existing architectural decisions.

    Args:
        file_path: Path to the file you're about to modify.
        project: Project identifier. Auto-detected from governance.yaml if empty.
        task_description: Brief description of what you plan to do.
        agent_id: Agent identifier for access control.
    """
    project = project or _detect_project()
    session = _get_or_create_session(project, agent_id=agent_id or None)
    session.context_injections += 1

    # Agent access enforcement
    agent_cfg = _get_agent_config(agent_id)
    if agent_cfg:
        path_check = _check_path_access(agent_cfg, file_path)
        if not path_check["allowed"]:
            result = {
                "session_id": session.session_id,
                "project": project,
                "file_path": file_path,
                "access_denied": True,
                "reason": path_check["reason"],
            }
            return json.dumps(result, indent=2)

    try:
        client = _get_graph_client()
        decisions = client.get_decisions(project)
    except Exception:
        logger.debug("Graph client unavailable, returning empty context")
        decisions = []

    # Filter to decisions whose dimensions are relevant to the file path
    relevant = _filter_relevant_decisions(decisions, file_path)

    # Apply agent context-level filtering
    if agent_cfg:
        relevant = _filter_decisions_by_context(relevant, agent_cfg)

    # Get unresolved contradictions
    try:
        client = _get_graph_client()
        contradictions = client.get_unresolved_contradictions(project)
    except Exception:
        contradictions = []

    result = {
        "session_id": session.session_id,
        "project": project,
        "file_path": file_path,
        "relevant_decisions": [
            {
                "id": str(d.id),
                "title": d.title,
                "content": d.content[:500] if not agent_cfg or agent_cfg.context_level != "minimal" else d.title,
                "dimensions": [dim.value for dim in d.dimensions],
                "status": d.status.value,
                "confidence": d.confidence,
            }
            for d in relevant[:5]  # Top 5 most relevant
        ],
        "unresolved_contradictions": len(contradictions),
        "governance_note": (
            f"Found {len(relevant)} relevant decisions. "
            f"{len(contradictions)} unresolved contradictions in project."
        ),
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Tool 2: validate_change
# ---------------------------------------------------------------------------


@mcp.tool()
def validate_change(
    diff: str,
    project: str = "",
    file_path: str = "",
    agent_id: str = "",
) -> str:
    """Validate a proposed code change against the decision graph.

    Checks if the change introduces any contradictions with existing
    architectural decisions. Returns pass/fail with details.

    Args:
        diff: The proposed code change (unified diff format or description).
        project: Project identifier.
        file_path: Path to the file being changed.
        agent_id: Agent identifier for access control.
    """
    project = project or _detect_project()

    # Agent access enforcement
    agent_cfg = _get_agent_config(agent_id)
    if agent_cfg and file_path:
        path_check = _check_path_access(agent_cfg, file_path)
        if not path_check["allowed"]:
            return json.dumps({
                "project": project,
                "status": "blocked",
                "access_denied": True,
                "reason": path_check["reason"],
            }, indent=2)

    try:
        client = _get_graph_client()
        decisions = client.get_decisions(project)
    except Exception:
        decisions = []

    config = _load_config()
    max_deps = config.rules.max_new_deps_per_task if config else 3

    # Check for new dependencies in the diff
    new_deps = _detect_new_deps_in_diff(diff)
    dep_warning = ""
    if len(new_deps) > max_deps:
        dep_warning = (
            f"WARNING: {len(new_deps)} new dependencies detected "
            f"(max {max_deps} per task): {', '.join(new_deps)}"
        )

    # Get relevant decisions for the file
    relevant = _filter_relevant_decisions(decisions, file_path) if file_path else decisions

    result = {
        "project": project,
        "status": "pass" if not dep_warning else "warning",
        "relevant_decisions": len(relevant),
        "dependency_check": {
            "new_deps_found": new_deps,
            "max_allowed": max_deps,
            "passed": len(new_deps) <= max_deps,
        },
        "warnings": [dep_warning] if dep_warning else [],
        "note": (
            "Change validated against decision graph. "
            f"{len(relevant)} active decisions in scope."
        ),
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Tool 3: get_project_decisions
# ---------------------------------------------------------------------------


@mcp.tool()
def get_project_decisions(
    project: str = "",
    dimension: str = "",
    active_only: bool = True,
    agent_id: str = "",
) -> str:
    """List active architectural decisions for a project.

    Optionally filter by dimension (e.g., "database", "auth", "security").
    Results are ranked by relevance and reordered for LLM attention bias.

    Args:
        project: Project identifier.
        dimension: Filter by dimension name (e.g. "database"). Empty = all.
        active_only: Only return active (non-superseded) decisions.
        agent_id: Agent identifier for context-level filtering.
    """
    project = project or _detect_project()
    session = _get_or_create_session(project, agent_id=agent_id or None)
    session.context_injections += 1

    try:
        client = _get_graph_client()
        decisions = client.get_decisions(project, active_only=active_only)
    except Exception:
        decisions = []

    # Filter by dimension if specified
    if dimension:
        try:
            dim = Dimension(dimension)
            decisions = [d for d in decisions if dim in d.dimensions]
        except ValueError:
            pass  # Invalid dimension name, return all

    # Agent context-level filtering
    agent_cfg = _get_agent_config(agent_id)
    if agent_cfg:
        decisions = _filter_decisions_by_context(decisions, agent_cfg)

    content_limit = 300
    if agent_cfg and agent_cfg.context_level == "minimal":
        content_limit = 0  # Only titles

    result = {
        "project": project,
        "dimension_filter": dimension or "all",
        "total_decisions": len(decisions),
        "decisions": [
            {
                "id": str(d.id),
                "title": d.title,
                "content": d.content[:content_limit] if content_limit else "",
                "decision_type": d.decision_type.value,
                "dimensions": [dim.value for dim in d.dimensions],
                "status": d.status.value,
                "confidence": d.confidence,
                "made_by": d.made_by,
                "created_at": d.created_at.isoformat(),
            }
            for d in decisions[:10]
        ],
    }
    return json.dumps(result, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool 4: report_decision
# ---------------------------------------------------------------------------


@mcp.tool()
def report_decision(
    title: str,
    content: str,
    rationale: str = "",
    decision_type: str = "technical",
    dimensions: list[str] | None = None,
    alternatives: list[str] | None = None,
    constraints: list[str] | None = None,
    project: str = "",
    supersedes: str = "",
    agent_id: str = "",
) -> str:
    """Record a new architectural or technical decision.

    Call this when you make a significant choice about system design,
    tech stack, libraries, protocols, data models, security, or deployment.

    Args:
        title: Short title (max 500 chars).
        content: Full description of the decision.
        rationale: Why this choice was made.
        decision_type: One of: architectural, technical, product, constraint.
        dimensions: List of dimension names (e.g. ["database", "security"]).
        alternatives: Alternatives that were considered.
        constraints: Constraints that influenced the decision.
        project: Project identifier.
        supersedes: UUID of the decision this supersedes (if any).
        agent_id: Agent identifier for access control.
    """
    project = project or _detect_project()
    session = _get_or_create_session(project, agent_id=agent_id or None)

    # Parse dimensions
    parsed_dims: list[Dimension] = []
    for d in (dimensions or []):
        try:
            parsed_dims.append(Dimension(d))
        except ValueError:
            logger.debug("Unknown dimension: %s", d)

    # Agent dimension enforcement
    agent_cfg = _get_agent_config(agent_id)
    proposed_status = DecisionStatus.ACTIVE
    restricted_note = ""
    if agent_cfg and dimensions:
        dim_check = _check_dimension_access(agent_cfg, dimensions)
        if dim_check.get("restricted"):
            proposed_status = DecisionStatus.PROPOSED
            restricted_note = (
                f"Dimensions {dim_check['restricted']} are restricted for this agent. "
                "Decision saved as PROPOSED — requires human approval."
            )

    decision = Decision(
        title=title,
        content=content,
        rationale=rationale,
        status=proposed_status,
        decision_type=DecisionType.normalize(decision_type),
        dimensions=parsed_dims,
        alternatives=alternatives or [],
        constraints=constraints or [],
        made_by=session.agent_id or agent_id or "mcp-agent",
        project=project,
        source_type=SourceType.AGENT,
        session_id=session.session_id,
        supersedes=UUID(supersedes) if supersedes else None,
    )

    try:
        client = _get_graph_client()
        if supersedes:
            decision_id = client.supersede(UUID(supersedes), decision)
        else:
            decision_id = client.add_decision(decision)
        session.decisions_made.append(decision_id)
        stored = True
    except Exception:
        logger.debug("Graph client unavailable, decision not persisted to DB")
        decision_id = decision.id
        stored = False

    status_label = "recorded" if stored else "recorded_locally"
    if proposed_status == DecisionStatus.PROPOSED:
        status_label = "proposed"

    note = (
        "Decision recorded in graph."
        if stored
        else "Decision created but not persisted (database unavailable)."
    )
    if restricted_note:
        note = restricted_note

    result = {
        "decision_id": str(decision_id),
        "title": title,
        "status": status_label,
        "dimensions": [d.value for d in parsed_dims],
        "confidence": decision.confidence,
        "session_id": session.session_id,
        "supersedes": supersedes or None,
        "note": note,
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Tool 5: get_resolution
# ---------------------------------------------------------------------------


@mcp.tool()
def get_resolution(
    contradiction_id: str,
    project: str = "",
) -> str:
    """Fetch a previous contradiction resolution.

    Use this to understand how a contradiction between two decisions was
    previously resolved, or to check the current status of an unresolved
    contradiction.

    Args:
        contradiction_id: UUID of the contradiction to look up.
        project: Project identifier.
    """
    project = project or _detect_project()

    try:
        client = _get_graph_client()
        # Get all contradictions and find the matching one
        contradictions = client.get_unresolved_contradictions(project)
        # Also check resolved ones — we need a broader search
        # For now, check unresolved. In the future, add get_contradiction_by_id.
        match = None
        target_id = UUID(contradiction_id)
        for c in contradictions:
            if c.id == target_id:
                match = c
                break

        if match is None:
            return json.dumps({
                "error": f"Contradiction {contradiction_id} not found",
                "note": "The contradiction may have been resolved or doesn't exist.",
            }, indent=2)

        result = {
            "contradiction_id": str(match.id),
            "decision_a": {
                "id": str(match.decision_a_id),
                "title": match.decision_a_title,
            },
            "decision_b": {
                "id": str(match.decision_b_id),
                "title": match.decision_b_title,
            },
            "verdict": match.verdict.value,
            "reasoning": match.reasoning,
            "evidence_a": match.evidence_a,
            "evidence_b": match.evidence_b,
            "confidence": match.confidence,
            "status": match.status.value,
            "resolved_by": match.resolved_by,
            "resolution_note": match.resolution_note,
            "is_actionable": match.is_actionable,
        }
        return json.dumps(result, indent=2)

    except ValueError:
        return json.dumps({
            "error": f"Invalid UUID: {contradiction_id}",
        }, indent=2)
    except Exception:
        return json.dumps({
            "error": "Database unavailable",
            "note": "Cannot look up contradictions without database connection.",
        }, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_agent_config(agent_id: str | None) -> AgentConfig | None:
    """Look up agent config from governance.yaml. Returns None if not found or bool agent."""
    if not agent_id:
        return None
    config = _load_config()
    if config is None:
        return None
    val = config.agents.get(agent_id)
    if isinstance(val, AgentConfig):
        return val
    return None


def _check_path_access(agent: AgentConfig, file_path: str) -> dict[str, Any]:
    """Check if an agent can access a file path.

    Returns dict with 'allowed' (bool) and 'reason' (str).
    """
    import fnmatch

    if not file_path:
        return {"allowed": True, "reason": ""}

    # Check blocked paths first
    for pattern in agent.blocked_paths:
        if fnmatch.fnmatch(file_path, pattern):
            return {"allowed": False, "reason": f"File '{file_path}' matches blocked pattern '{pattern}'"}

    # If allowed_paths is empty, everything not blocked is allowed
    if not agent.allowed_paths:
        return {"allowed": True, "reason": ""}

    # Check allowed paths
    for pattern in agent.allowed_paths:
        if fnmatch.fnmatch(file_path, pattern):
            return {"allowed": True, "reason": ""}

    return {"allowed": False, "reason": f"File '{file_path}' not in allowed paths"}


def _check_dimension_access(agent: AgentConfig, dimensions: list[str]) -> dict[str, Any]:
    """Check if agent can operate on given dimensions.

    Returns dict with 'allowed' (bool), 'restricted' (list), 'reason' (str).
    """
    if not dimensions:
        return {"allowed": True, "restricted": [], "reason": ""}

    restricted_hits = [d for d in dimensions if d in agent.restricted_dimensions]
    if restricted_hits:
        return {
            "allowed": False,
            "restricted": restricted_hits,
            "reason": f"Dimensions {restricted_hits} require human approval",
        }

    # If allowed_dimensions is empty, all non-restricted are allowed
    if not agent.allowed_dimensions:
        return {"allowed": True, "restricted": [], "reason": ""}

    denied = [d for d in dimensions if d not in agent.allowed_dimensions and d not in agent.restricted_dimensions]
    if denied:
        return {
            "allowed": False,
            "restricted": [],
            "reason": f"Dimensions {denied} not in agent's allowed list",
        }

    return {"allowed": True, "restricted": [], "reason": ""}


def _filter_decisions_by_context(
    decisions: list[Decision],
    agent: AgentConfig,
) -> list[Decision]:
    """Filter decisions based on agent's context_level and dimensions."""
    # Filter by allowed dimensions if specified
    if agent.allowed_dimensions:
        allowed = set(agent.allowed_dimensions) | set(agent.restricted_dimensions)
        decisions = [
            d for d in decisions
            if any(dim.value in allowed for dim in d.dimensions) or not d.dimensions
        ]

    # Apply context level
    if agent.context_level == "minimal":
        # Only return decisions involved in unresolved contradictions
        # For now, return top 5 most recent
        decisions = decisions[:5]
    elif agent.context_level == "relevant":
        decisions = decisions[:10]

    return decisions


def _detect_project() -> str:
    """Auto-detect project name from governance.yaml or directory name."""
    try:
        root = find_project_root(Path.cwd())
        config = load_governance_config(root)
        # Use directory name as project identifier
        return root.name
    except (FileNotFoundError, Exception):
        return Path.cwd().name


def _load_config():  # type: ignore[no-untyped-def]
    """Load governance config, returning None on failure."""
    try:
        root = find_project_root(Path.cwd())
        return load_governance_config(root)
    except Exception:
        return None


def _filter_relevant_decisions(
    decisions: list[Decision], file_path: str
) -> list[Decision]:
    """Filter decisions relevant to a file path based on dimensions.

    Uses simple heuristics to match file paths to dimensions.
    """
    path_lower = file_path.lower()

    # Map file path patterns to likely dimensions
    path_dims: set[Dimension] = set()
    if any(p in path_lower for p in ("db", "model", "migration", "sql", "schema")):
        path_dims.add(Dimension.DATABASE)
    if any(p in path_lower for p in ("auth", "login", "session", "token", "jwt")):
        path_dims.add(Dimension.AUTH)
    if any(p in path_lower for p in ("cache", "redis", "memcache")):
        path_dims.add(Dimension.CACHING)
    if any(p in path_lower for p in ("api", "route", "endpoint", "handler", "view")):
        path_dims.add(Dimension.API_STYLE)
    if any(p in path_lower for p in ("deploy", "docker", "k8s", "terraform", "infra")):
        path_dims.add(Dimension.DEPLOYMENT)
    if any(p in path_lower for p in ("async", "worker", "queue", "celery", "task")):
        path_dims.add(Dimension.CONCURRENCY)
    if any(p in path_lower for p in ("log", "monitor", "metric", "trace")):
        path_dims.add(Dimension.LOGGING)
    if any(p in path_lower for p in ("test", "spec", "fixture")):
        path_dims.add(Dimension.TESTING)
    if any(p in path_lower for p in ("error", "exception", "handler")):
        path_dims.add(Dimension.ERROR_HANDLING)
    if any(p in path_lower for p in ("state", "store", "redux", "context")):
        path_dims.add(Dimension.STATE_MANAGEMENT)
    if any(p in path_lower for p in ("message", "event", "pubsub", "kafka", "rabbitmq")):
        path_dims.add(Dimension.MESSAGING)
    if any(p in path_lower for p in ("security", "encrypt", "secret", "cert")):
        path_dims.add(Dimension.SECURITY)

    if not path_dims:
        # No specific match — return all decisions
        return decisions

    # Return decisions that share at least one dimension with the file
    return [
        d for d in decisions
        if any(dim in path_dims for dim in d.dimensions)
    ]


def _detect_new_deps_in_diff(diff: str) -> list[str]:
    """Extract potential new dependency names from a diff string.

    Simple heuristic: lines starting with + that look like dependency additions.
    """
    new_deps: list[str] = []
    for line in diff.split("\n"):
        line = line.strip()
        if not line.startswith("+"):
            continue
        line = line[1:].strip()
        # Skip diff headers
        if line.startswith("+"):
            continue
        # Python: "package>=1.0" or "package"
        if ">=" in line or "==" in line or "~=" in line:
            dep = line.split(">=")[0].split("==")[0].split("~=")[0].strip()
            if dep and not dep.startswith("#") and not dep.startswith("["):
                new_deps.append(dep)
        # Node: "\"package\": \"^1.0\""
        elif '": "' in line and ("^" in line or "~" in line):
            parts = line.split('"')
            if len(parts) >= 2:
                dep = parts[1].strip()
                if dep and not dep.startswith("@"):
                    new_deps.append(dep)
    return new_deps

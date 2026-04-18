"""Project configuration, path resolution, and governance.yaml parser.

Ported from Axiom Hub's config.py path resolution + new governance.yaml
loading from SPEC T1: "governance.yaml is our Dockerfile moment."

Supports:
- Project root detection via .smm/ or .git/ markers
- governance.yaml loading and validation against GovernanceConfig schema
- ``extends`` field for shareable config inheritance
- .smm/ directory structure initialization
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from vt_protocol.decisions.models import AgentConfig, GovernanceConfig, GovernanceRules, ModelConfig
from vt_protocol.exceptions import GovernanceConfigError

logger = logging.getLogger(__name__)

GOVERNANCE_FILENAME = "governance.yaml"

# Default governance.yaml content for `vt init`
DEFAULT_GOVERNANCE_YAML = """\
# governance.yaml — VT Protocol governance configuration
# Docs: https://github.com/varunajaytawde/vt-protocol
extends:
  - "@vt/recommended"

model:
  provider: anthropic
  model: claude-haiku-4-5-20251001
  api-key-env: ANTHROPIC_API_KEY
  temperature: 0.0
  timeout-seconds: 10
  fallback: nli-only

agents:
  claude: true
  cursor: true
  copilot: true

rules:
  freeze-on-adopt: true
  contradiction-threshold: 0.7
  max-new-deps-per-task: 3

decisions:
  path: ".smm/decisions/"
"""


# ---------------------------------------------------------------------------
# Path resolution (ported from Axiom Hub config.py)
# ---------------------------------------------------------------------------


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: cwd) looking for .smm/ or .git/.

    Returns the directory containing the marker, or raises FileNotFoundError.
    """
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (directory / ".smm").is_dir() or (directory / ".git").is_dir():
            return directory
    msg = f"No .smm/ or .git/ found above {current}"
    raise FileNotFoundError(msg)


def get_smm_dir(start: Path | None = None) -> Path:
    """Return the .smm/ directory, creating it if needed."""
    root = find_project_root(start)
    smm = root / ".smm"
    smm.mkdir(exist_ok=True)
    return smm


def get_decisions_dir(start: Path | None = None) -> Path:
    """Return .smm/decisions/ directory."""
    d = get_smm_dir(start) / "decisions"
    d.mkdir(exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# .smm/ structure initialization
# ---------------------------------------------------------------------------


def ensure_smm_structure(root: Path) -> Path:
    """Create the full .smm/ directory tree.

    From SPEC T1:
      .smm/decisions/  — individual decision YAML files (tracked in git)
      .smm/cache/      — graph cache, embeddings (gitignored)
      .smm/generated/  — CLAUDE.md, .cursorrules outputs (tracked in git)
      .smm/audit/      — Merkle tree log (gitignored, synced to cloud)
    """
    smm = root / ".smm"
    for subdir in ("decisions", "cache", "generated", "audit", "traces", "contradictions", "pending-refactors"):
        (smm / subdir).mkdir(parents=True, exist_ok=True)

    # .smm/.gitignore — ignore transient state
    gitignore = smm / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("cache/\naudit/\n")

    return smm


# ---------------------------------------------------------------------------
# governance.yaml parser
# ---------------------------------------------------------------------------


def load_governance_config(root: Path | None = None) -> GovernanceConfig:
    """Load and validate governance.yaml from the project root.

    If the file doesn't exist, returns the default GovernanceConfig.
    Raises GovernanceConfigError on malformed YAML.
    """
    if root is None:
        root = find_project_root()

    path = root / GOVERNANCE_FILENAME
    if not path.is_file():
        logger.debug("No governance.yaml found at %s — using defaults", root)
        return GovernanceConfig()

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise GovernanceConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if raw is None:
        return GovernanceConfig()

    if not isinstance(raw, dict):
        raise GovernanceConfigError(
            f"governance.yaml must be a mapping, got {type(raw).__name__}"
        )

    return _parse_governance_dict(raw)


def save_governance_config(root: Path, config: GovernanceConfig | None = None) -> Path:
    """Write governance.yaml to the project root.

    If config is None, writes the default template.
    Returns the path to the written file.
    """
    path = root / GOVERNANCE_FILENAME
    if config is None:
        path.write_text(DEFAULT_GOVERNANCE_YAML)
    else:
        data = _config_to_dict(config)
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return path


# ---------------------------------------------------------------------------
# Internal parsing
# ---------------------------------------------------------------------------


def _parse_governance_dict(raw: dict[str, Any]) -> GovernanceConfig:
    """Parse a raw YAML dict into a validated GovernanceConfig."""
    extends = raw.get("extends", ["@vt/recommended"])
    if isinstance(extends, str):
        extends = [extends]

    agents_raw = raw.get("agents", {"claude": True, "cursor": True, "copilot": True})
    if not isinstance(agents_raw, dict):
        raise GovernanceConfigError("'agents' must be a mapping")
    agents = _parse_agents(agents_raw)

    model = _parse_model(raw.get("model", {}))

    rules_raw = raw.get("rules", {})
    rules = _parse_rules(rules_raw)

    decisions_path = ".smm/decisions/"
    decisions_section = raw.get("decisions", {})
    if isinstance(decisions_section, dict):
        decisions_path = decisions_section.get("path", decisions_path)
    elif isinstance(decisions_section, str):
        decisions_path = decisions_section

    return GovernanceConfig(
        extends=extends,
        agents=agents,
        model=model,
        rules=rules,
        decisions_path=decisions_path,
    )


def _parse_model(raw: dict[str, Any] | Any) -> ModelConfig:
    """Parse the model: section of governance.yaml."""
    if not isinstance(raw, dict):
        return ModelConfig()
    return ModelConfig(
        provider=raw.get("provider", "anthropic"),
        model=raw.get("model", "claude-haiku-4-5-20251001"),
        api_key_env=raw.get("api-key-env", raw.get("api_key_env")),
        base_url=raw.get("base-url", raw.get("base_url")),
        temperature=float(raw.get("temperature", 0.0)),
        timeout_seconds=int(raw.get("timeout-seconds", raw.get("timeout_seconds", 10))),
        fallback=raw.get("fallback", "nli-only"),
    )


def _parse_agents(raw: dict[str, Any]) -> dict[str, bool | AgentConfig]:
    """Parse agents section — supports both bool shorthand and full AgentConfig."""
    result: dict[str, bool | AgentConfig] = {}
    for name, value in raw.items():
        if isinstance(value, bool):
            result[name] = value
        elif isinstance(value, dict):
            result[name] = AgentConfig(
                type=value.get("type", "claude-code"),
                role=value.get("role", "full-stack"),
                display_name=value.get("display-name", value.get("display_name", "")),
                allowed_paths=value.get("allowed-paths", value.get("allowed_paths", [])),
                blocked_paths=value.get("blocked-paths", value.get("blocked_paths", [])),
                allowed_dimensions=value.get("allowed-dimensions", value.get("allowed_dimensions", [])),
                restricted_dimensions=value.get("restricted-dimensions", value.get("restricted_dimensions", [])),
                context_level=value.get("context-level", value.get("context_level", "full")),
                auto_resolve=value.get("auto-resolve", value.get("auto_resolve", False)),
                session_ttl_minutes=int(value.get("session-ttl-minutes", value.get("session_ttl_minutes", 60))),
                block_on_contradiction=value.get("block-on-contradiction", value.get("block_on_contradiction", True)),
                owner=value.get("owner", ""),
                created_at=value.get("created-at", value.get("created_at", "")),
                last_active=value.get("last-active", value.get("last_active")),
            )
        else:
            # Treat unknown values as enabled
            result[name] = bool(value)
    return result


def _parse_rules(raw: dict[str, Any] | Any) -> GovernanceRules:
    """Parse the rules section, handling kebab-case YAML keys."""
    if not isinstance(raw, dict):
        return GovernanceRules()

    # Map kebab-case YAML keys to snake_case Python fields
    return GovernanceRules(
        freeze_on_adopt=raw.get("freeze-on-adopt", raw.get("freeze_on_adopt", True)),
        contradiction_threshold=raw.get(
            "contradiction-threshold",
            raw.get("contradiction_threshold", 0.7),
        ),
        max_new_deps_per_task=raw.get(
            "max-new-deps-per-task",
            raw.get("max_new_deps_per_task", 3),
        ),
    )


def _config_to_dict(config: GovernanceConfig) -> dict[str, Any]:
    """Convert a GovernanceConfig to a YAML-friendly dict with kebab-case keys."""
    # Serialize agents — keep bool shorthand where possible
    agents_dict: dict[str, Any] = {}
    for name, value in config.agents.items():
        if isinstance(value, bool):
            agents_dict[name] = value
        elif isinstance(value, AgentConfig):
            agent_d: dict[str, Any] = {
                "type": value.type,
                "role": value.role,
            }
            if value.display_name:
                agent_d["display-name"] = value.display_name
            if value.allowed_paths:
                agent_d["allowed-paths"] = value.allowed_paths
            if value.blocked_paths:
                agent_d["blocked-paths"] = value.blocked_paths
            if value.allowed_dimensions:
                agent_d["allowed-dimensions"] = value.allowed_dimensions
            if value.restricted_dimensions:
                agent_d["restricted-dimensions"] = value.restricted_dimensions
            agent_d["context-level"] = value.context_level
            agent_d["auto-resolve"] = value.auto_resolve
            agent_d["session-ttl-minutes"] = value.session_ttl_minutes
            agent_d["block-on-contradiction"] = value.block_on_contradiction
            if value.owner:
                agent_d["owner"] = value.owner
            if value.created_at:
                agent_d["created-at"] = value.created_at
            agents_dict[name] = agent_d
        else:
            agents_dict[name] = value

    result: dict[str, Any] = {
        "extends": config.extends,
        "model": {
            "provider": config.model.provider,
            "model": config.model.model,
        },
        "agents": agents_dict,
        "rules": {
            "freeze-on-adopt": config.rules.freeze_on_adopt,
            "contradiction-threshold": config.rules.contradiction_threshold,
            "max-new-deps-per-task": config.rules.max_new_deps_per_task,
        },
        "decisions": {
            "path": config.decisions_path,
        },
    }

    # Only include non-default model fields
    m = config.model
    if m.api_key_env:
        result["model"]["api-key-env"] = m.api_key_env
    if m.base_url:
        result["model"]["base-url"] = m.base_url
    if m.temperature != 0.0:
        result["model"]["temperature"] = m.temperature
    if m.timeout_seconds != 10:
        result["model"]["timeout-seconds"] = m.timeout_seconds
    if m.fallback != "nli-only":
        result["model"]["fallback"] = m.fallback

    return result

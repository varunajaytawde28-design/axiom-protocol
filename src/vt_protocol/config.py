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

from vt_protocol.decisions.models import GovernanceConfig, GovernanceRules
from vt_protocol.exceptions import GovernanceConfigError

logger = logging.getLogger(__name__)

GOVERNANCE_FILENAME = "governance.yaml"

# Default governance.yaml content for `vt init`
DEFAULT_GOVERNANCE_YAML = """\
# governance.yaml — VT Protocol governance configuration
# Docs: https://github.com/varunajaytawde/vt-protocol
extends:
  - "@vt/recommended"

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
    for subdir in ("decisions", "cache", "generated", "audit"):
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

    agents = raw.get("agents", {"claude": True, "cursor": True, "copilot": True})
    if not isinstance(agents, dict):
        raise GovernanceConfigError("'agents' must be a mapping of name → bool")

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
        rules=rules,
        decisions_path=decisions_path,
    )


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
    return {
        "extends": config.extends,
        "agents": config.agents,
        "rules": {
            "freeze-on-adopt": config.rules.freeze_on_adopt,
            "contradiction-threshold": config.rules.contradiction_threshold,
            "max-new-deps-per-task": config.rules.max_new_deps_per_task,
        },
        "decisions": {
            "path": config.decisions_path,
        },
    }

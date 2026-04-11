"""CODEOWNERS parsing and file-to-owner mapping.

Parses GitHub CODEOWNERS format (docs/, .github/CODEOWNERS, or CODEOWNERS
at project root). Maps file paths to owners so contradictions can be
auto-assigned to the right person.

Supports:
  - Glob patterns (*.py, src/auth/**, docs/*)
  - Multiple owners per pattern (@user, @org/team, email)
  - Last-match-wins semantics (same as GitHub)
  - Comment lines (#) and blank lines
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CodeownersRule:
    """A single CODEOWNERS rule: pattern → owners."""

    pattern: str
    owners: list[str]
    line_number: int = 0


@dataclass
class CodeownersFile:
    """Parsed CODEOWNERS file with lookup capability."""

    rules: list[CodeownersRule] = field(default_factory=list)
    source_path: str = ""

    def owners_for(self, file_path: str) -> list[str]:
        """Find owners for a file path (last match wins, per GitHub semantics)."""
        result: list[str] = []
        normalized = file_path.lstrip("/")

        for rule in self.rules:
            if _matches(rule.pattern, normalized):
                result = rule.owners

        return result

    def owners_for_files(self, file_paths: list[str]) -> dict[str, list[str]]:
        """Map multiple file paths to their owners."""
        return {fp: self.owners_for(fp) for fp in file_paths}

    def all_owners(self) -> set[str]:
        """Return all unique owners across all rules."""
        owners: set[str] = set()
        for rule in self.rules:
            owners.update(rule.owners)
        return owners

    def rules_for_owner(self, owner: str) -> list[CodeownersRule]:
        """Return all rules that include a specific owner."""
        return [r for r in self.rules if owner in r.owners]


def parse_codeowners(content: str) -> CodeownersFile:
    """Parse CODEOWNERS file content into structured rules.

    Format per line: <pattern> <owner1> <owner2> ...
    Lines starting with # are comments. Blank lines are ignored.
    """
    rules: list[CodeownersRule] = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        pattern = parts[0]
        owners = parts[1:]

        rules.append(CodeownersRule(
            pattern=pattern,
            owners=owners,
            line_number=line_number,
        ))

    return CodeownersFile(rules=rules)


def load_codeowners(project_root: Path) -> CodeownersFile | None:
    """Load CODEOWNERS from standard locations.

    Checks (in order): CODEOWNERS, .github/CODEOWNERS, docs/CODEOWNERS.
    Returns None if no CODEOWNERS file is found.
    """
    candidates = [
        project_root / "CODEOWNERS",
        project_root / ".github" / "CODEOWNERS",
        project_root / "docs" / "CODEOWNERS",
    ]

    for path in candidates:
        if path.is_file():
            content = path.read_text()
            result = parse_codeowners(content)
            result.source_path = str(path)
            logger.debug("Loaded CODEOWNERS from %s (%d rules)", path, len(result.rules))
            return result

    return None


def assign_contradiction_owners(
    codeowners: CodeownersFile,
    contradiction_dimensions: list[str],
    affected_files: list[str] | None = None,
) -> list[str]:
    """Determine who should own a contradiction based on affected files.

    If affected_files is provided, use CODEOWNERS to find owners.
    Returns deduplicated owner list.
    """
    if not affected_files:
        return []

    all_owners: set[str] = set()
    for fp in affected_files:
        owners = codeowners.owners_for(fp)
        all_owners.update(owners)

    return sorted(all_owners)


def files_for_dimensions(
    dimensions: list[str],
    project_root: Path,
) -> list[str]:
    """Heuristic: map dimensions to likely file paths for CODEOWNERS lookup.

    Uses common patterns to guess which files are relevant to a dimension.
    """
    dimension_patterns: dict[str, list[str]] = {
        "database": ["**/db/**", "**/models/**", "**/migrations/**", "**/schema.*"],
        "auth": ["**/auth/**", "**/login/**", "**/session/**", "**/jwt.*"],
        "caching": ["**/cache/**", "**/redis.*"],
        "api-style": ["**/api/**", "**/routes/**", "**/endpoints/**", "**/handlers/**"],
        "deployment": ["**/docker*", "**/k8s/**", "**/terraform/**", "Dockerfile*"],
        "concurrency": ["**/workers/**", "**/tasks/**", "**/queue/**", "**/async_*"],
        "logging": ["**/logging.*", "**/log_*", "**/monitor/**"],
        "testing": ["**/tests/**", "**/test_*", "**/*_test.*"],
        "error-handling": ["**/errors/**", "**/exceptions.*"],
        "state-management": ["**/store/**", "**/state/**"],
        "messaging": ["**/events/**", "**/pubsub/**", "**/messaging/**"],
        "security": ["**/security/**", "**/crypto/**", "**/secrets.*"],
    }

    files: list[str] = []
    for dim in dimensions:
        patterns = dimension_patterns.get(dim, [])
        for pattern in patterns:
            for match in project_root.glob(pattern):
                if match.is_file():
                    try:
                        files.append(str(match.relative_to(project_root)))
                    except ValueError:
                        files.append(str(match))

    return files


def _matches(pattern: str, file_path: str) -> bool:
    """Match a CODEOWNERS pattern against a file path.

    Supports:
      - /pattern anchors to root (only matches at top level)
      - *.ext matches files in any directory
      - dir/ matches everything under dir
      - dir/* matches direct children of dir
      - dir/** matches all descendants of dir
    """
    rooted = pattern.startswith("/")
    pat = pattern.lstrip("/")

    # Pattern ending in / matches everything under that directory
    if pat.endswith("/"):
        return file_path.startswith(pat) or file_path.startswith(pat.rstrip("/") + "/")

    # ** in pattern: match any number of directories
    if "**" in pat:
        parts = pat.split("**")
        if len(parts) == 2:
            prefix = parts[0].rstrip("/")
            suffix = parts[1].lstrip("/")
            if prefix and suffix:
                return file_path.startswith(prefix + "/") and fnmatch.fnmatch(
                    file_path.split("/")[-1], suffix
                )
            elif prefix:
                return file_path.startswith(prefix + "/") or file_path == prefix
            elif suffix:
                return fnmatch.fnmatch(file_path.split("/")[-1], suffix)
            else:
                return True

    # If pattern has no directory separator, match against basename
    # Unless rooted, in which case match only at root
    if "/" not in pat:
        if rooted:
            return fnmatch.fnmatch(file_path, pat)
        return fnmatch.fnmatch(file_path.split("/")[-1], pat)

    # Otherwise, match the full path
    return fnmatch.fnmatch(file_path, pat)

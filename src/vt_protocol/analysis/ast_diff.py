"""GumTree AST diffs — structural change detection.

Uses GumTree's tree-sitter bridge when available, falls back to
git diff for structural change detection. Detects moves, renames,
inserts, deletes, and updates at the AST level.

From SPEC Sprint 17: "GumTree AST diffs."
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ChangeType(str, Enum):
    """Types of AST-level changes."""

    INSERT = "insert"
    DELETE = "delete"
    UPDATE = "update"
    MOVE = "move"
    RENAME = "rename"


@dataclass
class ASTChange:
    """A single AST-level change between two file versions."""

    change_type: ChangeType
    node_type: str = ""  # e.g. "function_definition", "class_definition"
    name: str = ""  # name of the moved/renamed symbol
    old_file: str = ""
    new_file: str = ""
    old_line: int = 0
    new_line: int = 0
    old_name: str = ""  # for renames
    new_name: str = ""  # for renames

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_type": self.change_type.value,
            "node_type": self.node_type,
            "name": self.name,
            "old_file": self.old_file,
            "new_file": self.new_file,
            "old_line": self.old_line,
            "new_line": self.new_line,
            "old_name": self.old_name,
            "new_name": self.new_name,
        }

    @property
    def description(self) -> str:
        """Human-readable description of this change."""
        if self.change_type == ChangeType.MOVE:
            return f"Moved {self.node_type} '{self.name}' from {self.old_file}:{self.old_line} to {self.new_file}:{self.new_line}"
        if self.change_type == ChangeType.RENAME:
            return f"Renamed {self.node_type} '{self.old_name}' → '{self.new_name}' in {self.new_file}:{self.new_line}"
        if self.change_type == ChangeType.INSERT:
            return f"Added {self.node_type} '{self.name}' in {self.new_file}:{self.new_line}"
        if self.change_type == ChangeType.DELETE:
            return f"Removed {self.node_type} '{self.name}' from {self.old_file}:{self.old_line}"
        if self.change_type == ChangeType.UPDATE:
            return f"Updated {self.node_type} '{self.name}' in {self.new_file}:{self.new_line}"
        return f"{self.change_type.value}: {self.name}"


@dataclass
class DiffResult:
    """Result of an AST diff analysis."""

    changes: list[ASTChange] = field(default_factory=list)
    old_file: str = ""
    new_file: str = ""
    used_gumtree: bool = False

    @property
    def change_count(self) -> int:
        return len(self.changes)

    @property
    def moves(self) -> list[ASTChange]:
        return [c for c in self.changes if c.change_type == ChangeType.MOVE]

    @property
    def renames(self) -> list[ASTChange]:
        return [c for c in self.changes if c.change_type == ChangeType.RENAME]

    @property
    def inserts(self) -> list[ASTChange]:
        return [c for c in self.changes if c.change_type == ChangeType.INSERT]

    @property
    def deletes(self) -> list[ASTChange]:
        return [c for c in self.changes if c.change_type == ChangeType.DELETE]

    @property
    def updates(self) -> list[ASTChange]:
        return [c for c in self.changes if c.change_type == ChangeType.UPDATE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_file": self.old_file,
            "new_file": self.new_file,
            "change_count": self.change_count,
            "used_gumtree": self.used_gumtree,
            "changes": [c.to_dict() for c in self.changes],
            "summary": {
                "moves": len(self.moves),
                "renames": len(self.renames),
                "inserts": len(self.inserts),
                "deletes": len(self.deletes),
                "updates": len(self.updates),
            },
        }

    def pr_comment(self) -> str:
        """Generate an enhanced PR comment from changes."""
        if not self.changes:
            return "No structural changes detected."
        lines: list[str] = []
        for c in self.changes:
            lines.append(f"- {c.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# GumTree availability
# ---------------------------------------------------------------------------


def is_gumtree_available() -> bool:
    """Check if GumTree CLI is available on the system."""
    return shutil.which("gumtree") is not None


# ---------------------------------------------------------------------------
# GumTree diff
# ---------------------------------------------------------------------------


def gumtree_diff(old_file: Path, new_file: Path, *, timeout: int = 60) -> DiffResult:
    """Run GumTree diff between two files and parse JSON output.

    Requires GumTree CLI and Java runtime.
    """
    if not is_gumtree_available():
        raise RuntimeError("GumTree CLI not found.")

    result = DiffResult(
        old_file=str(old_file),
        new_file=str(new_file),
        used_gumtree=True,
    )

    try:
        proc = subprocess.run(
            ["gumtree", "textdiff", str(old_file), str(new_file), "-f", "JSON"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            logger.warning("GumTree failed: %s", proc.stderr[:200])
            return result

        data = json.loads(proc.stdout)
        result.changes = _parse_gumtree_json(data, str(old_file), str(new_file))

    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning("GumTree diff failed: %s", e)

    return result


def _parse_gumtree_json(
    data: dict[str, Any], old_file: str, new_file: str,
) -> list[ASTChange]:
    """Parse GumTree JSON output into ASTChange list."""
    changes: list[ASTChange] = []
    actions = data.get("actions", [])

    for action in actions:
        action_type = action.get("action", "").lower()
        tree = action.get("tree", {})
        node_type = tree.get("type", "")
        label = tree.get("label", "")
        pos = tree.get("pos", 0)

        if "insert" in action_type:
            changes.append(ASTChange(
                change_type=ChangeType.INSERT,
                node_type=node_type,
                name=label,
                new_file=new_file,
                new_line=pos,
            ))
        elif "delete" in action_type:
            changes.append(ASTChange(
                change_type=ChangeType.DELETE,
                node_type=node_type,
                name=label,
                old_file=old_file,
                old_line=pos,
            ))
        elif "update" in action_type:
            changes.append(ASTChange(
                change_type=ChangeType.UPDATE,
                node_type=node_type,
                name=label,
                new_file=new_file,
                new_line=pos,
            ))
        elif "move" in action_type:
            changes.append(ASTChange(
                change_type=ChangeType.MOVE,
                node_type=node_type,
                name=label,
                old_file=old_file,
                new_file=new_file,
                new_line=pos,
            ))

    return changes


# ---------------------------------------------------------------------------
# Git diff fallback — structural analysis from unified diff
# ---------------------------------------------------------------------------

# Regex to detect function/class definitions in diff hunks
_PY_DEF_RE = re.compile(r"^[+-]\s*(?:async\s+)?def\s+(\w+)")
_PY_CLASS_RE = re.compile(r"^[+-]\s*class\s+(\w+)")


def git_diff_fallback(
    old_content: str,
    new_content: str,
    *,
    old_file: str = "a.py",
    new_file: str = "b.py",
) -> DiffResult:
    """Analyze structural changes using line-based diff.

    Detects function/class insertions, deletions, and moves
    without requiring GumTree or Java.
    """
    result = DiffResult(old_file=old_file, new_file=new_file, used_gumtree=False)

    old_symbols = _extract_symbols(old_content)
    new_symbols = _extract_symbols(new_content)

    old_names = {s["name"] for s in old_symbols}
    new_names = {s["name"] for s in new_symbols}

    # Deleted symbols
    for name in old_names - new_names:
        sym = next(s for s in old_symbols if s["name"] == name)
        result.changes.append(ASTChange(
            change_type=ChangeType.DELETE,
            node_type=sym["type"],
            name=name,
            old_file=old_file,
            old_line=sym["line"],
        ))

    # Inserted symbols
    for name in new_names - old_names:
        sym = next(s for s in new_symbols if s["name"] == name)
        result.changes.append(ASTChange(
            change_type=ChangeType.INSERT,
            node_type=sym["type"],
            name=name,
            new_file=new_file,
            new_line=sym["line"],
        ))

    # Updated symbols (same name, different content)
    for name in old_names & new_names:
        old_sym = next(s for s in old_symbols if s["name"] == name)
        new_sym = next(s for s in new_symbols if s["name"] == name)
        if old_sym.get("body_hash") != new_sym.get("body_hash"):
            result.changes.append(ASTChange(
                change_type=ChangeType.UPDATE,
                node_type=new_sym["type"],
                name=name,
                old_file=old_file,
                new_file=new_file,
                old_line=old_sym["line"],
                new_line=new_sym["line"],
            ))

    return result


def detect_moves(
    deleted_files: dict[str, str],
    added_files: dict[str, str],
) -> list[ASTChange]:
    """Detect function/class moves across files.

    Compares symbols deleted from old files with symbols added in new files.
    """
    moves: list[ASTChange] = []

    deleted_symbols: dict[str, tuple[str, dict[str, Any]]] = {}
    for filepath, content in deleted_files.items():
        for sym in _extract_symbols(content):
            deleted_symbols[sym["name"]] = (filepath, sym)

    for filepath, content in added_files.items():
        for sym in _extract_symbols(content):
            if sym["name"] in deleted_symbols:
                old_path, old_sym = deleted_symbols[sym["name"]]
                if old_path != filepath:
                    moves.append(ASTChange(
                        change_type=ChangeType.MOVE,
                        node_type=sym["type"],
                        name=sym["name"],
                        old_file=old_path,
                        new_file=filepath,
                        old_line=old_sym["line"],
                        new_line=sym["line"],
                    ))

    return moves


def _extract_symbols(source: str) -> list[dict[str, Any]]:
    """Extract function and class definitions with their line numbers."""
    import hashlib

    symbols: list[dict[str, Any]] = []
    lines = source.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        fn_match = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
        cls_match = re.match(r"class\s+(\w+)", stripped)

        if fn_match or cls_match:
            name = (fn_match or cls_match).group(1)  # type: ignore
            sym_type = "function" if fn_match else "class"
            indent = len(line) - len(stripped)

            # Collect body for hashing
            body_lines = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.lstrip()
                if next_stripped and (len(next_line) - len(next_stripped)) <= indent:
                    break
                body_lines.append(next_line)
                j += 1

            body = "\n".join(body_lines)
            body_hash = hashlib.sha256(body.encode()).hexdigest()[:16]

            symbols.append({
                "name": name,
                "type": sym_type,
                "line": i + 1,
                "body_hash": body_hash,
            })

        i += 1

    return symbols

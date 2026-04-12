"""File-hash cache for change detection.

Tracks file state via SHA-256 content hashing. Used by the observation engine
to detect what changed between agent task start and end. Auto-categorizes
changes by path pattern (source/config/test/dependency/CI/docs).

From SPEC: "File change tracking — git snapshot diffing before/after agent task,
auto-categorize by path pattern."
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ChangeCategory(str, Enum):
    """Auto-categorization of file changes by path pattern."""

    SOURCE = "source"
    CONFIG = "config"
    TEST = "test"
    DEPENDENCY = "dependency"
    CI = "ci"
    DOCS = "docs"
    GENERATED = "generated"
    OTHER = "other"


# Path patterns for auto-categorization
_CATEGORY_RULES: list[tuple[ChangeCategory, list[str]]] = [
    (ChangeCategory.TEST, [
        "test", "tests", "__tests__", "spec", "specs", "e2e",
        "test_", "_test.py", "_test.ts", "_test.js", ".test.", ".spec.",
    ]),
    (ChangeCategory.CI, [
        ".github/workflows", ".gitlab-ci", "Jenkinsfile", ".circleci",
        ".github/actions",
    ]),
    (ChangeCategory.DEPENDENCY, [
        "requirements.txt", "requirements-", "pyproject.toml", "setup.py",
        "setup.cfg", "Pipfile", "poetry.lock",
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
    ]),
    (ChangeCategory.CONFIG, [
        ".env", "dockerfile", "docker-compose", ".dockerignore",
        ".eslintrc", ".prettierrc", "tsconfig", "ruff.toml",
        ".gitignore", ".editorconfig", "makefile",
        "alembic.ini", "pytest.ini", "tox.ini",
        "governance.yaml", ".smm/",
    ]),
    (ChangeCategory.GENERATED, [
        "agents.md", "claude.md", ".cursor/rules",
        ".smm/generated/", "dist/", "build/",
    ]),
    (ChangeCategory.DOCS, [
        "readme", "changelog", "contributing", "license",
        "docs/", "doc/", ".md",
    ]),
]


@dataclass
class FileEntry:
    """A single file's state in a snapshot."""

    path: str
    content_hash: str
    size: int
    category: ChangeCategory


@dataclass
class SnapshotDiff:
    """Diff between two file snapshots."""

    added: list[FileEntry] = field(default_factory=list)
    removed: list[FileEntry] = field(default_factory=list)
    modified: list[tuple[FileEntry, FileEntry]] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    def changes_by_category(self) -> dict[ChangeCategory, int]:
        """Count changes grouped by category."""
        counts: dict[ChangeCategory, int] = {}
        for entry in self.added:
            counts[entry.category] = counts.get(entry.category, 0) + 1
        for entry in self.removed:
            counts[entry.category] = counts.get(entry.category, 0) + 1
        for _, after in self.modified:
            counts[after.category] = counts.get(after.category, 0) + 1
        return counts

    @property
    def has_dependency_changes(self) -> bool:
        """True if any dependency files (package.json, requirements.txt) changed."""
        return ChangeCategory.DEPENDENCY in self.changes_by_category()


def categorize_path(path: str) -> ChangeCategory:
    """Auto-categorize a file path by matching against known patterns."""
    path_lower = path.lower()
    parts = path_lower.replace("\\", "/").split("/")
    basename = parts[-1] if parts else path_lower

    for category, patterns in _CATEGORY_RULES:
        for pattern in patterns:
            if pattern in path_lower or pattern in basename:
                return category
            if any(pattern == part for part in parts):
                return category

    # Fallback: source code detection by extension
    source_exts = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
        ".java", ".kt", ".rb", ".php", ".cs", ".swift", ".c", ".cpp",
    }
    suffix = Path(path).suffix.lower()
    if suffix in source_exts:
        return ChangeCategory.SOURCE

    return ChangeCategory.OTHER


def hash_file(path: Path) -> str:
    """Compute SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def take_snapshot(
    root: Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> dict[str, FileEntry]:
    """Hash all tracked files under root, returning path → FileEntry map.

    Args:
        root: Project root directory.
        include_patterns: If set, only include files matching these suffixes.
        exclude_patterns: Exclude files matching these patterns.
    """
    root = root.resolve()
    exclude = set(exclude_patterns or [
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
        ".smm/cache",
    ])

    snapshot: dict[str, FileEntry] = {}

    for path in _walk_files(root, exclude):
        rel = str(path.relative_to(root))

        if include_patterns:
            if not any(rel.endswith(p) for p in include_patterns):
                continue

        content_hash = hash_file(path)
        if not content_hash:
            continue

        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        snapshot[rel] = FileEntry(
            path=rel,
            content_hash=content_hash,
            size=size,
            category=categorize_path(rel),
        )

    return snapshot


def diff_snapshots(
    before: dict[str, FileEntry],
    after: dict[str, FileEntry],
) -> SnapshotDiff:
    """Compute the diff between two file snapshots."""
    diff = SnapshotDiff()

    before_keys = set(before.keys())
    after_keys = set(after.keys())

    for path in sorted(after_keys - before_keys):
        diff.added.append(after[path])

    for path in sorted(before_keys - after_keys):
        diff.removed.append(before[path])

    for path in sorted(before_keys & after_keys):
        if before[path].content_hash != after[path].content_hash:
            diff.modified.append((before[path], after[path]))

    return diff


def save_snapshot(snapshot: dict[str, FileEntry], path: Path) -> None:
    """Persist a snapshot to a JSON file."""
    data = {k: asdict(v) for k, v in snapshot.items()}
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def load_snapshot(path: Path) -> dict[str, FileEntry]:
    """Load a snapshot from a JSON file."""
    data = json.loads(path.read_text())
    return {
        k: FileEntry(
            path=v["path"],
            content_hash=v["content_hash"],
            size=v["size"],
            category=ChangeCategory(v["category"]),
        )
        for k, v in data.items()
    }


# ---------------------------------------------------------------------------
# File read tracking
# ---------------------------------------------------------------------------


@dataclass
class FileReadRecord:
    """A single file read event."""

    file_path: str
    timestamp: float  # Unix timestamp
    agent_id: str = ""
    session_id: str = ""


class FileReadTracker:
    """Tracks which files an agent reads during a session."""

    def __init__(self) -> None:
        self._reads: list[FileReadRecord] = []

    @property
    def reads(self) -> list[FileReadRecord]:
        return list(self._reads)

    @property
    def read_count(self) -> int:
        return len(self._reads)

    @property
    def unique_files(self) -> set[str]:
        return {r.file_path for r in self._reads}

    def record_read(
        self,
        file_path: str,
        *,
        agent_id: str = "",
        session_id: str = "",
    ) -> FileReadRecord:
        """Record a file read event."""
        import time

        record = FileReadRecord(
            file_path=file_path,
            timestamp=time.time(),
            agent_id=agent_id,
            session_id=session_id,
        )
        self._reads.append(record)
        return record

    def summary_by_agent(self) -> dict[str, dict[str, int]]:
        """Summarize reads per agent: {agent_id: {reads: N, unique_files: M}}."""
        from collections import Counter

        agent_reads: dict[str, list[str]] = {}
        for r in self._reads:
            key = r.agent_id or "unknown"
            agent_reads.setdefault(key, []).append(r.file_path)

        return {
            agent: {
                "reads": len(files),
                "unique_files": len(set(files)),
            }
            for agent, files in agent_reads.items()
        }

    def to_activity_entries(self) -> list[dict[str, Any]]:
        """Convert all reads to unified ActivityEntry dicts."""
        import uuid as _uuid

        entries = []
        for r in self._reads:
            entries.append({
                "entry_id": _uuid.uuid4().hex[:16],
                "timestamp": r.timestamp,
                "agent_id": r.agent_id,
                "session_id": r.session_id,
                "action_type": "file_read",
                "tool_name": "read_file",
                "summary": f"Read: {r.file_path}",
                "severity": "info",
                "details": {"file_path": r.file_path},
                "duration_ms": 0.0,
            })
        return entries

    def save(self, path: Path) -> None:
        """Persist read records to a JSON file."""
        data = [asdict(r) for r in self._reads]
        path.write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> None:
        """Load read records from a JSON file."""
        if not path.exists():
            return
        data = json.loads(path.read_text())
        self._reads = [FileReadRecord(**r) for r in data]

    def reset(self) -> None:
        """Clear all recorded reads."""
        self._reads.clear()


def _walk_files(root: Path, exclude: set[str]) -> list[Path]:
    """Recursively list files, skipping excluded directories."""
    files: list[Path] = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.name in exclude:
                continue
            if entry.is_file() and not entry.name.startswith("."):
                files.append(entry)
            elif entry.is_dir() and not entry.name.startswith("."):
                files.extend(_walk_files(entry, exclude))
    except OSError:
        pass
    return files

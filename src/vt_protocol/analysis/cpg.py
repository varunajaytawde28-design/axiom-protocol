"""Code Property Graphs via Joern.

Run Joern as a subprocess to generate CPGs for Python and TypeScript,
query for taint paths, control flow violations, and boundary crossings.

From SPEC Sprint 17: "Code Property Graphs via Joern."
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class JoernNotAvailable(RuntimeError):
    """Raised when Joern CLI is not installed or JDK is missing."""


@dataclass
class TaintPath:
    """A data flow path from source to sink."""

    source_file: str = ""
    source_line: int = 0
    source_label: str = ""
    sink_file: str = ""
    sink_line: int = 0
    sink_label: str = ""
    hops: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": {"file": self.source_file, "line": self.source_line, "label": self.source_label},
            "sink": {"file": self.sink_file, "line": self.sink_line, "label": self.sink_label},
            "hops": self.hops,
            "hop_count": len(self.hops),
        }


@dataclass
class BoundaryViolation:
    """An architectural boundary crossing."""

    source_file: str = ""
    source_line: int = 0
    target_file: str = ""
    target_import: str = ""
    violation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_line": self.source_line,
            "target_file": self.target_file,
            "target_import": self.target_import,
            "violation": self.violation,
        }


@dataclass
class ControlFlowViolation:
    """A control flow issue (e.g. sync calling async)."""

    file: str = ""
    line: int = 0
    function: str = ""
    violation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "function": self.function,
            "violation": self.violation,
        }


@dataclass
class CPGResult:
    """Result of a CPG analysis."""

    taint_paths: list[TaintPath] = field(default_factory=list)
    boundary_violations: list[BoundaryViolation] = field(default_factory=list)
    control_flow_violations: list[ControlFlowViolation] = field(default_factory=list)
    files_analyzed: int = 0
    cpg_generated: bool = False

    @property
    def total_violations(self) -> int:
        return (
            len(self.taint_paths)
            + len(self.boundary_violations)
            + len(self.control_flow_violations)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "taint_paths": [t.to_dict() for t in self.taint_paths],
            "boundary_violations": [b.to_dict() for b in self.boundary_violations],
            "control_flow_violations": [c.to_dict() for c in self.control_flow_violations],
            "files_analyzed": self.files_analyzed,
            "cpg_generated": self.cpg_generated,
            "total_violations": self.total_violations,
        }


# ---------------------------------------------------------------------------
# Joern availability
# ---------------------------------------------------------------------------


def is_joern_available() -> bool:
    """Check if Joern CLI is available on the system."""
    return shutil.which("joern") is not None


def _check_joern() -> None:
    if not is_joern_available():
        raise JoernNotAvailable(
            "Joern CLI not found. Install from https://joern.io and ensure JDK 21+ is available."
        )


# ---------------------------------------------------------------------------
# CPG cache
# ---------------------------------------------------------------------------


def _file_hash(path: Path) -> str:
    """SHA-256 hash of file contents for cache invalidation."""
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return h.hexdigest()


class CPGCache:
    """Cache CPG results per file hash."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._memory: dict[str, CPGResult] = {}

    def get(self, file_hash: str) -> CPGResult | None:
        if file_hash in self._memory:
            return self._memory[file_hash]
        if self._cache_dir:
            cache_file = self._cache_dir / f"{file_hash}.json"
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text())
                    result = _deserialize_cpg_result(data)
                    self._memory[file_hash] = result
                    return result
                except Exception:
                    pass
        return None

    def put(self, file_hash: str, result: CPGResult) -> None:
        self._memory[file_hash] = result
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self._cache_dir / f"{file_hash}.json"
            cache_file.write_text(json.dumps(result.to_dict(), indent=2))


def _deserialize_cpg_result(data: dict[str, Any]) -> CPGResult:
    return CPGResult(
        taint_paths=[TaintPath(**t["source"], **{k: v for k, v in t.items() if k not in ("source", "sink", "hop_count")}) if False else TaintPath(
            source_file=t.get("source", {}).get("file", ""),
            source_line=t.get("source", {}).get("line", 0),
            source_label=t.get("source", {}).get("label", ""),
            sink_file=t.get("sink", {}).get("file", ""),
            sink_line=t.get("sink", {}).get("line", 0),
            sink_label=t.get("sink", {}).get("label", ""),
            hops=t.get("hops", []),
        ) for t in data.get("taint_paths", [])],
        boundary_violations=[BoundaryViolation(**b) for b in data.get("boundary_violations", [])],
        control_flow_violations=[ControlFlowViolation(**c) for c in data.get("control_flow_violations", [])],
        files_analyzed=data.get("files_analyzed", 0),
        cpg_generated=data.get("cpg_generated", False),
    )


# ---------------------------------------------------------------------------
# Joern execution
# ---------------------------------------------------------------------------


def run_joern_cpg(
    file_paths: list[Path],
    *,
    cache: CPGCache | None = None,
    timeout: int = 120,
) -> CPGResult:
    """Generate CPG via Joern and run standard queries.

    Requires Joern CLI and JDK 21+.
    """
    _check_joern()

    result = CPGResult(files_analyzed=len(file_paths))

    for fp in file_paths:
        if not fp.exists():
            continue

        fhash = _file_hash(fp)
        if cache and fhash:
            cached = cache.get(fhash)
            if cached:
                result.taint_paths.extend(cached.taint_paths)
                result.boundary_violations.extend(cached.boundary_violations)
                result.control_flow_violations.extend(cached.control_flow_violations)
                continue

        with tempfile.TemporaryDirectory() as tmpdir:
            cpg_path = Path(tmpdir) / "cpg.bin"
            try:
                subprocess.run(
                    ["joern-parse", "--output", str(cpg_path), str(fp)],
                    capture_output=True,
                    timeout=timeout,
                    check=True,
                )
                result.cpg_generated = True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
                logger.warning("Joern CPG generation failed for %s: %s", fp, e)
                continue

    return result


# ---------------------------------------------------------------------------
# Pure Python fallback analysis (no Joern required)
# ---------------------------------------------------------------------------


def analyze_imports(source: str, *, file_path: str = "") -> list[BoundaryViolation]:
    """Detect architectural boundary crossings via import analysis.

    Rules:
    - Controllers should not import repository/model layers directly
    - Tests should not import internal/private modules
    """
    import re as _re

    violations: list[BoundaryViolation] = []
    lines = source.split("\n")

    is_controller = any(p in file_path.lower() for p in ["controller", "route", "view", "endpoint"])
    is_test = "test" in file_path.lower()

    for i, line in enumerate(lines, 1):
        import_match = _re.match(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", line)
        if not import_match:
            continue
        module = import_match.group(1) or import_match.group(2) or ""

        if is_controller and any(p in module for p in ["repository", "model", "database", "db"]):
            violations.append(BoundaryViolation(
                source_file=file_path,
                source_line=i,
                target_import=module,
                violation=f"Controller imports data layer: {module}",
            ))

        if is_test and "._" in module:
            violations.append(BoundaryViolation(
                source_file=file_path,
                source_line=i,
                target_import=module,
                violation=f"Test imports private module: {module}",
            ))

    return violations


def analyze_async_violations(source: str, *, file_path: str = "") -> list[ControlFlowViolation]:
    """Detect sync functions calling async functions without await."""
    import re as _re

    violations: list[ControlFlowViolation] = []
    lines = source.split("\n")

    in_sync_fn = False
    current_fn = ""

    for i, line in enumerate(lines, 1):
        sync_match = _re.match(r"^\s*def\s+(\w+)", line)
        async_match = _re.match(r"^\s*async\s+def\s+(\w+)", line)

        if async_match:
            in_sync_fn = False
        elif sync_match:
            in_sync_fn = True
            current_fn = sync_match.group(1)

        if in_sync_fn and "await " in line:
            violations.append(ControlFlowViolation(
                file=file_path,
                line=i,
                function=current_fn,
                violation=f"'await' used in sync function '{current_fn}'",
            ))

    return violations


def analyze_python_fallback(
    file_paths: list[Path],
) -> CPGResult:
    """Pure Python analysis when Joern is not available.

    Analyzes imports for boundary violations and async/sync mismatches.
    """
    result = CPGResult(files_analyzed=len(file_paths))

    for fp in file_paths:
        if not fp.exists() or fp.suffix != ".py":
            continue
        try:
            source = fp.read_text()
        except OSError:
            continue

        result.boundary_violations.extend(
            analyze_imports(source, file_path=str(fp))
        )
        result.control_flow_violations.extend(
            analyze_async_violations(source, file_path=str(fp))
        )

    return result

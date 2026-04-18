"""Graph-Based Centrality Scoring for assumption prioritization.

Builds a module dependency graph from Python import statements across
a project, calculates in-degree centrality for each module, and uses
the centrality scores (along with optional git-churn data) to produce
a priority ranking of detected domain assumptions.

Priority formula:
    priority_score = base_severity * centrality_multiplier * churn_multiplier

Where:
    centrality_multiplier = 1.0 + (in_degree / max_in_degree)
    churn_multiplier      = 1.0 + (file_churn / max_file_churn)

Modules that many other modules depend on are more central to the
codebase and therefore assumptions tied to them are higher priority.
Modules that change frequently (high churn) compound this further.
"""

from __future__ import annotations

import ast
import logging
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from vt_protocol.decisions.models import DomainAssumption

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------

SEVERITY_SCORES: dict[str, float] = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
}

# Directories / patterns to skip when scanning for Python files
_SKIP_DIRS: set[str] = {
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".nox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".git",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ModuleGraph:
    """Dependency graph built from Python import statements.

    Attributes:
        modules: Mapping of module name to the set of modules that import it.
        in_degree: Number of distinct modules that import each module.
        max_in_degree: The highest in-degree observed across all modules.
    """

    modules: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    in_degree: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    max_in_degree: int = 0


@dataclass
class PrioritizedAssumption:
    """A domain assumption annotated with priority scoring metadata.

    Attributes:
        assumption: The original ``DomainAssumption``.
        priority_score: Composite score used for ranking.
        centrality_multiplier: Multiplier derived from in-degree centrality.
        churn_multiplier: Multiplier derived from git file churn.
        in_degree: Raw in-degree count for the most-central file in the
            assumption's ``code_evidence``.
    """

    assumption: DomainAssumption
    priority_score: float
    centrality_multiplier: float
    churn_multiplier: float
    in_degree: int


# ---------------------------------------------------------------------------
# Helpers — file path <-> module name mapping
# ---------------------------------------------------------------------------


def _should_skip_dir(dirname: str) -> bool:
    """Return True if *dirname* should be excluded from scanning."""
    if dirname in _SKIP_DIRS:
        return True
    if dirname.endswith(".egg-info"):
        return True
    return False


def _file_to_module(filepath: Path, root: Path) -> str | None:
    """Convert a filesystem path to a dotted Python module name.

    The function tries to resolve relative to *root* and any ``src/``
    subdirectory inside *root* (common ``src``-layout convention).

    Returns ``None`` if the path cannot be meaningfully converted.
    """
    try:
        rel = filepath.relative_to(root)
    except ValueError:
        return None

    parts = list(rel.parts)

    # Strip leading 'src' directory if present (src-layout projects)
    if parts and parts[0] == "src":
        parts = parts[1:]

    if not parts:
        return None

    # Replace file stem: foo/bar.py -> foo.bar, foo/__init__.py -> foo
    filename = parts[-1]
    if filename == "__init__.py":
        parts = parts[:-1]
    elif filename.endswith(".py"):
        parts[-1] = filename[:-3]
    else:
        return None

    if not parts:
        return None

    return ".".join(parts)


def _collect_python_files(root: Path) -> list[Path]:
    """Recursively collect all .py files under *root*, skipping excluded dirs."""
    py_files: list[Path] = []

    for child in sorted(root.iterdir()):
        if child.is_dir():
            if _should_skip_dir(child.name):
                continue
            # Also skip test directories
            if child.name in {"tests", "test"}:
                continue
            py_files.extend(_collect_python_files(child))
        elif child.is_file() and child.suffix == ".py":
            # Skip test files at any level
            if child.name.startswith("test_") or child.name.startswith("conftest"):
                continue
            py_files.append(child)

    return py_files


# ---------------------------------------------------------------------------
# Import extraction via AST
# ---------------------------------------------------------------------------


def _extract_imports(filepath: Path, root: Path) -> list[tuple[str, str]]:
    """Parse *filepath* and return (importer_module, imported_module) pairs.

    Handles both ``import X`` and ``from X import Y`` statements.
    Relative imports are resolved to absolute module paths when possible.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.debug("Could not read %s, skipping", filepath)
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        logger.debug("SyntaxError in %s, skipping", filepath)
        return []

    importer = _file_to_module(filepath, root)
    if importer is None:
        return []

    edges: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = alias.name
                edges.append((importer, imported))

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level  # 0 = absolute, 1+ = relative

            if level > 0:
                # Resolve relative import
                resolved = _resolve_relative(importer, module, level)
                if resolved:
                    edges.append((importer, resolved))
            else:
                if module:
                    edges.append((importer, module))

    return edges


def _resolve_relative(importer: str, module: str, level: int) -> str | None:
    """Resolve a relative import to an absolute module path.

    Given the importer ``foo.bar.baz``, ``from ..qux import X`` (level=2,
    module='qux') resolves to ``foo.qux``.
    """
    parts = importer.split(".")

    # Go up *level* package levels.  ``level=1`` means current package,
    # so we drop the last part (the module itself) and stay in its package.
    if level > len(parts):
        return None

    base_parts = parts[: len(parts) - level]

    if module:
        base_parts.append(module)

    if not base_parts:
        return None

    return ".".join(base_parts)


# ---------------------------------------------------------------------------
# Public API — build_module_graph
# ---------------------------------------------------------------------------


def build_module_graph(root: Path) -> ModuleGraph:
    """Scan all Python files under *root* and build a module dependency graph.

    Each edge ``A -> B`` means module ``A`` imports module ``B``.
    The graph tracks which modules import each target module (``modules``
    maps target -> set of importers) and the in-degree (number of
    distinct importers) for every observed module.

    Args:
        root: The project root directory to scan.

    Returns:
        A populated ``ModuleGraph`` instance.
    """
    root = root.resolve()
    graph = ModuleGraph()
    py_files = _collect_python_files(root)

    for py_file in py_files:
        edges = _extract_imports(py_file, root)
        for importer, imported in edges:
            # We track the top-level imported module as well as the full path
            # to give credit to deeply-nested modules.
            graph.modules[imported].add(importer)

    # Compute in-degree from the collected sets
    max_deg = 0
    for mod, importers in graph.modules.items():
        deg = len(importers)
        graph.in_degree[mod] = deg
        if deg > max_deg:
            max_deg = deg

    graph.max_in_degree = max_deg
    return graph


# ---------------------------------------------------------------------------
# Public API — get_churn_scores
# ---------------------------------------------------------------------------


def get_churn_scores(root: Path) -> dict[str, float]:
    """Compute file-level churn scores from git history (last 6 months).

    Churn is defined as the number of commits that touched each file.
    The returned dict maps file paths (relative to *root*) to a normalized
    churn multiplier in the range ``[1.0, 2.0]``:

        churn_multiplier = 1.0 + (file_count / max_file_count)

    If *root* is not inside a git repository or the subprocess call fails,
    an empty dict is returned (callers should default to a multiplier of 1.0).

    Args:
        root: The project root directory.

    Returns:
        Mapping of relative file path strings to churn multiplier floats.
    """
    root = root.resolve()

    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--format=",
                "--name-only",
                "--since=6 months ago",
            ],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=60,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        logger.debug("git log failed or git not available — churn scores disabled")
        return {}

    if result.returncode != 0:
        logger.debug("git log returned %d — churn scores disabled", result.returncode)
        return {}

    # Count file appearances
    counts: dict[str, int] = defaultdict(int)
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            counts[line] += 1

    if not counts:
        return {}

    max_count = max(counts.values())
    if max_count == 0:
        return {}

    churn: dict[str, float] = {}
    for filepath, count in counts.items():
        churn[filepath] = 1.0 + (count / max_count)

    return churn


# ---------------------------------------------------------------------------
# Public API — prioritize_assumptions
# ---------------------------------------------------------------------------


def _module_for_file(filepath: str, root: Path) -> str | None:
    """Try to convert a code-evidence file path to a module name."""
    # Handle both absolute and relative paths
    p = Path(filepath)
    if not p.is_absolute():
        p = root / p

    return _file_to_module(p, root)


def _best_centrality(
    assumption: DomainAssumption,
    graph: ModuleGraph,
    root: Path,
) -> tuple[float, int]:
    """Return the best (centrality_multiplier, in_degree) across evidence files.

    Checks each file in ``code_evidence`` against the module graph, including
    both exact module name matches and prefix matches (a file in package
    ``foo.bar`` benefits from imports of ``foo.bar.baz``).
    """
    if graph.max_in_degree == 0:
        return 1.0, 0

    best_degree = 0

    for evidence in assumption.code_evidence:
        mod = _module_for_file(evidence.file, root)
        if mod is None:
            continue

        # Exact match
        deg = graph.in_degree.get(mod, 0)
        if deg > best_degree:
            best_degree = deg

        # Also check if this module is a prefix of any imported module
        # (e.g., package ``foo.bar`` benefits from imports of ``foo.bar.sub``)
        for graph_mod, graph_deg in graph.in_degree.items():
            if graph_mod.startswith(mod + ".") or graph_mod == mod:
                if graph_deg > best_degree:
                    best_degree = graph_deg

    multiplier = 1.0 + (best_degree / graph.max_in_degree) if graph.max_in_degree > 0 else 1.0
    return multiplier, best_degree


def _best_churn(
    assumption: DomainAssumption,
    churn_scores: dict[str, float],
    root: Path,
) -> float:
    """Return the best churn multiplier across evidence files."""
    if not churn_scores:
        return 1.0

    best = 1.0

    for evidence in assumption.code_evidence:
        filepath = evidence.file

        # Try the path as-is
        if filepath in churn_scores:
            if churn_scores[filepath] > best:
                best = churn_scores[filepath]
            continue

        # Try making it relative to root
        try:
            p = Path(filepath)
            if p.is_absolute():
                rel = str(p.relative_to(root))
            else:
                rel = filepath
        except ValueError:
            rel = filepath

        if rel in churn_scores:
            if churn_scores[rel] > best:
                best = churn_scores[rel]

    return best


def prioritize_assumptions(
    assumptions: list[DomainAssumption],
    root: Path,
) -> list[PrioritizedAssumption]:
    """Score and sort domain assumptions by graph-based priority.

    For each assumption the function:

    1. Maps its ``code_evidence`` files to module names.
    2. Looks up in-degree centrality in the module graph.
    3. Looks up git churn for the evidence files.
    4. Computes a composite priority score:

       ``priority_score = base_severity * centrality_multiplier * churn_multiplier``

    The returned list is sorted in descending order of ``priority_score``
    (highest priority first).

    Args:
        assumptions: Domain assumptions to prioritize.
        root: Project root directory (used to build the module graph and
            resolve file paths).

    Returns:
        Sorted list of ``PrioritizedAssumption`` instances.
    """
    root = root.resolve()

    # Build graph and churn data
    graph = build_module_graph(root)
    churn_scores = get_churn_scores(root)

    results: list[PrioritizedAssumption] = []

    for assumption in assumptions:
        base_severity = SEVERITY_SCORES.get(assumption.severity.lower(), 2.0)
        centrality_mult, in_deg = _best_centrality(assumption, graph, root)
        churn_mult = _best_churn(assumption, churn_scores, root)

        priority = base_severity * centrality_mult * churn_mult

        results.append(
            PrioritizedAssumption(
                assumption=assumption,
                priority_score=priority,
                centrality_multiplier=centrality_mult,
                churn_multiplier=churn_mult,
                in_degree=in_deg,
            )
        )

    # Sort descending by priority score, then by severity as tiebreaker
    results.sort(
        key=lambda p: (p.priority_score, SEVERITY_SCORES.get(p.assumption.severity.lower(), 0)),
        reverse=True,
    )

    return results

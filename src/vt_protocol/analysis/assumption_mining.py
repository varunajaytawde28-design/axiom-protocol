"""Association Rule Mining — statistical co-occurrence detection for code assumptions.

PR-Miner-inspired approach: parse Python AST, extract feature sets per function,
mine frequent itemsets, flag functions that handle only a SUBSET of expected patterns.

From research: "PR-Miner extracts implicit programming rules from large codebases
using frequent itemset mining and identifies violations as code handling only a
subset of frequently co-occurring elements."
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Skip directories when scanning (matches assumptions.py)
# ---------------------------------------------------------------------------

_SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".eggs",
    "dist",
    "build",
    ".nox",
})

# Patterns to detect test file paths (matches assumptions.py).
import re as _re

_TEST_PATH_RE = _re.compile(
    r"(?:^|/)(?:test_|tests/|testing/|conftest\.py|_test\.py$)"
)


def _is_test_path(path: Path) -> bool:
    """Return True if *path* looks like a test file or is inside a test directory."""
    return bool(_TEST_PATH_RE.search(str(path)))


# ---------------------------------------------------------------------------
# MiningAlert dataclass
# ---------------------------------------------------------------------------


@dataclass
class MiningAlert:
    """A single alert raised when code handles only a subset of a mined pattern."""

    file: str
    line: int
    pattern_subset: frozenset[str]
    full_pattern: frozenset[str]
    confidence: float
    description: str


# ---------------------------------------------------------------------------
# Feature-extraction helpers (per AST node type)
# ---------------------------------------------------------------------------

# Method names considered as database calls.
_DB_METHODS: frozenset[str] = frozenset({
    "execute", "commit", "query", "filter", "filter_by",
    "fetchone", "fetchall", "fetchmany", "scalar",
    "add", "add_all", "merge", "flush", "rollback",
    "bulk_create", "update_or_create", "get_or_create",
})

# Module/attribute prefixes considered as HTTP calls.
_HTTP_NAMES: frozenset[str] = frozenset({
    "requests", "httpx", "urllib", "aiohttp", "fetch",
})

# Method names considered as HTTP calls.
_HTTP_METHODS: frozenset[str] = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options", "request",
})

# Built-in functions / names considered type conversions.
_TYPE_CONV_FUNCS: frozenset[str] = frozenset({
    "int", "float", "str", "bool", "bytes", "list", "tuple", "dict", "set",
})

# Method names considered type conversions.
_TYPE_CONV_METHODS: frozenset[str] = frozenset({
    "encode", "decode",
})

# Method names considered as file I/O.
_FILE_IO_METHODS: frozenset[str] = frozenset({
    "read_text", "read_bytes", "write_text", "write_bytes",
    "read", "write", "readlines", "writelines",
})

# os.path function names considered as file I/O.
_OS_PATH_FUNCS: frozenset[str] = frozenset({
    "exists", "isfile", "isdir", "join", "abspath", "dirname",
    "basename", "splitext", "getsize",
})


class _FeatureVisitor(ast.NodeVisitor):
    """Walk the body of a single function/method and collect feature tags."""

    def __init__(self) -> None:
        self.features: set[str] = set()
        # Track whether we are inside a try/except block.
        self._in_try: bool = False
        # Track whether we are inside an if block (for return_early).
        self._in_if: bool = False

    # -- try / except --------------------------------------------------

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        if node.handlers:
            self.features.add("try_except")
        old = self._in_try
        self._in_try = True
        self.generic_visit(node)
        self._in_try = old

    # Python 3.11+ uses TryStar for except* (ExceptionGroup).
    visit_TryStar = visit_Try

    # -- calls ---------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self._classify_call(node)
        self.generic_visit(node)

    def _classify_call(self, node: ast.Call) -> None:
        """Classify a Call node into zero or more feature tags."""
        func = node.func

        # --- Simple name calls: open(), int(), isinstance(), etc. ---
        if isinstance(func, ast.Name):
            name = func.id
            if name == "open":
                self.features.add("file_io")
            if name in _TYPE_CONV_FUNCS:
                self.features.add("type_conversion")
            if name == "isinstance":
                self.features.add("validation")
            if name in ("print",):
                # Not classified — too noisy.
                pass
            # fetch() as a top-level call (rare, but specified).
            if name == "fetch":
                self.features.add("http_call")
            return

        # --- Attribute calls: obj.method() ---
        if isinstance(func, ast.Attribute):
            method = func.attr

            # Database calls
            if method in _DB_METHODS:
                self.features.add("db_call")

            # Logging calls: logger.info(), logging.warning(), etc.
            self._check_logging(func, method)

            # HTTP calls
            self._check_http(func, method)

            # File I/O: Path.read_text(), f.read(), etc.
            if method in _FILE_IO_METHODS:
                self.features.add("file_io")

            # os.path.* calls
            self._check_os_path(func, method)

            # os.environ.get / os.getenv
            self._check_env_access(func, method)

            # Type conversion methods: .encode(), .decode()
            if method in _TYPE_CONV_METHODS:
                self.features.add("type_conversion")

    def _check_logging(self, func: ast.Attribute, method: str) -> None:
        """Detect logger.* and logging.* calls."""
        log_methods = {"debug", "info", "warning", "error", "critical",
                       "exception", "log", "warn"}
        if method not in log_methods:
            return
        value = func.value
        # logger.info(...)
        if isinstance(value, ast.Name) and value.id in ("logger", "log", "logging"):
            self.features.add("logging")
        # logging.info(...)
        if isinstance(value, ast.Name) and value.id == "logging":
            self.features.add("logging")
        # self.logger.info(...)
        if isinstance(value, ast.Attribute) and value.attr in ("logger", "log"):
            self.features.add("logging")

    def _check_http(self, func: ast.Attribute, method: str) -> None:
        """Detect requests.get(), httpx.post(), session.get() in HTTP context, etc."""
        value = func.value
        # requests.get(), httpx.post(), urllib.request.urlopen(), aiohttp.*
        if isinstance(value, ast.Name) and value.id in _HTTP_NAMES:
            self.features.add("http_call")
            return
        # client.get(), session.post() — only if the method is a known HTTP verb
        if method in _HTTP_METHODS:
            # Heuristic: the receiver name often hints at HTTP usage
            if isinstance(value, ast.Name):
                receiver = value.id.lower()
                http_hints = {"client", "session", "http", "conn", "connection",
                              "resp", "response", "req", "request", "api"}
                if any(hint in receiver for hint in http_hints):
                    self.features.add("http_call")
        # urllib.request.urlopen or similar chained attribute
        if isinstance(value, ast.Attribute):
            if isinstance(value.value, ast.Name) and value.value.id in _HTTP_NAMES:
                self.features.add("http_call")

    def _check_os_path(self, func: ast.Attribute, method: str) -> None:
        """Detect os.path.* calls."""
        value = func.value
        if method in _OS_PATH_FUNCS and isinstance(value, ast.Attribute):
            if value.attr == "path" and isinstance(value.value, ast.Name):
                if value.value.id == "os":
                    self.features.add("file_io")

    def _check_env_access(self, func: ast.Attribute, method: str) -> None:
        """Detect os.environ[...], os.environ.get(...), os.getenv(...)."""
        value = func.value
        # os.getenv(...)
        if method == "getenv" and isinstance(value, ast.Name) and value.id == "os":
            self.features.add("env_access")
        # os.environ.get(...)
        if method == "get" and isinstance(value, ast.Attribute):
            if value.attr == "environ" and isinstance(value.value, ast.Name):
                if value.value.id == "os":
                    self.features.add("env_access")

    # -- null checks ---------------------------------------------------

    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        """Detect ``if x is None``, ``if x is not None``."""
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Is, ast.IsNot)):
                if isinstance(comparator, ast.Constant) and comparator.value is None:
                    self.features.add("null_check")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        # Check for ``if not x`` style null checks.
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            # ``if not x`` — a common truthiness/null check.
            self.features.add("null_check")

        # Check for return inside if body (return_early).
        old_in_if = self._in_if
        self._in_if = True
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child is not node:
                self.features.add("return_early")
                break
        self._in_if = old_in_if

        self.generic_visit(node)

    # -- validation: assert, raise ValueError/TypeError ----------------

    def visit_Assert(self, node: ast.Assert) -> None:  # noqa: N802
        self.features.add("validation")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:  # noqa: N802
        exc = node.exc
        if exc is not None:
            exc_name = self._get_exception_name(exc)
            if exc_name in ("ValueError", "TypeError", "ValidationError",
                            "AssertionError", "AttributeError"):
                self.features.add("validation")
        self.generic_visit(node)

    @staticmethod
    def _get_exception_name(node: ast.expr) -> str:
        """Extract the exception class name from a raise expression."""
        # raise ValueError(...)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return node.func.id
            if isinstance(node.func, ast.Attribute):
                return node.func.attr
        # raise ValueError  (no call)
        if isinstance(node, ast.Name):
            return node.id
        return ""

    # -- env access via subscript: os.environ["KEY"] -------------------

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "environ":
            if isinstance(value.value, ast.Name) and value.value.id == "os":
                self.features.add("env_access")
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Top-level feature extraction
# ---------------------------------------------------------------------------


def _extract_function_features(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Extract the set of feature tags present in a single function body."""
    visitor = _FeatureVisitor()
    visitor.visit(func_node)
    return frozenset(visitor.features)


def extract_features(source: str) -> list[frozenset[str]]:
    """Extract feature sets from every function/method in *source*.

    Each element in the returned list is a frozenset of feature tags for one
    function.  Empty functions (no features detected) are omitted.

    Args:
        source: Python source code as a string.

    Returns:
        List of frozensets, one per non-trivial function found.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        logger.debug("SyntaxError while parsing source — skipping")
        return []

    feature_sets: list[frozenset[str]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            features = _extract_function_features(node)
            if features:
                feature_sets.append(features)

    return feature_sets


# ---------------------------------------------------------------------------
# Frequent itemset mining (Apriori-like)
# ---------------------------------------------------------------------------


def _count_support(
    itemset: frozenset[str],
    feature_sets: Sequence[frozenset[str]],
) -> int:
    """Count how many feature sets contain *itemset* as a subset."""
    count = 0
    for fs in feature_sets:
        if itemset <= fs:
            count += 1
    return count


def mine_frequent_itemsets(
    feature_sets: list[frozenset[str]],
    min_support: float = 0.7,
) -> list[frozenset[str]]:
    """Find all frequent itemsets using an Apriori-like level-wise algorithm.

    An itemset is frequent if it appears in at least ``min_support`` fraction
    of the provided feature sets.

    Args:
        feature_sets: List of feature sets (one per function).
        min_support: Minimum support threshold in [0, 1].

    Returns:
        List of frequent itemsets (frozensets), sorted largest-first so that
        the most specific patterns come first.
    """
    if not feature_sets:
        return []

    n = len(feature_sets)
    min_count = max(1, int(min_support * n))

    # Collect all individual items.
    all_items: set[str] = set()
    for fs in feature_sets:
        all_items.update(fs)

    if not all_items:
        return []

    # Level 1: single-item frequent sets.
    current_frequent: dict[frozenset[str], int] = {}
    for item in all_items:
        candidate = frozenset({item})
        support = _count_support(candidate, feature_sets)
        if support >= min_count:
            current_frequent[candidate] = support

    all_frequent: dict[frozenset[str], int] = dict(current_frequent)

    # Level k >= 2: generate candidates from level k-1 frequent sets.
    k = 2
    while current_frequent:
        # Extract sorted items from current frequent sets for candidate generation.
        prev_items: set[str] = set()
        for fs in current_frequent:
            prev_items.update(fs)

        # Generate candidates of size k by joining pairs of (k-1)-itemsets
        # that share (k-2) items.
        prev_sets = list(current_frequent.keys())
        candidates: set[frozenset[str]] = set()

        for i in range(len(prev_sets)):
            for j in range(i + 1, len(prev_sets)):
                union = prev_sets[i] | prev_sets[j]
                if len(union) == k:
                    # Apriori pruning: every (k-1)-subset must be frequent.
                    if _all_subsets_frequent(union, k - 1, current_frequent):
                        candidates.add(union)

        if not candidates:
            break

        next_frequent: dict[frozenset[str], int] = {}
        for candidate in candidates:
            support = _count_support(candidate, feature_sets)
            if support >= min_count:
                next_frequent[candidate] = support

        all_frequent.update(next_frequent)
        current_frequent = next_frequent
        k += 1

        # Safety cap: feature tags are bounded (~10), so max itemset size is small.
        if k > 12:
            break

    # Only return itemsets of size >= 2 (single items are not interesting patterns).
    result = [fs for fs in all_frequent if len(fs) >= 2]

    # Sort: largest first, then alphabetically for determinism.
    result.sort(key=lambda fs: (-len(fs), sorted(fs)))
    return result


def _all_subsets_frequent(
    itemset: frozenset[str],
    subset_size: int,
    frequent: dict[frozenset[str], int],
) -> bool:
    """Check that every subset of *itemset* of the given size is in *frequent*."""
    items = sorted(itemset)
    for combo in combinations(items, subset_size):
        if frozenset(combo) not in frequent:
            return False
    return True


# ---------------------------------------------------------------------------
# Missing-pattern detection
# ---------------------------------------------------------------------------


def detect_missing_patterns(
    source: str,
    frequent_itemsets: list[frozenset[str]],
    *,
    file_path: str = "<unknown>",
) -> list[MiningAlert]:
    """Detect functions that handle only a SUBSET of a frequent pattern.

    A function is flagged when:
    - Its feature set overlaps with a frequent itemset (shares at least one item).
    - The feature set is a strict subset — it is missing one or more items from
      the frequent itemset.
    - The overlap is non-trivial (at least 50% of the pattern is present).

    Args:
        source: Python source code as a string.
        frequent_itemsets: Pre-mined frequent itemsets from the project.
        file_path: Path string used in alert reporting.

    Returns:
        List of ``MiningAlert`` instances.
    """
    if not frequent_itemsets:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        logger.debug("SyntaxError while parsing source for detection — skipping")
        return []

    alerts: list[MiningAlert] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        features = _extract_function_features(node)
        if not features:
            continue

        func_line = node.lineno
        func_name = node.name

        for pattern in frequent_itemsets:
            overlap = features & pattern
            missing = pattern - features

            # Skip if there is no overlap or if the function already has the
            # full pattern (nothing missing).
            if not overlap or not missing:
                continue

            # Require non-trivial overlap: at least 50% of the pattern present.
            overlap_ratio = len(overlap) / len(pattern)
            if overlap_ratio < 0.5:
                continue

            # Confidence: higher when more of the pattern is present but
            # something is still missing.
            confidence = round(overlap_ratio, 2)

            missing_str = ", ".join(sorted(missing))
            present_str = ", ".join(sorted(overlap))
            description = (
                f"Function '{func_name}' has [{present_str}] but is missing "
                f"[{missing_str}] from frequently co-occurring pattern "
                f"{{{', '.join(sorted(pattern))}}} "
                f"(confidence: {confidence:.0%})"
            )

            alerts.append(MiningAlert(
                file=file_path,
                line=func_line,
                pattern_subset=overlap,
                full_pattern=pattern,
                confidence=confidence,
                description=description,
            ))

    # Deduplicate: if a function is flagged by both a large pattern and a
    # smaller subset of that pattern, keep only the most specific (largest)
    # alert per function line.
    alerts = _deduplicate_alerts(alerts)
    return alerts


def _deduplicate_alerts(alerts: list[MiningAlert]) -> list[MiningAlert]:
    """Remove redundant alerts where a smaller pattern is subsumed by a larger one.

    For each (file, line) pair, if alert A's full_pattern is a subset of alert
    B's full_pattern and both flag the same subset, drop A (the less specific one).
    """
    if len(alerts) <= 1:
        return alerts

    # Group by (file, line).
    by_location: dict[tuple[str, int], list[MiningAlert]] = {}
    for alert in alerts:
        key = (alert.file, alert.line)
        by_location.setdefault(key, []).append(alert)

    result: list[MiningAlert] = []
    for _key, group in by_location.items():
        if len(group) == 1:
            result.extend(group)
            continue

        # Sort by full_pattern size descending — largest patterns first.
        group.sort(key=lambda a: -len(a.full_pattern))

        kept: list[MiningAlert] = []
        for alert in group:
            # Check if this alert is subsumed by an already-kept alert.
            subsumed = False
            for existing in kept:
                if (alert.full_pattern <= existing.full_pattern
                        and alert.pattern_subset <= existing.pattern_subset):
                    subsumed = True
                    break
            if not subsumed:
                kept.append(alert)
        result.extend(kept)

    # Restore deterministic ordering.
    result.sort(key=lambda a: (a.file, a.line, sorted(a.full_pattern)))
    return result


# ---------------------------------------------------------------------------
# Project-level mining
# ---------------------------------------------------------------------------


def _walk_python_files(
    root: Path,
    *,
    max_depth: int = 8,
    _current_depth: int = 0,
) -> list[Path]:
    """Recursively collect Python files, respecting skip rules."""
    if _current_depth > max_depth:
        return []

    results: list[Path] = []

    try:
        entries = sorted(root.iterdir())
    except (OSError, PermissionError) as exc:
        logger.warning("Cannot list directory %s: %s", root, exc)
        return []

    for entry in entries:
        if entry.name.startswith(".") and entry.is_dir():
            continue
        if entry.is_dir():
            if entry.name in _SKIP_DIRS:
                continue
            results.extend(
                _walk_python_files(
                    entry,
                    max_depth=max_depth,
                    _current_depth=_current_depth + 1,
                )
            )
        elif entry.is_file() and entry.suffix == ".py":
            if _is_test_path(entry):
                continue
            results.append(entry)

    return results


# Minimum file size (bytes) to bother analysing — skip tiny __init__.py stubs.
_MIN_FILE_SIZE = 50


def mine_project(
    root: Path,
    *,
    min_support: float = 0.7,
    max_depth: int = 8,
) -> list[MiningAlert]:
    """Scan a full project: extract features, mine itemsets, detect violations.

    This is the main entry point for project-wide association-rule mining.

    Args:
        root: Project root directory.
        min_support: Minimum support threshold for frequent itemsets (0-1).
        max_depth: Maximum directory depth to recurse into.

    Returns:
        List of ``MiningAlert`` instances across all files.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        logger.warning("Not a directory: %s", root)
        return []

    # Phase 1: collect all feature sets across the project.
    all_feature_sets: list[frozenset[str]] = []
    file_sources: dict[str, str] = {}

    python_files = _walk_python_files(root, max_depth=max_depth)
    logger.info("Found %d Python files under %s", len(python_files), root)

    for file_path in python_files:
        try:
            size = file_path.stat().st_size
        except OSError:
            continue

        if size < _MIN_FILE_SIZE:
            continue

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError) as exc:
            logger.warning("Cannot read %s: %s", file_path, exc)
            continue

        file_sources[str(file_path)] = source

        feature_sets = extract_features(source)
        all_feature_sets.extend(feature_sets)

    logger.info(
        "Extracted features from %d functions across %d files",
        len(all_feature_sets),
        len(file_sources),
    )

    if not all_feature_sets:
        return []

    # Phase 2: mine frequent itemsets.
    frequent = mine_frequent_itemsets(all_feature_sets, min_support=min_support)
    logger.info(
        "Mined %d frequent itemsets (min_support=%.0f%%)",
        len(frequent),
        min_support * 100,
    )

    if not frequent:
        return []

    # Phase 3: detect missing patterns in each file.
    all_alerts: list[MiningAlert] = []

    for file_str, source in file_sources.items():
        alerts = detect_missing_patterns(
            source,
            frequent,
            file_path=file_str,
        )
        all_alerts.extend(alerts)

    logger.info(
        "Mining complete: %d alerts across %d files",
        len(all_alerts),
        len(file_sources),
    )
    return all_alerts

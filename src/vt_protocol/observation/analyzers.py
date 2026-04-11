"""Tree-sitter architectural pattern queries.

SPEC Phase 1: "Tree-sitter extraction for Python + TypeScript."

Extracts imports, class definitions, function signatures, and decorator
patterns from source files. Feeds into taxonomy auto-detection for
code-level architectural signals.

Tree-sitter is an OPTIONAL dependency. When not installed, all functions
return empty results and log a debug message. Install with:
    pip install vt-protocol[analyzers]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy tree-sitter import — graceful fallback if not installed
# ---------------------------------------------------------------------------

_ts_available = False
_py_language = None
_ts_language = None


def _ensure_tree_sitter() -> bool:
    """Try to load tree-sitter and language grammars. Returns True if available."""
    global _ts_available, _py_language, _ts_language
    if _ts_available:
        return True
    try:
        from tree_sitter import Language

        try:
            import tree_sitter_python as tspython
            _py_language = Language(tspython.language())
        except (ImportError, Exception):
            logger.debug("tree-sitter-python not available")

        try:
            import tree_sitter_typescript as tstypescript
            _ts_language = Language(tstypescript.language_typescript())
        except (ImportError, Exception):
            logger.debug("tree-sitter-typescript not available")

        _ts_available = _py_language is not None or _ts_language is not None
        return _ts_available
    except ImportError:
        logger.debug("tree-sitter not installed — analyzers disabled")
        return False


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass
class ImportInfo:
    """A detected import statement."""

    module: str
    names: list[str] = field(default_factory=list)
    is_relative: bool = False


@dataclass
class ClassInfo:
    """A detected class definition."""

    name: str
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class FunctionInfo:
    """A detected function/method definition."""

    name: str
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    line: int = 0


@dataclass
class AnalysisResult:
    """Combined analysis output for a single file."""

    path: str
    language: str
    imports: list[ImportInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)

    @property
    def import_modules(self) -> list[str]:
        """Flat list of top-level module names."""
        modules: list[str] = []
        for imp in self.imports:
            top = imp.module.split(".")[0]
            if top and top not in modules:
                modules.append(top)
        return modules


# ---------------------------------------------------------------------------
# Regex fallback (when tree-sitter is not available)
# ---------------------------------------------------------------------------

import re

_PY_IMPORT_RE = re.compile(
    r"^(?:from\s+([\w.]+)\s+import\s+(.+)|import\s+([\w., ]+))", re.MULTILINE
)
_PY_CLASS_RE = re.compile(
    r"^class\s+(\w+)(?:\(([^)]*)\))?:", re.MULTILINE
)
_PY_FUNC_RE = re.compile(
    r"^(async\s+)?def\s+(\w+)\s*\(", re.MULTILINE
)
_PY_DECORATOR_RE = re.compile(
    r"^@([\w.]+(?:\([^)]*\))?)\s*$", re.MULTILINE
)

_TS_IMPORT_RE = re.compile(
    r"""import\s+(?:\{[^}]+\}\s+from\s+|[\w]+\s+from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_TS_CLASS_RE = re.compile(
    r"(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", re.MULTILINE
)
_TS_FUNC_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*[(<]", re.MULTILINE
)


def _analyze_python_regex(source: str, path: str) -> AnalysisResult:
    """Regex-based Python analysis (fallback when tree-sitter unavailable)."""
    result = AnalysisResult(path=path, language="python")

    for m in _PY_IMPORT_RE.finditer(source):
        if m.group(1):  # from X import Y
            names = [n.strip() for n in m.group(2).split(",")]
            result.imports.append(ImportInfo(
                module=m.group(1),
                names=names,
                is_relative=m.group(1).startswith("."),
            ))
        elif m.group(3):  # import X, Y
            for mod in m.group(3).split(","):
                mod = mod.strip()
                if mod:
                    result.imports.append(ImportInfo(module=mod))

    # Collect decorators for pairing with classes/functions
    decorators_by_line: dict[int, list[str]] = {}
    for m in _PY_DECORATOR_RE.finditer(source):
        line = source[:m.start()].count("\n")
        decorators_by_line.setdefault(line, [])
        name = m.group(1).split("(")[0]
        decorators_by_line[line].append(name)

    for m in _PY_CLASS_RE.finditer(source):
        line = source[:m.start()].count("\n")
        bases = [b.strip() for b in (m.group(2) or "").split(",") if b.strip()]
        # Collect decorators from preceding lines
        decos: list[str] = []
        for dl in range(max(0, line - 5), line):
            decos.extend(decorators_by_line.get(dl, []))
        result.classes.append(ClassInfo(
            name=m.group(1), bases=bases, decorators=decos, line=line + 1,
        ))

    for m in _PY_FUNC_RE.finditer(source):
        line = source[:m.start()].count("\n")
        is_async = bool(m.group(1))
        decos: list[str] = []
        for dl in range(max(0, line - 5), line):
            decos.extend(decorators_by_line.get(dl, []))
        result.functions.append(FunctionInfo(
            name=m.group(2), decorators=decos, is_async=is_async, line=line + 1,
        ))

    return result


def _analyze_typescript_regex(source: str, path: str) -> AnalysisResult:
    """Regex-based TypeScript analysis (fallback)."""
    result = AnalysisResult(path=path, language="typescript")

    for m in _TS_IMPORT_RE.finditer(source):
        result.imports.append(ImportInfo(module=m.group(1)))

    for m in _TS_CLASS_RE.finditer(source):
        line = source[:m.start()].count("\n") + 1
        bases = [m.group(2)] if m.group(2) else []
        result.classes.append(ClassInfo(name=m.group(1), bases=bases, line=line))

    for m in _TS_FUNC_RE.finditer(source):
        line = source[:m.start()].count("\n") + 1
        is_async = "async" in source[max(0, m.start() - 10):m.start()]
        result.functions.append(FunctionInfo(
            name=m.group(1), is_async=is_async, line=line,
        ))

    return result


# ---------------------------------------------------------------------------
# Tree-sitter analysis (preferred when available)
# ---------------------------------------------------------------------------


def _analyze_python_ts(source: bytes, path: str) -> AnalysisResult:
    """Tree-sitter-based Python analysis."""
    from tree_sitter import Parser

    parser = Parser(_py_language)
    tree = parser.parse(source)
    result = AnalysisResult(path=path, language="python")

    def _text(node) -> str:  # type: ignore[no-untyped-def]
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    for node in _walk(tree.root_node):
        if node.type == "import_statement":
            for child in node.children:
                if child.type in ("dotted_name", "aliased_import"):
                    result.imports.append(ImportInfo(module=_text(child).split(" as ")[0]))

        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            module = _text(module_node) if module_node else ""
            names: list[str] = []
            for child in node.children:
                if child.type == "import_prefix":
                    module = _text(child) + module
                if child.type in ("dotted_name", "aliased_import") and child != module_node:
                    names.append(_text(child).split(" as ")[0])
                if child.type == "import_list":
                    for sub in child.children:
                        if sub.type in ("dotted_name", "aliased_import"):
                            names.append(_text(sub).split(" as ")[0])
            result.imports.append(ImportInfo(
                module=module, names=names, is_relative=module.startswith("."),
            ))

        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            name = _text(name_node) if name_node else ""
            bases: list[str] = []
            for child in node.children:
                if child.type == "argument_list":
                    for arg in child.children:
                        t = _text(arg).strip("(,) ")
                        if t:
                            bases.append(t)
            result.classes.append(ClassInfo(
                name=name, bases=bases,
                line=node.start_point[0] + 1,
            ))

        elif node.type in ("function_definition", "decorated_definition"):
            actual = node
            decos: list[str] = []
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type == "decorator":
                        decos.append(_text(child).lstrip("@").split("(")[0])
                    elif child.type == "function_definition":
                        actual = child

            name_node = actual.child_by_field_name("name")
            fname = _text(name_node) if name_node else ""
            is_async = any(
                c.type == "async" for c in actual.children
            ) if hasattr(actual, "children") else False
            result.functions.append(FunctionInfo(
                name=fname, decorators=decos, is_async=is_async,
                line=actual.start_point[0] + 1,
            ))

    return result


def _walk(node):  # type: ignore[no-untyped-def]
    """Walk all nodes in a tree-sitter parse tree."""
    yield node
    for child in node.children:
        yield from _walk(child)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}


def analyze_file(path: Path) -> AnalysisResult:
    """Analyze a source file and extract structural information.

    Uses tree-sitter if available, falls back to regex patterns.
    """
    suffix = path.suffix.lower()
    language = _LANG_MAP.get(suffix)
    if not language:
        return AnalysisResult(path=str(path), language="unknown")

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return AnalysisResult(path=str(path), language=language)

    if language == "python":
        if _ensure_tree_sitter() and _py_language is not None:
            return _analyze_python_ts(source.encode("utf-8"), str(path))
        return _analyze_python_regex(source, str(path))

    if language in ("typescript", "javascript"):
        if _ensure_tree_sitter() and _ts_language is not None:
            # TS tree-sitter analysis would go here
            pass
        return _analyze_typescript_regex(source, str(path))

    return AnalysisResult(path=str(path), language=language)


def extract_imports(path: Path) -> list[str]:
    """Extract top-level module names from a source file."""
    return analyze_file(path).import_modules


def analyze_directory(
    root: Path,
    extensions: list[str] | None = None,
) -> list[AnalysisResult]:
    """Analyze all source files in a directory tree."""
    exts = set(extensions or [".py", ".ts", ".tsx", ".js", ".jsx"])
    results: list[AnalysisResult] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue
        # Skip common non-source directories
        parts = path.relative_to(root).parts
        if any(p in ("node_modules", ".venv", "venv", "__pycache__", "dist", "build")
               for p in parts):
            continue
        results.append(analyze_file(path))

    return results

"""Tests for Code Property Graphs via Joern."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.analysis.cpg import (
    BoundaryViolation,
    CPGCache,
    CPGResult,
    ControlFlowViolation,
    TaintPath,
    analyze_async_violations,
    analyze_imports,
    analyze_python_fallback,
    is_joern_available,
)


class TestTaintPath:
    def test_to_dict(self) -> None:
        tp = TaintPath(
            source_file="a.py",
            source_line=10,
            source_label="user_input",
            sink_file="b.py",
            sink_line=20,
            sink_label="db_query",
        )
        d = tp.to_dict()
        assert d["source"]["file"] == "a.py"
        assert d["sink"]["file"] == "b.py"
        assert d["hop_count"] == 0


class TestBoundaryViolation:
    def test_to_dict(self) -> None:
        bv = BoundaryViolation(
            source_file="controller.py",
            source_line=5,
            target_import="db.repository",
            violation="Controller imports data layer",
        )
        d = bv.to_dict()
        assert "controller.py" in d["source_file"]


class TestControlFlowViolation:
    def test_to_dict(self) -> None:
        cfv = ControlFlowViolation(
            file="app.py",
            line=10,
            function="sync_handler",
            violation="await in sync function",
        )
        d = cfv.to_dict()
        assert d["function"] == "sync_handler"


class TestCPGResult:
    def test_empty(self) -> None:
        result = CPGResult()
        assert result.total_violations == 0
        assert result.files_analyzed == 0

    def test_total_violations(self) -> None:
        result = CPGResult(
            taint_paths=[TaintPath()],
            boundary_violations=[BoundaryViolation()],
            control_flow_violations=[ControlFlowViolation()],
        )
        assert result.total_violations == 3

    def test_to_dict(self) -> None:
        result = CPGResult(files_analyzed=3, cpg_generated=True)
        d = result.to_dict()
        assert d["files_analyzed"] == 3
        assert d["cpg_generated"] is True


class TestCPGCache:
    def test_memory_cache(self) -> None:
        cache = CPGCache()
        result = CPGResult(files_analyzed=1)
        cache.put("abc123", result)
        assert cache.get("abc123") is result

    def test_cache_miss(self) -> None:
        cache = CPGCache()
        assert cache.get("nonexistent") is None

    def test_file_cache(self, tmp_path: Path) -> None:
        cache = CPGCache(cache_dir=tmp_path / "cpg_cache")
        result = CPGResult(files_analyzed=2, cpg_generated=True)
        cache.put("hash123", result)

        # New cache instance should find it
        cache2 = CPGCache(cache_dir=tmp_path / "cpg_cache")
        cached = cache2.get("hash123")
        assert cached is not None
        assert cached.files_analyzed == 2


class TestAnalyzeImports:
    def test_controller_importing_repository(self) -> None:
        source = "from app.repository import UserRepo\n"
        violations = analyze_imports(source, file_path="controller.py")
        assert len(violations) == 1
        assert "data layer" in violations[0].violation

    def test_controller_importing_model(self) -> None:
        source = "from database.models import User\n"
        violations = analyze_imports(source, file_path="views/controller.py")
        assert len(violations) >= 1

    def test_non_controller_ok(self) -> None:
        source = "from app.repository import UserRepo\n"
        violations = analyze_imports(source, file_path="service.py")
        assert len(violations) == 0

    def test_test_importing_private(self) -> None:
        source = "from app._internal import secret\n"
        violations = analyze_imports(source, file_path="test_app.py")
        assert len(violations) == 1
        assert "private" in violations[0].violation

    def test_no_violations(self) -> None:
        source = "from app.service import UserService\n"
        violations = analyze_imports(source, file_path="controller.py")
        assert len(violations) == 0


class TestAnalyzeAsyncViolations:
    def test_await_in_sync(self) -> None:
        source = "def my_func():\n    result = await some_call()\n"
        violations = analyze_async_violations(source, file_path="app.py")
        assert len(violations) == 1
        assert "sync function" in violations[0].violation

    def test_await_in_async_ok(self) -> None:
        source = "async def my_func():\n    result = await some_call()\n"
        violations = analyze_async_violations(source, file_path="app.py")
        assert len(violations) == 0

    def test_no_await(self) -> None:
        source = "def my_func():\n    return 42\n"
        violations = analyze_async_violations(source, file_path="app.py")
        assert len(violations) == 0


class TestAnalyzePythonFallback:
    def test_analyzes_python_files(self, tmp_path: Path) -> None:
        py_file = tmp_path / "controller.py"
        py_file.write_text("from app.repository import UserRepo\n")
        result = analyze_python_fallback([py_file])
        assert result.files_analyzed == 1

    def test_skips_non_python(self, tmp_path: Path) -> None:
        ts_file = tmp_path / "app.ts"
        ts_file.write_text("import { Repo } from './repo';\n")
        result = analyze_python_fallback([ts_file])
        assert len(result.boundary_violations) == 0

    def test_missing_file(self, tmp_path: Path) -> None:
        result = analyze_python_fallback([tmp_path / "nonexistent.py"])
        assert result.files_analyzed == 1
        assert result.total_violations == 0


class TestJoernAvailability:
    def test_check(self) -> None:
        # Just verify it returns a bool (Joern likely not installed in CI)
        assert isinstance(is_joern_available(), bool)

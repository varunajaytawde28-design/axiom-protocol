"""Tests for tree-sitter architectural pattern analyzers.

Uses regex fallback when tree-sitter is not installed — the regex
analysis is always tested. Tree-sitter-specific tests are skipped
if the optional dependency is not available.
"""

from __future__ import annotations

from pathlib import Path

from vt_protocol.observation.analyzers import (
    AnalysisResult,
    analyze_directory,
    analyze_file,
    extract_imports,
)


class TestPythonAnalysis:
    def test_import_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "example.py"
        f.write_text(
            "import os\n"
            "import json\n"
            "from pathlib import Path\n"
            "from collections import defaultdict, OrderedDict\n"
        )
        result = analyze_file(f)
        assert result.language == "python"
        modules = result.import_modules
        assert "os" in modules
        assert "json" in modules
        assert "pathlib" in modules
        assert "collections" in modules

    def test_from_import(self, tmp_path: Path) -> None:
        f = tmp_path / "example.py"
        f.write_text("from fastapi import FastAPI, Depends\n")
        result = analyze_file(f)
        assert len(result.imports) >= 1
        fastapi_imp = [i for i in result.imports if "fastapi" in i.module]
        assert len(fastapi_imp) == 1
        assert "FastAPI" in fastapi_imp[0].names

    def test_class_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "models.py"
        f.write_text(
            "from pydantic import BaseModel\n\n"
            "class UserCreate(BaseModel):\n"
            "    name: str\n\n"
            "class UserRepo:\n"
            "    pass\n"
        )
        result = analyze_file(f)
        names = [c.name for c in result.classes]
        assert "UserCreate" in names
        assert "UserRepo" in names
        # Check base class detection
        user_create = next(c for c in result.classes if c.name == "UserCreate")
        assert "BaseModel" in user_create.bases

    def test_function_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "routes.py"
        f.write_text(
            "def get_users():\n"
            "    pass\n\n"
            "async def create_user(data):\n"
            "    pass\n"
        )
        result = analyze_file(f)
        names = [fn.name for fn in result.functions]
        assert "get_users" in names
        assert "create_user" in names
        create = next(fn for fn in result.functions if fn.name == "create_user")
        assert create.is_async

    def test_decorator_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "api.py"
        f.write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            "@app.get\n"
            "def index():\n"
            "    return {}\n"
        )
        result = analyze_file(f)
        index_fn = next((fn for fn in result.functions if fn.name == "index"), None)
        assert index_fn is not None
        assert "app.get" in index_fn.decorators

    def test_extract_imports_shortcut(self, tmp_path: Path) -> None:
        f = tmp_path / "example.py"
        f.write_text("import redis\nfrom celery import Celery\n")
        modules = extract_imports(f)
        assert "redis" in modules
        assert "celery" in modules


class TestTypeScriptAnalysis:
    def test_import_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "index.ts"
        f.write_text(
            'import express from "express";\n'
            'import { PrismaClient } from "@prisma/client";\n'
            'import * as fs from "fs";\n'
        )
        result = analyze_file(f)
        assert result.language == "typescript"
        modules = result.import_modules
        assert "express" in modules
        assert "@prisma/client" in modules

    def test_class_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "service.ts"
        f.write_text(
            "export class UserService extends BaseService {\n"
            "  constructor() { super(); }\n"
            "}\n\n"
            "class InternalHelper {\n"
            "  run() {}\n"
            "}\n"
        )
        result = analyze_file(f)
        names = [c.name for c in result.classes]
        assert "UserService" in names
        assert "InternalHelper" in names
        user_svc = next(c for c in result.classes if c.name == "UserService")
        assert "BaseService" in user_svc.bases

    def test_function_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "utils.ts"
        f.write_text(
            "export function formatDate(d: Date): string {\n"
            "  return d.toISOString();\n"
            "}\n\n"
            "export async function fetchData(url: string) {\n"
            "  return fetch(url);\n"
            "}\n"
        )
        result = analyze_file(f)
        names = [fn.name for fn in result.functions]
        assert "formatDate" in names
        assert "fetchData" in names


class TestUnknownLanguage:
    def test_unsupported_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n1,2,3\n")
        result = analyze_file(f)
        assert result.language == "unknown"
        assert result.imports == []

    def test_missing_file(self, tmp_path: Path) -> None:
        result = analyze_file(tmp_path / "nonexistent.py")
        assert result.imports == []


class TestAnalyzeDirectory:
    def test_scans_python_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("import os\n")
        (src / "utils.py").write_text("from pathlib import Path\n")
        (src / "data.txt").write_text("not code")

        results = analyze_directory(tmp_path, extensions=[".py"])
        assert len(results) == 2
        all_modules = []
        for r in results:
            all_modules.extend(r.import_modules)
        assert "os" in all_modules
        assert "pathlib" in all_modules

    def test_skips_venv(self, tmp_path: Path) -> None:
        venv = tmp_path / "venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "site.py").write_text("import sys\n")
        (tmp_path / "main.py").write_text("import os\n")

        results = analyze_directory(tmp_path, extensions=[".py"])
        assert len(results) == 1
        assert results[0].path.endswith("main.py")

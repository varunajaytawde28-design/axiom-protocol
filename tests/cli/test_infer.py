"""Tests for Bug 2: vt infer — detect new imports and create decisions.

Verifies that `vt infer` re-scans source files, compares against existing
decisions, and creates new decision records for newly detected patterns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import _extract_existing_sub_ids, main
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """Create a minimal project with .smm structure and a Python file using sqlite3."""
    root = tmp_path / "myproject"
    root.mkdir()
    (root / ".git").mkdir()
    smm = root / ".smm"
    smm.mkdir()
    (smm / "decisions").mkdir()
    (smm / "contradictions").mkdir()
    (root / "governance.yaml").write_text(
        "extends:\n  - '@vt/recommended'\n"
        "model:\n  provider: none\n  model: ''\n"
        "agents:\n  claude: true\n"
        "rules:\n  freeze-on-adopt: true\n"
    )
    # pyproject.toml so scan_project finds packages
    (root / "pyproject.toml").write_text(
        '[project]\nname = "testproj"\nversion = "0.1.0"\n'
        'dependencies = []\n'
    )
    return root


class TestExtractExistingSubIds:
    def test_extracts_from_detected_prefix(self) -> None:
        d = Decision(
            title="Detected: SQLite Database",
            content="Uses sqlite3.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="vt-init",
            project="test",
        )
        ids = _extract_existing_sub_ids([d])
        assert "database.sqlite" in ids

    def test_extracts_from_label_in_title(self) -> None:
        d = Decision(
            title="Use Relational Database for storage",
            content="PostgreSQL handles all data.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="test",
            project="test",
        )
        ids = _extract_existing_sub_ids([d])
        assert "database.relational" in ids

    def test_empty_decisions_returns_empty(self) -> None:
        assert _extract_existing_sub_ids([]) == set()


class TestVtInfer:
    def test_infer_detects_new_import(self, runner, project_root) -> None:
        """After initial decisions for sqlite3, adding psycopg2 import should create new decision."""
        # Write initial decision for sqlite
        ddir = project_root / ".smm" / "decisions"
        initial = Decision(
            title="Detected: SQLite Database",
            content="This project uses sqlite database via sqlite3.",
            rationale="Auto-detected",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="vt-init",
            project="test",
            source_type=SourceType.SCAN,
        )
        (ddir / "001-database-sqlite.json").write_text(initial.model_dump_json(indent=2))

        # Add a Python file with psycopg2 import
        src = project_root / "src"
        src.mkdir()
        (src / "db.py").write_text("import psycopg2\n\nconn = psycopg2.connect('dbname=test')\n")

        result = runner.invoke(main, ["infer", "--path", str(project_root)])
        assert result.exit_code == 0
        assert "new pattern" in result.output.lower() or "Relational Database" in result.output

        # Verify new decision file was written
        decision_files = list(ddir.glob("*.json"))
        assert len(decision_files) >= 2  # original + new

    def test_infer_no_new_patterns(self, runner, project_root) -> None:
        """If all detected patterns already have decisions, report nothing new."""
        # pyproject.toml triggers "Package Management" detection, so pre-create that decision
        ddir = project_root / ".smm" / "decisions"
        existing = Decision(
            title="Detected: Package Management",
            content="This project uses package management via pyproject.toml.",
            rationale="Auto-detected",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DEPLOYMENT],
            made_by="vt-init",
            project="test",
            source_type=SourceType.SCAN,
        )
        (ddir / "001-arch-package-mgmt.json").write_text(existing.model_dump_json(indent=2))

        result = runner.invoke(main, ["infer", "--path", str(project_root)])
        assert result.exit_code == 0
        assert "already tracked" in result.output

    def test_infer_not_initialized(self, runner, tmp_path) -> None:
        """Running vt infer without vt init should error."""
        bare = tmp_path / "bare"
        bare.mkdir()
        result = runner.invoke(main, ["infer", "--path", str(bare)])
        assert result.exit_code == 1
        assert "not a VT Protocol project" in result.output

    def test_infer_creates_decision_for_new_import(self, runner, project_root) -> None:
        """Full flow: vt init found sqlite3, user adds psycopg2, vt infer picks it up."""
        ddir = project_root / ".smm" / "decisions"

        # Simulate vt init having found sqlite3
        initial = Decision(
            title="Detected: SQLite Database",
            content="This project uses sqlite database via sqlite3.",
            rationale="Auto-detected from project scan. Evidence: import:sqlite3",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="vt-init",
            project="myproject",
            source_type=SourceType.SCAN,
        )
        (ddir / "001-database-sqlite.json").write_text(initial.model_dump_json(indent=2))

        # User adds psycopg2 import to their code
        src = project_root / "src"
        src.mkdir(exist_ok=True)
        (src / "app.py").write_text("import psycopg2\nimport sqlite3\n")

        # Run vt infer
        result = runner.invoke(main, ["infer", "--path", str(project_root)])
        assert result.exit_code == 0

        # Load all decisions and verify psycopg2/relational was added
        decisions = []
        for f in ddir.glob("*.json"):
            decisions.append(json.loads(f.read_text()))

        titles = [d["title"] for d in decisions]
        assert any("Relational Database" in t for t in titles)

    def test_infer_then_check_finds_contradiction(self, runner, project_root) -> None:
        """Integration: vt infer creates new decision, vt check finds the contradiction."""
        ddir = project_root / ".smm" / "decisions"

        # Initial sqlite decision
        initial = Decision(
            title="Detected: SQLite Database",
            content="This project uses sqlite database via sqlite3. Do not introduce PostgreSQL, MongoDB, or any external database without explicit approval.",
            rationale="Auto-detected from project scan. Evidence: import:sqlite3",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="vt-init",
            project="myproject",
            source_type=SourceType.SCAN,
        )
        (ddir / "001-database-sqlite.json").write_text(initial.model_dump_json(indent=2))

        # Add psycopg2 import
        src = project_root / "src"
        src.mkdir(exist_ok=True)
        (src / "app.py").write_text("import psycopg2\n")

        # Run vt infer to detect the new import
        infer_result = runner.invoke(main, ["infer", "--path", str(project_root)])
        assert infer_result.exit_code == 0

        # Run vt check — should detect contradiction between sqlite and pg
        check_result = runner.invoke(main, ["check", "--path", str(project_root), "--exit-code"])
        assert check_result.exit_code == 1
        assert "FAIL" in check_result.output

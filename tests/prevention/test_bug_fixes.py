"""Tests for the three bug fixes: root file writing, all-patterns written, constraint generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    GovernanceConfig,
    SourceType,
)
from vt_protocol.decisions.taxonomy import (
    DimensionMatch,
    SubDimension,
    _extract_detected_libs,
    generate_constraint,
)
from vt_protocol.prevention.rulesync import (
    _GOVERNANCE_END,
    _GOVERNANCE_START,
    _write_root_agent_file,
    _write_root_claude_md,
    sync_rules,
)
from vt_protocol.cli.commands import _write_initial_decisions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_decision(title: str = "Test decision", dim: Dimension = Dimension.DATABASE) -> Decision:
    return Decision(
        title=title,
        content="Test content for this decision that is long enough.",
        rationale="Test rationale",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[dim],
        constraints=["Do not do X"],
        made_by="test",
        project="test-project",
        source_type=SourceType.SCAN,
    )


def _make_sub(sub_id: str, label: str, core: Dimension) -> SubDimension:
    return SubDimension(
        id=sub_id,
        label=label,
        core_dimension=core,
        facet="data",
    )


def _make_match(sub_id: str, label: str, core: Dimension, evidence: list[str]) -> DimensionMatch:
    sub = _make_sub(sub_id, label, core)
    return DimensionMatch(
        sub_dimension=sub,
        core_dimension=core,
        confidence=0.8,
        evidence=evidence,
    )


# ===========================================================================
# Bug 1: Root file writing (rulesync.py)
# ===========================================================================


class TestRootFileWriting:
    """Test that sync_rules writes CLAUDE.md and AGENTS.md to the project root,
    with correct append/replace behaviour for existing files."""

    def test_claude_md_written_to_root(self, tmp_path: Path) -> None:
        """sync_rules with claude enabled creates root CLAUDE.md."""
        config = GovernanceConfig(agents={"claude": True})
        decisions = [_make_decision()]

        # Ensure .smm/generated dir can be created
        result = sync_rules(decisions, tmp_path, config)

        root_claude = tmp_path / "CLAUDE.md"
        assert root_claude.is_file(), "CLAUDE.md was not written to the project root"
        assert root_claude in result.files_written

    def test_claude_md_appends_to_existing(self, tmp_path: Path) -> None:
        """If CLAUDE.md exists with custom content, governance section is appended and custom content preserved."""
        root_claude = tmp_path / "CLAUDE.md"
        custom_content = "# My Project\n\nCustom project instructions here."
        root_claude.write_text(custom_content)

        governance = "## Governance Rules\nRule 1: do not break things."
        _write_root_claude_md(root_claude, governance)

        result_text = root_claude.read_text()
        # Custom content preserved
        assert "# My Project" in result_text
        assert "Custom project instructions here." in result_text
        # Governance section appended between markers
        assert _GOVERNANCE_START in result_text
        assert _GOVERNANCE_END in result_text
        assert governance in result_text
        # Custom content comes before governance markers
        assert result_text.index("# My Project") < result_text.index(_GOVERNANCE_START)

    def test_claude_md_replaces_existing_governance(self, tmp_path: Path) -> None:
        """If CLAUDE.md has governance markers, only that section is replaced."""
        root_claude = tmp_path / "CLAUDE.md"
        old_governance = "Old rules here."
        initial = (
            "# My Project\n\n"
            f"{_GOVERNANCE_START}\n{old_governance}\n{_GOVERNANCE_END}\n\n"
            "# Footer section"
        )
        root_claude.write_text(initial)

        new_governance = "New rules here."
        _write_root_claude_md(root_claude, new_governance)

        result_text = root_claude.read_text()
        assert "# My Project" in result_text
        assert "# Footer section" in result_text
        assert new_governance in result_text
        assert old_governance not in result_text
        # Markers appear exactly once
        assert result_text.count(_GOVERNANCE_START) == 1
        assert result_text.count(_GOVERNANCE_END) == 1

    def test_agents_md_appends_to_existing(self, tmp_path: Path) -> None:
        """If AGENTS.md exists with custom content, governance section is appended."""
        root_agents = tmp_path / "AGENTS.md"
        custom_content = "# Agent Instructions\n\nBe careful with security."
        root_agents.write_text(custom_content)

        governance = "## Generated Governance\nConstraint A."
        _write_root_agent_file(root_agents, governance)

        result_text = root_agents.read_text()
        assert "# Agent Instructions" in result_text
        assert "Be careful with security." in result_text
        assert _GOVERNANCE_START in result_text
        assert _GOVERNANCE_END in result_text
        assert governance in result_text

    def test_agents_md_replaces_existing_governance(self, tmp_path: Path) -> None:
        """If AGENTS.md has governance markers, only that section is replaced."""
        root_agents = tmp_path / "AGENTS.md"
        old_gov = "Stale governance text."
        initial = (
            "# Custom Header\n\n"
            f"{_GOVERNANCE_START}\n{old_gov}\n{_GOVERNANCE_END}\n\n"
            "# Custom Footer"
        )
        root_agents.write_text(initial)

        new_gov = "Fresh governance text."
        _write_root_agent_file(root_agents, new_gov)

        result_text = root_agents.read_text()
        assert "# Custom Header" in result_text
        assert "# Custom Footer" in result_text
        assert new_gov in result_text
        assert old_gov not in result_text
        assert result_text.count(_GOVERNANCE_START) == 1
        assert result_text.count(_GOVERNANCE_END) == 1


# ===========================================================================
# Bug 2: All patterns written (commands.py _write_initial_decisions)
# ===========================================================================


class TestAllPatternsWritten:
    """Test that _write_initial_decisions writes ONE decision per sub-dimension,
    with correct filenames, types, and constraint content."""

    def test_writes_all_subdimensions(self, tmp_path: Path) -> None:
        """4 matches with different sub-dimensions (same core) produce 4 decision files."""
        matches = [
            _make_match("database.relational", "Relational Database", Dimension.DATABASE, ["python:psycopg2"]),
            _make_match("database.sqlite", "SQLite Database", Dimension.DATABASE, ["import:sqlite3"]),
            _make_match("database.orm", "ORM / Data Access", Dimension.DATABASE, ["python:sqlalchemy"]),
            _make_match("data.similarity", "Similarity Detection", Dimension.DATABASE, ["python:datasketch"]),
        ]

        _write_initial_decisions(tmp_path, matches)

        decisions_dir = tmp_path / ".smm" / "decisions"
        written_files = sorted(decisions_dir.glob("*.json"))
        assert len(written_files) == 4

    def test_no_dedup_by_core_dimension(self, tmp_path: Path) -> None:
        """Two matches sharing core dimension DATABASE both get written."""
        matches = [
            _make_match("database.relational", "Relational Database", Dimension.DATABASE, ["python:psycopg2"]),
            _make_match("database.sqlite", "SQLite Database", Dimension.DATABASE, ["import:sqlite3"]),
        ]

        _write_initial_decisions(tmp_path, matches)

        decisions_dir = tmp_path / ".smm" / "decisions"
        written_files = sorted(decisions_dir.glob("*.json"))
        assert len(written_files) == 2

    def test_filename_uses_sub_id(self, tmp_path: Path) -> None:
        """Filenames use the sub-dimension id with dots replaced by dashes."""
        matches = [
            _make_match("database.sqlite", "SQLite Database", Dimension.DATABASE, ["import:sqlite3"]),
            _make_match("data.similarity", "Similarity Detection", Dimension.DATABASE, ["python:datasketch"]),
        ]

        _write_initial_decisions(tmp_path, matches)

        decisions_dir = tmp_path / ".smm" / "decisions"
        filenames = sorted(f.name for f in decisions_dir.glob("*.json"))
        assert "001-database-sqlite.json" in filenames
        assert "002-data-similarity.json" in filenames

    def test_decision_type_is_constraint(self, tmp_path: Path) -> None:
        """Written decisions have decision_type=constraint."""
        matches = [
            _make_match("database.sqlite", "SQLite Database", Dimension.DATABASE, ["import:sqlite3"]),
        ]

        _write_initial_decisions(tmp_path, matches)

        decisions_dir = tmp_path / ".smm" / "decisions"
        filepath = next(decisions_dir.glob("*.json"))
        data = json.loads(filepath.read_text())
        assert data["decision_type"] == DecisionType.CONSTRAINT.value

    def test_decision_has_constraint_field(self, tmp_path: Path) -> None:
        """Written decisions have a populated constraints list."""
        matches = [
            _make_match("database.sqlite", "SQLite Database", Dimension.DATABASE, ["import:sqlite3"]),
        ]

        _write_initial_decisions(tmp_path, matches)

        decisions_dir = tmp_path / ".smm" / "decisions"
        filepath = next(decisions_dir.glob("*.json"))
        data = json.loads(filepath.read_text())
        assert isinstance(data["constraints"], list)
        assert len(data["constraints"]) >= 1
        assert data["constraints"][0]  # non-empty string


# ===========================================================================
# Bug 3: Constraint generation (taxonomy.py generate_constraint)
# ===========================================================================


class TestGenerateConstraint:
    """Test generate_constraint produces correct imperative constraint text."""

    def test_sqlite_constraint(self) -> None:
        """database.sqlite with evidence ['import:sqlite3'] forbids PostgreSQL."""
        sub = _make_sub("database.sqlite", "SQLite Database", Dimension.DATABASE)
        result = generate_constraint(sub, ["import:sqlite3"])

        assert "sqlite3" in result
        assert "PostgreSQL" in result
        assert "Do not introduce" in result

    def test_llm_constraint(self) -> None:
        """integration.llm with multiple providers includes both lib names."""
        sub = _make_sub("integration.llm", "LLM Provider Integration", Dimension.API_STYLE)
        result = generate_constraint(sub, ["python:anthropic", "python:openai"])

        assert "anthropic" in result
        assert "openai" in result
        assert "Do not introduce" in result

    def test_threading_constraint(self) -> None:
        """arch.threading with threading and queue evidence produces constraint."""
        sub = _make_sub("arch.threading", "Threading / Concurrency Primitives", Dimension.CONCURRENCY)
        result = generate_constraint(sub, ["import:threading", "import:queue"])

        assert "threading" in result
        assert "Do not introduce" in result
        # Should mention alternatives from _CONSTRAINT_ALTERNATIVES
        assert "asyncio" in result or "multiprocessing" in result or "Celery" in result

    def test_monkey_patching_constraint(self) -> None:
        """arch.monkey_patching produces a valid constraint."""
        sub = _make_sub("arch.monkey_patching", "Monkey Patching / AOP", Dimension.STATE_MANAGEMENT)
        result = generate_constraint(sub, ["python:wrapt"])

        assert "wrapt" in result
        assert "Do not introduce" in result
        assert "monkey" in result.lower() or "AOP" in result or "patching" in result.lower()

    def test_unknown_subdimension_fallback(self) -> None:
        """SubDimension not in _CONSTRAINT_ALTERNATIVES still produces valid constraint."""
        sub = _make_sub("custom.unknown", "Unknown Widget", Dimension.DATABASE)
        result = generate_constraint(sub, ["python:some_lib"])

        assert "some.lib" in result or "some_lib" in result
        assert "Do not introduce alternative implementations" in result

    def test_extract_detected_libs(self) -> None:
        """_extract_detected_libs handles various evidence tag formats."""
        evidence = [
            "python:flask",
            "import:sqlite3",
            "node:express",
            "config:alembic.ini",
            "dir:migrations",
        ]
        libs = _extract_detected_libs(evidence)

        assert "flask" in libs
        assert "sqlite3" in libs
        assert "express" in libs
        assert "alembic.ini" in libs
        assert "migrations" in libs
        assert len(libs) == 5

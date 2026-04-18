"""Tests for vt validate-change CLI and Claude Code hook integration.

Tests the PreToolUse hook flow: extract content → validate against decisions →
return pass/fail JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import _check_content_against_decisions, main
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
    root = tmp_path / "myproject"
    root.mkdir()
    (root / ".git").mkdir()
    smm = root / ".smm"
    smm.mkdir()
    (smm / "decisions").mkdir()
    (root / "governance.yaml").write_text(
        "extends:\n  - '@vt/recommended'\n"
        "model:\n  provider: none\n  model: ''\n"
        "agents:\n  claude: true\n"
    )
    return root


@pytest.fixture()
def sqlite_decision() -> Decision:
    return Decision(
        title="Detected: SQLite Database",
        content="This project uses sqlite database via sqlite3. Do not introduce PostgreSQL, MongoDB, or any external database without explicit approval.",
        rationale="Auto-detected from project scan. Evidence: import:sqlite3",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.DATABASE],
        constraints=[
            "This project uses sqlite database via sqlite3. Do not introduce PostgreSQL, MongoDB, or any external database without explicit approval."
        ],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


class TestCheckContentAgainstDecisions:
    def test_clean_content_passes(self, sqlite_decision) -> None:
        content = "import sqlite3\n\ndb = sqlite3.connect('test.db')\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert violations == []

    def test_violating_import_detected(self, sqlite_decision) -> None:
        content = "import psycopg2\n\nconn = psycopg2.connect('dbname=test')\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert len(violations) == 1
        assert "psycopg2" in violations[0]["import"]
        assert "SQLite" in violations[0]["decision"]

    def test_no_imports_passes(self, sqlite_decision) -> None:
        content = "x = 1\ny = 2\nprint(x + y)\n"
        violations = _check_content_against_decisions("math.py", content, [sqlite_decision])
        assert violations == []

    def test_same_tech_import_passes(self, sqlite_decision) -> None:
        content = "import sqlite3\nimport json\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert violations == []

    def test_from_import_detected(self, sqlite_decision) -> None:
        content = "from psycopg2 import sql\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert len(violations) == 1

    def test_no_decisions_passes(self) -> None:
        content = "import psycopg2\n"
        violations = _check_content_against_decisions("db.py", content, [])
        assert violations == []


class TestValidateChangeCommand:
    def test_pass_no_violations(self, runner, project_root, sqlite_decision) -> None:
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001.json").write_text(sqlite_decision.model_dump_json(indent=2))

        result = runner.invoke(
            main,
            ["validate-change", "--path", str(project_root),
             "--file-path", "db.py", "--content", "import sqlite3\nx=1"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "pass"

    def test_fail_on_violation(self, runner, project_root, sqlite_decision) -> None:
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001.json").write_text(sqlite_decision.model_dump_json(indent=2))

        result = runner.invoke(
            main,
            ["validate-change", "--path", str(project_root),
             "--file-path", "db.py", "--content", "import psycopg2"],
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"
        assert len(data["violations"]) >= 1

    def test_pass_no_project(self, runner, tmp_path) -> None:
        bare = tmp_path / "bare"
        bare.mkdir()

        result = runner.invoke(
            main,
            ["validate-change", "--path", str(bare),
             "--file-path", "x.py", "--content", "import psycopg2"],
        )
        # No project → pass (don't block)
        assert result.exit_code == 0

    def test_pass_no_decisions(self, runner, project_root) -> None:
        result = runner.invoke(
            main,
            ["validate-change", "--path", str(project_root),
             "--file-path", "x.py", "--content", "import psycopg2"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "pass"

    def test_stdin_content(self, runner, project_root, sqlite_decision) -> None:
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001.json").write_text(sqlite_decision.model_dump_json(indent=2))

        result = runner.invoke(
            main,
            ["validate-change", "--path", str(project_root), "--file-path", "db.py"],
            input="import psycopg2\n",
        )
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"


class TestAssumptionsNeverBlock:
    """Assumptions must NEVER cause validate-change to block.

    Only decisions and contradictions drive blocking behaviour.
    Assumptions with any status (proposed, detected, validated, rejected)
    must be completely ignored by the PreToolUse validation path.
    """

    def test_proposed_assumption_does_not_block(
        self, runner, project_root, sqlite_decision
    ) -> None:
        """A PROPOSED assumption must not block a write that violates it."""
        from vt_protocol.decisions.models import DomainAssumption, AssumptionStatus, AssumptionCategory

        # Write a proposed assumption that overlaps with the import we're testing
        assumptions_dir = project_root / ".smm" / "assumptions"
        assumptions_dir.mkdir(parents=True, exist_ok=True)
        assumption = DomainAssumption(
            category=AssumptionCategory.DATA_SCOPE,
            status=AssumptionStatus.PROPOSED,
            pattern_id="single_db_write",
            summary="Project assumes a single database technology",
            question="Which best describes the database strategy?",
            options=["Only SQLite (correct)", "Multiple databases needed"],
            severity="high",
        )
        (assumptions_dir / "test.json").write_text(assumption.model_dump_json(indent=2))

        # Content that imports psycopg2 — would "violate" the assumption conceptually,
        # but validate-change must only check decisions, so this passes.
        result = runner.invoke(
            main,
            ["validate-change", "--path", str(project_root),
             "--file-path", "db.py", "--content", "import psycopg2"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "pass"

    def test_validated_assumption_does_not_block(
        self, runner, project_root
    ) -> None:
        """A VALIDATED assumption must not block writes either."""
        from vt_protocol.decisions.models import DomainAssumption, AssumptionStatus, AssumptionCategory

        assumptions_dir = project_root / ".smm" / "assumptions"
        assumptions_dir.mkdir(parents=True, exist_ok=True)
        assumption = DomainAssumption(
            category=AssumptionCategory.DATA_SCOPE,
            status=AssumptionStatus.VALIDATED,
            pattern_id="single_db_write",
            summary="Project uses only SQLite",
            selected_option=0,
            resolved_by="human",
            severity="high",
        )
        (assumptions_dir / "test.json").write_text(assumption.model_dump_json(indent=2))

        # No decisions on disk — assumptions alone must never block
        result = runner.invoke(
            main,
            ["validate-change", "--path", str(project_root),
             "--file-path", "db.py", "--content", "import psycopg2"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "pass"

    def test_decision_still_blocks_when_assumption_present(
        self, runner, project_root, sqlite_decision
    ) -> None:
        """When a decision and a proposed assumption both exist, only the
        decision drives blocking — the assumption is informational only."""
        from vt_protocol.decisions.models import DomainAssumption, AssumptionStatus, AssumptionCategory

        # Write the blocking decision
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001.json").write_text(sqlite_decision.model_dump_json(indent=2))

        # Also write an unresolved assumption
        assumptions_dir = project_root / ".smm" / "assumptions"
        assumptions_dir.mkdir(parents=True, exist_ok=True)
        assumption = DomainAssumption(
            category=AssumptionCategory.DATA_SCOPE,
            status=AssumptionStatus.PROPOSED,
            pattern_id="single_db_write",
            summary="Project assumes single database technology",
            severity="high",
        )
        (assumptions_dir / "a.json").write_text(assumption.model_dump_json(indent=2))

        result = runner.invoke(
            main,
            ["validate-change", "--path", str(project_root),
             "--file-path", "db.py", "--content", "import psycopg2"],
        )
        # Decision blocks — exit code 1
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"
        # The violation references the decision, not the assumption
        assert any("SQLite" in v["decision"] for v in data["violations"])

    def test_check_content_ignores_assumptions_list(self, sqlite_decision) -> None:
        """_check_content_against_decisions only takes a decisions list — no
        assumption parameter exists, so assumptions can never be passed in."""
        import inspect
        sig = inspect.signature(_check_content_against_decisions)
        param_names = list(sig.parameters.keys())
        assert "assumptions" not in param_names
        assert "assumption" not in param_names


class TestClaudeCodeHookInstall:
    def test_install_creates_hook_and_settings(self, tmp_path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()

        installed = install_claude_code_hook(root)
        assert installed is True

        hook_path = root / ".claude" / "hooks" / "vt-validate.sh"
        assert hook_path.exists()
        assert hook_path.stat().st_mode & 0o111  # executable

        settings_path = root / ".claude" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert "hooks" in data
        assert "PreToolUse" in data["hooks"]
        assert data["hooks"]["PreToolUse"][0]["matcher"] == "Write|Edit"

    def test_install_idempotent(self, tmp_path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()

        assert install_claude_code_hook(root) is True
        assert install_claude_code_hook(root) is False  # already installed

    def test_install_merges_existing_settings(self, tmp_path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        claude_dir = root / ".claude"
        claude_dir.mkdir()

        # Pre-existing settings with permissions
        existing = {"permissions": {"allow": ["Bash(git:*)"]}}
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        install_claude_code_hook(root)

        data = json.loads((claude_dir / "settings.json").read_text())
        # Both original permissions and new hooks should exist
        assert "permissions" in data
        assert "hooks" in data
        assert data["hooks"]["PreToolUse"][0]["matcher"] == "Write|Edit"

    def test_vt_init_installs_hook(self, runner, tmp_path) -> None:
        root = tmp_path / "proj"
        root.mkdir()
        (root / ".git").mkdir()

        result = runner.invoke(
            main,
            ["init", "--path", str(root), "--no-llm-prompt", "--no-agent-prompt"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Claude Code PreToolUse hook" in result.output

        hook_path = root / ".claude" / "hooks" / "vt-validate.sh"
        assert hook_path.exists()

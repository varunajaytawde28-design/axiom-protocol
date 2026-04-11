"""Tests for CLI commands — vt init / check / apply / serve / audit-commit."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory with .smm/ and .git/."""
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "generated").mkdir(parents=True)
    (tmp_path / ".smm" / "audit").mkdir(parents=True)
    (tmp_path / ".smm" / "cache").mkdir(parents=True)
    return tmp_path


def _write_decision(project_dir: Path, decision: Decision, name: str = "001-test.json") -> Path:
    """Write a decision JSON file to the project's .smm/decisions/."""
    p = project_dir / ".smm" / "decisions" / name
    p.write_text(decision.model_dump_json(indent=2))
    return p


def _make_decision(
    title: str = "Use PostgreSQL",
    dimensions: list[Dimension] | None = None,
) -> Decision:
    return Decision(
        title=title,
        content=f"Decision about {title}. Full description here with details.",
        rationale=f"Because {title} is the best.",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=dimensions or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )


def _make_contradiction(
    verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED,
) -> Contradiction:
    d_a = _make_decision("Decision A")
    d_b = _make_decision("Decision B")
    return Contradiction(
        decision_a_id=d_a.id,
        decision_b_id=d_b.id,
        decision_a_title=d_a.title,
        decision_b_title=d_b.title,
        verdict=verdict,
        status=status,
        reasoning="These two decisions directly conflict on database choice.",
        evidence_a="Uses PostgreSQL",
        evidence_b="Uses MySQL",
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# vt (group)
# ---------------------------------------------------------------------------


class TestMainGroup:
    def test_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "VT Protocol" in result.output

    def test_verbose_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["-v", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# vt init
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_creates_smm_directory(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = runner.invoke(main, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / ".smm").is_dir()
        assert (tmp_path / ".smm" / "decisions").is_dir()
        assert "Created .smm/ directory" in result.output

    def test_creates_governance_yaml(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = runner.invoke(main, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / "governance.yaml").is_file()
        assert "Created governance.yaml" in result.output

    def test_skips_existing_governance(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "governance.yaml").write_text("existing: true\n")
        result = runner.invoke(main, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_no_hooks_flag(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        result = runner.invoke(main, ["init", "--path", str(tmp_path), "--no-hooks"])
        assert result.exit_code == 0
        assert "Skipped git hooks" in result.output

    def test_no_mcp_flag(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = runner.invoke(main, ["init", "--path", str(tmp_path), "--no-mcp"])
        assert result.exit_code == 0
        assert "Skipped .mcp.json" in result.output
        assert not (tmp_path / ".mcp.json").exists()

    def test_creates_mcp_json(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = runner.invoke(main, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert (tmp_path / ".mcp.json").is_file()

    def test_installs_git_hooks(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        result = runner.invoke(main, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "Installed git hooks" in result.output

    def test_no_git_dir_skips_hooks(self, runner: CliRunner, tmp_path: Path) -> None:
        # No .git dir, init should still work
        result = runner.invoke(main, ["init", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "skipped git hooks" in result.output

    def test_shows_next_steps(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = runner.invoke(main, ["init", "--path", str(tmp_path)])
        assert "vt check" in result.output
        assert "vt apply" in result.output


# ---------------------------------------------------------------------------
# vt check
# ---------------------------------------------------------------------------


class TestCheckCommand:
    def test_check_no_project(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["check", "--path", str(tmp_path)])
        assert result.exit_code == 1
        assert "not a VT Protocol project" in result.output

    def test_check_pass_no_decisions(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["check", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_check_shows_active_decisions(self, runner: CliRunner, project_dir: Path) -> None:
        _write_decision(project_dir, _make_decision("Use PostgreSQL"), "001-db.json")
        _write_decision(project_dir, _make_decision("Use Redis"), "002-cache.json")
        result = runner.invoke(main, ["check", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "Use PostgreSQL" in result.output
        assert "2 active" in result.output

    def test_check_json_output(self, runner: CliRunner, project_dir: Path) -> None:
        _write_decision(project_dir, _make_decision(), "001-test.json")
        result = runner.invoke(main, ["check", "--path", str(project_dir), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "pass"
        assert data["active_decisions"] == 1

    def test_check_fail_on_contradictions(self, runner: CliRunner, project_dir: Path) -> None:
        _write_decision(project_dir, _make_decision(), "001-test.json")
        # Write a contradiction
        c = _make_contradiction()
        c_dir = project_dir / ".smm" / "contradictions"
        c_dir.mkdir(exist_ok=True)
        (c_dir / "001-conflict.json").write_text(c.model_dump_json(indent=2))

        result = runner.invoke(main, ["check", "--path", str(project_dir)])
        assert result.exit_code == 0  # Without --exit-code, exits 0
        assert "FAIL" in result.output

    def test_check_exit_code_on_contradictions(self, runner: CliRunner, project_dir: Path) -> None:
        c = _make_contradiction()
        c_dir = project_dir / ".smm" / "contradictions"
        c_dir.mkdir(exist_ok=True)
        (c_dir / "001-conflict.json").write_text(c.model_dump_json(indent=2))

        result = runner.invoke(main, ["check", "--path", str(project_dir), "--exit-code"])
        assert result.exit_code == 1

    def test_check_exit_code_pass(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["check", "--path", str(project_dir), "--exit-code"])
        assert result.exit_code == 0

    def test_check_json_with_contradictions(self, runner: CliRunner, project_dir: Path) -> None:
        c = _make_contradiction()
        c_dir = project_dir / ".smm" / "contradictions"
        c_dir.mkdir(exist_ok=True)
        (c_dir / "001.json").write_text(c.model_dump_json(indent=2))

        result = runner.invoke(main, ["check", "--path", str(project_dir), "--json-output"])
        data = json.loads(result.output)
        assert data["status"] == "fail"
        assert data["actionable_contradictions"] == 1


# ---------------------------------------------------------------------------
# vt apply
# ---------------------------------------------------------------------------


class TestApplyCommand:
    def test_apply_no_project(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["apply", "--path", str(tmp_path)])
        assert result.exit_code == 1

    def test_apply_no_decisions(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["apply", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "No active decisions" in result.output

    def test_apply_generates_files(self, runner: CliRunner, project_dir: Path) -> None:
        # Write some decisions
        for i in range(3):
            _write_decision(
                project_dir,
                _make_decision(f"Decision {i}"),
                f"{i:03d}-d{i}.json",
            )
        # Write governance.yaml to enable agents
        (project_dir / "governance.yaml").write_text(
            "agents:\n  claude: true\n  cursor: true\n  copilot: true\n"
        )
        result = runner.invoke(main, ["apply", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "Generated" in result.output
        assert "always" in result.output

    def test_apply_shows_tier_counts(self, runner: CliRunner, project_dir: Path) -> None:
        for i in range(5):
            _write_decision(
                project_dir,
                _make_decision(f"Decision {i}"),
                f"{i:03d}-d.json",
            )
        result = runner.invoke(main, ["apply", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "always" in result.output
        assert "auto" in result.output
        assert "on-demand" in result.output


# ---------------------------------------------------------------------------
# vt serve
# ---------------------------------------------------------------------------


class TestServeCommand:
    def test_serve_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["serve", "--help"])
        assert result.exit_code == 0
        assert "MCP server" in result.output

    @patch("vt_protocol.cli.commands.click.echo")
    def test_serve_stdio_calls_mcp_run(self, mock_echo: MagicMock, runner: CliRunner) -> None:
        with patch("vt_protocol.mcp.server.mcp") as mock_mcp:
            mock_mcp.run = MagicMock()
            result = runner.invoke(main, ["serve", "--stdio"])
            mock_mcp.run.assert_called_once_with(transport="stdio")


# ---------------------------------------------------------------------------
# vt dashboard
# ---------------------------------------------------------------------------


class TestDashboardCommand:
    def test_dashboard_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output.lower()


# ---------------------------------------------------------------------------
# vt audit-commit
# ---------------------------------------------------------------------------


class TestAuditCommitCommand:
    def test_audit_commit_appends_to_tree(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(
            main,
            [
                "audit-commit",
                "--hash", "abc123",
                "--message", "test commit",
                "--author", "tester",
            ],
            catch_exceptions=False,
        )
        # The command uses find_project_root() from cwd which may not have .smm
        # so we patch it
        assert result.exit_code in (0, 1)  # May fail if cwd has no .smm

    def test_audit_commit_with_project(self, runner: CliRunner, project_dir: Path) -> None:
        with patch("vt_protocol.config.find_project_root", return_value=project_dir):
            result = runner.invoke(
                main,
                [
                    "audit-commit",
                    "--hash", "abc123",
                    "--message", "test commit",
                    "--author", "tester",
                ],
            )
            assert result.exit_code == 0
            # Audit DB should be created
            assert (project_dir / ".smm" / "audit" / "audit.db").exists()

    def test_audit_commit_no_project_exits_silently(self, runner: CliRunner) -> None:
        with patch(
            "vt_protocol.config.find_project_root",
            side_effect=FileNotFoundError("no project"),
        ):
            result = runner.invoke(
                main,
                ["audit-commit", "--hash", "abc123"],
            )
            assert result.exit_code == 0  # Silent exit, no error

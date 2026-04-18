"""Tests for vt resolve CLI command and vt check --resolve flag."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Minimal VT project directory."""
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
    (tmp_path / ".smm" / "generated").mkdir(parents=True)
    (tmp_path / ".smm" / "audit").mkdir(parents=True)
    (tmp_path / ".smm" / "cache").mkdir(parents=True)
    # Governance config
    (tmp_path / ".smm" / "governance.yaml").write_text(
        "agents:\n  claude: true\n  cursor: false\n"
    )
    return tmp_path


def _make_decision(
    title: str,
    dimensions: list[Dimension] | None = None,
) -> Decision:
    return Decision(
        title=title,
        content=f"This project uses {title}. Full description.",
        rationale=f"Because {title} was chosen.",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=dimensions or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )


def _write_decision(
    project_dir: Path,
    decision: Decision,
    filename: str,
) -> Path:
    p = project_dir / ".smm" / "decisions" / filename
    p.write_text(decision.model_dump_json(indent=2))
    return p


def _write_contradiction(
    project_dir: Path,
    contradiction: Contradiction,
    filename: str,
) -> Path:
    p = project_dir / ".smm" / "contradictions" / filename
    p.write_text(contradiction.model_dump_json(indent=2))
    return p


def _setup_graphql_vs_rest(project_dir: Path) -> tuple[Decision, Decision, Contradiction]:
    """Create the canonical GraphQL vs REST test scenario."""
    d_graphql = _make_decision("Use GraphQL", [Dimension.API_STYLE])
    d_rest = _make_decision("Use REST with FastAPI", [Dimension.API_STYLE])

    _write_decision(project_dir, d_graphql, "001-api-graphql.json")
    _write_decision(project_dir, d_rest, "002-api-rest.json")

    c = Contradiction(
        decision_a_id=d_graphql.id,
        decision_b_id=d_rest.id,
        decision_a_title=d_graphql.title,
        decision_b_title=d_rest.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="GraphQL and REST are different API paradigms.",
        evidence_a="Uses GraphQL",
        evidence_b="Uses REST",
        shared_dimensions=[Dimension.API_STYLE],
        confidence=0.8,
        status=ContradictionStatus.UNRESOLVED,
    )
    _write_contradiction(project_dir, c, "contradiction-001.json")

    return d_graphql, d_rest, c


class TestResolveCommand:
    """Tests for `vt resolve`."""

    def test_no_unresolved_shows_all_clear(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        result = runner.invoke(main, ["resolve", "--path", str(project_dir)])
        assert result.exit_code == 0
        assert "No unresolved contradictions" in result.output

    def test_pick_a_supersedes_b(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        d_graphql, d_rest, c = _setup_graphql_vs_rest(project_dir)

        # Choose A (GraphQL wins)
        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="A\n",
        )
        assert result.exit_code == 0
        assert "supersede" in result.output.lower() or "Resolved" in result.output

        # Verify REST decision is superseded on disk
        rest_data = json.loads(
            (project_dir / ".smm" / "decisions" / "002-api-rest.json").read_text()
        )
        assert rest_data["status"] == "superseded"
        assert rest_data["valid"] is False

        # GraphQL should still be active
        graphql_data = json.loads(
            (project_dir / ".smm" / "decisions" / "001-api-graphql.json").read_text()
        )
        assert graphql_data["status"] == "active"
        assert graphql_data["valid"] is True

    def test_pick_b_supersedes_a(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        d_graphql, d_rest, c = _setup_graphql_vs_rest(project_dir)

        # Choose B (REST wins)
        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="B\n",
        )
        assert result.exit_code == 0

        # Verify GraphQL decision is superseded on disk
        graphql_data = json.loads(
            (project_dir / ".smm" / "decisions" / "001-api-graphql.json").read_text()
        )
        assert graphql_data["status"] == "superseded"
        assert graphql_data["valid"] is False

        # REST should still be active
        rest_data = json.loads(
            (project_dir / ".smm" / "decisions" / "002-api-rest.json").read_text()
        )
        assert rest_data["status"] == "active"
        assert rest_data["valid"] is True

    def test_accept_exception_keeps_both(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        d_graphql, d_rest, c = _setup_graphql_vs_rest(project_dir)

        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="C\n",
        )
        assert result.exit_code == 0
        assert "exception" in result.output.lower()

        # Both decisions should remain active
        for fname in ["001-api-graphql.json", "002-api-rest.json"]:
            data = json.loads(
                (project_dir / ".smm" / "decisions" / fname).read_text()
            )
            assert data["status"] == "active"
            assert data["valid"] is True

        # Contradiction should be marked ignored
        c_data = json.loads(
            (project_dir / ".smm" / "contradictions" / "contradiction-001.json").read_text()
        )
        assert c_data["status"] == "ignored"

    def test_skip_leaves_unresolved(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="S\n",
        )
        assert result.exit_code == 0
        assert "Skipped" in result.output
        assert "No contradictions resolved" in result.output

        # Contradiction should remain unresolved
        c_data = json.loads(
            (project_dir / ".smm" / "contradictions" / "contradiction-001.json").read_text()
        )
        assert c_data["status"] == "unresolved"

    def test_auto_runs_apply_after_resolution(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="A\n",
        )
        assert result.exit_code == 0
        assert "Running vt apply" in result.output
        assert "Generated" in result.output

    def test_no_duplicate_decision_files(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        """Resolving should overwrite existing files, not create UUID-named ones."""
        _setup_graphql_vs_rest(project_dir)

        runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="A\n",
        )

        files = list((project_dir / ".smm" / "decisions").glob("*.json"))
        assert len(files) == 2  # No extra file created

    def test_contradiction_status_persisted(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="A\n",
        )

        c_data = json.loads(
            (project_dir / ".smm" / "contradictions" / "contradiction-001.json").read_text()
        )
        assert c_data["status"] == "resolved"
        assert c_data["resolved_by"] == "cli-user"

    def test_multiple_contradictions(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        """Resolve two contradictions in one session."""
        d_graphql, d_rest, c1 = _setup_graphql_vs_rest(project_dir)

        # Second contradiction: database
        d_pg = _make_decision("Use PostgreSQL", [Dimension.DATABASE])
        d_mysql = _make_decision("Use MySQL", [Dimension.DATABASE])
        _write_decision(project_dir, d_pg, "003-db-postgres.json")
        _write_decision(project_dir, d_mysql, "004-db-mysql.json")

        c2 = Contradiction(
            decision_a_id=d_pg.id,
            decision_b_id=d_mysql.id,
            decision_a_title=d_pg.title,
            decision_b_title=d_mysql.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="PostgreSQL vs MySQL.",
            evidence_a="Uses PostgreSQL",
            evidence_b="Uses MySQL",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.8,
            status=ContradictionStatus.UNRESOLVED,
        )
        _write_contradiction(project_dir, c2, "contradiction-002.json")

        # Resolve both: A for first, B for second
        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="A\nB\n",
        )
        assert result.exit_code == 0
        assert "Resolved 2 contradiction(s)" in result.output

    def test_ignores_already_resolved(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        """Already-resolved contradictions are not shown."""
        d_graphql = _make_decision("Use GraphQL", [Dimension.API_STYLE])
        d_rest = _make_decision("Use REST", [Dimension.API_STYLE])
        _write_decision(project_dir, d_graphql, "001-api-graphql.json")
        _write_decision(project_dir, d_rest, "002-api-rest.json")

        c = Contradiction(
            decision_a_id=d_graphql.id,
            decision_b_id=d_rest.id,
            decision_a_title=d_graphql.title,
            decision_b_title=d_rest.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="Already resolved.",
            evidence_a="GraphQL",
            evidence_b="REST",
            shared_dimensions=[Dimension.API_STYLE],
            confidence=0.8,
            status=ContradictionStatus.RESOLVED,
        )
        _write_contradiction(project_dir, c, "contradiction-001.json")

        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
        )
        assert result.exit_code == 0
        assert "No unresolved contradictions" in result.output


class TestDeferOption:
    """Tests for the D) Defer option in `vt resolve`."""

    def test_defer_marks_contradiction_deferred(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _, _, c = _setup_graphql_vs_rest(project_dir)

        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="D\n",
        )
        assert result.exit_code == 0

        # _save_contradiction_file writes to canonical contradiction-{uuid[:8]}.json
        canonical = project_dir / ".smm" / "contradictions" / f"contradiction-{str(c.id)[:8]}.json"
        c_data = json.loads(canonical.read_text())
        assert c_data["status"] == "deferred"
        assert c_data["resolved_by"] == "cli-user"

    def test_defer_prints_message(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="D\n",
        )
        assert result.exit_code == 0
        assert "Deferred" in result.output
        assert "Agent unblocked" in result.output

    def test_defer_does_not_run_apply(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        result = runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="D\n",
        )
        assert result.exit_code == 0
        assert "Running vt apply" not in result.output

    def test_defer_deletes_lock(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        # Create a lock file to simulate blocked state
        lock = project_dir / ".smm" / "contradiction.lock"
        lock.write_text('{"contradiction_id": "abc"}')

        runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="D\n",
        )

        assert not lock.exists(), "Lock file should be deleted after defer"

    def test_defer_decisions_remain_active(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="D\n",
        )

        # Neither decision should be superseded — defer doesn't pick a winner
        for fname in ["001-api-graphql.json", "002-api-rest.json"]:
            data = json.loads(
                (project_dir / ".smm" / "decisions" / fname).read_text()
            )
            assert data["status"] == "active"
            assert data["valid"] is True

    def test_defer_then_still_counts_as_unresolved_in_check(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        """After deferring, vt check still reports the contradiction (not PASS)."""
        _setup_graphql_vs_rest(project_dir)

        runner.invoke(
            main, ["resolve", "--path", str(project_dir)],
            input="D\n",
        )

        # vt check should not block on deferred (is_actionable is False for DEFERRED)
        check_result = runner.invoke(main, ["check", "--path", str(project_dir)])
        assert check_result.exit_code == 0


class TestCheckWithResolve:
    """Tests for `vt check --resolve`."""

    def test_check_resolve_triggers_interactive(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        """--resolve flag should enter interactive mode when contradictions found."""
        d_graphql = _make_decision("Use GraphQL API", [Dimension.API_STYLE])
        d_rest = _make_decision("Use REST API via FastAPI", [Dimension.API_STYLE])
        d_graphql.content = "This project uses GraphQL for the API layer."
        d_rest.content = "This project uses REST via fastapi for the API layer."
        _write_decision(project_dir, d_graphql, "001-api-graphql.json")
        _write_decision(project_dir, d_rest, "002-api-rest.json")

        # Write a pre-existing contradiction so we don't rely on heuristic
        c = Contradiction(
            decision_a_id=d_graphql.id,
            decision_b_id=d_rest.id,
            decision_a_title=d_graphql.title,
            decision_b_title=d_rest.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="GraphQL vs REST.",
            evidence_a="GraphQL",
            evidence_b="REST",
            shared_dimensions=[Dimension.API_STYLE],
            confidence=0.8,
            status=ContradictionStatus.UNRESOLVED,
        )
        _write_contradiction(project_dir, c, "contradiction-001.json")

        result = runner.invoke(
            main, ["check", "--path", str(project_dir), "--resolve"],
            input="A\n",
        )
        assert result.exit_code == 0
        assert "contradiction(s) to resolve" in result.output

    def test_check_resolve_no_contradictions(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        """--resolve with no contradictions just shows the normal check output."""
        d = _make_decision("Use PostgreSQL", [Dimension.DATABASE])
        _write_decision(project_dir, d, "001-db.json")

        result = runner.invoke(
            main, ["check", "--path", str(project_dir), "--resolve"],
        )
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_check_resolve_auto_applies(
        self, runner: CliRunner, project_dir: Path
    ) -> None:
        _setup_graphql_vs_rest(project_dir)

        result = runner.invoke(
            main, ["check", "--path", str(project_dir), "--resolve"],
            input="B\n",
        )
        assert result.exit_code == 0
        assert "Running vt apply" in result.output


class TestSaveDecisionCli:
    """Test the CLI _save_decision helper."""

    def test_overwrites_existing_file(self, project_dir: Path) -> None:
        from vt_protocol.cli.commands import _save_decision

        d = _make_decision("Use PostgreSQL", [Dimension.DATABASE])
        _write_decision(project_dir, d, "001-database.json")

        # Modify and save
        d.status = DecisionStatus.SUPERSEDED
        d.valid = False
        _save_decision(project_dir, d)

        data = json.loads(
            (project_dir / ".smm" / "decisions" / "001-database.json").read_text()
        )
        assert data["status"] == "superseded"
        assert data["valid"] is False

    def test_no_duplicate_file(self, project_dir: Path) -> None:
        from vt_protocol.cli.commands import _save_decision

        d = _make_decision("Use PostgreSQL", [Dimension.DATABASE])
        _write_decision(project_dir, d, "001-database.json")

        d.status = DecisionStatus.SUPERSEDED
        _save_decision(project_dir, d)

        files = list((project_dir / ".smm" / "decisions").glob("*.json"))
        assert len(files) == 1


class TestSaveContradictionFileCli:
    """Test the CLI _save_contradiction_file helper."""

    def test_overwrites_existing(self, project_dir: Path) -> None:
        from vt_protocol.cli.commands import _save_contradiction_file

        c = Contradiction(
            decision_a_id=_make_decision("A").id,
            decision_b_id=_make_decision("B").id,
            decision_a_title="A",
            decision_b_title="B",
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="Conflict.",
            evidence_a="A",
            evidence_b="B",
            confidence=0.8,
            status=ContradictionStatus.UNRESOLVED,
        )
        _write_contradiction(project_dir, c, "contradiction-001.json")

        c.status = ContradictionStatus.RESOLVED
        _save_contradiction_file(project_dir, c)

        data = json.loads(
            (project_dir / ".smm" / "contradictions" / "contradiction-001.json").read_text()
        )
        assert data["status"] == "resolved"

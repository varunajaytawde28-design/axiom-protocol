"""Tests for `vt gate` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

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
    (smm / "contradictions").mkdir()
    (root / "governance.yaml").write_text(
        "extends:\n  - '@vt/recommended'\nagents:\n  claude: true\nrules:\n  freeze-on-adopt: true\n"
    )
    return root


def _write_decision(
    root: Path,
    title: str = "Test",
    *,
    dims: list[str] | None = None,
    rationale: str = "Good reason",
    index: int = 1,
) -> Decision:
    d = Decision(
        title=title,
        content="Content",
        rationale=rationale,
        dimensions=[Dimension(d) for d in (dims if dims is not None else ["database"])],
        made_by="test",
        project="myproject",
        source_type=SourceType.MANUAL,
    )
    filepath = root / ".smm" / "decisions" / f"{index:03d}.json"
    filepath.write_text(d.model_dump_json(indent=2))
    return d


def _write_contradiction(root: Path, d1: Decision, d2: Decision) -> Contradiction:
    c = Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="They conflict",
        evidence_a="A says X",
        evidence_b="B says Y",
        confidence=0.85,
    )
    filepath = root / ".smm" / "contradictions" / f"{str(c.id)[:8]}.json"
    filepath.write_text(c.model_dump_json(indent=2))
    return c


class TestGateCommand:
    def test_gate_passes_clean_project(self, runner: CliRunner, project_root: Path) -> None:
        _write_decision(project_root, "Use PostgreSQL")
        result = runner.invoke(main, ["gate", "--path", str(project_root)])
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_gate_fails_on_contradiction(self, runner: CliRunner, project_root: Path) -> None:
        d1 = _write_decision(project_root, "Use PostgreSQL", index=1)
        d2 = _write_decision(project_root, "Use MongoDB", index=2)
        _write_contradiction(project_root, d1, d2)

        result = runner.invoke(main, ["gate", "--path", str(project_root)])
        assert result.exit_code == 1
        assert "FAIL" in result.output

    def test_gate_fails_on_missing_dimensions(self, runner: CliRunner, project_root: Path) -> None:
        _write_decision(project_root, "Vague Decision", dims=[])
        result = runner.invoke(main, ["gate", "--path", str(project_root)])
        assert result.exit_code == 1

    def test_gate_json_output(self, runner: CliRunner, project_root: Path) -> None:
        _write_decision(project_root, "Good Decision")
        result = runner.invoke(main, ["gate", "--path", str(project_root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["passed"] is True
        assert data["checks_run"] == 2

    def test_gate_json_with_violations(self, runner: CliRunner, project_root: Path) -> None:
        d1 = _write_decision(project_root, "Decision A", index=1)
        d2 = _write_decision(project_root, "Decision B", index=2)
        _write_contradiction(project_root, d1, d2)

        result = runner.invoke(main, ["gate", "--path", str(project_root), "--json-output"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["passed"] is False
        assert len(data["errors"]) >= 1

    def test_gate_no_project(self, runner: CliRunner, tmp_path: Path) -> None:
        bare = tmp_path / "bare"
        bare.mkdir()
        result = runner.invoke(main, ["gate", "--path", str(bare)])
        assert result.exit_code == 1
        assert "not a VT Protocol project" in result.output

    def test_gate_empty_project(self, runner: CliRunner, project_root: Path) -> None:
        result = runner.invoke(main, ["gate", "--path", str(project_root)])
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_gate_shows_check_counts(self, runner: CliRunner, project_root: Path) -> None:
        _write_decision(project_root, "Decision")
        result = runner.invoke(main, ["gate", "--path", str(project_root)])
        assert "2/2 passed" in result.output

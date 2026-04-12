"""Gemini Chaos: Corrupted governance.yaml and Malformed Inputs.

Tests resilience when governance.yaml is corrupted, decision files
contain invalid JSON, and .smm/ structure is partially missing.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.config import (
    DEFAULT_GOVERNANCE_YAML,
    ensure_smm_structure,
    load_governance_config,
)

from tests.helpers.repo_factory import create_project, write_decision
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

pytestmark = pytest.mark.chaos


def _decision(title: str = "Test Decision") -> Decision:
    return Decision(
        title=title,
        content="Sufficient content for testing corrupted scenarios.",
        rationale="Test rationale",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.DATABASE],
        made_by="test-agent",
        project="chaos-test",
        source_type=SourceType.AGENT,
    )


class TestCorruptedGovernanceYaml:
    """governance.yaml corruption scenarios."""

    def test_missing_governance_yaml(self, tmp_path):
        """Project without governance.yaml — CLI check should still work."""
        root = create_project(tmp_path)
        (root / "governance.yaml").unlink()

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        # Should not crash — may report zero decisions
        data = json.loads(result.output)
        assert "status" in data

    def test_empty_governance_yaml(self, tmp_path):
        """Empty governance.yaml — should use defaults or report gracefully."""
        root = create_project(tmp_path)
        (root / "governance.yaml").write_text("")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data

    def test_invalid_yaml_syntax(self, tmp_path):
        """governance.yaml with broken YAML syntax — CLI exits non-zero."""
        root = create_project(tmp_path)
        (root / "governance.yaml").write_text("{{{{invalid yaml: [missing close")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        # CLI raises GovernanceConfigError → exit code 1
        assert result.exit_code != 0

    def test_yaml_with_unexpected_types(self, tmp_path):
        """governance.yaml with wrong value types — CLI exits non-zero."""
        root = create_project(tmp_path)
        (root / "governance.yaml").write_text(
            "extends: not-a-list\n"
            "agents: also-not-a-dict\n"
            "rules:\n"
            "  freeze_on_adopt: maybe\n"
        )

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        # Invalid types cause validation error → exit code 1
        assert result.exit_code != 0


class TestCorruptedDecisionFiles:
    """Malformed decision JSON files in .smm/decisions/."""

    def test_invalid_json_in_decisions_dir(self, tmp_path):
        """Decision file with broken JSON — should be skipped, not crash."""
        root = create_project(tmp_path)
        decisions_dir = root / ".smm" / "decisions"
        (decisions_dir / "bad.json").write_text("{not valid json")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data

    def test_empty_decision_file(self, tmp_path):
        """Empty decision file — should be skipped."""
        root = create_project(tmp_path)
        decisions_dir = root / ".smm" / "decisions"
        (decisions_dir / "empty.json").write_text("")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data

    def test_decision_missing_required_fields(self, tmp_path):
        """Decision JSON with missing required fields — should be skipped."""
        root = create_project(tmp_path)
        decisions_dir = root / ".smm" / "decisions"
        (decisions_dir / "partial.json").write_text(
            json.dumps({"title": "Only Title", "content": "x"})
        )

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data

    def test_mix_valid_and_invalid_decisions(self, tmp_path):
        """Mix of valid and invalid decision files — valid ones load, invalid skipped."""
        root = create_project(tmp_path)
        d = _decision("Valid Decision")
        write_decision(root, d)
        decisions_dir = root / ".smm" / "decisions"
        (decisions_dir / "bad.json").write_text("not json")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert data["active_decisions"] >= 1


class TestCorruptedContradictionFiles:
    """Malformed contradiction JSON files."""

    def test_invalid_contradiction_json(self, tmp_path):
        """Broken contradiction JSON — CLI check should not crash."""
        root = create_project(tmp_path)
        contradictions_dir = root / ".smm" / "contradictions"
        contradictions_dir.mkdir(parents=True, exist_ok=True)
        (contradictions_dir / "bad.json").write_text("{broken")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data

    def test_contradiction_with_nonexistent_decision_ids(self, tmp_path):
        """Contradiction referencing decisions that don't exist."""
        root = create_project(tmp_path)
        contradictions_dir = root / ".smm" / "contradictions"
        contradictions_dir.mkdir(parents=True, exist_ok=True)
        (contradictions_dir / "orphan.json").write_text(json.dumps({
            "decision_a_id": "00000000-0000-0000-0000-000000000001",
            "decision_b_id": "00000000-0000-0000-0000-000000000002",
            "decision_a_title": "Ghost A",
            "decision_b_title": "Ghost B",
            "verdict": "contradiction",
            "reasoning": "These don't exist",
            "evidence_a": "None",
            "evidence_b": "None",
            "confidence": 0.9,
        }))

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data


class TestMissingSmmStructure:
    """Missing or partial .smm/ directory."""

    def test_no_smm_dir(self, tmp_path):
        """Project with no .smm/ directory at all."""
        root = tmp_path / "no-smm"
        root.mkdir()
        (root / ".git").mkdir()

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert data["active_decisions"] == 0

    def test_smm_without_decisions_dir(self, tmp_path):
        """Has .smm/ but no decisions/ subdirectory."""
        root = tmp_path / "empty-smm"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm").mkdir()

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        data = json.loads(result.output)
        assert data["active_decisions"] == 0

    def test_ensure_smm_structure_creates_dirs(self, tmp_path):
        """ensure_smm_structure creates all expected subdirectories."""
        root = tmp_path / "new-project"
        root.mkdir()
        ensure_smm_structure(root)

        assert (root / ".smm").is_dir()
        assert (root / ".smm" / "decisions").is_dir()
        assert (root / ".smm" / "audit").is_dir()

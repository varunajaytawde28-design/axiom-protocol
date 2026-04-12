"""Chaos test: Corruption Recovery.

Tests that the system handles corrupted .smm/ files gracefully:
  - Malformed JSON in decisions
  - Missing required fields
  - Corrupted governance.yaml
  - Empty files
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main

from tests.helpers.decision_factory import make_decision
from tests.helpers.repo_factory import create_project, write_decision

pytestmark = pytest.mark.chaos


class TestCorruptedDecisions:
    """Corrupted decision files should be skipped, not crash."""

    def test_malformed_json(self, tmp_path):
        """Malformed JSON decision files are skipped."""
        root = create_project(tmp_path)
        # Write a valid decision
        write_decision(root, make_decision(title="Valid"))
        # Write a corrupt decision
        (root / ".smm" / "decisions" / "corrupt.json").write_text("not json {{{")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == 1  # only the valid one

    def test_empty_json_file(self, tmp_path):
        """Empty JSON file is skipped."""
        root = create_project(tmp_path)
        write_decision(root, make_decision(title="Valid"))
        (root / ".smm" / "decisions" / "empty.json").write_text("")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == 1

    def test_missing_required_fields(self, tmp_path):
        """Decision JSON missing required fields is skipped."""
        root = create_project(tmp_path)
        write_decision(root, make_decision(title="Valid"))
        # Write decision missing 'title' and 'content'
        (root / ".smm" / "decisions" / "incomplete.json").write_text(
            json.dumps({"rationale": "only this field"})
        )

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == 1

    def test_binary_file_in_decisions_dir(self, tmp_path):
        """Binary file in decisions dir is skipped."""
        root = create_project(tmp_path)
        write_decision(root, make_decision(title="Valid"))
        (root / ".smm" / "decisions" / "binary.json").write_bytes(b"\x89PNG\r\n\x1a\n")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0


class TestCorruptedGovernance:
    """Corrupted governance.yaml handling."""

    def test_invalid_yaml(self, tmp_path):
        """Invalid YAML in governance.yaml doesn't crash check."""
        root = create_project(tmp_path)
        (root / "governance.yaml").write_text("invalid: yaml: [: broken")

        runner = CliRunner()
        # This might error or use defaults — either is acceptable
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        # Should not be exit code 2 (Click usage error)
        assert result.exit_code in (0, 1)

    def test_empty_governance(self, tmp_path):
        """Empty governance.yaml uses defaults."""
        root = create_project(tmp_path)
        (root / "governance.yaml").write_text("")
        write_decision(root, make_decision(title="Test"))

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0


class TestCorruptedContradictions:
    """Corrupted contradiction files."""

    def test_malformed_contradiction(self, tmp_path):
        """Malformed contradiction files are skipped."""
        root = create_project(tmp_path)
        write_decision(root, make_decision(title="Valid"))
        contradictions_dir = root / ".smm" / "contradictions"
        contradictions_dir.mkdir(parents=True, exist_ok=True)
        (contradictions_dir / "corrupt.json").write_text("{bad json")

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["actionable_contradictions"] == 0

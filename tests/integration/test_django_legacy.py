"""Integration test: Django Legacy Project.

Tests vt init on a realistic Django project structure with
settings.py, models.py, requirements.txt, Dockerfile.
Verifies that architectural patterns are detected and decisions
are auto-created by the scanner.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main

from tests.helpers.repo_factory import create_django_project, create_project

pytestmark = pytest.mark.integration


class TestDjangoLegacy:
    """vt init on a Django project auto-detects architecture."""

    @pytest.fixture
    def django_project(self, tmp_path):
        root = tmp_path / "django-app"
        root.mkdir()
        (root / ".git").mkdir()
        create_django_project(root)
        return root

    def test_init_detects_patterns(self, django_project):
        """vt init detects Django-specific architectural patterns."""
        runner = CliRunner()
        result = runner.invoke(main, ["init", "--path", str(django_project), "--no-hooks", "--no-mcp"])
        assert result.exit_code == 0
        assert "Detected" in result.output or "patterns" in result.output.lower()

    def test_init_creates_decisions(self, django_project):
        """vt init writes initial decision records for detected patterns."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(django_project), "--no-hooks", "--no-mcp"])

        decisions_dir = django_project / ".smm" / "decisions"
        assert decisions_dir.is_dir()
        decision_files = list(decisions_dir.glob("*.json"))
        # Should detect at least database dimension from settings.py
        assert len(decision_files) >= 1

    def test_check_after_init(self, django_project):
        """vt check works after init on a Django project."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(django_project), "--no-hooks", "--no-mcp"])
        result = runner.invoke(main, ["check", "--path", str(django_project), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] >= 1
        assert data["status"] == "pass"

    def test_gate_after_init(self, django_project):
        """vt gate passes on a freshly initialized Django project."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(django_project), "--no-hooks", "--no-mcp"])
        result = runner.invoke(main, ["gate", "--path", str(django_project), "--json-output"])
        assert result.exit_code == 0

    def test_apply_after_init(self, django_project):
        """vt apply generates instruction files from auto-detected decisions."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(django_project), "--no-hooks", "--no-mcp"])
        result = runner.invoke(main, ["apply", "--path", str(django_project)])
        assert result.exit_code == 0
        assert "Generated" in result.output

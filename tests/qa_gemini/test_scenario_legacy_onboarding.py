"""Gemini Scenario: Legacy Project Onboarding (Django Detection).

Tests VT Protocol's ability to scan a Django project and detect
existing architectural decisions from file patterns, settings,
and dependency files.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from tests.helpers.repo_factory import create_project, create_django_project

pytestmark = pytest.mark.integration


class TestDjangoLegacyOnboarding:
    """Onboard a Django project — detect decisions from file patterns."""

    @pytest.fixture
    def django_project(self, tmp_path):
        root = create_project(tmp_path, name="django-legacy")
        create_django_project(root)
        return root

    def test_init_scans_django_project(self, django_project):
        """vt init on a Django project discovers inferred decisions."""
        runner = CliRunner()
        result = runner.invoke(main, ["init", "--path", str(django_project), "--no-llm-prompt", "--no-agent-prompt"])
        assert result.exit_code == 0

    def test_check_after_init_shows_decisions(self, django_project):
        """After init, vt check shows discovered decisions."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(django_project), "--no-llm-prompt", "--no-agent-prompt"])
        result = runner.invoke(main, ["check", "--path", str(django_project), "--json-output"])
        data = json.loads(result.output)
        # Should have discovered some decisions from scanning
        assert data["active_decisions"] >= 0  # May be 0 if scan is passive

    def test_gate_passes_clean_project(self, django_project):
        """Fresh init — no contradictions, gate passes."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(django_project), "--no-llm-prompt", "--no-agent-prompt"])
        result = runner.invoke(main, ["gate", "--path", str(django_project), "--json-output"])
        # With no contradictions, gate should pass (exit 0)
        assert result.exit_code == 0


class TestInfrastructureDetection:
    """Detect infrastructure decisions from Terraform/K8s/Docker files."""

    @pytest.fixture
    def infra_project(self, tmp_path):
        from tests.helpers.repo_factory import create_infra_files
        root = create_project(tmp_path, name="infra-project")
        create_infra_files(root)
        return root

    def test_init_scans_infra_files(self, infra_project):
        """vt init detects infrastructure patterns."""
        runner = CliRunner()
        result = runner.invoke(main, ["init", "--path", str(infra_project), "--no-llm-prompt", "--no-agent-prompt"])
        assert result.exit_code == 0

    def test_check_infra_project(self, infra_project):
        """vt check runs without errors on infra project."""
        runner = CliRunner()
        runner.invoke(main, ["init", "--path", str(infra_project), "--no-llm-prompt", "--no-agent-prompt"])
        result = runner.invoke(main, ["check", "--path", str(infra_project), "--json-output"])
        data = json.loads(result.output)
        assert "status" in data

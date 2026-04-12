"""Integration test: Infrastructure Drift Detection.

Tests governance on a project with Terraform, Kubernetes, and Docker
infrastructure files. Verifies that infrastructure analysis detects
relevant patterns and that decisions cover deployment dimensions.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.decisions.models import Dimension

from tests.helpers.decision_factory import make_decision
from tests.helpers.repo_factory import create_infra_files, create_project, write_decision

pytestmark = pytest.mark.integration


class TestInfraDrift:
    """Infrastructure-level governance tests."""

    @pytest.fixture
    def infra_project(self, tmp_path):
        root = create_project(tmp_path)
        create_infra_files(root)
        return root

    def test_deployment_decision_check(self, infra_project):
        """Deployment decisions are visible to vt check."""
        d = make_decision(
            title="Docker containers on ECS",
            content="Containerized deployment on AWS ECS Fargate.",
            dimensions=[Dimension.DEPLOYMENT],
        )
        write_decision(infra_project, d)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(infra_project), "--json-output"])
        data = json.loads(result.output)
        assert data["active_decisions"] >= 1

    def test_conflicting_infra_decisions(self, infra_project):
        """Conflicting infrastructure decisions detected by gate."""
        from tests.helpers.decision_factory import make_contradiction
        from tests.helpers.repo_factory import write_contradiction

        d_ecs = make_decision(
            title="Deploy on ECS Fargate",
            content="AWS ECS Fargate for container orchestration.",
            dimensions=[Dimension.DEPLOYMENT],
        )
        d_k8s = make_decision(
            title="Deploy on Kubernetes",
            content="Self-managed Kubernetes on EC2 for container orchestration.",
            dimensions=[Dimension.DEPLOYMENT],
        )
        write_decision(infra_project, d_ecs, filename="deploy-ecs.json")
        write_decision(infra_project, d_k8s, filename="deploy-k8s.json")
        write_contradiction(
            infra_project,
            make_contradiction(d_ecs, d_k8s, reasoning="ECS vs K8s are exclusive orchestration choices"),
        )

        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(infra_project), "--json-output"])
        assert result.exit_code == 1

    def test_infra_and_app_decisions_coexist(self, infra_project):
        """Application and infrastructure decisions exist in the same graph."""
        d_app = make_decision(
            title="Use PostgreSQL",
            content="PostgreSQL for primary data storage.",
            dimensions=[Dimension.DATABASE],
        )
        d_infra = make_decision(
            title="Docker containers",
            content="Docker for deployment packaging.",
            dimensions=[Dimension.DEPLOYMENT],
        )
        write_decision(infra_project, d_app)
        write_decision(infra_project, d_infra)

        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(infra_project), "--json-output"])
        data = json.loads(result.output)
        assert data["active_decisions"] == 2

    def test_init_on_infra_project(self, tmp_path):
        """vt init on a project with infra files detects patterns."""
        root = tmp_path / "infra-proj"
        root.mkdir()
        (root / ".git").mkdir()
        create_infra_files(root)
        (root / "requirements.txt").write_text("django>=4.2\npsycopg2>=2.9\n")

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--path", str(root), "--no-hooks", "--no-mcp", "--no-llm-prompt", "--no-agent-prompt"])
        assert result.exit_code == 0

"""Tests for DRAFT legacy codebase inference."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.prevention.draft_infer import (
    InferenceReport,
    InferredDecision,
    infer_decisions,
)
from vt_protocol.decisions.models import Dimension


class TestInferredDecision:
    def test_to_dict(self) -> None:
        d = InferredDecision(
            title="Use PostgreSQL",
            content="Detected psycopg2",
            dimensions=["database"],
            confidence=0.9,
        )
        data = d.to_dict()
        assert data["title"] == "Use PostgreSQL"
        assert data["confidence"] == 0.9


class TestInferenceReport:
    def test_empty(self) -> None:
        report = InferenceReport()
        assert report.decision_count == 0

    def test_to_dict(self) -> None:
        report = InferenceReport(
            decisions=[InferredDecision(title="test", content="test")],
            files_scanned=5,
            patterns_detected=1,
        )
        d = report.to_dict()
        assert d["decision_count"] == 1
        assert d["files_scanned"] == 5


class TestInferFromRequirements:
    def test_detects_psycopg(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("psycopg2-binary==2.9.9\nfastapi\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("PostgreSQL" in t for t in titles)

    def test_detects_fastapi(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("fastapi==0.100.0\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("FastAPI" in t for t in titles)

    def test_detects_graphene(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["graphene"]\n')
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("GraphQL" in t for t in titles)

    def test_detects_pytest(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("pytest\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("pytest" in t for t in titles)

    def test_detects_redis(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("redis==4.0.0\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("Redis" in t for t in titles)

    def test_no_requirements(self, tmp_path: Path) -> None:
        report = infer_decisions(tmp_path)
        # No requirements file — should still work
        assert isinstance(report, InferenceReport)


class TestInferFromConfigFiles:
    def test_detects_dockerfile(self, tmp_path: Path) -> None:
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("Docker" in t for t in titles)

    def test_detects_docker_compose(self, tmp_path: Path) -> None:
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("Docker" in t for t in titles)

    def test_detects_github_actions(self, tmp_path: Path) -> None:
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("on: push\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("GitHub Actions" in t for t in titles)

    def test_detects_k8s(self, tmp_path: Path) -> None:
        k8s = tmp_path / "k8s"
        k8s.mkdir()
        (k8s / "deployment.yaml").write_text("kind: Deployment\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("Kubernetes" in t for t in titles)


class TestInferFromDirectoryStructure:
    def test_detects_auth_module(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "auth").mkdir(parents=True)
        (tmp_path / "src" / "auth" / "__init__.py").write_text("")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("auth" in t.lower() for t in titles)

    def test_detects_test_directory(self, tmp_path: Path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_a(): pass\n")
        report = infer_decisions(tmp_path)
        titles = [d.title for d in report.decisions]
        assert any("test" in t.lower() for t in titles)


class TestInferDecisionsIntegration:
    def test_full_project(self, tmp_path: Path) -> None:
        """Simulate a full project structure."""
        (tmp_path / "requirements.txt").write_text("fastapi\npsycopg2\npytest\n")
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_app.py").write_text("def test_a(): pass\n")
        (tmp_path / "src" / "auth").mkdir(parents=True)

        report = infer_decisions(tmp_path)
        assert report.decision_count >= 4  # FastAPI, Postgres, Docker, tests
        assert report.files_scanned > 0
        assert report.patterns_detected > 0

    def test_empty_project(self, tmp_path: Path) -> None:
        report = infer_decisions(tmp_path)
        assert report.decision_count == 0

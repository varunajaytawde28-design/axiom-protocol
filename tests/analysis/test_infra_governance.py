"""Tests for infrastructure governance."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.analysis.infra_governance import (
    InfraDimension,
    InfraFileType,
    InfraFinding,
    InfraReport,
    analyze_dockerfile,
    analyze_github_actions,
    analyze_kubernetes,
    analyze_terraform,
    check_infra,
    detect_infra_type,
)


class TestInfraFinding:
    def test_to_dict(self) -> None:
        f = InfraFinding(
            file_path="main.tf",
            file_type=InfraFileType.TERRAFORM,
            dimension=InfraDimension.SECURITY_POSTURE,
            message="test",
        )
        d = f.to_dict()
        assert d["file_type"] == "terraform"
        assert d["dimension"] == "security-posture"


class TestInfraReport:
    def test_empty(self) -> None:
        report = InfraReport()
        assert report.finding_count == 0
        assert report.has_blockers is False

    def test_with_findings(self) -> None:
        report = InfraReport(findings=[
            InfraFinding(severity="critical"),
            InfraFinding(severity="warning"),
        ])
        assert report.finding_count == 2
        assert report.critical_count == 1
        assert report.has_blockers is True


class TestDetectInfraType:
    def test_terraform(self, tmp_path: Path) -> None:
        f = tmp_path / "main.tf"
        f.write_text("")
        assert detect_infra_type(f) == InfraFileType.TERRAFORM

    def test_tfvars(self, tmp_path: Path) -> None:
        f = tmp_path / "vars.tfvars"
        f.write_text("")
        assert detect_infra_type(f) == InfraFileType.TERRAFORM

    def test_dockerfile(self, tmp_path: Path) -> None:
        f = tmp_path / "Dockerfile"
        f.write_text("")
        assert detect_infra_type(f) == InfraFileType.DOCKERFILE

    def test_github_actions(self, tmp_path: Path) -> None:
        d = tmp_path / ".github" / "workflows"
        d.mkdir(parents=True)
        f = d / "ci.yml"
        f.write_text("on: push")
        assert detect_infra_type(f) == InfraFileType.GITHUB_ACTIONS

    def test_kubernetes_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / "k8s"
        d.mkdir()
        f = d / "deployment.yaml"
        f.write_text("apiVersion: apps/v1\nkind: Deployment\n")
        assert detect_infra_type(f) == InfraFileType.KUBERNETES

    def test_unknown(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.md"
        f.write_text("")
        assert detect_infra_type(f) == InfraFileType.UNKNOWN


class TestAnalyzeTerraform:
    def test_detects_hardcoded_secret(self) -> None:
        source = 'resource "aws_instance" "web" {\n  password = "supersecret"\n}\n'
        findings = analyze_terraform(source, file_path="main.tf")
        assert any("credential" in f.message.lower() for f in findings)

    def test_detects_wildcard_iam(self) -> None:
        source = 'actions = ["*"]\n'
        findings = analyze_terraform(source, file_path="iam.tf")
        assert any("wildcard" in f.message.lower() for f in findings)

    def test_detects_large_instance(self) -> None:
        source = 'instance_type = "m5.16xlarge"\n'
        findings = analyze_terraform(source, file_path="ec2.tf")
        assert any(f.dimension == InfraDimension.COST_IMPACT for f in findings)

    def test_detects_public_access(self) -> None:
        source = "publicly_accessible = true\n"
        findings = analyze_terraform(source, file_path="rds.tf")
        assert any("public" in f.message.lower() for f in findings)

    def test_clean_terraform(self) -> None:
        source = 'instance_type = "t3.micro"\n'
        findings = analyze_terraform(source)
        assert len(findings) == 0


class TestAnalyzeKubernetes:
    def test_detects_privileged(self) -> None:
        source = "containers:\n  - name: app\n    securityContext:\n      privileged: true\n"
        findings = analyze_kubernetes(source)
        assert any("privileged" in f.message.lower() for f in findings)

    def test_detects_host_network(self) -> None:
        source = "spec:\n  hostNetwork: true\n"
        findings = analyze_kubernetes(source)
        assert any("host network" in f.message.lower() for f in findings)

    def test_detects_latest_tag(self) -> None:
        source = "image: myapp:latest\n"
        findings = analyze_kubernetes(source)
        assert any("latest" in f.message.lower() for f in findings)

    def test_clean_kubernetes(self) -> None:
        source = "image: myapp:v1.2.3\n"
        findings = analyze_kubernetes(source)
        latest_findings = [f for f in findings if "latest" in f.message.lower()]
        assert len(latest_findings) == 0


class TestAnalyzeDockerfile:
    def test_detects_root_user(self) -> None:
        source = "FROM python:3.12\nUSER root\n"
        findings = analyze_dockerfile(source)
        assert any("root" in f.message.lower() for f in findings)

    def test_detects_no_user(self) -> None:
        source = "FROM python:3.12\nRUN pip install app\n"
        findings = analyze_dockerfile(source)
        assert any("no user" in f.message.lower() for f in findings)

    def test_detects_large_base_image(self) -> None:
        source = "FROM ubuntu:22.04\n"
        findings = analyze_dockerfile(source)
        assert any("large base" in f.message.lower() or "attack surface" in f.message.lower() for f in findings)

    def test_clean_dockerfile(self) -> None:
        source = "FROM python:3.12-slim\nUSER appuser\n"
        findings = analyze_dockerfile(source)
        # No root or missing USER violations
        root_findings = [f for f in findings if "root" in f.message.lower() or "no user" in f.message.lower()]
        assert len(root_findings) == 0

    def test_slim_base_ok(self) -> None:
        source = "FROM python:3.12-alpine\nUSER app\n"
        findings = analyze_dockerfile(source)
        base_findings = [f for f in findings if "base image" in f.message.lower()]
        assert len(base_findings) == 0


class TestAnalyzeGithubActions:
    def test_detects_unpinned_action(self) -> None:
        source = "- uses: actions/checkout\n"
        findings = analyze_github_actions(source)
        assert any("unpinned" in f.message.lower() for f in findings)

    def test_pinned_action_ok(self) -> None:
        source = "- uses: actions/checkout@v4\n"
        findings = analyze_github_actions(source)
        unpinned = [f for f in findings if "unpinned" in f.message.lower()]
        assert len(unpinned) == 0

    def test_no_findings(self) -> None:
        source = "name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
        findings = analyze_github_actions(source)
        assert len(findings) == 0


class TestCheckInfra:
    def test_scans_directory(self, tmp_path: Path) -> None:
        # Create a Dockerfile
        (tmp_path / "Dockerfile").write_text("FROM ubuntu:22.04\n")
        report = check_infra(tmp_path)
        assert report.files_scanned >= 1
        assert report.finding_count >= 1

    def test_empty_directory(self, tmp_path: Path) -> None:
        report = check_infra(tmp_path)
        assert report.files_scanned == 0
        assert report.finding_count == 0

    def test_terraform_files(self, tmp_path: Path) -> None:
        (tmp_path / "main.tf").write_text('instance_type = "m5.16xlarge"\n')
        report = check_infra(tmp_path)
        assert any(f.file_type == InfraFileType.TERRAFORM for f in report.findings)

    def test_k8s_files(self, tmp_path: Path) -> None:
        k8s = tmp_path / "k8s"
        k8s.mkdir()
        (k8s / "deploy.yaml").write_text("image: myapp:latest\napiVersion: v1\nkind: Service\n")
        report = check_infra(tmp_path)
        assert report.files_scanned >= 1

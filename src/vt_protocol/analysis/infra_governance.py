"""Infrastructure governance — Terraform, K8s, Dockerfile, GH Actions analysis.

Extends the dimension taxonomy with infra-specific dimensions and provides
Tree-sitter-style queries for infrastructure-as-code files. Infra
contradictions get mandatory CRITICAL severity.

From SPEC Sprint 20: "Infrastructure governance."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class InfraDimension(str, Enum):
    """Infrastructure-specific dimensions."""

    COST_IMPACT = "cost-impact"
    BLAST_RADIUS = "blast-radius"
    SECURITY_POSTURE = "security-posture"
    COMPLIANCE_ZONE = "compliance-zone"
    DATA_RESIDENCY = "data-residency"
    AVAILABILITY_IMPACT = "availability-impact"
    ROLLBACK_COMPLEXITY = "rollback-complexity"


class InfraFileType(str, Enum):
    TERRAFORM = "terraform"
    KUBERNETES = "kubernetes"
    DOCKERFILE = "dockerfile"
    GITHUB_ACTIONS = "github_actions"
    UNKNOWN = "unknown"


@dataclass
class InfraFinding:
    """A governance finding from infrastructure analysis."""

    file_path: str = ""
    file_type: InfraFileType = InfraFileType.UNKNOWN
    line_number: int = 0
    dimension: InfraDimension | None = None
    severity: str = "critical"  # Infra contradictions are always critical
    message: str = ""
    resource: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_type": self.file_type.value,
            "line_number": self.line_number,
            "dimension": self.dimension.value if self.dimension else None,
            "severity": self.severity,
            "message": self.message,
            "resource": self.resource,
            "suggestion": self.suggestion,
        }


@dataclass
class InfraReport:
    """Report from infrastructure governance check."""

    findings: list[InfraFinding] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def has_blockers(self) -> bool:
        return self.critical_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "files_scanned": self.files_scanned,
            "finding_count": self.finding_count,
            "critical_count": self.critical_count,
            "has_blockers": self.has_blockers,
        }


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------


def detect_infra_type(path: Path) -> InfraFileType:
    """Detect infrastructure file type from path and extension."""
    name = path.name.lower()
    suffix = path.suffix.lower()

    if suffix == ".tf" or suffix == ".tfvars":
        return InfraFileType.TERRAFORM
    if name == "dockerfile" or name.startswith("dockerfile."):
        return InfraFileType.DOCKERFILE
    if suffix in (".yml", ".yaml"):
        # Check if Kubernetes or GitHub Actions
        parts = str(path).lower().replace("\\", "/")
        if ".github/workflows" in parts:
            return InfraFileType.GITHUB_ACTIONS
        if any(k in parts for k in ["k8s", "kubernetes", "deploy", "manifests"]):
            return InfraFileType.KUBERNETES
        # Peek at content indicator
        try:
            first_lines = path.read_text()[:500].lower()
            if "apiversion:" in first_lines and "kind:" in first_lines:
                return InfraFileType.KUBERNETES
        except OSError:
            pass
    return InfraFileType.UNKNOWN


# ---------------------------------------------------------------------------
# Terraform analysis
# ---------------------------------------------------------------------------


def analyze_terraform(source: str, *, file_path: str = "") -> list[InfraFinding]:
    """Analyze Terraform .tf file for governance issues."""
    findings: list[InfraFinding] = []
    lines = source.split("\n")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Check for hardcoded credentials
        if re.search(r'(password|secret|api_key|token)\s*=\s*"[^"]{3,}"', stripped, re.IGNORECASE):
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.TERRAFORM,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="Hardcoded credential detected in Terraform config",
                suggestion="Use variables or a secrets manager",
            ))

        # Check for overly permissive IAM
        if "actions" in stripped and '"*"' in stripped:
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.TERRAFORM,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="Wildcard IAM action '*' is overly permissive",
                suggestion="Use least-privilege principle — specify exact actions",
            ))

        # Check for large instance types (cost)
        instance_match = re.search(r'instance_type\s*=\s*"([^"]+)"', stripped)
        if instance_match:
            itype = instance_match.group(1)
            if any(s in itype for s in ["xlarge", "metal", "16x", "24x"]):
                findings.append(InfraFinding(
                    file_path=file_path,
                    file_type=InfraFileType.TERRAFORM,
                    line_number=i,
                    dimension=InfraDimension.COST_IMPACT,
                    message=f"Large instance type '{itype}' may have high cost impact",
                    resource=itype,
                    suggestion="Review instance sizing requirements",
                ))

        # Check for public access
        if re.search(r"(publicly_accessible|public)\s*=\s*true", stripped, re.IGNORECASE):
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.TERRAFORM,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="Resource configured with public access",
                suggestion="Consider restricting access to VPC/private subnets",
            ))

    return findings


# ---------------------------------------------------------------------------
# Kubernetes YAML analysis
# ---------------------------------------------------------------------------


def analyze_kubernetes(source: str, *, file_path: str = "") -> list[InfraFinding]:
    """Analyze Kubernetes YAML for governance issues."""
    findings: list[InfraFinding] = []
    lines = source.split("\n")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Privileged containers
        if "privileged: true" in stripped:
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.KUBERNETES,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="Privileged container detected",
                suggestion="Remove privileged: true unless absolutely required",
            ))

        # No resource limits
        if "resources:" in stripped:
            # Look ahead for limits
            has_limits = False
            for j in range(i, min(i + 10, len(lines))):
                if "limits:" in lines[j]:
                    has_limits = True
                    break
            if not has_limits:
                findings.append(InfraFinding(
                    file_path=file_path,
                    file_type=InfraFileType.KUBERNETES,
                    line_number=i,
                    dimension=InfraDimension.BLAST_RADIUS,
                    message="Container resources without limits",
                    suggestion="Set CPU and memory limits to prevent resource exhaustion",
                ))

        # Host network
        if "hostNetwork: true" in stripped:
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.KUBERNETES,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="Pod using host network",
                suggestion="Use pod networking instead of host network",
            ))

        # Latest tag
        if re.search(r"image:\s*\S+:latest", stripped):
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.KUBERNETES,
                line_number=i,
                dimension=InfraDimension.ROLLBACK_COMPLEXITY,
                message="Image using ':latest' tag — not reproducible",
                suggestion="Pin image to specific version or SHA digest",
            ))

    return findings


# ---------------------------------------------------------------------------
# Dockerfile analysis
# ---------------------------------------------------------------------------


def analyze_dockerfile(source: str, *, file_path: str = "") -> list[InfraFinding]:
    """Analyze Dockerfile for governance issues."""
    findings: list[InfraFinding] = []
    lines = source.split("\n")

    has_user = False
    exposed_ports: list[int] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        if stripped.startswith("USER "):
            has_user = True

        # Root user
        if stripped == "USER root":
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.DOCKERFILE,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="Container runs as root",
                suggestion="Use a non-root USER directive",
            ))

        # Exposed ports
        expose_match = re.match(r"EXPOSE\s+(\d+)", stripped)
        if expose_match:
            port = int(expose_match.group(1))
            exposed_ports.append(port)

        # ADD instead of COPY (potential URL download)
        if stripped.startswith("ADD ") and ("http://" in stripped or "https://" in stripped):
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.DOCKERFILE,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="ADD with URL download — use COPY + curl for better layer caching",
                suggestion="Replace ADD URL with RUN curl + COPY",
            ))

        # Large base images
        from_match = re.match(r"FROM\s+(\S+)", stripped, re.IGNORECASE)
        if from_match:
            base = from_match.group(1).lower()
            if not any(s in base for s in ["slim", "alpine", "distroless", "minimal"]):
                if any(s in base for s in ["ubuntu", "debian", "centos", "fedora"]):
                    findings.append(InfraFinding(
                        file_path=file_path,
                        file_type=InfraFileType.DOCKERFILE,
                        line_number=i,
                        dimension=InfraDimension.BLAST_RADIUS,
                        resource=base,
                        message=f"Large base image '{base}' increases attack surface",
                        suggestion="Use slim/alpine/distroless variant",
                    ))

    if not has_user:
        findings.append(InfraFinding(
            file_path=file_path,
            file_type=InfraFileType.DOCKERFILE,
            line_number=0,
            dimension=InfraDimension.SECURITY_POSTURE,
            message="No USER directive — container will run as root",
            suggestion="Add USER directive with a non-root user",
        ))

    return findings


# ---------------------------------------------------------------------------
# GitHub Actions analysis
# ---------------------------------------------------------------------------


def analyze_github_actions(source: str, *, file_path: str = "") -> list[InfraFinding]:
    """Analyze GitHub Actions workflow for governance issues."""
    findings: list[InfraFinding] = []
    lines = source.split("\n")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Unpinned actions (no @sha or @vN.N.N)
        uses_match = re.search(r"uses:\s*(\S+)", stripped)
        if uses_match:
            action = uses_match.group(1)
            if "@" not in action:
                findings.append(InfraFinding(
                    file_path=file_path,
                    file_type=InfraFileType.GITHUB_ACTIONS,
                    line_number=i,
                    dimension=InfraDimension.SECURITY_POSTURE,
                    resource=action,
                    message=f"Unpinned action '{action}' — supply chain risk",
                    suggestion="Pin to specific version or commit SHA",
                ))

        # Secrets in env
        if "${{" in stripped and "secrets." in stripped and "echo" in stripped.lower():
            findings.append(InfraFinding(
                file_path=file_path,
                file_type=InfraFileType.GITHUB_ACTIONS,
                line_number=i,
                dimension=InfraDimension.SECURITY_POSTURE,
                message="Secret potentially exposed via echo/print",
                suggestion="Avoid echoing secrets — use them directly in commands",
            ))

    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_infra(root: Path) -> InfraReport:
    """Run infrastructure governance check on a directory.

    Scans for Terraform, Kubernetes, Dockerfile, and GitHub Actions files.
    """
    report = InfraReport()

    # Find infra files
    patterns = ["**/*.tf", "**/*.tfvars", "**/Dockerfile*",
                "**/k8s/**/*.yaml", "**/k8s/**/*.yml",
                "**/kubernetes/**/*.yaml", "**/kubernetes/**/*.yml",
                "**/.github/workflows/*.yml", "**/.github/workflows/*.yaml"]

    scanned: set[Path] = set()
    for pattern in patterns:
        for fp in root.glob(pattern):
            if fp in scanned or not fp.is_file():
                continue
            scanned.add(fp)
            report.files_scanned += 1

            try:
                source = fp.read_text()
            except OSError:
                continue

            rel_path = str(fp.relative_to(root))
            file_type = detect_infra_type(fp)

            if file_type == InfraFileType.TERRAFORM:
                report.findings.extend(analyze_terraform(source, file_path=rel_path))
            elif file_type == InfraFileType.KUBERNETES:
                report.findings.extend(analyze_kubernetes(source, file_path=rel_path))
            elif file_type == InfraFileType.DOCKERFILE:
                report.findings.extend(analyze_dockerfile(source, file_path=rel_path))
            elif file_type == InfraFileType.GITHUB_ACTIONS:
                report.findings.extend(analyze_github_actions(source, file_path=rel_path))

    return report


def check_infra_file(path: Path) -> list[InfraFinding]:
    """Check a single infrastructure file."""
    if not path.exists():
        return []
    source = path.read_text()
    file_type = detect_infra_type(path)

    analyzers = {
        InfraFileType.TERRAFORM: analyze_terraform,
        InfraFileType.KUBERNETES: analyze_kubernetes,
        InfraFileType.DOCKERFILE: analyze_dockerfile,
        InfraFileType.GITHUB_ACTIONS: analyze_github_actions,
    }
    analyzer = analyzers.get(file_type)
    if analyzer:
        return analyzer(source, file_path=str(path))
    return []

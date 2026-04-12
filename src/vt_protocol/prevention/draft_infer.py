"""DRAFT approach for legacy codebases — `smm infer` command.

Scans existing code to infer architectural decisions that haven't been
formally recorded. Analyzes dependencies, config files, directory
structure, and code patterns.

From SPEC Sprint 15: "DRAFT approach for legacy codebases — `smm infer`"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vt_protocol.decisions.models import DecisionType, Dimension

logger = logging.getLogger(__name__)


@dataclass
class InferredDecision:
    """An architectural decision inferred from existing code."""

    title: str
    content: str
    dimensions: list[str] = field(default_factory=list)
    decision_type: str = DecisionType.TECHNICAL.value
    confidence: float = 0.5
    source_file: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "dimensions": self.dimensions,
            "decision_type": self.decision_type,
            "confidence": self.confidence,
            "source_file": self.source_file,
            "evidence": self.evidence,
        }


@dataclass
class InferenceReport:
    """Complete report from scanning a codebase."""

    decisions: list[InferredDecision] = field(default_factory=list)
    files_scanned: int = 0
    patterns_detected: int = 0

    @property
    def decision_count(self) -> int:
        return len(self.decisions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_count": self.decision_count,
            "files_scanned": self.files_scanned,
            "patterns_detected": self.patterns_detected,
            "decisions": [d.to_dict() for d in self.decisions],
        }


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------


def _detect_from_requirements(root: Path) -> list[InferredDecision]:
    """Infer decisions from Python requirements."""
    decisions: list[InferredDecision] = []
    for req_file in ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"]:
        path = root / req_file
        if not path.exists():
            continue
        content = path.read_text().lower()

        # Database
        db_packages = {
            "psycopg": ("PostgreSQL", 0.9),
            "sqlalchemy": ("SQLAlchemy ORM", 0.8),
            "django": ("Django ORM", 0.8),
            "pymongo": ("MongoDB", 0.9),
            "redis": ("Redis", 0.8),
            "sqlite": ("SQLite", 0.7),
        }
        for pkg, (name, conf) in db_packages.items():
            if pkg in content:
                decisions.append(InferredDecision(
                    title=f"Use {name}",
                    content=f"Project uses {name} based on dependency: {pkg}",
                    dimensions=[Dimension.DATABASE.value],
                    confidence=conf,
                    source_file=str(path),
                    evidence=[f"Found '{pkg}' in {req_file}"],
                ))

        # API framework
        api_packages = {
            "fastapi": ("FastAPI (REST)", 0.9),
            "flask": ("Flask (REST)", 0.8),
            "django-rest": ("Django REST Framework", 0.9),
            "graphene": ("GraphQL (Graphene)", 0.9),
            "strawberry": ("GraphQL (Strawberry)", 0.9),
            "grpcio": ("gRPC", 0.9),
        }
        for pkg, (name, conf) in api_packages.items():
            if pkg in content:
                decisions.append(InferredDecision(
                    title=f"Use {name}",
                    content=f"Project uses {name} based on dependency: {pkg}",
                    dimensions=[Dimension.API_STYLE.value],
                    confidence=conf,
                    source_file=str(path),
                    evidence=[f"Found '{pkg}' in {req_file}"],
                ))

        # Testing
        test_packages = {"pytest": ("pytest", 0.9), "unittest": ("unittest", 0.7)}
        for pkg, (name, conf) in test_packages.items():
            if pkg in content:
                decisions.append(InferredDecision(
                    title=f"Use {name} for testing",
                    content=f"Project uses {name} based on dependency.",
                    dimensions=[Dimension.TESTING.value],
                    confidence=conf,
                    source_file=str(path),
                    evidence=[f"Found '{pkg}' in {req_file}"],
                ))

    return decisions


def _detect_from_config_files(root: Path) -> list[InferredDecision]:
    """Infer decisions from config files."""
    decisions: list[InferredDecision] = []

    # Docker
    if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists():
        decisions.append(InferredDecision(
            title="Use Docker for deployment",
            content="Project is containerized with Docker.",
            dimensions=[Dimension.DEPLOYMENT.value],
            confidence=0.9,
            source_file="Dockerfile",
            evidence=["Dockerfile or docker-compose.yml found"],
        ))

    # Kubernetes
    k8s_files = list(root.glob("k8s/**/*.yaml")) + list(root.glob("kubernetes/**/*.yaml"))
    if k8s_files:
        decisions.append(InferredDecision(
            title="Use Kubernetes for orchestration",
            content="Project has Kubernetes manifests.",
            dimensions=[Dimension.DEPLOYMENT.value],
            decision_type=DecisionType.ARCHITECTURAL.value,
            confidence=0.9,
            source_file=str(k8s_files[0]),
            evidence=[f"Found {len(k8s_files)} K8s manifest files"],
        ))

    # CI/CD
    ci_files = {
        ".github/workflows": "GitHub Actions",
        ".gitlab-ci.yml": "GitLab CI",
        "Jenkinsfile": "Jenkins",
        ".circleci": "CircleCI",
    }
    for ci_path, ci_name in ci_files.items():
        if (root / ci_path).exists():
            decisions.append(InferredDecision(
                title=f"Use {ci_name} for CI/CD",
                content=f"Project uses {ci_name} based on config file.",
                dimensions=[Dimension.DEPLOYMENT.value],
                confidence=0.85,
                source_file=ci_path,
                evidence=[f"Found {ci_path}"],
            ))

    # Logging
    if (root / "logging.conf").exists() or (root / "logging.yaml").exists():
        decisions.append(InferredDecision(
            title="Centralized logging configuration",
            content="Project has explicit logging config.",
            dimensions=[Dimension.LOGGING.value],
            confidence=0.7,
            source_file="logging.conf/yaml",
            evidence=["Logging config file found"],
        ))

    return decisions


def _detect_from_directory_structure(root: Path) -> list[InferredDecision]:
    """Infer decisions from directory structure."""
    decisions: list[InferredDecision] = []

    # Auth module
    auth_dirs = ["auth", "authentication", "login"]
    for auth_dir in auth_dirs:
        if (root / "src" / auth_dir).is_dir() or (root / auth_dir).is_dir():
            decisions.append(InferredDecision(
                title="Custom authentication module",
                content=f"Project has a dedicated '{auth_dir}' module.",
                dimensions=[Dimension.AUTH.value],
                confidence=0.7,
                source_file=auth_dir,
                evidence=[f"Directory '{auth_dir}' exists"],
            ))
            break

    # Tests
    if (root / "tests").is_dir() or (root / "test").is_dir():
        test_count = len(list(root.rglob("test_*.py"))) + len(list(root.rglob("*_test.py")))
        if test_count > 0:
            decisions.append(InferredDecision(
                title="Automated test suite",
                content=f"Project has {test_count} test files.",
                dimensions=[Dimension.TESTING.value],
                confidence=0.8,
                source_file="tests/",
                evidence=[f"Found {test_count} test files"],
            ))

    return decisions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def infer_decisions(root: Path) -> InferenceReport:
    """Scan a codebase and infer architectural decisions.

    Analyzes:
    - Dependency files (requirements.txt, pyproject.toml)
    - Config files (Dockerfile, CI configs, logging)
    - Directory structure (auth modules, test directories)
    """
    report = InferenceReport()

    detectors = [
        _detect_from_requirements,
        _detect_from_config_files,
        _detect_from_directory_structure,
    ]

    files_scanned = 0
    for detector in detectors:
        results = detector(root)
        report.decisions.extend(results)
        files_scanned += 1  # Each detector counts as scanning

    report.files_scanned = files_scanned
    report.patterns_detected = len(report.decisions)

    return report

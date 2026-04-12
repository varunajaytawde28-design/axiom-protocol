"""Factory for creating temporary VT Protocol project repos.

Creates a realistic .smm/ directory structure with decisions,
contradictions, and governance config for integration testing.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import yaml

from vt_protocol.config import DEFAULT_GOVERNANCE_YAML, ensure_smm_structure
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


def create_project(tmp_path: Path, *, name: str = "test-project") -> Path:
    """Create a minimal VT Protocol project at tmp_path.

    Returns the project root.
    """
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)

    # .smm/ structure
    ensure_smm_structure(root)

    # governance.yaml
    gov_path = root / "governance.yaml"
    gov_path.write_text(DEFAULT_GOVERNANCE_YAML)

    # .git marker (for find_project_root)
    (root / ".git").mkdir(exist_ok=True)

    return root


def write_decision(root: Path, decision: Decision, *, filename: str = "") -> Path:
    """Write a decision JSON to .smm/decisions/."""
    decisions_dir = root / ".smm" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        filename = f"{str(decision.id)[:8]}.json"
    fp = decisions_dir / filename
    fp.write_text(decision.model_dump_json(indent=2))
    return fp


def write_contradiction(root: Path, contradiction: Contradiction, *, filename: str = "") -> Path:
    """Write a contradiction JSON to .smm/contradictions/."""
    contradictions_dir = root / ".smm" / "contradictions"
    contradictions_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        filename = f"{str(contradiction.id)[:8]}.json"
    fp = contradictions_dir / filename
    fp.write_text(contradiction.model_dump_json(indent=2))
    return fp


def create_django_project(root: Path) -> None:
    """Create a realistic Django project structure for scanning."""
    # settings.py
    (root / "settings.py").write_text(
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql'}}\n"
        "CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache'}}\n"
        "REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.TokenAuthentication']}\n"
    )
    # models.py
    models_dir = root / "myapp"
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "__init__.py").write_text("")
    (models_dir / "models.py").write_text(
        "from django.db import models\n\nclass User(models.Model):\n    name = models.CharField(max_length=100)\n"
    )
    # requirements.txt
    (root / "requirements.txt").write_text(
        "django>=4.2\ndjango-rest-framework>=3.14\ncelery>=5.3\nredis>=5.0\npsycopg2-binary>=2.9\n"
    )
    # manage.py
    (root / "manage.py").write_text("#!/usr/bin/env python\nimport sys\n")
    # Dockerfile
    (root / "Dockerfile").write_text("FROM python:3.11\nCOPY . /app\n")


def create_infra_files(root: Path) -> None:
    """Create Terraform, K8s, and Docker infrastructure files."""
    infra = root / "infra"
    infra.mkdir(parents=True, exist_ok=True)

    (infra / "main.tf").write_text(
        'resource "aws_instance" "web" {\n'
        '  ami           = "ami-12345"\n'
        '  instance_type = "t3.medium"\n'
        "}\n"
        'resource "aws_rds_instance" "db" {\n'
        '  engine         = "postgres"\n'
        '  instance_class = "db.t3.medium"\n'
        "}\n"
    )

    k8s_dir = root / "k8s"
    k8s_dir.mkdir(parents=True, exist_ok=True)
    (k8s_dir / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"
        "spec:\n  replicas: 3\n  template:\n    spec:\n"
        "      containers:\n      - name: web\n        image: myapp:latest\n"
        "        resources:\n          limits:\n            memory: 512Mi\n"
    )

    (root / "Dockerfile").write_text(
        "FROM python:3.11-slim\nRUN pip install gunicorn\nCOPY . /app\n"
        "CMD [\"gunicorn\", \"app:app\"]\n"
    )

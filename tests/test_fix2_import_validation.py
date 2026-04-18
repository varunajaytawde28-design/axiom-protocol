"""Tests for Fix 2: PreToolUse hook blocks valid imports.

Verifies that validate-change only blocks imports on the SAME dimension
as an existing decision with a DIFFERENT technology. Imports from different
dimensions (e.g., fastapi vs sqlite3) should never be blocked.
"""

from __future__ import annotations

import json

import pytest

from vt_protocol.cli.commands import _check_content_against_decisions
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


@pytest.fixture
def sqlite_decision() -> Decision:
    """Decision: project uses SQLite database."""
    return Decision(
        title="Detected: Relational Database",
        content="This project uses sqlite database via sqlite3. Do not introduce PostgreSQL, MongoDB, or any external database without explicit approval.",
        rationale="Auto-detected from project scan. Evidence: import:sqlite3",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.DATABASE],
        constraints=[
            "This project uses sqlite database via sqlite3. Do not introduce PostgreSQL, MongoDB, or any external database without explicit approval."
        ],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


@pytest.fixture
def rest_api_decision() -> Decision:
    """Decision: project uses FastAPI."""
    return Decision(
        title="Detected: REST API",
        content="This project uses REST API via fastapi. Do not introduce GraphQL, gRPC, or alternative API patterns without explicit approval.",
        rationale="Auto-detected from project scan. Evidence: import:fastapi",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.API_STYLE],
        constraints=[
            "This project uses REST API via fastapi. Do not introduce GraphQL, gRPC, or alternative API patterns without explicit approval."
        ],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


class TestDimensionIsolation:
    """Imports from different dimensions should never be blocked."""

    def test_fastapi_not_blocked_by_sqlite_decision(self, sqlite_decision: Decision) -> None:
        """Core bug: fastapi (API_STYLE) should not be blocked by sqlite (DATABASE) decision."""
        content = "from fastapi import FastAPI\n\napp = FastAPI()\n"
        violations = _check_content_against_decisions("app.py", content, [sqlite_decision])
        assert violations == [], f"fastapi should not violate a DATABASE decision, got: {violations}"

    def test_celery_not_blocked_by_sqlite_decision(self, sqlite_decision: Decision) -> None:
        content = "from celery import Celery\n"
        violations = _check_content_against_decisions("tasks.py", content, [sqlite_decision])
        assert violations == []

    def test_pydantic_not_blocked_by_sqlite_decision(self, sqlite_decision: Decision) -> None:
        content = "from pydantic import BaseModel\n"
        violations = _check_content_against_decisions("models.py", content, [sqlite_decision])
        assert violations == []

    def test_structlog_not_blocked_by_rest_decision(self, rest_api_decision: Decision) -> None:
        content = "import structlog\n"
        violations = _check_content_against_decisions("log.py", content, [rest_api_decision])
        assert violations == []

    def test_jwt_not_blocked_by_sqlite_decision(self, sqlite_decision: Decision) -> None:
        content = "import pyjwt\n"
        violations = _check_content_against_decisions("auth.py", content, [sqlite_decision])
        assert violations == []


class TestSameDimensionViolation:
    """Imports of different tech on the SAME dimension SHOULD be blocked."""

    def test_psycopg2_blocked_by_sqlite_decision(self, sqlite_decision: Decision) -> None:
        content = "import psycopg2\nconn = psycopg2.connect('dbname=test')\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert len(violations) == 1
        assert "psycopg2" in violations[0]["import"]

    def test_pymongo_blocked_by_sqlite_decision(self, sqlite_decision: Decision) -> None:
        content = "from pymongo import MongoClient\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert len(violations) == 1

    def test_graphql_blocked_by_rest_decision(self, rest_api_decision: Decision) -> None:
        content = "import graphene\n"
        violations = _check_content_against_decisions("schema.py", content, [rest_api_decision])
        assert len(violations) == 1

    def test_grpc_blocked_by_rest_decision(self, rest_api_decision: Decision) -> None:
        content = "import grpcio\n"
        violations = _check_content_against_decisions("service.py", content, [rest_api_decision])
        assert len(violations) == 1


class TestApprovedTechPasses:
    """Imports that match the decided technology should always pass."""

    def test_sqlite3_passes_sqlite_decision(self, sqlite_decision: Decision) -> None:
        content = "import sqlite3\ndb = sqlite3.connect('test.db')\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert violations == []

    def test_fastapi_passes_rest_decision(self, rest_api_decision: Decision) -> None:
        content = "from fastapi import FastAPI\n"
        violations = _check_content_against_decisions("main.py", content, [rest_api_decision])
        assert violations == []

    def test_starlette_passes_rest_decision(self, rest_api_decision: Decision) -> None:
        """Starlette is also REST API, same sub-dimension as fastapi."""
        content = "from starlette.responses import JSONResponse\n"
        violations = _check_content_against_decisions("resp.py", content, [rest_api_decision])
        assert violations == []


class TestMultipleDecisions:
    """When multiple decisions exist, each should be checked independently."""

    def test_fastapi_passes_with_both_decisions(
        self, sqlite_decision: Decision, rest_api_decision: Decision
    ) -> None:
        content = "from fastapi import FastAPI\n"
        violations = _check_content_against_decisions(
            "app.py", content, [sqlite_decision, rest_api_decision]
        )
        assert violations == []

    def test_psycopg2_blocked_with_both_decisions(
        self, sqlite_decision: Decision, rest_api_decision: Decision
    ) -> None:
        content = "import psycopg2\n"
        violations = _check_content_against_decisions(
            "db.py", content, [sqlite_decision, rest_api_decision]
        )
        assert len(violations) == 1
        # Should only be blocked by the database decision, not the API decision
        assert "Database" in violations[0]["decision"] or "database" in violations[0]["decision"].lower()


class TestEdgeCases:
    def test_no_imports_passes(self, sqlite_decision: Decision) -> None:
        content = "x = 1\ny = 2\nprint(x + y)\n"
        violations = _check_content_against_decisions("math.py", content, [sqlite_decision])
        assert violations == []

    def test_no_decisions_passes(self) -> None:
        content = "import psycopg2\n"
        violations = _check_content_against_decisions("db.py", content, [])
        assert violations == []

    def test_empty_content_passes(self, sqlite_decision: Decision) -> None:
        violations = _check_content_against_decisions("empty.py", "", [sqlite_decision])
        assert violations == []

    def test_from_import_style(self, sqlite_decision: Decision) -> None:
        content = "from psycopg2 import sql\n"
        violations = _check_content_against_decisions("db.py", content, [sqlite_decision])
        assert len(violations) == 1

    def test_stdlib_import_not_blocked(self, sqlite_decision: Decision) -> None:
        """Standard library imports should never be blocked."""
        content = "import json\nimport os\nimport sys\n"
        violations = _check_content_against_decisions("util.py", content, [sqlite_decision])
        assert violations == []

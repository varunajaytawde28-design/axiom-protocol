"""Tests for auto-generated architecture fitness tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.prevention.fitness_tests import (
    FitnessTest,
    FitnessTestSuite,
    generate_fitness_tests,
    write_fitness_tests,
)
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


def _make_decision(
    title: str = "Test Decision",
    dimensions: list[Dimension] | None = None,
    content: str = "Decision content for testing.",
    **kwargs,
) -> Decision:
    defaults = dict(
        title=title,
        content=content,
        rationale="Because testing.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=dimensions or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )
    defaults.update(kwargs)
    return Decision(**defaults)


class TestFitnessTest:
    def test_to_dict(self) -> None:
        ft = FitnessTest(
            name="test_api",
            description="Check API style",
            decision_id="abc",
            decision_title="Use REST",
            dimension="api-style",
        )
        d = ft.to_dict()
        assert d["name"] == "test_api"
        assert d["dimension"] == "api-style"


class TestFitnessTestSuite:
    def test_empty(self) -> None:
        suite = FitnessTestSuite()
        assert suite.test_count == 0

    def test_to_dict(self) -> None:
        suite = FitnessTestSuite(tests=[
            FitnessTest(name="t1", description="", decision_id="", decision_title="", dimension=""),
        ])
        d = suite.to_dict()
        assert d["test_count"] == 1


class TestGenerateFitnessTests:
    def test_rest_api_decision(self) -> None:
        d = _make_decision(
            title="Use REST API",
            dimensions=[Dimension.API_STYLE],
            content="All public APIs should be REST",
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count >= 1
        assert any("rest" in t.name for t in suite.tests)

    def test_graphql_api_decision(self) -> None:
        d = _make_decision(
            title="Use GraphQL",
            dimensions=[Dimension.API_STYLE],
            content="Internal APIs use GraphQL",
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count >= 1
        assert any("graphql" in t.name for t in suite.tests)

    def test_postgres_decision(self) -> None:
        d = _make_decision(
            title="Use PostgreSQL",
            dimensions=[Dimension.DATABASE],
            content="Primary database is PostgreSQL",
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count >= 1
        assert any("postgres" in t.name for t in suite.tests)

    def test_sqlite_decision(self) -> None:
        d = _make_decision(
            title="Use SQLite for local storage",
            dimensions=[Dimension.DATABASE],
            content="SQLite for embedded data",
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count >= 1
        assert any("sqlite" in t.name for t in suite.tests)

    def test_testing_decision(self) -> None:
        d = _make_decision(
            title="Comprehensive test coverage",
            dimensions=[Dimension.TESTING],
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count >= 1

    def test_docker_deployment(self) -> None:
        d = _make_decision(
            title="Containerized deployment",
            dimensions=[Dimension.DEPLOYMENT],
            content="Deploy with Docker containers",
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count >= 1
        assert any("docker" in t.name for t in suite.tests)

    def test_no_match_dimension(self) -> None:
        d = _make_decision(
            title="Use JWT auth",
            dimensions=[Dimension.AUTH],
        )
        suite = generate_fitness_tests([d])
        # AUTH doesn't have a generator yet
        assert suite.test_count == 0

    def test_invalid_decision_skipped(self) -> None:
        d = _make_decision(
            title="Use REST",
            dimensions=[Dimension.API_STYLE],
            content="REST APIs",
            valid=False,
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count == 0

    def test_multiple_dimensions(self) -> None:
        d = _make_decision(
            title="Use PostgreSQL with REST API",
            dimensions=[Dimension.DATABASE, Dimension.API_STYLE],
            content="PostgreSQL backend with REST API",
        )
        suite = generate_fitness_tests([d])
        assert suite.test_count >= 2  # One for DB, one for API

    def test_test_code_not_empty(self) -> None:
        d = _make_decision(
            title="Use REST",
            dimensions=[Dimension.API_STYLE],
            content="REST APIs for everything",
        )
        suite = generate_fitness_tests([d])
        assert all(t.test_code != "" for t in suite.tests)


class TestWriteFitnessTests:
    def test_writes_files(self, tmp_path: Path) -> None:
        d = _make_decision(
            title="Use REST API",
            dimensions=[Dimension.API_STYLE],
            content="REST for all",
        )
        suite = generate_fitness_tests([d])
        output_dir = tmp_path / ".smm" / "generated" / "tests"
        paths = write_fitness_tests(suite, output_dir)
        assert len(paths) >= 1
        assert all(p.exists() for p in paths)
        # Read and check content
        content = paths[0].read_text()
        assert "def test_" in content

    def test_creates_directory(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "deep" / "nested" / "tests"
        suite = FitnessTestSuite()
        write_fitness_tests(suite, output_dir)
        assert output_dir.exists()

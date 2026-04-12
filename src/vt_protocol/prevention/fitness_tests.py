"""Auto-generated architecture fitness tests.

Generates pytest/jest test files from architectural decisions. For example,
"Decision: API is REST" generates a test that fails if a GraphQL schema
appears. Tests are written to .smm/generated/tests/.

From SPEC Phase 3: "Auto-generated architecture fitness tests — run in CI
as Jest/pytest/JUnit."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vt_protocol.decisions.models import Decision, DecisionType, Dimension

logger = logging.getLogger(__name__)


@dataclass
class FitnessTest:
    """A generated architecture fitness test."""

    name: str
    description: str
    decision_id: str
    decision_title: str
    dimension: str
    test_type: str = "pytest"  # pytest, jest, junit
    test_code: str = ""
    file_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "decision_id": self.decision_id,
            "decision_title": self.decision_title,
            "dimension": self.dimension,
            "test_type": self.test_type,
            "test_code": self.test_code,
            "file_path": self.file_path,
        }


@dataclass
class FitnessTestSuite:
    """Collection of generated fitness tests."""

    tests: list[FitnessTest] = field(default_factory=list)
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def test_count(self) -> int:
        return len(self.tests)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_count": self.test_count,
            "generated_at": self.generated_at.isoformat(),
            "tests": [t.to_dict() for t in self.tests],
        }


# ---------------------------------------------------------------------------
# Test generators by dimension
# ---------------------------------------------------------------------------

# Maps dimensions to test generation functions
_GENERATORS: dict[str, Any] = {}


def _register(dimension: str):
    def decorator(fn):
        _GENERATORS[dimension] = fn
        return fn
    return decorator


@_register(Dimension.API_STYLE.value)
def _gen_api_style(decision: Decision) -> FitnessTest | None:
    title_lower = decision.title.lower()
    content_lower = decision.content.lower()

    if "rest" in title_lower or "rest" in content_lower:
        return FitnessTest(
            name=f"test_api_is_rest_{decision.id.hex[:8]}",
            description=f"Verify API follows REST style per: {decision.title}",
            decision_id=str(decision.id),
            decision_title=decision.title,
            dimension=Dimension.API_STYLE.value,
            test_code=_pytest_no_graphql(),
        )
    elif "graphql" in title_lower or "graphql" in content_lower:
        return FitnessTest(
            name=f"test_api_is_graphql_{decision.id.hex[:8]}",
            description=f"Verify API uses GraphQL per: {decision.title}",
            decision_id=str(decision.id),
            decision_title=decision.title,
            dimension=Dimension.API_STYLE.value,
            test_code=_pytest_has_graphql(),
        )
    return None


@_register(Dimension.DATABASE.value)
def _gen_database(decision: Decision) -> FitnessTest | None:
    title_lower = decision.title.lower()
    content_lower = decision.content.lower()

    if "postgres" in title_lower or "postgresql" in content_lower:
        return FitnessTest(
            name=f"test_database_is_postgres_{decision.id.hex[:8]}",
            description=f"Verify PostgreSQL usage per: {decision.title}",
            decision_id=str(decision.id),
            decision_title=decision.title,
            dimension=Dimension.DATABASE.value,
            test_code=_pytest_check_dependency("psycopg", "sqlalchemy"),
        )
    elif "sqlite" in title_lower or "sqlite" in content_lower:
        return FitnessTest(
            name=f"test_database_is_sqlite_{decision.id.hex[:8]}",
            description=f"Verify SQLite usage per: {decision.title}",
            decision_id=str(decision.id),
            decision_title=decision.title,
            dimension=Dimension.DATABASE.value,
            test_code=_pytest_no_heavy_db(),
        )
    return None


@_register(Dimension.TESTING.value)
def _gen_testing(decision: Decision) -> FitnessTest | None:
    return FitnessTest(
        name=f"test_testing_standards_{decision.id.hex[:8]}",
        description=f"Verify testing standards per: {decision.title}",
        decision_id=str(decision.id),
        decision_title=decision.title,
        dimension=Dimension.TESTING.value,
        test_code=_pytest_has_tests(),
    )


@_register(Dimension.DEPLOYMENT.value)
def _gen_deployment(decision: Decision) -> FitnessTest | None:
    content_lower = decision.content.lower()
    if "docker" in content_lower or "container" in content_lower:
        return FitnessTest(
            name=f"test_deployment_docker_{decision.id.hex[:8]}",
            description=f"Verify Docker deployment per: {decision.title}",
            decision_id=str(decision.id),
            decision_title=decision.title,
            dimension=Dimension.DEPLOYMENT.value,
            test_code=_pytest_has_dockerfile(),
        )
    return None


# ---------------------------------------------------------------------------
# Test code templates
# ---------------------------------------------------------------------------


def _pytest_no_graphql() -> str:
    return '''"""Auto-generated fitness test: API should be REST, not GraphQL."""
import subprocess

def test_no_graphql_schema():
    """Fail if GraphQL schema files are found."""
    result = subprocess.run(
        ["find", ".", "-name", "*.graphql", "-o", "-name", "schema.gql"],
        capture_output=True, text=True, timeout=30,
    )
    graphql_files = [f for f in result.stdout.strip().split("\\n") if f]
    assert not graphql_files, f"GraphQL files found but API should be REST: {graphql_files}"
'''


def _pytest_has_graphql() -> str:
    return '''"""Auto-generated fitness test: API should use GraphQL."""
from pathlib import Path

def test_graphql_schema_exists():
    """Verify GraphQL schema file exists."""
    graphql_files = list(Path(".").rglob("*.graphql")) + list(Path(".").rglob("schema.gql"))
    assert graphql_files, "No GraphQL schema files found but API should use GraphQL"
'''


def _pytest_check_dependency(primary: str, secondary: str) -> str:
    return f'''"""Auto-generated fitness test: check database dependency."""
from pathlib import Path

def test_database_dependency():
    """Verify expected database driver is in dependencies."""
    for dep_file in ["requirements.txt", "pyproject.toml", "setup.py"]:
        p = Path(dep_file)
        if p.exists():
            content = p.read_text().lower()
            assert "{primary}" in content or "{secondary}" in content, \\
                f"Expected {primary} or {secondary} in {{dep_file}}"
            return
    # No dependency file found — skip
'''


def _pytest_no_heavy_db() -> str:
    return '''"""Auto-generated fitness test: should use SQLite, not heavy DB."""
from pathlib import Path

def test_no_heavy_database_drivers():
    """Fail if heavy database drivers are found when SQLite is the decision."""
    heavy_dbs = ["psycopg", "mysql-connector", "pymongo", "cassandra-driver"]
    for dep_file in ["requirements.txt", "pyproject.toml"]:
        p = Path(dep_file)
        if p.exists():
            content = p.read_text().lower()
            found = [db for db in heavy_dbs if db in content]
            assert not found, f"Heavy DB drivers found but decision is SQLite: {found}"
'''


def _pytest_has_tests() -> str:
    return '''"""Auto-generated fitness test: project should have tests."""
from pathlib import Path

def test_tests_directory_exists():
    """Verify tests directory exists."""
    assert Path("tests").is_dir() or Path("test").is_dir(), "No tests directory found"

def test_test_files_exist():
    """Verify at least one test file exists."""
    test_files = list(Path(".").rglob("test_*.py")) + list(Path(".").rglob("*_test.py"))
    assert test_files, "No test files found"
'''


def _pytest_has_dockerfile() -> str:
    return '''"""Auto-generated fitness test: Docker deployment required."""
from pathlib import Path

def test_dockerfile_exists():
    """Verify Dockerfile exists for containerized deployment."""
    assert Path("Dockerfile").exists() or Path("docker-compose.yml").exists(), \\
        "No Dockerfile or docker-compose.yml found but deployment requires Docker"
'''


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_fitness_tests(
    decisions: list[Decision],
    *,
    test_type: str = "pytest",
) -> FitnessTestSuite:
    """Generate fitness tests from active decisions.

    Only generates tests for decisions that have known dimension patterns.
    """
    suite = FitnessTestSuite()

    for d in decisions:
        if not d.valid:
            continue
        for dim in d.dimensions:
            gen = _GENERATORS.get(dim.value)
            if gen:
                test = gen(d)
                if test:
                    test.test_type = test_type
                    suite.tests.append(test)

    return suite


def write_fitness_tests(
    suite: FitnessTestSuite,
    output_dir: Path,
) -> list[Path]:
    """Write generated fitness tests to disk.

    Returns list of created file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for test in suite.tests:
        filename = f"{test.name}.py"
        filepath = output_dir / filename
        filepath.write_text(test.test_code)
        test.file_path = str(filepath)
        paths.append(filepath)

    return paths

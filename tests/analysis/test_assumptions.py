"""Tests for the assumption extractor module.

Covers all 6 categories of domain assumption detection:
DATA_SCOPE, TEMPORAL, ACCESS, COMPLETENESS, CONFIGURATION, FRAMEWORK.
Plus directory/file scanning, dedup keys, confidence/severity validation,
and false-positive resistance.
"""

from __future__ import annotations

from pathlib import Path

from vt_protocol.analysis.assumptions import (
    PATTERNS,
    scan_changed_files,
    scan_directory,
    scan_file,
)
from vt_protocol.decisions.models import AssumptionCategory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# pytest's tmp_path creates directories named after the test function,
# e.g. "test_single_source_write_insert0/". The module's _is_test_path
# regex matches "/test_" in any part of the path and skips those files.
# To work around this we either:
#   (a) use scan_file(path, source=...) so scan_file receives a non-test
#       looking path but reads source from the argument, OR
#   (b) for scan_directory / scan_changed_files (which read from disk),
#       create a "src/" sub-directory so the walker only sees clean paths.


def _scan_code(tmp_path: Path, filename: str, code: str):
    """Write code to a file and scan it, dodging the test-path filter.

    Uses the source= kwarg so the file path seen by scan_file is the
    real tmp_path/<filename> which does NOT match the test-path regex
    (the parent dir from pytest like test_foo0/ is only matched when
    the path string contains /test_; our filename never starts with test_).

    However, pytest names the parent dir after the test, so the full path
    *does* contain /test_.  We work around this by passing source= and
    constructing a synthetic non-test path for reporting.
    """
    fake_path = Path("/src/app") / filename  # never matches test pattern
    return scan_file(fake_path, source=code)


def _pattern_ids(assumptions: list) -> set[str]:
    """Extract the set of pattern_id values from a list of DomainAssumptions."""
    return {a.pattern_id for a in assumptions}


def _write_py(directory: Path, name: str, content: str) -> Path:
    """Write a .py file under directory and return its path."""
    p = directory / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ===========================================================================
# TestDataScopePatterns
# ===========================================================================


class TestDataScopePatterns:
    """DATA_SCOPE category: single_source_write, narrow_where_clause,
    single_table_query, hardcoded_table_name."""

    def test_single_source_write_insert(self, tmp_path: Path) -> None:
        """INSERT INTO inside a single function triggers single_source_write."""
        code = '''\
import db

def place_order(item, user_id):
    db.execute(
        "INSERT INTO transactions (item, user_id, source) VALUES (?, ?, 'internal')",
        item, user_id,
    )

def get_orders(user_id):
    return db.execute("SELECT * FROM transactions WHERE user_id = ?", user_id)
'''
        results = _scan_code(tmp_path, "order_service.py", code)
        assert "single_source_write" in _pattern_ids(results)

    def test_single_source_write_orm(self, tmp_path: Path) -> None:
        """db.session.add() inside one function triggers single_source_write."""
        code = '''\
from models import Transaction

def create_transaction(item, user_id):
    txn = Transaction(item=item, user_id=user_id, source="internal")
    db.session.add(txn)
    db.session.commit()
'''
        results = _scan_code(tmp_path, "txn_service.py", code)
        assert "single_source_write" in _pattern_ids(results)

    def test_narrow_where_clause(self, tmp_path: Path) -> None:
        """WHERE with string literal triggers narrow_where_clause."""
        code = '''\
def get_internal_transactions(user_id):
    return db.execute(
        "SELECT * FROM transactions WHERE source = 'internal' AND user_id = ?",
        user_id,
    )
'''
        results = _scan_code(tmp_path, "query.py", code)
        assert "narrow_where_clause" in _pattern_ids(results)

    def test_single_table_query(self, tmp_path: Path) -> None:
        """SELECT FROM without JOIN triggers single_table_query."""
        code = '''\
def list_all():
    return db.execute("SELECT * FROM transactions WHERE active = 1")
'''
        results = _scan_code(tmp_path, "listing.py", code)
        assert "single_table_query" in _pattern_ids(results)

    def test_hardcoded_table_name(self, tmp_path: Path) -> None:
        """__tablename__ = '...' triggers hardcoded_table_name."""
        code = '''\
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    item = Column(String)
'''
        results = _scan_code(tmp_path, "models.py", code)
        assert "hardcoded_table_name" in _pattern_ids(results)


# ===========================================================================
# TestTemporalPatterns
# ===========================================================================


class TestTemporalPatterns:
    """TEMPORAL category: hardcoded_date."""

    def test_hardcoded_date(self, tmp_path: Path) -> None:
        """datetime(2023, 1, 1) triggers hardcoded_date."""
        code = '''\
from datetime import datetime

START_DATE = datetime(2023, 1, 1)

def get_recent():
    return query.filter(created_at >= START_DATE)
'''
        results = _scan_code(tmp_path, "dates.py", code)
        assert "hardcoded_date" in _pattern_ids(results)

    def test_hardcoded_date_string(self, tmp_path: Path) -> None:
        """String literal '2023-01-01' triggers hardcoded_date."""
        code = '''\
def get_recent():
    cutoff = "2023-01-01"
    return query.filter(created_at >= cutoff)
'''
        results = _scan_code(tmp_path, "date_str.py", code)
        assert "hardcoded_date" in _pattern_ids(results)


# ===========================================================================
# TestAccessPatterns
# ===========================================================================


class TestAccessPatterns:
    """ACCESS category: single_role_access."""

    def test_single_role_access_decorator(self, tmp_path: Path) -> None:
        """@require_role('admin') triggers single_role_access."""
        code = '''\
from auth import require_role

@require_role("admin")
def delete_user(user_id):
    db.execute("DELETE FROM users WHERE id = ?", user_id)
'''
        results = _scan_code(tmp_path, "admin_views.py", code)
        assert "single_role_access" in _pattern_ids(results)

    def test_single_role_equality(self, tmp_path: Path) -> None:
        """if user.role == 'admin' triggers single_role_access."""
        code = '''\
def can_delete(user):
    if user.role == "admin":
        return True
    return False
'''
        results = _scan_code(tmp_path, "perms.py", code)
        assert "single_role_access" in _pattern_ids(results)


# ===========================================================================
# TestCompletenessPatterns
# ===========================================================================


class TestCompletenessPatterns:
    """COMPLETENESS category: incomplete_enum, no_null_handling, no_pagination."""

    def test_incomplete_enum(self, tmp_path: Path) -> None:
        """status IN ('active', 'inactive') triggers incomplete_enum."""
        code = '''\
def get_visible_users():
    return db.execute(
        "SELECT * FROM users WHERE status IN ('active', 'inactive')"
    )
'''
        results = _scan_code(tmp_path, "users.py", code)
        assert "incomplete_enum" in _pattern_ids(results)

    def test_no_null_handling(self, tmp_path: Path) -> None:
        """d['key'] without .get() triggers no_null_handling."""
        code = '''\
def process_payload(data):
    name = data["name"]
    email = data["email"]
    return {"name": name, "email": email}
'''
        results = _scan_code(tmp_path, "processor.py", code)
        assert "no_null_handling" in _pattern_ids(results)

    def test_no_pagination(self, tmp_path: Path) -> None:
        """.all() without limit triggers no_pagination."""
        code = '''\
def get_all_users():
    return User.query.filter_by(active=True).all()
'''
        results = _scan_code(tmp_path, "user_repo.py", code)
        assert "no_pagination" in _pattern_ids(results)


# ===========================================================================
# TestConfigurationPatterns
# ===========================================================================


class TestConfigurationPatterns:
    """CONFIGURATION category: env_no_fallback, hardcoded_path."""

    def test_env_no_fallback(self, tmp_path: Path) -> None:
        """os.environ['API_KEY'] triggers env_no_fallback."""
        code = '''\
import os

API_KEY = os.environ["API_KEY"]
DB_HOST = os.environ["DB_HOST"]
'''
        results = _scan_code(tmp_path, "config.py", code)
        ids = _pattern_ids(results)
        assert "env_no_fallback" in ids

    def test_env_with_fallback_no_flag(self, tmp_path: Path) -> None:
        """os.getenv('KEY', 'default') should NOT trigger env_no_fallback."""
        code = '''\
import os

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
'''
        results = _scan_code(tmp_path, "safe_config.py", code)
        env_hits = [a for a in results if a.pattern_id == "env_no_fallback"]
        assert len(env_hits) == 0

    def test_hardcoded_path(self, tmp_path: Path) -> None:
        """'/tmp/data.csv' triggers hardcoded_path."""
        code = '''\
import pandas as pd

def load_data():
    return pd.read_csv("/tmp/data.csv")
'''
        results = _scan_code(tmp_path, "loader.py", code)
        assert "hardcoded_path" in _pattern_ids(results)


# ===========================================================================
# TestFrameworkPatterns
# ===========================================================================


class TestFrameworkPatterns:
    """FRAMEWORK category: orm_no_loading_strategy, no_cascade_behavior."""

    def test_orm_no_loading_strategy(self, tmp_path: Path) -> None:
        """relationship('User') without lazy= triggers orm_no_loading_strategy."""
        code = '''\
from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.orm import relationship

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User")
'''
        results = _scan_code(tmp_path, "orm_models.py", code)
        assert "orm_no_loading_strategy" in _pattern_ids(results)

    def test_orm_with_lazy_no_flag(self, tmp_path: Path) -> None:
        """relationship('User', lazy='joined') should NOT trigger."""
        code = '''\
from sqlalchemy.orm import relationship

class Order(Base):
    user = relationship("User", lazy="joined")
'''
        results = _scan_code(tmp_path, "orm_good.py", code)
        orm_hits = [a for a in results if a.pattern_id == "orm_no_loading_strategy"]
        assert len(orm_hits) == 0

    def test_no_cascade_behavior(self, tmp_path: Path) -> None:
        """ForeignKey('user.id') without on_delete triggers no_cascade_behavior."""
        code = '''\
from sqlalchemy import Column, Integer, ForeignKey

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
'''
        results = _scan_code(tmp_path, "fk_models.py", code)
        assert "no_cascade_behavior" in _pattern_ids(results)

    def test_fk_with_cascade_no_flag(self, tmp_path: Path) -> None:
        """ForeignKey with on_delete should NOT trigger no_cascade_behavior."""
        code = '''\
from sqlalchemy import Column, Integer, ForeignKey

class Order(Base):
    user_id = Column(Integer, ForeignKey("users.id", on_delete="CASCADE"))
'''
        results = _scan_code(tmp_path, "fk_good.py", code)
        fk_hits = [a for a in results if a.pattern_id == "no_cascade_behavior"]
        assert len(fk_hits) == 0


# ===========================================================================
# TestScanDirectory
# ===========================================================================


class TestScanDirectory:
    """scan_directory: recursive scanning with skip rules.

    pytest's tmp_path includes the test function name (e.g. test_scan_...0/)
    in the resolved path, which triggers _is_test_path.  We use tempfile
    with a non-test prefix to get clean paths for disk-based scanning.
    """

    def test_scan_directory_finds_all(self, tmp_path: Path) -> None:
        """Create 3 py files with different patterns -- scan_directory finds all."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            svc = scan_root / "svc"
            svc.mkdir()

            # File 1: hardcoded date
            _write_py(svc, "dates.py", '''\
from datetime import datetime
START = datetime(2023, 6, 15)
''')

            # File 2: env no fallback
            _write_py(svc, "config.py", '''\
import os
SECRET = os.environ["SECRET_KEY"]
''')

            # File 3: hardcoded path
            _write_py(svc, "io_utils.py", '''\
path = "/tmp/output.json"
''')

            results = scan_directory(scan_root)
            ids = _pattern_ids(results)
            assert "hardcoded_date" in ids
            assert "env_no_fallback" in ids
            assert "hardcoded_path" in ids
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)

    def test_scan_skips_hidden(self, tmp_path: Path) -> None:
        """Files inside .hidden/ directories are skipped."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            hidden = scan_root / ".hidden"
            hidden.mkdir()
            _write_py(hidden, "secret.py", '''\
import os
KEY = os.environ["KEY"]
''')
            results = scan_directory(scan_root)
            assert len(results) == 0
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)

    def test_scan_skips_tests(self, tmp_path: Path) -> None:
        """Files matching test patterns are skipped."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            tests_dir = scan_root / "tests"
            tests_dir.mkdir()
            _write_py(tests_dir, "test_foo.py", '''\
import os
KEY = os.environ["KEY"]
''')
            results = scan_directory(scan_root)
            assert len(results) == 0
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)


# ===========================================================================
# TestScanChangedFiles
# ===========================================================================


class TestScanChangedFiles:
    """scan_changed_files: only scans the specified file list.

    Uses tempfile with clean prefix to avoid pytest's test-named tmp dirs
    triggering the test-path filter in the resolved path.
    """

    def test_scan_changed_files(self, tmp_path: Path) -> None:
        """Only the specified files are scanned, not neighbours."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            _write_py(scan_root, "a.py", '''\
import os
KEY = os.environ["API_KEY"]
''')
            _write_py(scan_root, "b.py", '''\
from datetime import datetime
START = datetime(2023, 1, 1)
''')
            # Only scan a.py
            results = scan_changed_files(scan_root, ["a.py"])
            ids = _pattern_ids(results)
            assert "env_no_fallback" in ids
            # b.py should NOT have been scanned
            date_hits = [a for a in results if a.pattern_id == "hardcoded_date"]
            assert len(date_hits) == 0
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)

    def test_scan_changed_files_skips_test(self, tmp_path: Path) -> None:
        """Test files in the changed list are still skipped."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            _write_py(scan_root, "test_stuff.py", '''\
import os
KEY = os.environ["KEY"]
''')
            results = scan_changed_files(scan_root, ["test_stuff.py"])
            assert len(results) == 0
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)

    def test_scan_changed_files_nonexistent(self, tmp_path: Path) -> None:
        """Non-existent files in the list are silently skipped."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            results = scan_changed_files(scan_root, ["does_not_exist.py"])
            assert results == []
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)


# ===========================================================================
# TestConfidence
# ===========================================================================


class TestConfidence:
    """All detected assumptions must have confidence in [0.0, 1.0]."""

    def test_confidence_range(self, tmp_path: Path) -> None:
        code = '''\
import os
from datetime import datetime

API_KEY = os.environ["API_KEY"]
START = datetime(2023, 1, 1)
data = payload["key"]
path = "/tmp/data.csv"

def get_users():
    return db.execute("SELECT * FROM users WHERE status = 'active'")
'''
        results = _scan_code(tmp_path, "mixed.py", code)
        assert len(results) > 0
        for a in results:
            assert 0.0 <= a.confidence <= 1.0, (
                f"pattern {a.pattern_id} has confidence {a.confidence} out of range"
            )


# ===========================================================================
# TestSeverityValues
# ===========================================================================


class TestSeverityValues:
    """All detected assumptions must have severity in {low, medium, high, critical}."""

    def test_severity_values(self, tmp_path: Path) -> None:
        code = '''\
import os
from datetime import datetime

API_KEY = os.environ["API_KEY"]
START = datetime(2023, 1, 1)
data = payload["key"]
path = "/tmp/data.csv"

def get_users():
    return db.execute("SELECT * FROM users WHERE status = 'active'")
'''
        results = _scan_code(tmp_path, "mixed2.py", code)
        valid_severities = {"low", "medium", "high", "critical"}
        assert len(results) > 0
        for a in results:
            assert a.severity in valid_severities, (
                f"pattern {a.pattern_id} has invalid severity '{a.severity}'"
            )


# ===========================================================================
# TestDedupKey
# ===========================================================================


class TestDedupKey:
    """dedup_key should be stable for the same pattern in the same file."""

    def test_dedup_key_stable(self, tmp_path: Path) -> None:
        code = '''\
import os
KEY = os.environ["SECRET"]
'''
        fake_path = Path("/src/app/stable.py")
        results_1 = scan_file(fake_path, source=code)
        results_2 = scan_file(fake_path, source=code)

        assert len(results_1) > 0
        assert len(results_2) > 0

        keys_1 = {a.dedup_key for a in results_1}
        keys_2 = {a.dedup_key for a in results_2}
        assert keys_1 == keys_2

    def test_dedup_key_different_files(self, tmp_path: Path) -> None:
        """Same pattern in different files produces different dedup_keys."""
        code = '''\
import os
KEY = os.environ["SECRET"]
'''
        results_a = scan_file(Path("/src/app/file_a.py"), source=code)
        results_b = scan_file(Path("/src/app/file_b.py"), source=code)

        assert len(results_a) > 0
        assert len(results_b) > 0

        keys_a = {a.dedup_key for a in results_a}
        keys_b = {a.dedup_key for a in results_b}
        # The keys should differ because file paths differ
        assert keys_a != keys_b


# ===========================================================================
# TestThresholdScenario
# ===========================================================================


class TestThresholdScenario:
    """The real transaction table bug: multiple assumptions from one service."""

    def test_transaction_table_scenario(self, tmp_path: Path) -> None:
        code = '''\
import db

def place_order(item, user_id):
    db.execute(
        "INSERT INTO transactions (item, user_id, source) VALUES (?, ?, 'internal')",
        item, user_id,
    )

def get_transactions(user_id):
    return db.execute(
        "SELECT * FROM transactions WHERE source = 'internal' AND user_id = ?",
        user_id,
    )
'''
        results = _scan_code(tmp_path, "order_service.py", code)
        ids = _pattern_ids(results)

        # Must detect these domain assumptions:
        assert "single_source_write" in ids, "Should detect single_source_write for INSERT INTO"
        assert "narrow_where_clause" in ids, "Should detect narrow_where_clause for source = 'internal'"

        # Verify categories are correct
        for a in results:
            if a.pattern_id in ("single_source_write", "narrow_where_clause", "single_table_query"):
                assert a.category == AssumptionCategory.DATA_SCOPE

    def test_transaction_scenario_all_assumptions_have_evidence(self, tmp_path: Path) -> None:
        """Every detected assumption should carry code evidence."""
        code = '''\
import db

def place_order(item, user_id):
    db.execute("INSERT INTO transactions (item, user_id) VALUES (?, ?)", item, user_id)
'''
        results = _scan_code(tmp_path, "order_svc.py", code)
        assert len(results) > 0
        for a in results:
            assert len(a.code_evidence) >= 1
            assert a.code_evidence[0].file == "/src/app/order_svc.py"
            assert a.code_evidence[0].line > 0


# ===========================================================================
# TestNoFalsePositives
# ===========================================================================


class TestNoFalsePositives:
    """Clean code without assumptions should produce empty or near-empty results."""

    def test_safe_code_no_flags(self, tmp_path: Path) -> None:
        code = '''\
"""A simple utility module with no domain assumptions."""

import math


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def factorial(n: int) -> int:
    """Calculate factorial iteratively."""
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result
'''
        results = _scan_code(tmp_path, "pure_math.py", code)
        assert len(results) == 0, (
            f"Expected no assumptions for clean math code, got: "
            f"{[a.pattern_id for a in results]}"
        )

    def test_comments_not_flagged(self, tmp_path: Path) -> None:
        """Patterns inside comments should not be detected."""
        code = '''\
# INSERT INTO transactions (this is just a comment)
# os.environ["SECRET"]
# datetime(2023, 1, 1)

def clean_func():
    return 42
'''
        results = _scan_code(tmp_path, "commented.py", code)
        assert len(results) == 0, (
            f"Comments should not trigger detections, got: "
            f"{[a.pattern_id for a in results]}"
        )

    def test_docstrings_not_flagged(self, tmp_path: Path) -> None:
        """Patterns inside docstrings should not be detected."""
        code = '''\
def example():
    """
    Example SQL: INSERT INTO transactions (id) VALUES (1)
    Example env: os.environ["KEY"]
    """
    return 42
'''
        results = _scan_code(tmp_path, "docstringed.py", code)
        # Patterns inside docstrings should ideally be filtered out.
        # The module uses a heuristic; verify single_source_write is
        # not triggered for code inside a docstring.
        sql_hits = [a for a in results if a.pattern_id == "single_source_write"]
        assert len(sql_hits) == 0, "INSERT inside docstring should not trigger"


# ===========================================================================
# TestPatternRegistry
# ===========================================================================


class TestPatternRegistry:
    """Verify PATTERNS registry integrity."""

    def test_pattern_count(self) -> None:
        """Module docstring says 19 patterns."""
        assert len(PATTERNS) == 19

    def test_unique_pattern_ids(self) -> None:
        """All pattern_ids must be unique."""
        ids = [p.pattern_id for p in PATTERNS]
        assert len(ids) == len(set(ids))

    def test_all_categories_covered(self) -> None:
        """All 6 AssumptionCategory values should have at least one pattern."""
        covered = {p.category for p in PATTERNS}
        for cat in AssumptionCategory:
            assert cat in covered, f"No patterns cover category {cat}"

    def test_all_severities_valid(self) -> None:
        """Every pattern has a severity in the allowed set."""
        valid = {"low", "medium", "high", "critical"}
        for p in PATTERNS:
            assert p.severity in valid, f"Pattern {p.pattern_id} has invalid severity"

    def test_all_confidences_in_range(self) -> None:
        """Every pattern has base_confidence in [0, 1]."""
        for p in PATTERNS:
            assert 0.0 <= p.base_confidence <= 1.0, (
                f"Pattern {p.pattern_id} has confidence {p.base_confidence} out of range"
            )


# ===========================================================================
# TestScanFileEdgeCases
# ===========================================================================


class TestScanFileEdgeCases:
    """Edge cases for scan_file."""

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file produces no assumptions."""
        results = _scan_code(tmp_path, "empty.py", "")
        assert results == []

    def test_whitespace_only_file(self, tmp_path: Path) -> None:
        """Whitespace-only file produces no assumptions."""
        results = _scan_code(tmp_path, "blank.py", "   \n\n   \n")
        assert results == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file returns empty list (no crash)."""
        p = Path("/nonexistent/ghost.py")
        results = scan_file(p)
        assert results == []

    def test_scan_file_skips_test_path(self, tmp_path: Path) -> None:
        """scan_file skips files with test-like paths."""
        code = '''\
import os
KEY = os.environ["KEY"]
'''
        p = Path("/src/tests/test_something.py")
        results = scan_file(p, source=code)
        assert results == []

    def test_source_override(self, tmp_path: Path) -> None:
        """Passing source= avoids reading the file from disk."""
        source = '''\
import os
KEY = os.environ["MY_SECRET"]
'''
        fake_path = Path("/src/app/service.py")
        results = scan_file(fake_path, source=source)
        assert "env_no_fallback" in _pattern_ids(results)


# ===========================================================================
# TestScanDirectoryDepth
# ===========================================================================


class TestScanDirectoryDepth:
    """Directory scanning respects max_depth and skip rules."""

    def test_respects_skip_dirs(self, tmp_path: Path) -> None:
        """__pycache__, .venv, node_modules are all skipped."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            for skip_name in ("__pycache__", ".venv", "node_modules"):
                d = scan_root / skip_name
                d.mkdir(exist_ok=True)
                _write_py(d, "bad.py", 'import os\nKEY = os.environ["K"]\n')

            results = scan_directory(scan_root)
            assert len(results) == 0
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)

    def test_non_py_files_skipped(self, tmp_path: Path) -> None:
        """Non-.py files are ignored."""
        import shutil
        import tempfile

        scan_root = Path(tempfile.mkdtemp(prefix="vt_scan_"))
        try:
            (scan_root / "data.json").write_text('{"key": "value"}')
            (scan_root / "script.sh").write_text('echo "hello"')
            results = scan_directory(scan_root)
            assert results == []
        finally:
            shutil.rmtree(scan_root, ignore_errors=True)


# ===========================================================================
# TestAssumptionModel
# ===========================================================================


class TestAssumptionModel:
    """Verify DomainAssumption model fields on detected assumptions."""

    def test_status_is_detected(self, tmp_path: Path) -> None:
        """All results from scan_file have status=DETECTED."""
        from vt_protocol.decisions.models import AssumptionStatus

        code = '''\
import os
KEY = os.environ["API_KEY"]
'''
        results = _scan_code(tmp_path, "svc.py", code)
        assert len(results) > 0
        for a in results:
            assert a.status == AssumptionStatus.DETECTED

    def test_evidence_line_number(self, tmp_path: Path) -> None:
        """Code evidence should carry correct 1-based line numbers."""
        code = '''\
x = 1
y = 2
z = os.environ["KEY"]
import os
'''
        results = _scan_code(tmp_path, "lines.py", code)
        env_hits = [a for a in results if a.pattern_id == "env_no_fallback"]
        assert len(env_hits) >= 1
        # The os.environ line is line 3 (1-based)
        assert env_hits[0].code_evidence[0].line == 3

    def test_summary_populated(self, tmp_path: Path) -> None:
        """Every assumption should have a non-empty summary."""
        code = '''\
import os
KEY = os.environ["SECRET"]
'''
        results = _scan_code(tmp_path, "summary_check.py", code)
        for a in results:
            assert a.summary, f"Pattern {a.pattern_id} produced empty summary"
            assert len(a.summary) > 5


# ===========================================================================
# TestCrossCategoryFile
# ===========================================================================


class TestCrossCategoryFile:
    """A single file can trigger multiple categories."""

    def test_multiple_categories_detected(self, tmp_path: Path) -> None:
        code = '''\
import os
from datetime import datetime
from sqlalchemy.orm import relationship
from sqlalchemy import Column, Integer, ForeignKey

API_KEY = os.environ["API_KEY"]
START_DATE = datetime(2024, 3, 15)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    user = relationship("User")

def get_active():
    return db.execute("SELECT * FROM orders WHERE status = 'active'")

def load():
    return open("/tmp/cache.json").read()
'''
        results = _scan_code(tmp_path, "multi.py", code)
        categories = {a.category for a in results}
        # Should span at least 3 categories
        assert len(categories) >= 3, (
            f"Expected >= 3 categories, got {categories}"
        )
        # Verify specific ones
        assert AssumptionCategory.CONFIGURATION in categories
        assert AssumptionCategory.TEMPORAL in categories
        assert AssumptionCategory.DATA_SCOPE in categories

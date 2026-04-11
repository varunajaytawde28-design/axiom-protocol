"""Tests for file-hash cache and change detection."""

from __future__ import annotations

from pathlib import Path

from vt_protocol.observation.cache import (
    ChangeCategory,
    FileEntry,
    categorize_path,
    diff_snapshots,
    hash_file,
    load_snapshot,
    save_snapshot,
    take_snapshot,
)


class TestCategorize:
    def test_source_python(self) -> None:
        assert categorize_path("src/main.py") == ChangeCategory.SOURCE

    def test_source_typescript(self) -> None:
        assert categorize_path("src/index.ts") == ChangeCategory.SOURCE

    def test_test_directory(self) -> None:
        assert categorize_path("tests/test_foo.py") == ChangeCategory.TEST

    def test_test_file_pattern(self) -> None:
        assert categorize_path("src/foo.test.ts") == ChangeCategory.TEST

    def test_config_dockerfile(self) -> None:
        assert categorize_path("Dockerfile") == ChangeCategory.CONFIG

    def test_config_env(self) -> None:
        assert categorize_path(".env") == ChangeCategory.CONFIG

    def test_dependency_requirements(self) -> None:
        assert categorize_path("requirements.txt") == ChangeCategory.DEPENDENCY

    def test_dependency_package_json(self) -> None:
        assert categorize_path("package.json") == ChangeCategory.DEPENDENCY

    def test_dependency_lock(self) -> None:
        assert categorize_path("yarn.lock") == ChangeCategory.DEPENDENCY

    def test_ci_github_actions(self) -> None:
        assert categorize_path(".github/workflows/ci.yml") == ChangeCategory.CI

    def test_docs_readme(self) -> None:
        assert categorize_path("README.md") == ChangeCategory.DOCS

    def test_generated_agents_md(self) -> None:
        assert categorize_path("AGENTS.md") == ChangeCategory.GENERATED

    def test_other(self) -> None:
        assert categorize_path("data.bin") == ChangeCategory.OTHER

    def test_governance_yaml(self) -> None:
        assert categorize_path("governance.yaml") == ChangeCategory.CONFIG


class TestHashFile:
    def test_hash_consistent(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h1 = hash_file(f)
        h2 = hash_file(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_changes_with_content(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h1 = hash_file(f)
        f.write_text("world")
        h2 = hash_file(f)
        assert h1 != h2

    def test_hash_missing_file(self, tmp_path: Path) -> None:
        assert hash_file(tmp_path / "nonexistent") == ""


class TestSnapshot:
    def _make_project(self, root: Path) -> None:
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('hello')")
        (root / "tests").mkdir()
        (root / "tests" / "test_main.py").write_text("def test(): pass")
        (root / "requirements.txt").write_text("fastapi\n")

    def test_take_snapshot(self, tmp_path: Path) -> None:
        self._make_project(tmp_path)
        snap = take_snapshot(tmp_path)
        assert "src/main.py" in snap
        assert "requirements.txt" in snap
        assert snap["src/main.py"].category == ChangeCategory.SOURCE

    def test_diff_added_file(self, tmp_path: Path) -> None:
        self._make_project(tmp_path)
        before = take_snapshot(tmp_path)

        (tmp_path / "src" / "new.py").write_text("# new file")
        after = take_snapshot(tmp_path)

        diff = diff_snapshots(before, after)
        assert diff.total_changes == 1
        assert len(diff.added) == 1
        assert diff.added[0].path == "src/new.py"

    def test_diff_removed_file(self, tmp_path: Path) -> None:
        self._make_project(tmp_path)
        before = take_snapshot(tmp_path)

        (tmp_path / "src" / "main.py").unlink()
        after = take_snapshot(tmp_path)

        diff = diff_snapshots(before, after)
        assert len(diff.removed) == 1
        assert diff.removed[0].path == "src/main.py"

    def test_diff_modified_file(self, tmp_path: Path) -> None:
        self._make_project(tmp_path)
        before = take_snapshot(tmp_path)

        (tmp_path / "src" / "main.py").write_text("print('changed')")
        after = take_snapshot(tmp_path)

        diff = diff_snapshots(before, after)
        assert len(diff.modified) == 1
        assert diff.modified[0][1].path == "src/main.py"

    def test_diff_no_changes(self, tmp_path: Path) -> None:
        self._make_project(tmp_path)
        snap = take_snapshot(tmp_path)
        diff = diff_snapshots(snap, snap)
        assert diff.total_changes == 0

    def test_dependency_change_detected(self, tmp_path: Path) -> None:
        self._make_project(tmp_path)
        before = take_snapshot(tmp_path)

        (tmp_path / "requirements.txt").write_text("fastapi\ncelery\n")
        after = take_snapshot(tmp_path)

        diff = diff_snapshots(before, after)
        assert diff.has_dependency_changes

    def test_changes_by_category(self, tmp_path: Path) -> None:
        self._make_project(tmp_path)
        before = take_snapshot(tmp_path)

        (tmp_path / "src" / "main.py").write_text("# modified")
        (tmp_path / "requirements.txt").write_text("fastapi\nnew-dep\n")
        after = take_snapshot(tmp_path)

        diff = diff_snapshots(before, after)
        by_cat = diff.changes_by_category()
        assert ChangeCategory.SOURCE in by_cat
        assert ChangeCategory.DEPENDENCY in by_cat


class TestSnapshotPersistence:
    def test_save_and_load(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        snap = take_snapshot(tmp_path)

        snap_file = tmp_path / "snapshot.json"
        save_snapshot(snap, snap_file)
        loaded = load_snapshot(snap_file)

        assert set(loaded.keys()) == set(snap.keys())
        for key in snap:
            assert loaded[key].content_hash == snap[key].content_hash
            assert loaded[key].category == snap[key].category

"""Tests for GumTree AST diffs."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.analysis.ast_diff import (
    ASTChange,
    ChangeType,
    DiffResult,
    detect_moves,
    git_diff_fallback,
    is_gumtree_available,
)


class TestASTChange:
    def test_to_dict(self) -> None:
        c = ASTChange(
            change_type=ChangeType.INSERT,
            node_type="function",
            name="process_payment",
            new_file="services/payment.py",
            new_line=10,
        )
        d = c.to_dict()
        assert d["change_type"] == "insert"
        assert d["name"] == "process_payment"

    def test_description_insert(self) -> None:
        c = ASTChange(
            change_type=ChangeType.INSERT,
            node_type="function",
            name="foo",
            new_file="a.py",
            new_line=5,
        )
        assert "Added" in c.description
        assert "foo" in c.description

    def test_description_delete(self) -> None:
        c = ASTChange(
            change_type=ChangeType.DELETE,
            node_type="class",
            name="OldClass",
            old_file="old.py",
            old_line=1,
        )
        assert "Removed" in c.description

    def test_description_move(self) -> None:
        c = ASTChange(
            change_type=ChangeType.MOVE,
            node_type="function",
            name="process_payment",
            old_file="utils.py",
            new_file="services/payment.py",
            old_line=10,
            new_line=5,
        )
        desc = c.description
        assert "Moved" in desc
        assert "utils.py" in desc
        assert "services/payment.py" in desc

    def test_description_rename(self) -> None:
        c = ASTChange(
            change_type=ChangeType.RENAME,
            node_type="function",
            old_name="old_name",
            new_name="new_name",
            new_file="a.py",
            new_line=5,
        )
        assert "Renamed" in c.description
        assert "old_name" in c.description
        assert "new_name" in c.description

    def test_description_update(self) -> None:
        c = ASTChange(
            change_type=ChangeType.UPDATE,
            node_type="function",
            name="foo",
            new_file="a.py",
            new_line=5,
        )
        assert "Updated" in c.description


class TestDiffResult:
    def test_empty(self) -> None:
        result = DiffResult()
        assert result.change_count == 0
        assert result.pr_comment() == "No structural changes detected."

    def test_categorized_changes(self) -> None:
        result = DiffResult(changes=[
            ASTChange(change_type=ChangeType.INSERT, name="new_fn"),
            ASTChange(change_type=ChangeType.DELETE, name="old_fn"),
            ASTChange(change_type=ChangeType.MOVE, name="moved_fn"),
            ASTChange(change_type=ChangeType.UPDATE, name="updated_fn"),
            ASTChange(change_type=ChangeType.RENAME, name="renamed_fn"),
        ])
        assert len(result.inserts) == 1
        assert len(result.deletes) == 1
        assert len(result.moves) == 1
        assert len(result.updates) == 1
        assert len(result.renames) == 1

    def test_to_dict(self) -> None:
        result = DiffResult(
            old_file="a.py",
            new_file="b.py",
            changes=[ASTChange(change_type=ChangeType.INSERT, name="fn")],
        )
        d = result.to_dict()
        assert d["change_count"] == 1
        assert d["summary"]["inserts"] == 1

    def test_pr_comment(self) -> None:
        result = DiffResult(changes=[
            ASTChange(change_type=ChangeType.MOVE, node_type="function", name="process",
                      old_file="utils.py", new_file="services.py", old_line=1, new_line=1),
        ])
        comment = result.pr_comment()
        assert "Moved" in comment
        assert "process" in comment


class TestGitDiffFallback:
    def test_detect_insertion(self) -> None:
        old = "def existing():\n    pass\n"
        new = "def existing():\n    pass\n\ndef new_fn():\n    return 42\n"
        result = git_diff_fallback(old, new)
        assert any(c.change_type == ChangeType.INSERT and c.name == "new_fn" for c in result.changes)

    def test_detect_deletion(self) -> None:
        old = "def old_fn():\n    pass\n\ndef remaining():\n    pass\n"
        new = "def remaining():\n    pass\n"
        result = git_diff_fallback(old, new)
        assert any(c.change_type == ChangeType.DELETE and c.name == "old_fn" for c in result.changes)

    def test_detect_update(self) -> None:
        old = "def my_fn():\n    return 1\n"
        new = "def my_fn():\n    return 42\n"
        result = git_diff_fallback(old, new)
        assert any(c.change_type == ChangeType.UPDATE and c.name == "my_fn" for c in result.changes)

    def test_no_changes(self) -> None:
        content = "def my_fn():\n    return 1\n"
        result = git_diff_fallback(content, content)
        assert result.change_count == 0

    def test_class_detection(self) -> None:
        old = ""
        new = "class NewClass:\n    pass\n"
        result = git_diff_fallback(old, new)
        assert any(c.name == "NewClass" and c.node_type == "class" for c in result.changes)

    def test_used_gumtree_false(self) -> None:
        result = git_diff_fallback("", "def f(): pass\n")
        assert result.used_gumtree is False


class TestDetectMoves:
    def test_function_moved(self) -> None:
        deleted = {"utils.py": "def process():\n    return 1\n"}
        added = {"services/payment.py": "def process():\n    return 1\n"}
        moves = detect_moves(deleted, added)
        assert len(moves) == 1
        assert moves[0].name == "process"
        assert moves[0].change_type == ChangeType.MOVE
        assert moves[0].old_file == "utils.py"
        assert moves[0].new_file == "services/payment.py"

    def test_no_moves(self) -> None:
        deleted = {"a.py": "def unique_a():\n    pass\n"}
        added = {"b.py": "def unique_b():\n    pass\n"}
        moves = detect_moves(deleted, added)
        assert len(moves) == 0

    def test_same_file_not_move(self) -> None:
        deleted = {"a.py": "def fn():\n    pass\n"}
        added = {"a.py": "def fn():\n    return 1\n"}
        moves = detect_moves(deleted, added)
        assert len(moves) == 0


class TestGumTreeAvailability:
    def test_check(self) -> None:
        assert isinstance(is_gumtree_available(), bool)

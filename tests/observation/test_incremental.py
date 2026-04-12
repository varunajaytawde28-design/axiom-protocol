"""Tests for Salsa-style incremental computation."""

from __future__ import annotations

import pytest

from vt_protocol.observation.incremental import (
    Durability,
    IncrementalDB,
    MemoEntry,
    incremental_query,
)


class TestMemoEntry:
    def test_compute_value_hash(self) -> None:
        entry = MemoEntry(key="test", value=42)
        h = entry.compute_value_hash()
        assert isinstance(h, str)
        assert len(h) == 16

    def test_hash_deterministic(self) -> None:
        e1 = MemoEntry(key="a", value=[1, 2, 3])
        e2 = MemoEntry(key="b", value=[1, 2, 3])
        assert e1.compute_value_hash() == e2.compute_value_hash()


class TestIncrementalDB:
    def test_set_input(self) -> None:
        db = IncrementalDB()
        changed = db.set_input("file_a", "content_a")
        assert changed is True
        assert db.get("file_a") == "content_a"

    def test_set_input_same_value(self) -> None:
        db = IncrementalDB()
        db.set_input("key", "value")
        changed = db.set_input("key", "value")
        assert changed is False  # Early cutoff

    def test_set_input_different_value(self) -> None:
        db = IncrementalDB()
        db.set_input("key", "value1")
        changed = db.set_input("key", "value2")
        assert changed is True

    def test_revision_increments(self) -> None:
        db = IncrementalDB()
        assert db.revision == 0
        db.set_input("a", 1)
        assert db.revision == 1
        db.set_input("b", 2)
        assert db.revision == 2

    def test_get_missing(self) -> None:
        db = IncrementalDB()
        assert db.get("nonexistent") is None

    def test_register_and_execute_query(self) -> None:
        db = IncrementalDB()
        db.set_input("x", 10)

        def compute_double() -> int:
            val = db.get("x")
            return val * 2

        db.register_query("double_x", compute_double)
        result = db.execute_query("double_x")
        assert result == 20

    def test_query_caching(self) -> None:
        db = IncrementalDB()
        call_count = 0

        def expensive():
            nonlocal call_count
            call_count += 1
            return 42

        db.register_query("expensive", expensive)
        db.execute_query("expensive")
        db.execute_query("expensive")  # Should use cache
        assert call_count == 1

    def test_invalidation_reruns_query(self) -> None:
        db = IncrementalDB()
        db.set_input("x", 10)
        call_count = 0

        def compute():
            nonlocal call_count
            call_count += 1
            return db.get("x") * 2

        db.register_query("doubled", compute)
        db.execute_query("doubled")  # First run
        assert call_count == 1

        db.set_input("x", 20)  # Invalidate
        result = db.execute_query("doubled")  # Should re-run
        assert call_count == 2
        assert result == 40

    def test_dependency_tracking(self) -> None:
        db = IncrementalDB()
        db.set_input("a", 1)
        db.set_input("b", 2)

        def sum_ab():
            return db.get("a") + db.get("b")

        db.register_query("sum", sum_ab)
        db.execute_query("sum")

        deps = db.get_dependencies("sum")
        assert "a" in deps
        assert "b" in deps

    def test_dependents(self) -> None:
        db = IncrementalDB()
        db.set_input("x", 1)

        def use_x():
            return db.get("x")

        db.register_query("user", use_x)
        db.execute_query("user")

        dependents = db.get_dependents("x")
        assert "user" in dependents

    def test_early_cutoff(self) -> None:
        db = IncrementalDB()
        downstream_calls = 0

        def identity():
            return 42  # Always returns the same thing

        def downstream():
            nonlocal downstream_calls
            downstream_calls += 1
            return db.get("identity")

        db.register_query("identity", identity)
        db.register_query("downstream", downstream)

        db.execute_query("identity")
        db.execute_query("downstream")
        assert downstream_calls == 1

    def test_durability_high_resists_invalidation(self) -> None:
        db = IncrementalDB()
        db.set_input("stdlib_fact", 42, durability=Durability.HIGH)
        count = db.invalidate("stdlib_fact", respect_durability=True)
        assert count == 0  # HIGH durability resists

    def test_durability_low_invalidates(self) -> None:
        db = IncrementalDB()
        db.set_input("user_code", "fn", durability=Durability.LOW)
        count = db.invalidate("user_code", respect_durability=True)
        assert count >= 1

    def test_stats(self) -> None:
        db = IncrementalDB()
        db.set_input("a", 1)
        db.set_input("b", 2, durability=Durability.HIGH)
        stats = db.stats()
        assert stats["total_entries"] == 2
        assert stats["valid"] == 2
        assert stats["revision"] == 2

    def test_clear(self) -> None:
        db = IncrementalDB()
        db.set_input("a", 1)
        db.clear()
        assert db.entry_count == 0
        assert db.revision == 0

    def test_unregistered_query_raises(self) -> None:
        db = IncrementalDB()
        with pytest.raises(KeyError):
            db.execute_query("nonexistent")

    def test_cascade_invalidation(self) -> None:
        db = IncrementalDB()
        db.set_input("root", 1)

        def mid():
            return db.get("root") * 2

        def leaf():
            return db.get("mid") + 1 if db.get("mid") else 0

        db.register_query("mid", mid)
        db.register_query("leaf", leaf)
        db.execute_query("mid")
        db.execute_query("leaf")

        # Changing root should invalidate mid (direct dep)
        db.set_input("root", 2)
        # Mid should be invalid now
        result = db.execute_query("mid")
        assert result == 4


class TestIncrementalQueryDecorator:
    def test_basic_decorator(self) -> None:
        db = IncrementalDB()
        db.set_input("val", 5)

        @incremental_query(db, name="doubled")
        def doubled():
            return db.get("val") * 2

        result = doubled()
        assert result == 10

    def test_decorator_caches(self) -> None:
        db = IncrementalDB()
        calls = 0

        @incremental_query(db, name="cached_fn")
        def cached_fn():
            nonlocal calls
            calls += 1
            return 99

        cached_fn()
        cached_fn()
        assert calls == 1

    def test_decorator_with_durability(self) -> None:
        db = IncrementalDB()

        @incremental_query(db, name="stable", durability=Durability.HIGH)
        def stable():
            return "stdlib"

        stable()
        entry = db._memo.get("stable")
        assert entry is not None
        assert entry.durability == Durability.HIGH

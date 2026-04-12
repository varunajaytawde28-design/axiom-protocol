"""Salsa-style incremental computation engine.

Memo tables with dependency tracking, early cutoff, and durability levels.
When a file changes, only re-analyze that file + dependents.

From SPEC Sprint 18: "Salsa-style incremental computation."
"""

from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ContextVar tracking the currently executing query for dependency recording
_active_query: ContextVar[str | None] = ContextVar("_active_query", default=None)


class Durability(str, Enum):
    """Durability levels — controls re-validation priority."""

    LOW = "low"  # User code — always recheck on change
    NORMAL = "normal"  # Dependencies — moderate recheck
    HIGH = "high"  # Stdlib — skip revalidation unless forced


@dataclass
class MemoEntry:
    """A memoized query result with dependency tracking."""

    key: str
    value: Any = None
    value_hash: str = ""
    durability: Durability = Durability.LOW
    dependencies: set[str] = field(default_factory=set)
    revision: int = 0
    valid: bool = True

    def compute_value_hash(self) -> str:
        """Hash the stored value for early cutoff comparison."""
        return hashlib.sha256(repr(self.value).encode()).hexdigest()[:16]


class IncrementalDB:
    """Salsa-style incremental computation database.

    Provides memo tables with automatic dependency tracking,
    early cutoff, and durability-based invalidation.
    """

    def __init__(self) -> None:
        self._memo: dict[str, MemoEntry] = {}
        self._revision: int = 0
        self._queries: dict[str, Callable[..., Any]] = {}

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def entry_count(self) -> int:
        return len(self._memo)

    def register_query(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        durability: Durability = Durability.LOW,
    ) -> None:
        """Register a named query function."""
        self._queries[name] = fn
        # Pre-create entry metadata
        if name not in self._memo:
            self._memo[name] = MemoEntry(key=name, durability=durability)

    def set_input(self, key: str, value: Any, *, durability: Durability = Durability.LOW) -> bool:
        """Set an input value. Returns True if value actually changed.

        Input values are the leaves of the dependency graph.
        """
        existing = self._memo.get(key)
        new_hash = hashlib.sha256(repr(value).encode()).hexdigest()[:16]

        if existing and existing.value_hash == new_hash and existing.valid:
            return False  # Early cutoff — same value, no propagation

        self._revision += 1
        self._memo[key] = MemoEntry(
            key=key,
            value=value,
            value_hash=new_hash,
            durability=durability,
            revision=self._revision,
            valid=True,
        )

        # Invalidate dependents
        self._invalidate_dependents(key)
        return True

    def get(self, key: str) -> Any:
        """Get a memoized value, recording dependency if inside a query."""
        # Record dependency on the active query
        active = _active_query.get()
        if active and active != key:
            entry = self._memo.get(active)
            if entry:
                entry.dependencies.add(key)

        entry = self._memo.get(key)
        if entry and entry.valid:
            return entry.value
        return None

    def execute_query(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Execute a registered query with dependency tracking.

        If the result is already cached and valid, return it.
        Otherwise, re-execute and cache the result.
        """
        entry = self._memo.get(name)

        # Return cached result if valid
        if entry and entry.valid and entry.value is not None:
            return entry.value

        fn = self._queries.get(name)
        if fn is None:
            raise KeyError(f"Query '{name}' not registered")

        # Set active query for dependency tracking
        token = _active_query.set(name)
        try:
            if entry is None:
                entry = MemoEntry(key=name)
                self._memo[name] = entry

            entry.dependencies.clear()
            result = fn(*args, **kwargs)

            new_hash = hashlib.sha256(repr(result).encode()).hexdigest()[:16]

            # Early cutoff: if result unchanged, don't propagate
            if entry.value_hash == new_hash and entry.value is not None:
                entry.valid = True
                return entry.value

            entry.value = result
            entry.value_hash = new_hash
            entry.revision = self._revision
            entry.valid = True

            return result
        finally:
            _active_query.reset(token)

    def invalidate(self, key: str, *, respect_durability: bool = True) -> int:
        """Invalidate a key and its dependents.

        Returns the number of entries invalidated.
        """
        entry = self._memo.get(key)
        if entry is None:
            return 0

        if respect_durability and entry.durability == Durability.HIGH:
            return 0  # HIGH durability entries resist invalidation

        self._revision += 1
        return self._invalidate_dependents(key) + 1

    def _invalidate_dependents(self, key: str) -> int:
        """Recursively invalidate all entries that depend on key."""
        count = 0
        for name, entry in self._memo.items():
            if key in entry.dependencies and entry.valid:
                if entry.durability == Durability.HIGH:
                    continue  # HIGH durability entries resist cascading invalidation
                entry.valid = False
                count += 1
                count += self._invalidate_dependents(name)
        return count

    def get_dependents(self, key: str) -> set[str]:
        """Get all entries that directly depend on this key."""
        return {
            name for name, entry in self._memo.items()
            if key in entry.dependencies
        }

    def get_dependencies(self, key: str) -> set[str]:
        """Get all entries that this key depends on."""
        entry = self._memo.get(key)
        if entry is None:
            return set()
        return set(entry.dependencies)

    def stats(self) -> dict[str, Any]:
        """Return statistics about the memo table."""
        valid = sum(1 for e in self._memo.values() if e.valid)
        invalid = sum(1 for e in self._memo.values() if not e.valid)
        by_durability = {}
        for e in self._memo.values():
            by_durability[e.durability.value] = by_durability.get(e.durability.value, 0) + 1

        return {
            "total_entries": len(self._memo),
            "valid": valid,
            "invalid": invalid,
            "revision": self._revision,
            "by_durability": by_durability,
        }

    def clear(self) -> None:
        """Clear all memoized data."""
        self._memo.clear()
        self._revision = 0


# ---------------------------------------------------------------------------
# Decorator for query registration
# ---------------------------------------------------------------------------


def incremental_query(
    db: IncrementalDB,
    *,
    name: str | None = None,
    durability: Durability = Durability.LOW,
) -> Callable:
    """Decorator to register a function as an incremental query."""
    def decorator(fn: Callable) -> Callable:
        query_name = name or fn.__name__
        db.register_query(query_name, fn, durability=durability)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return db.execute_query(query_name, *args, **kwargs)

        wrapper.__name__ = fn.__name__
        wrapper._query_name = query_name  # type: ignore
        return wrapper

    return decorator

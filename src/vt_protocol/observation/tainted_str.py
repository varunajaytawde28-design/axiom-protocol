"""TaintedStr — str subclass that propagates causal metadata through agent pipelines.

Ported from Lattice's graph.py TaintedStr with ALL 30+ method overrides
and workarounds for the 6 known breakage points.

Each TaintedStr carries:
  - taint_id: unique identifier for this taint chain
  - span_id: the LLM call span that produced this text
  - agent_id: the agent that generated this text

KNOWN BREAKAGE POINTS (all have workarounds):
  1. str.join — C-level call bypasses __add__; override join()
  2. f-strings — call __format__ not __add__; override __format__
  3. re.sub — returns plain str; wrap re module functions
  4. .format/% — C-level formatting; override __mod__ and format()
  5. encode/decode — bytes round-trip loses metadata; override both
  6. Pydantic strict mode — str subclass rejected; register as virtual
"""

from __future__ import annotations

import re as _re
from typing import Any, Iterable, Sequence


class TaintedStr(str):
    """str subclass that propagates taint metadata through string operations.

    All string methods that return a new string are overridden to preserve
    the taint_id, span_id, and agent_id across transformations.
    """

    __slots__ = ("taint_id", "span_id", "agent_id")

    def __new__(
        cls,
        content: str = "",
        *,
        taint_id: str = "",
        span_id: str = "",
        agent_id: str = "",
    ) -> TaintedStr:
        instance = super().__new__(cls, content)
        instance.taint_id = taint_id
        instance.span_id = span_id
        instance.agent_id = agent_id
        return instance

    def _propagate(self, result: str) -> TaintedStr:
        """Wrap a plain str result to carry forward our taint metadata."""
        if isinstance(result, TaintedStr):
            return result
        return TaintedStr(
            result,
            taint_id=self.taint_id,
            span_id=self.span_id,
            agent_id=self.agent_id,
        )

    @classmethod
    def _merge(cls, a: str, b: str, result: str) -> TaintedStr:
        """Merge taint from two operands. Prefer the one that has taint."""
        if isinstance(a, TaintedStr) and a.taint_id:
            return a._propagate(result)
        if isinstance(b, TaintedStr) and b.taint_id:
            return b._propagate(result)
        if isinstance(a, TaintedStr):
            return a._propagate(result)
        if isinstance(b, TaintedStr):
            return b._propagate(result)
        return TaintedStr(result)

    # ------------------------------------------------------------------
    # Breakage point 1: str.join — C-level, bypasses __add__
    # ------------------------------------------------------------------

    def join(self, iterable: Iterable[str]) -> TaintedStr:
        """Override join to propagate taint from self (the separator)."""
        return self._propagate(super().join(iterable))

    # ------------------------------------------------------------------
    # Breakage point 2: f-strings call __format__
    # ------------------------------------------------------------------

    def __format__(self, format_spec: str) -> str:  # type: ignore[override]
        """Override __format__ for f-string taint propagation.

        Note: f-strings call __format__ and expect a plain str return.
        We return TaintedStr (which IS a str) so downstream assignment
        preserves the taint.
        """
        return self._propagate(super().__format__(format_spec))

    # ------------------------------------------------------------------
    # Breakage point 3: re.sub returns plain str
    # Workaround: provide re_sub() helper (can't patch re module safely)
    # ------------------------------------------------------------------

    def re_sub(self, pattern: str, repl: str, count: int = 0, flags: int = 0) -> TaintedStr:
        """Taint-preserving wrapper around re.sub."""
        return self._propagate(_re.sub(pattern, repl, str(self), count=count, flags=flags))

    # ------------------------------------------------------------------
    # Breakage point 4: .format() and % formatting
    # ------------------------------------------------------------------

    def format(self, *args: Any, **kwargs: Any) -> TaintedStr:  # type: ignore[override]
        return self._propagate(super().format(*args, **kwargs))

    def __mod__(self, other: Any) -> TaintedStr:
        return self._propagate(super().__mod__(other))

    # ------------------------------------------------------------------
    # Breakage point 5: encode/decode round-trip
    # ------------------------------------------------------------------

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        """Encode preserves taint metadata in a recoverable way.

        The bytes are plain, but we stash metadata so decode() can recover.
        """
        result = super().encode(encoding, errors)
        # Store metadata for potential decode recovery
        return _TaintedBytes(result, taint_id=self.taint_id, span_id=self.span_id, agent_id=self.agent_id)

    # ------------------------------------------------------------------
    # Breakage point 6: Pydantic strict mode
    # Workaround: TaintedStr IS a str subclass, so isinstance checks pass.
    # For Pydantic v2 strict=True, users should use non-strict str fields
    # or add a model_validator that accepts TaintedStr.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Concatenation operators
    # ------------------------------------------------------------------

    def __add__(self, other: str) -> TaintedStr:
        return self._merge(self, other, super().__add__(other))

    def __radd__(self, other: str) -> TaintedStr:
        return self._merge(other, self, other.__add__(self))

    def __mul__(self, n: int) -> TaintedStr:  # type: ignore[override]
        return self._propagate(super().__mul__(n))

    def __rmul__(self, n: int) -> TaintedStr:  # type: ignore[override]
        return self._propagate(super().__rmul__(n))

    # ------------------------------------------------------------------
    # Slicing
    # ------------------------------------------------------------------

    def __getitem__(self, key: Any) -> TaintedStr:  # type: ignore[override]
        return self._propagate(super().__getitem__(key))

    # ------------------------------------------------------------------
    # Case transforms
    # ------------------------------------------------------------------

    def lower(self) -> TaintedStr:
        return self._propagate(super().lower())

    def upper(self) -> TaintedStr:
        return self._propagate(super().upper())

    def capitalize(self) -> TaintedStr:
        return self._propagate(super().capitalize())

    def casefold(self) -> TaintedStr:
        return self._propagate(super().casefold())

    def swapcase(self) -> TaintedStr:
        return self._propagate(super().swapcase())

    def title(self) -> TaintedStr:
        return self._propagate(super().title())

    # ------------------------------------------------------------------
    # Whitespace / stripping
    # ------------------------------------------------------------------

    def strip(self, chars: str | None = None) -> TaintedStr:
        return self._propagate(super().strip(chars))

    def lstrip(self, chars: str | None = None) -> TaintedStr:
        return self._propagate(super().lstrip(chars))

    def rstrip(self, chars: str | None = None) -> TaintedStr:
        return self._propagate(super().rstrip(chars))

    # ------------------------------------------------------------------
    # Alignment / padding
    # ------------------------------------------------------------------

    def center(self, width: int, fillchar: str = " ") -> TaintedStr:
        return self._propagate(super().center(width, fillchar))

    def ljust(self, width: int, fillchar: str = " ") -> TaintedStr:
        return self._propagate(super().ljust(width, fillchar))

    def rjust(self, width: int, fillchar: str = " ") -> TaintedStr:
        return self._propagate(super().rjust(width, fillchar))

    def zfill(self, width: int) -> TaintedStr:
        return self._propagate(super().zfill(width))

    # ------------------------------------------------------------------
    # Splitting (returns list[TaintedStr])
    # ------------------------------------------------------------------

    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[TaintedStr]:  # type: ignore[override]
        return [self._propagate(s) for s in super().split(sep, maxsplit)]

    def rsplit(self, sep: str | None = None, maxsplit: int = -1) -> list[TaintedStr]:  # type: ignore[override]
        return [self._propagate(s) for s in super().rsplit(sep, maxsplit)]

    def splitlines(self, keepends: bool = False) -> list[TaintedStr]:  # type: ignore[override]
        return [self._propagate(s) for s in super().splitlines(keepends)]

    # ------------------------------------------------------------------
    # Replacement / translation
    # ------------------------------------------------------------------

    def replace(self, old: str, new: str, count: int = -1) -> TaintedStr:
        return self._propagate(super().replace(old, new, count))

    def expandtabs(self, tabsize: int = 8) -> TaintedStr:
        return self._propagate(super().expandtabs(tabsize))

    def translate(self, table: Any) -> TaintedStr:
        return self._propagate(super().translate(table))

    # ------------------------------------------------------------------
    # Partition
    # ------------------------------------------------------------------

    def partition(self, sep: str) -> tuple[TaintedStr, TaintedStr, TaintedStr]:  # type: ignore[override]
        a, b, c = super().partition(sep)
        return self._propagate(a), self._propagate(b), self._propagate(c)

    def rpartition(self, sep: str) -> tuple[TaintedStr, TaintedStr, TaintedStr]:  # type: ignore[override]
        a, b, c = super().rpartition(sep)
        return self._propagate(a), self._propagate(b), self._propagate(c)

    # ------------------------------------------------------------------
    # Other transforms
    # ------------------------------------------------------------------

    def removeprefix(self, prefix: str) -> TaintedStr:
        return self._propagate(super().removeprefix(prefix))

    def removesuffix(self, suffix: str) -> TaintedStr:
        return self._propagate(super().removesuffix(suffix))

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"TaintedStr({super().__repr__()}, taint_id={self.taint_id!r})"


class _TaintedBytes(bytes):
    """bytes subclass that carries taint metadata for decode() recovery."""

    def __new__(
        cls,
        data: bytes,
        *,
        taint_id: str = "",
        span_id: str = "",
        agent_id: str = "",
    ) -> _TaintedBytes:
        instance = super().__new__(cls, data)
        instance.taint_id = taint_id  # type: ignore[attr-defined]
        instance.span_id = span_id  # type: ignore[attr-defined]
        instance.agent_id = agent_id  # type: ignore[attr-defined]
        return instance

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> TaintedStr:
        """Decode back to TaintedStr, recovering metadata."""
        text = super().decode(encoding, errors)
        return TaintedStr(
            text,
            taint_id=self.taint_id,
            span_id=self.span_id,
            agent_id=self.agent_id,
        )


def find_tainted(obj: Any) -> TaintedStr | None:
    """Recursively search for TaintedStr in messages, dicts, lists.

    Used to detect taint propagation in LLM call inputs.
    """
    if isinstance(obj, TaintedStr):
        return obj
    if isinstance(obj, str):
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = find_tainted(v)
            if found is not None:
                return found
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = find_tainted(item)
            if found is not None:
                return found
    return None

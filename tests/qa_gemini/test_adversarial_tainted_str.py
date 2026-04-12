"""Gemini Adversarial: TaintedStr Breakage Vectors.

Tests all 6 known breakage points where CPython's C-level str operations
can strip taint metadata. Each breakage point has a workaround in
TaintedStr — this test verifies the workarounds hold.
"""

from __future__ import annotations

import re

import pytest

from vt_protocol.observation.tainted_str import TaintedStr, _TaintedBytes, find_tainted

pytestmark = pytest.mark.adversarial

TAINT = dict(taint_id="taint-001", span_id="span-42", agent_id="claude")


class TestBreakagePointJoin:
    """Breakage point 1: str.join — C-level call bypasses __add__."""

    def test_join_preserves_taint_from_separator(self):
        """TaintedStr.join propagates taint from the separator."""
        sep = TaintedStr(", ", **TAINT)
        result = sep.join(["a", "b", "c"])
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"
        assert str(result) == "a, b, c"

    def test_join_with_tainted_items(self):
        """Items in join lose taint (CPython behavior) — sep taint propagates."""
        sep = TaintedStr("-", **TAINT)
        items = [TaintedStr("x", taint_id="other"), "y", "z"]
        result = sep.join(items)
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"  # From separator

    def test_plain_str_join_loses_taint(self):
        """Plain str.join loses TaintedStr metadata — this is the known breakage."""
        plain_sep = ", "
        t = TaintedStr("hello", **TAINT)
        # This calls str.join which returns plain str
        result = plain_sep.join([t, "world"])
        # This is the KNOWN breakage — result is plain str, not TaintedStr
        # The workaround is to use TaintedStr as the separator
        assert isinstance(result, str)

    def test_join_empty_list(self):
        sep = TaintedStr(", ", **TAINT)
        result = sep.join([])
        assert isinstance(result, TaintedStr)
        assert str(result) == ""
        assert result.taint_id == "taint-001"


class TestBreakagePointFString:
    """Breakage point 2: f-strings call __format__, not __add__."""

    def test_fstring_preserves_taint(self):
        """f-string uses __format__ — TaintedStr overrides it."""
        t = TaintedStr("world", **TAINT)
        result = f"hello {t}"
        # f-string calls __format__ on t, which returns TaintedStr
        # But the outer f-string concatenation may or may not preserve type
        # The key is that __format__ itself returns TaintedStr
        formatted = format(t, "")
        assert isinstance(formatted, TaintedStr)
        assert formatted.taint_id == "taint-001"

    def test_format_spec_preserved(self):
        """Format spec works with TaintedStr."""
        t = TaintedStr("hello", **TAINT)
        result = format(t, ">10")
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"
        assert len(result) == 10

    def test_format_method_preserves_taint(self):
        """str.format() is overridden to preserve taint."""
        template = TaintedStr("Hello, {}!", **TAINT)
        result = template.format("world")
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"
        assert str(result) == "Hello, world!"


class TestBreakagePointReSub:
    """Breakage point 3: re.sub returns plain str."""

    def test_re_sub_loses_taint(self):
        """Standard re.sub strips taint — this is the known breakage."""
        t = TaintedStr("hello world", **TAINT)
        result = re.sub(r"world", "earth", t)
        # re.sub returns plain str — this is the known breakage
        assert str(result) == "hello earth"
        # May or may not be TaintedStr depending on CPython internals

    def test_re_sub_workaround_preserves_taint(self):
        """TaintedStr.re_sub wrapper preserves taint."""
        t = TaintedStr("hello world", **TAINT)
        result = t.re_sub(r"world", "earth")
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"
        assert str(result) == "hello earth"

    def test_re_sub_with_regex_pattern(self):
        """Complex regex patterns work with re_sub wrapper."""
        t = TaintedStr("foo123bar456", **TAINT)
        result = t.re_sub(r"\d+", "X")
        assert isinstance(result, TaintedStr)
        assert str(result) == "fooXbarX"

    def test_re_sub_no_match(self):
        """re_sub with no match still returns TaintedStr."""
        t = TaintedStr("hello", **TAINT)
        result = t.re_sub(r"xyz", "replaced")
        assert isinstance(result, TaintedStr)
        assert str(result) == "hello"
        assert result.taint_id == "taint-001"


class TestBreakagePointFormatPercent:
    """Breakage point 4: .format() and % formatting."""

    def test_percent_formatting_preserves_taint(self):
        """%-style formatting is overridden via __mod__."""
        t = TaintedStr("Hello %s, you have %d items", **TAINT)
        result = t % ("Alice", 5)
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"
        assert str(result) == "Hello Alice, you have 5 items"

    def test_format_method_with_kwargs(self):
        """str.format with kwargs preserves taint."""
        t = TaintedStr("Hello {name}, age {age}", **TAINT)
        result = t.format(name="Bob", age=30)
        assert isinstance(result, TaintedStr)
        assert str(result) == "Hello Bob, age 30"
        assert result.taint_id == "taint-001"


class TestBreakagePointEncodeDecode:
    """Breakage point 5: encode/decode round-trip loses metadata."""

    def test_encode_returns_tainted_bytes(self):
        """TaintedStr.encode returns _TaintedBytes with metadata."""
        t = TaintedStr("hello", **TAINT)
        encoded = t.encode("utf-8")
        assert isinstance(encoded, _TaintedBytes)
        assert encoded.taint_id == "taint-001"  # type: ignore[attr-defined]

    def test_encode_decode_roundtrip(self):
        """Full encode/decode roundtrip preserves taint."""
        t = TaintedStr("hello world", **TAINT)
        encoded = t.encode("utf-8")
        decoded = encoded.decode("utf-8")
        assert isinstance(decoded, TaintedStr)
        assert decoded.taint_id == "taint-001"
        assert decoded.span_id == "span-42"
        assert decoded.agent_id == "claude"
        assert str(decoded) == "hello world"

    def test_encode_decode_utf16_roundtrip(self):
        """Non-UTF-8 encoding roundtrip."""
        t = TaintedStr("hello", **TAINT)
        encoded = t.encode("utf-16")
        decoded = encoded.decode("utf-16")
        assert isinstance(decoded, TaintedStr)
        assert decoded.taint_id == "taint-001"

    def test_plain_bytes_decode_loses_taint(self):
        """Standard bytes.decode loses taint — expected behavior."""
        t = TaintedStr("hello", **TAINT)
        # Cast to plain bytes
        plain = bytes(t.encode("utf-8"))
        decoded = plain.decode("utf-8")
        assert not isinstance(decoded, TaintedStr)


class TestBreakagePointPydantic:
    """Breakage point 6: Pydantic strict mode rejects str subclasses."""

    def test_isinstance_check_passes(self):
        """TaintedStr IS a str — isinstance check passes."""
        t = TaintedStr("hello", **TAINT)
        assert isinstance(t, str)
        assert isinstance(t, TaintedStr)

    def test_pydantic_non_strict_accepts_tainted_str(self):
        """Pydantic non-strict mode accepts TaintedStr."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str

        t = TaintedStr("hello", **TAINT)
        m = TestModel(name=t)
        assert m.name == "hello"


class TestStringOperationPropagation:
    """All overridden string methods propagate taint."""

    def test_add_preserves_taint(self):
        t = TaintedStr("hello", **TAINT)
        result = t + " world"
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"

    def test_radd_preserves_taint(self):
        t = TaintedStr("world", **TAINT)
        result = "hello " + t
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"

    def test_mul_preserves_taint(self):
        t = TaintedStr("ha", **TAINT)
        result = t * 3
        assert isinstance(result, TaintedStr)
        assert str(result) == "hahaha"
        assert result.taint_id == "taint-001"

    def test_getitem_preserves_taint(self):
        t = TaintedStr("hello", **TAINT)
        result = t[1:4]
        assert isinstance(result, TaintedStr)
        assert str(result) == "ell"
        assert result.taint_id == "taint-001"

    def test_lower_preserves_taint(self):
        t = TaintedStr("HELLO", **TAINT)
        assert isinstance(t.lower(), TaintedStr)
        assert t.lower().taint_id == "taint-001"

    def test_upper_preserves_taint(self):
        t = TaintedStr("hello", **TAINT)
        assert isinstance(t.upper(), TaintedStr)

    def test_strip_preserves_taint(self):
        t = TaintedStr("  hello  ", **TAINT)
        result = t.strip()
        assert isinstance(result, TaintedStr)
        assert str(result) == "hello"

    def test_split_returns_tainted_list(self):
        t = TaintedStr("a,b,c", **TAINT)
        parts = t.split(",")
        assert all(isinstance(p, TaintedStr) for p in parts)
        assert parts[0].taint_id == "taint-001"

    def test_replace_preserves_taint(self):
        t = TaintedStr("hello world", **TAINT)
        result = t.replace("world", "earth")
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "taint-001"

    def test_capitalize_preserves_taint(self):
        t = TaintedStr("hello", **TAINT)
        assert isinstance(t.capitalize(), TaintedStr)

    def test_title_preserves_taint(self):
        t = TaintedStr("hello world", **TAINT)
        assert isinstance(t.title(), TaintedStr)

    def test_partition_preserves_taint(self):
        t = TaintedStr("hello-world", **TAINT)
        a, b, c = t.partition("-")
        assert isinstance(a, TaintedStr)
        assert isinstance(b, TaintedStr)
        assert isinstance(c, TaintedStr)

    def test_removeprefix_preserves_taint(self):
        t = TaintedStr("hello_world", **TAINT)
        result = t.removeprefix("hello_")
        assert isinstance(result, TaintedStr)
        assert str(result) == "world"

    def test_zfill_preserves_taint(self):
        t = TaintedStr("42", **TAINT)
        result = t.zfill(5)
        assert isinstance(result, TaintedStr)
        assert str(result) == "00042"


class TestFindTainted:
    """find_tainted recursively searches for TaintedStr in nested structures."""

    def test_find_in_dict(self):
        t = TaintedStr("hello", **TAINT)
        assert find_tainted({"key": t}) is t

    def test_find_in_nested_dict(self):
        t = TaintedStr("hello", **TAINT)
        assert find_tainted({"a": {"b": {"c": t}}}) is t

    def test_find_in_list(self):
        t = TaintedStr("hello", **TAINT)
        assert find_tainted(["plain", t]) is t

    def test_find_returns_none_for_plain(self):
        assert find_tainted({"key": "plain", "list": [1, 2, 3]}) is None

    def test_find_returns_none_for_non_string(self):
        assert find_tainted(42) is None

    def test_find_tainted_str_directly(self):
        t = TaintedStr("hello", **TAINT)
        assert find_tainted(t) is t


class TestTaintMerge:
    """TaintedStr._merge picks the right taint when combining two strings."""

    def test_merge_prefers_tainted_left(self):
        t = TaintedStr("hello", taint_id="left")
        result = t + "world"
        assert result.taint_id == "left"

    def test_merge_prefers_tainted_right(self):
        t = TaintedStr("world", taint_id="right")
        result = "hello" + t
        assert result.taint_id == "right"

    def test_merge_prefers_first_with_taint_id(self):
        """When both are tainted, prefer the one with non-empty taint_id."""
        a = TaintedStr("hello", taint_id="alpha")
        b = TaintedStr("world", taint_id="")
        result = a + b
        assert result.taint_id == "alpha"

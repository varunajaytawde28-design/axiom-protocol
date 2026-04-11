"""Tests for TaintedStr — all 30+ method overrides and 6 breakage workarounds."""

from __future__ import annotations

import pytest

from vt_protocol.observation.tainted_str import TaintedStr, _TaintedBytes, find_tainted


@pytest.fixture
def ts() -> TaintedStr:
    return TaintedStr("hello world", taint_id="t1", span_id="s1", agent_id="a1")


class TestConstruction:
    def test_basic(self, ts: TaintedStr) -> None:
        assert str(ts) == "hello world"
        assert ts.taint_id == "t1"
        assert ts.span_id == "s1"
        assert ts.agent_id == "a1"

    def test_isinstance_str(self, ts: TaintedStr) -> None:
        assert isinstance(ts, str)

    def test_empty(self) -> None:
        t = TaintedStr()
        assert t == ""
        assert t.taint_id == ""

    def test_repr(self, ts: TaintedStr) -> None:
        r = repr(ts)
        assert "TaintedStr" in r
        assert "t1" in r


# ---------------------------------------------------------------
# Breakage point 1: str.join
# ---------------------------------------------------------------

class TestJoin:
    def test_join_propagates(self, ts: TaintedStr) -> None:
        sep = TaintedStr(", ", taint_id="sep", span_id="s2", agent_id="a2")
        result = sep.join(["a", "b", "c"])
        assert result == "a, b, c"
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "sep"


# ---------------------------------------------------------------
# Breakage point 2: f-strings
# ---------------------------------------------------------------

class TestFString:
    def test_format_spec(self, ts: TaintedStr) -> None:
        result = f"{ts}"
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "t1"

    def test_format_with_spec(self, ts: TaintedStr) -> None:
        result = format(ts, "")
        assert isinstance(result, TaintedStr)


# ---------------------------------------------------------------
# Breakage point 3: re.sub workaround
# ---------------------------------------------------------------

class TestReSub:
    def test_re_sub(self, ts: TaintedStr) -> None:
        result = ts.re_sub(r"world", "earth")
        assert result == "hello earth"
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "t1"


# ---------------------------------------------------------------
# Breakage point 4: .format() and %
# ---------------------------------------------------------------

class TestFormat:
    def test_format_method(self) -> None:
        t = TaintedStr("Hello {}", taint_id="t1", span_id="s1", agent_id="a1")
        result = t.format("world")
        assert result == "Hello world"
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "t1"

    def test_mod_formatting(self) -> None:
        t = TaintedStr("Hello %s", taint_id="t1", span_id="s1", agent_id="a1")
        result = t % "world"
        assert result == "Hello world"
        assert isinstance(result, TaintedStr)


# ---------------------------------------------------------------
# Breakage point 5: encode/decode round-trip
# ---------------------------------------------------------------

class TestEncodeDecode:
    def test_encode_returns_tainted_bytes(self, ts: TaintedStr) -> None:
        b = ts.encode("utf-8")
        assert isinstance(b, _TaintedBytes)
        assert b.taint_id == "t1"

    def test_decode_recovers_taint(self, ts: TaintedStr) -> None:
        b = ts.encode("utf-8")
        recovered = b.decode("utf-8")
        assert isinstance(recovered, TaintedStr)
        assert recovered.taint_id == "t1"
        assert recovered == "hello world"

    def test_round_trip(self, ts: TaintedStr) -> None:
        result = ts.encode().decode()
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "t1"
        assert result.span_id == "s1"


# ---------------------------------------------------------------
# Breakage point 6: Pydantic strict mode
# ---------------------------------------------------------------

class TestPydanticCompat:
    def test_isinstance_str(self, ts: TaintedStr) -> None:
        """TaintedStr passes isinstance(x, str) checks."""
        assert isinstance(ts, str)

    def test_pydantic_field(self) -> None:
        """TaintedStr should work in Pydantic non-strict str fields."""
        from pydantic import BaseModel

        class M(BaseModel):
            text: str

        m = M(text=TaintedStr("test", taint_id="t1", span_id="s1", agent_id="a1"))
        assert m.text == "test"


# ---------------------------------------------------------------
# Concatenation
# ---------------------------------------------------------------

class TestConcatenation:
    def test_add(self, ts: TaintedStr) -> None:
        result = ts + " !"
        assert result == "hello world !"
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "t1"

    def test_radd(self, ts: TaintedStr) -> None:
        result = "prefix " + ts
        assert result == "prefix hello world"
        assert isinstance(result, TaintedStr)

    def test_mul(self, ts: TaintedStr) -> None:
        t = TaintedStr("ab", taint_id="t1", span_id="s1", agent_id="a1")
        result = t * 3
        assert result == "ababab"
        assert isinstance(result, TaintedStr)

    def test_rmul(self) -> None:
        t = TaintedStr("x", taint_id="t1", span_id="s1", agent_id="a1")
        result = 2 * t
        assert result == "xx"
        assert isinstance(result, TaintedStr)


# ---------------------------------------------------------------
# Slicing
# ---------------------------------------------------------------

class TestSlicing:
    def test_getitem(self, ts: TaintedStr) -> None:
        result = ts[0:5]
        assert result == "hello"
        assert isinstance(result, TaintedStr)
        assert result.taint_id == "t1"


# ---------------------------------------------------------------
# Case transforms
# ---------------------------------------------------------------

class TestCase:
    def test_lower(self) -> None:
        t = TaintedStr("HELLO", taint_id="t1", span_id="s1", agent_id="a1")
        assert t.lower() == "hello"
        assert isinstance(t.lower(), TaintedStr)

    def test_upper(self, ts: TaintedStr) -> None:
        assert ts.upper() == "HELLO WORLD"
        assert isinstance(ts.upper(), TaintedStr)

    def test_capitalize(self, ts: TaintedStr) -> None:
        assert isinstance(ts.capitalize(), TaintedStr)

    def test_title(self, ts: TaintedStr) -> None:
        assert isinstance(ts.title(), TaintedStr)

    def test_swapcase(self, ts: TaintedStr) -> None:
        assert isinstance(ts.swapcase(), TaintedStr)

    def test_casefold(self, ts: TaintedStr) -> None:
        assert isinstance(ts.casefold(), TaintedStr)


# ---------------------------------------------------------------
# Stripping / padding
# ---------------------------------------------------------------

class TestStripPad:
    def test_strip(self) -> None:
        t = TaintedStr("  hello  ", taint_id="t1", span_id="s1", agent_id="a1")
        assert t.strip() == "hello"
        assert isinstance(t.strip(), TaintedStr)

    def test_lstrip(self) -> None:
        t = TaintedStr("  hello", taint_id="t1", span_id="s1", agent_id="a1")
        assert isinstance(t.lstrip(), TaintedStr)

    def test_rstrip(self) -> None:
        t = TaintedStr("hello  ", taint_id="t1", span_id="s1", agent_id="a1")
        assert isinstance(t.rstrip(), TaintedStr)

    def test_center(self, ts: TaintedStr) -> None:
        assert isinstance(ts.center(20), TaintedStr)

    def test_ljust(self, ts: TaintedStr) -> None:
        assert isinstance(ts.ljust(20), TaintedStr)

    def test_rjust(self, ts: TaintedStr) -> None:
        assert isinstance(ts.rjust(20), TaintedStr)

    def test_zfill(self) -> None:
        t = TaintedStr("42", taint_id="t1", span_id="s1", agent_id="a1")
        assert isinstance(t.zfill(5), TaintedStr)


# ---------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------

class TestSplit:
    def test_split(self, ts: TaintedStr) -> None:
        parts = ts.split()
        assert parts == ["hello", "world"]
        assert all(isinstance(p, TaintedStr) for p in parts)
        assert parts[0].taint_id == "t1"

    def test_rsplit(self, ts: TaintedStr) -> None:
        parts = ts.rsplit(" ", 1)
        assert all(isinstance(p, TaintedStr) for p in parts)

    def test_splitlines(self) -> None:
        t = TaintedStr("a\nb\nc", taint_id="t1", span_id="s1", agent_id="a1")
        parts = t.splitlines()
        assert len(parts) == 3
        assert all(isinstance(p, TaintedStr) for p in parts)


# ---------------------------------------------------------------
# Replace / partition / misc
# ---------------------------------------------------------------

class TestReplaceMisc:
    def test_replace(self, ts: TaintedStr) -> None:
        result = ts.replace("world", "earth")
        assert result == "hello earth"
        assert isinstance(result, TaintedStr)

    def test_partition(self, ts: TaintedStr) -> None:
        a, b, c = ts.partition(" ")
        assert a == "hello"
        assert all(isinstance(x, TaintedStr) for x in (a, b, c))

    def test_rpartition(self, ts: TaintedStr) -> None:
        a, b, c = ts.rpartition(" ")
        assert all(isinstance(x, TaintedStr) for x in (a, b, c))

    def test_removeprefix(self, ts: TaintedStr) -> None:
        result = ts.removeprefix("hello ")
        assert result == "world"
        assert isinstance(result, TaintedStr)

    def test_removesuffix(self, ts: TaintedStr) -> None:
        result = ts.removesuffix(" world")
        assert result == "hello"
        assert isinstance(result, TaintedStr)

    def test_expandtabs(self) -> None:
        t = TaintedStr("a\tb", taint_id="t1", span_id="s1", agent_id="a1")
        assert isinstance(t.expandtabs(), TaintedStr)


# ---------------------------------------------------------------
# find_tainted utility
# ---------------------------------------------------------------

class TestFindTainted:
    def test_finds_in_dict(self) -> None:
        ts = TaintedStr("secret", taint_id="t1", span_id="s1", agent_id="a1")
        found = find_tainted({"key": ts})
        assert found is ts

    def test_finds_in_list(self) -> None:
        ts = TaintedStr("data", taint_id="t2", span_id="s2", agent_id="a2")
        found = find_tainted(["plain", ts, "other"])
        assert found is ts

    def test_finds_nested(self) -> None:
        ts = TaintedStr("deep", taint_id="t3", span_id="s3", agent_id="a3")
        found = find_tainted({"messages": [{"content": ts}]})
        assert found is ts

    def test_returns_none_for_plain(self) -> None:
        assert find_tainted({"key": "value"}) is None
        assert find_tainted("just a string") is None
        assert find_tainted(42) is None

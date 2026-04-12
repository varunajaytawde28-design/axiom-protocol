"""Adversarial test: Unicode Edge Cases.

Tests that Unicode-heavy content in decisions, contradictions,
and governance data does not break serialization or pipelines.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import main
from vt_protocol.decisions.models import Decision, Dimension

from tests.helpers.decision_factory import make_decision
from tests.helpers.repo_factory import create_project, write_decision

pytestmark = pytest.mark.adversarial


UNICODE_STRINGS = [
    "日本語のテスト — Japanese decision title",
    "Решение на русском языке — Russian decision",
    "قرار باللغة العربية — Arabic decision",
    "🎯 Emoji in decision title 🚀",
    "café résumé naïve — accented characters",
    "数学: ∫∑∏√∞ — math symbols",
    "Zero-width: \u200b\u200c\u200d — invisible chars",
    "RTL override: \u202e reversed text",
    "Surrogate pair: 𝕳𝖊𝖑𝖑𝖔 — mathematical bold fraktur",
    "Mixed: 你好world مرحبا 🌍",
]


class TestUnicodeInDecisions:
    """Unicode content must survive the full pipeline."""

    @pytest.fixture
    def project_with_unicode(self, tmp_path):
        root = create_project(tmp_path)
        decisions = []
        for i, text in enumerate(UNICODE_STRINGS):
            d = make_decision(
                title=text[:400],
                content=f"Decision content: {text}",
                dimensions=[Dimension.DATABASE],
            )
            write_decision(root, d, filename=f"unicode-{i:03d}.json")
            decisions.append(d)
        return root, decisions

    def test_check_with_unicode(self, project_with_unicode):
        """CLI check handles Unicode decisions."""
        root, _ = project_with_unicode
        runner = CliRunner()
        result = runner.invoke(main, ["check", "--path", str(root), "--json-output"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["active_decisions"] == len(UNICODE_STRINGS)

    def test_decision_roundtrip(self, project_with_unicode):
        """Decisions survive write → read JSON roundtrip."""
        root, original_decisions = project_with_unicode
        decisions_dir = root / ".smm" / "decisions"
        for fp in sorted(decisions_dir.glob("*.json")):
            raw = json.loads(fp.read_text())
            d = Decision(**raw)
            assert len(d.title) > 0
            assert len(d.content) > 0

    def test_gate_with_unicode(self, project_with_unicode):
        """Quality gate handles Unicode without crashing."""
        root, _ = project_with_unicode
        runner = CliRunner()
        result = runner.invoke(main, ["gate", "--path", str(root), "--json-output"])
        assert result.exit_code == 0


class TestUnicodeInTaintedStr:
    """TaintedStr preserves taint through Unicode operations."""

    def test_unicode_content(self):
        from vt_protocol.observation.tainted_str import TaintedStr

        for text in UNICODE_STRINGS:
            ts = TaintedStr(text, taint_id="t1")
            assert isinstance(ts, TaintedStr)
            assert ts.taint_id == "t1"
            assert str(ts) == text

    def test_unicode_split_join(self):
        from vt_protocol.observation.tainted_str import TaintedStr

        ts = TaintedStr("日本語,中文,한국어", taint_id="t1")
        parts = ts.split(",")
        assert all(isinstance(p, TaintedStr) for p in parts)
        rejoined = TaintedStr(",", taint_id="t1").join(parts)
        assert isinstance(rejoined, TaintedStr)
        assert rejoined == "日本語,中文,한국어"

    def test_unicode_encode_decode(self):
        from vt_protocol.observation.tainted_str import TaintedStr

        ts = TaintedStr("café ñ 日本語", taint_id="t1")
        roundtripped = ts.encode("utf-8").decode("utf-8")
        assert isinstance(roundtripped, TaintedStr)
        assert roundtripped.taint_id == "t1"
        assert roundtripped == "café ñ 日本語"

"""Tests for SDK monkey-patching — span building, cost estimation, taint scanning."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from vt_protocol.observation.patch import (
    _build_anthropic_input,
    _build_openai_input,
    _build_span,
    _extract_anthropic_response,
    _extract_openai_response,
    _scan_tainted,
    _safe_serialize,
    _taint_anthropic_response,
    _taint_openai_response,
    estimate_cost,
    set_span_callback,
)
from vt_protocol.observation.tainted_str import TaintedStr


class TestEstimateCost:
    def test_anthropic_haiku(self) -> None:
        cost = estimate_cost("claude-haiku-4-5-20251001", 1000, 500)
        assert cost > 0
        assert cost < 0.01  # Haiku is cheap

    def test_anthropic_sonnet(self) -> None:
        cost = estimate_cost("claude-sonnet-4-20250514", 1000, 500)
        assert cost > 0

    def test_openai_gpt4o(self) -> None:
        cost = estimate_cost("gpt-4o-2024-05-13", 1000, 500)
        assert cost > 0

    def test_unknown_model(self) -> None:
        cost = estimate_cost("unknown-model-v1", 1000, 500)
        assert cost == 0.0


class TestBuildSpan:
    def test_creates_span(self) -> None:
        span = _build_span(
            model="claude-haiku-4-5",
            provider="anthropic",
            input_messages=[{"role": "user", "content": "hello"}],
            output_text="world",
            tokens_in=10,
            tokens_out=5,
            latency_ms=100.0,
        )
        assert span.model == "claude-haiku-4-5"
        assert span.provider == "anthropic"
        assert span.output == "world"
        assert span.tokens_in == 10
        assert span.tokens_out == 5
        assert span.latency_ms == 100.0
        assert len(span.span_id) == 16

    def test_with_tainted_source(self) -> None:
        span = _build_span(
            model="gpt-4o",
            provider="openai",
            input_messages=[],
            output_text="",
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
            tainted_source="upstream-span-123",
        )
        assert span.tainted_source == "upstream-span-123"


class TestAnthropicInput:
    def test_extracts_kwargs(self) -> None:
        result = _build_anthropic_input(
            (),
            {"model": "claude-haiku", "messages": [{"role": "user", "content": "hi"}], "system": "Be helpful"},
        )
        assert result["model"] == "claude-haiku"
        assert result["system"] == "Be helpful"
        assert len(result["messages"]) == 1


class TestAnthropicResponse:
    def test_extracts_text_and_tokens(self) -> None:
        mock_resp = MagicMock()
        mock_block = MagicMock()
        mock_block.text = "Hello world"
        mock_resp.content = [mock_block]
        mock_resp.usage.input_tokens = 10
        mock_resp.usage.output_tokens = 5

        result = _extract_anthropic_response(mock_resp)
        assert result["output_text"] == "Hello world"
        assert result["tokens_in"] == 10
        assert result["tokens_out"] == 5


class TestTaintAnthropicResponse:
    def test_injects_tainted_str(self) -> None:
        mock_resp = MagicMock()
        mock_block = MagicMock()
        mock_block.text = "response text"
        mock_resp.content = [mock_block]

        _taint_anthropic_response(mock_resp, "span-123")
        assert isinstance(mock_block.text, TaintedStr)
        assert mock_block.text.span_id == "span-123"


class TestOpenAIInput:
    def test_extracts_kwargs(self) -> None:
        result = _build_openai_input(
            (),
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "test"}]},
        )
        assert result["model"] == "gpt-4o"
        assert len(result["messages"]) == 1


class TestOpenAIResponse:
    def test_extracts_text_and_tokens(self) -> None:
        mock_resp = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = "OpenAI response"
        mock_resp.choices = [MagicMock(message=mock_msg)]
        mock_resp.usage.prompt_tokens = 20
        mock_resp.usage.completion_tokens = 10

        result = _extract_openai_response(mock_resp)
        assert result["output_text"] == "OpenAI response"
        assert result["tokens_in"] == 20
        assert result["tokens_out"] == 10


class TestTaintOpenAIResponse:
    def test_injects_tainted_str(self) -> None:
        mock_resp = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = "openai text"
        mock_resp.choices = [MagicMock(message=mock_msg)]

        _taint_openai_response(mock_resp, "oai-span")
        assert isinstance(mock_msg.content, TaintedStr)
        assert mock_msg.content.span_id == "oai-span"


class TestScanTainted:
    def test_finds_tainted_in_kwargs(self) -> None:
        ts = TaintedStr("prompt", taint_id="t1", span_id="upstream", agent_id="a1")
        result = _scan_tainted((), {"messages": [{"content": ts}]})
        assert result == "upstream"

    def test_returns_none_for_plain(self) -> None:
        result = _scan_tainted((), {"messages": [{"content": "plain"}]})
        assert result is None


class TestSpanCallback:
    def test_callback_invoked(self) -> None:
        captured = []
        set_span_callback(lambda span: captured.append(span))
        span = _build_span(
            model="test", provider="test",
            input_messages=[], output_text="", tokens_in=0, tokens_out=0, latency_ms=0,
        )
        from vt_protocol.observation.patch import _emit_span
        _emit_span(span)
        assert len(captured) == 1
        set_span_callback(None)


class TestSafeSerialize:
    def test_plain_dict(self) -> None:
        result = _safe_serialize({"key": "value"})
        assert result == {"key": "value"}

    def test_non_serializable(self) -> None:
        result = _safe_serialize(object())
        assert isinstance(result, str)

    def test_nested_list(self) -> None:
        result = _safe_serialize([{"a": 1}, {"b": [2, 3]}])
        assert result == [{"a": 1}, {"b": [2, 3]}]

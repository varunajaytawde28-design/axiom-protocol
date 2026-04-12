"""Tests for LLM provider abstraction layer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from vt_protocol.decisions.llm_providers import (
    NONE_CONFIDENCE_CAP,
    OLLAMA_CONFIDENCE_CAP,
    AnthropicProvider,
    LLMProvider,
    NoneProvider,
    OllamaProvider,
    OpenAIProvider,
    _parse_llm_json,
    get_llm_provider,
    test_ollama_connection,
)
from vt_protocol.decisions.models import ModelConfig
from vt_protocol.exceptions import LLMProviderError


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestGetLlmProvider:
    def test_anthropic_provider(self):
        cfg = ModelConfig(provider="anthropic")
        p = get_llm_provider(cfg)
        assert isinstance(p, AnthropicProvider)
        assert p.provider_name == "anthropic"

    def test_openai_provider(self):
        cfg = ModelConfig(provider="openai", model="gpt-4o-mini")
        p = get_llm_provider(cfg)
        assert isinstance(p, OpenAIProvider)
        assert p.provider_name == "openai"

    def test_ollama_provider(self):
        cfg = ModelConfig(provider="ollama", model="llama3:8b")
        p = get_llm_provider(cfg)
        assert isinstance(p, OllamaProvider)
        assert p.provider_name == "ollama"

    def test_none_provider(self):
        cfg = ModelConfig(provider="none")
        p = get_llm_provider(cfg)
        assert isinstance(p, NoneProvider)
        assert p.provider_name == "none"

    def test_unknown_provider_raises(self):
        cfg = ModelConfig(provider="foobar")
        with pytest.raises(LLMProviderError, match="Unknown LLM provider"):
            get_llm_provider(cfg)

    def test_default_config(self):
        p = get_llm_provider()
        assert isinstance(p, AnthropicProvider)

    def test_case_insensitive(self):
        cfg = ModelConfig(provider="Anthropic")
        p = get_llm_provider(cfg)
        assert isinstance(p, AnthropicProvider)


# ---------------------------------------------------------------------------
# NoneProvider
# ---------------------------------------------------------------------------


class TestNoneProvider:
    def test_always_returns_none(self):
        p = NoneProvider(ModelConfig(provider="none"))
        result = p.check("system", "user")
        assert result is None

    def test_confidence_cap(self):
        p = NoneProvider(ModelConfig(provider="none"))
        assert p.confidence_cap == NONE_CONFIDENCE_CAP


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


class TestAnthropicProvider:
    def test_missing_api_key_returns_none(self):
        cfg = ModelConfig(provider="anthropic", api_key_env="MISSING_KEY_VAR")
        p = AnthropicProvider(cfg)
        result = p.check("system", "user")
        assert result is None

    @patch.dict("os.environ", {"TEST_ANTHROPIC_KEY": "sk-test"})
    @patch("anthropic.Anthropic")
    def test_successful_call(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps({
            "reasoning": "test reason",
            "verdict": "compatible",
            "confidence": 0.9,
            "evidence_a": "a says X",
            "evidence_b": "b says Y",
        }))]
        mock_client.messages.create.return_value = mock_response

        cfg = ModelConfig(provider="anthropic", api_key_env="TEST_ANTHROPIC_KEY")
        p = AnthropicProvider(cfg)
        result = p.check("system", "user")

        assert result is not None
        assert result["verdict"] == "compatible"
        assert result["confidence"] == 0.9

    def test_confidence_cap_is_1(self):
        p = AnthropicProvider(ModelConfig(provider="anthropic"))
        assert p.confidence_cap == 1.0


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    def test_missing_api_key_returns_none(self):
        cfg = ModelConfig(provider="openai", api_key_env="MISSING_OPENAI_KEY_VAR")
        p = OpenAIProvider(cfg)
        result = p.check("system", "user")
        assert result is None

    @patch.dict("os.environ", {"TEST_OPENAI_KEY": "sk-test"})
    @patch("openai.OpenAI")
    def test_successful_call(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps({
            "reasoning": "openai reason",
            "verdict": "tension",
            "confidence": 0.7,
            "evidence_a": "a",
            "evidence_b": "b",
        })))]
        mock_client.chat.completions.create.return_value = mock_response

        cfg = ModelConfig(provider="openai", api_key_env="TEST_OPENAI_KEY")
        p = OpenAIProvider(cfg)
        result = p.check("system", "user")

        assert result is not None
        assert result["verdict"] == "tension"
        mock_client.chat.completions.create.assert_called_once()


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    @patch("httpx.post")
    def test_successful_call(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": json.dumps({
                "reasoning": "local reason",
                "verdict": "contradiction",
                "confidence": 0.95,
                "evidence_a": "a",
                "evidence_b": "b",
            })}},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        cfg = ModelConfig(provider="ollama", model="llama3:8b", base_url="http://localhost:11434")
        p = OllamaProvider(cfg)
        result = p.check("system", "user")

        assert result is not None
        assert result["verdict"] == "contradiction"
        # Confidence should be capped
        assert result["confidence"] <= OLLAMA_CONFIDENCE_CAP

    @patch("httpx.post", side_effect=Exception("Connection refused"))
    def test_connection_failure_returns_none(self, mock_post):
        cfg = ModelConfig(provider="ollama", model="llama3:8b")
        p = OllamaProvider(cfg)
        result = p.check("system", "user")
        assert result is None

    def test_confidence_cap(self):
        p = OllamaProvider(ModelConfig(provider="ollama"))
        assert p.confidence_cap == OLLAMA_CONFIDENCE_CAP


# ---------------------------------------------------------------------------
# Ollama connection test
# ---------------------------------------------------------------------------


class TestOllamaConnection:
    @patch("httpx.get")
    def test_connected(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"models": [{"name": "llama3:8b"}, {"name": "mistral:7b"}]},
        )
        mock_get.return_value.raise_for_status = MagicMock()

        result = test_ollama_connection()
        assert result["connected"] is True
        assert "llama3:8b" in result["models"]
        assert result["error"] is None

    @patch("httpx.get", side_effect=Exception("Connection refused"))
    def test_not_connected(self, mock_get):
        result = test_ollama_connection()
        assert result["connected"] is False
        assert len(result["models"]) == 0
        assert result["error"] is not None


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestParseLlmJson:
    def test_valid_json(self):
        raw = json.dumps({
            "reasoning": "test",
            "verdict": "compatible",
            "confidence": 0.8,
            "evidence_a": "a",
            "evidence_b": "b",
        })
        result = _parse_llm_json(raw)
        assert result["verdict"] == "compatible"

    def test_json_in_markdown_fences(self):
        raw = '```json\n{"reasoning": "t", "verdict": "tension", "confidence": 0.5, "evidence_a": "a", "evidence_b": "b"}\n```'
        result = _parse_llm_json(raw)
        assert result["verdict"] == "tension"

    def test_missing_field_raises(self):
        raw = json.dumps({"reasoning": "t", "verdict": "tension"})
        with pytest.raises(LLMProviderError, match="Missing"):
            _parse_llm_json(raw)

    def test_invalid_verdict_raises(self):
        raw = json.dumps({
            "reasoning": "t",
            "verdict": "maybe",
            "confidence": 0.5,
            "evidence_a": "a",
            "evidence_b": "b",
        })
        with pytest.raises(LLMProviderError, match="Invalid verdict"):
            _parse_llm_json(raw)

    def test_no_json_raises(self):
        with pytest.raises(LLMProviderError, match="No JSON"):
            _parse_llm_json("just plain text with no json")


# ---------------------------------------------------------------------------
# ModelConfig
# ---------------------------------------------------------------------------


class TestModelConfig:
    def test_defaults(self):
        cfg = ModelConfig()
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-haiku-4-5-20251001"
        assert cfg.temperature == 0.0

    def test_ollama_config(self):
        cfg = ModelConfig(
            provider="ollama",
            model="llama3:8b",
            base_url="http://localhost:11434",
        )
        assert cfg.provider == "ollama"
        assert cfg.base_url == "http://localhost:11434"

    def test_temperature_bounds(self):
        with pytest.raises(Exception):
            ModelConfig(temperature=-1.0)
        with pytest.raises(Exception):
            ModelConfig(temperature=3.0)

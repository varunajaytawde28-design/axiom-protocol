"""LLM provider abstraction for pluggable contradiction detection.

Supports four providers:
  - anthropic: Claude via Anthropic API (default)
  - openai: GPT via OpenAI API with structured output
  - ollama: Local LLM via Ollama's OpenAI-compatible API
  - none: Skip LLM, NLI-only with capped confidence

Factory function ``get_llm_provider()`` returns the right client
based on ModelConfig from governance.yaml.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

from vt_protocol.decisions.models import ModelConfig
from vt_protocol.exceptions import LLMProviderError

logger = logging.getLogger(__name__)

# Confidence caps per provider
OLLAMA_CONFIDENCE_CAP = 0.85
NONE_CONFIDENCE_CAP = 0.6


class LLMProvider(ABC):
    """Abstract base for LLM contradiction judgment providers."""

    @abstractmethod
    def check(
        self,
        system_prompt: str,
        user_msg: str,
        *,
        temperature: float | None = None,
    ) -> dict[str, Any] | None:
        """Send a contradiction check prompt and return parsed JSON response.

        Returns dict with keys: reasoning, verdict, confidence, evidence_a, evidence_b.
        Returns None if the check cannot be performed.
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name."""

    @property
    def confidence_cap(self) -> float:
        """Maximum confidence this provider can return. 1.0 = no cap."""
        return 1.0


class AnthropicProvider(LLMProvider):
    """Claude via Anthropic API."""

    def __init__(self, config: ModelConfig) -> None:
        self._config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise LLMProviderError("anthropic package not installed. Run: pip install anthropic")
            key_env = self._config.api_key_env or "ANTHROPIC_API_KEY"
            key = os.environ.get(key_env)
            if not key:
                raise LLMProviderError(f"Missing API key: set {key_env} environment variable")
            kwargs: dict[str, Any] = {"api_key": key}
            if self._config.base_url:
                kwargs["base_url"] = self._config.base_url
            self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def check(
        self,
        system_prompt: str,
        user_msg: str,
        *,
        temperature: float | None = None,
    ) -> dict[str, Any] | None:
        try:
            client = self._get_client()
        except LLMProviderError:
            logger.debug("Anthropic provider not available", exc_info=True)
            return None

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
        }
        temp = temperature if temperature is not None else self._config.temperature
        if temp is not None:
            kwargs["temperature"] = temp

        try:
            response = client.messages.create(**kwargs)
            raw = response.content[0].text
            return _parse_llm_json(raw)
        except Exception:
            logger.exception("Anthropic LLM call failed")
            return None

    @property
    def provider_name(self) -> str:
        return "anthropic"


class OpenAIProvider(LLMProvider):
    """GPT via OpenAI API with JSON response format."""

    def __init__(self, config: ModelConfig) -> None:
        self._config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise LLMProviderError("openai package not installed. Run: pip install openai")
            key_env = self._config.api_key_env or "OPENAI_API_KEY"
            key = os.environ.get(key_env)
            if not key:
                raise LLMProviderError(f"Missing API key: set {key_env} environment variable")
            kwargs: dict[str, Any] = {"api_key": key}
            if self._config.base_url:
                kwargs["base_url"] = self._config.base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def check(
        self,
        system_prompt: str,
        user_msg: str,
        *,
        temperature: float | None = None,
    ) -> dict[str, Any] | None:
        try:
            client = self._get_client()
        except LLMProviderError:
            logger.debug("OpenAI provider not available", exc_info=True)
            return None

        temp = temperature if temperature is not None else self._config.temperature

        try:
            response = client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=temp,
                max_tokens=1024,
            )
            raw = response.choices[0].message.content
            return _parse_llm_json(raw)
        except Exception:
            logger.exception("OpenAI LLM call failed")
            return None

    @property
    def provider_name(self) -> str:
        return "openai"


class OllamaProvider(LLMProvider):
    """Local LLM via Ollama's OpenAI-compatible API."""

    def __init__(self, config: ModelConfig) -> None:
        self._config = config

    def check(
        self,
        system_prompt: str,
        user_msg: str,
        *,
        temperature: float | None = None,
    ) -> dict[str, Any] | None:
        import httpx

        base_url = self._config.base_url or "http://localhost:11434"
        # Use Ollama's /api/chat endpoint
        url = f"{base_url.rstrip('/')}/api/chat"
        temp = temperature if temperature is not None else self._config.temperature

        payload = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "format": "json",
            "stream": False,
        }
        if temp is not None:
            payload["options"] = {"temperature": temp}

        try:
            resp = httpx.post(url, json=payload, timeout=self._config.timeout_seconds)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("message", {}).get("content", "")
            result = _parse_llm_json(raw)
            if result:
                # Cap confidence for local models
                result["confidence"] = min(float(result["confidence"]), OLLAMA_CONFIDENCE_CAP)
            return result
        except Exception:
            logger.exception("Ollama LLM call failed")
            return None

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def confidence_cap(self) -> float:
        return OLLAMA_CONFIDENCE_CAP


class NoneProvider(LLMProvider):
    """No LLM — NLI-only mode with capped confidence."""

    def __init__(self, config: ModelConfig | None = None) -> None:
        self._config = config

    def check(
        self,
        system_prompt: str,
        user_msg: str,
        *,
        temperature: float | None = None,
    ) -> dict[str, Any] | None:
        # Always returns None — the pipeline will use NLI scores only
        return None

    @property
    def provider_name(self) -> str:
        return "none"

    @property
    def confidence_cap(self) -> float:
        return NONE_CONFIDENCE_CAP


def get_llm_provider(config: ModelConfig | None = None) -> LLMProvider:
    """Factory: return the appropriate LLM provider based on config.

    Args:
        config: ModelConfig from governance.yaml. Uses defaults if None.

    Returns:
        An LLMProvider instance.

    Raises:
        LLMProviderError: If the provider string is unknown.
    """
    if config is None:
        config = ModelConfig()

    providers = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "ollama": OllamaProvider,
        "none": NoneProvider,
    }

    provider_cls = providers.get(config.provider.lower())
    if provider_cls is None:
        raise LLMProviderError(
            f"Unknown LLM provider: '{config.provider}'. "
            f"Choose from: {', '.join(providers)}"
        )
    return provider_cls(config)


def test_ollama_connection(base_url: str = "http://localhost:11434") -> dict[str, Any]:
    """Test Ollama connection and return available models.

    Returns:
        Dict with 'connected' (bool), 'models' (list of model names), 'error' (str or None).
    """
    import httpx

    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        resp = httpx.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        return {"connected": True, "models": models, "error": None}
    except Exception as exc:
        return {"connected": False, "models": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Shared JSON parsing
# ---------------------------------------------------------------------------


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """Parse LLM JSON response, stripping markdown fences if present."""
    import re

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise LLMProviderError(f"No JSON found in LLM response: {raw[:200]}")

    data = json.loads(match.group(0))

    for field in ("reasoning", "verdict", "confidence", "evidence_a", "evidence_b"):
        if field not in data:
            raise LLMProviderError(f"Missing '{field}' in LLM response")

    verdict_raw = data["verdict"].lower().strip()
    if verdict_raw not in ("contradiction", "tension", "compatible"):
        raise LLMProviderError(f"Invalid verdict: {verdict_raw}")
    data["verdict"] = verdict_raw

    return data

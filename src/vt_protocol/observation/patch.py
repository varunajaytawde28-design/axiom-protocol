"""SDK monkey-patching — wrapt-based instrumentation for Anthropic + OpenAI.

Ported from Lattice's patch.py. Intercepts LLM SDK calls to capture:
  - Input messages and model selection
  - Output text and token usage
  - Latency and cost
  - TaintedStr injection for causal tracking
  - Streaming wrappers that accumulate chunks

Supports both sync and async call paths.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from vt_protocol.observation.models import Span
from vt_protocol.observation.tainted_str import TaintedStr, find_tainted

logger = logging.getLogger(__name__)

# Approximate cost per 1M tokens (USD) — update as pricing changes
_COST_TABLE: dict[str, tuple[float, float]] = {
    # model_prefix: (input_per_1M, output_per_1M)
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.25, 1.25),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5": (0.5, 1.5),
}

_patched: set[str] = set()
_span_callback: Any = None


def set_span_callback(callback: Any) -> None:
    """Set a callback invoked with each captured Span."""
    global _span_callback
    _span_callback = callback


def patch_all() -> list[str]:
    """Patch all available SDKs. Returns list of patched providers."""
    patched = []
    if _patch_anthropic():
        patched.append("anthropic")
    if _patch_openai():
        patched.append("openai")
    return patched


def unpatch_all() -> None:
    """Remove all patches (best-effort)."""
    _patched.clear()


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost based on model name and token counts."""
    model_lower = model.lower()
    for prefix, (in_cost, out_cost) in _COST_TABLE.items():
        if prefix in model_lower:
            return (tokens_in * in_cost + tokens_out * out_cost) / 1_000_000
    return 0.0


# ---------------------------------------------------------------------------
# Span building
# ---------------------------------------------------------------------------


def _build_span(
    *,
    model: str,
    provider: str,
    input_messages: list[dict[str, Any]],
    output_text: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: float,
    status: str = "ok",
    framework: str = "",
    tainted_source: str | None = None,
) -> Span:
    """Build a Span from captured call data."""
    cost = estimate_cost(model, tokens_in, tokens_out)
    return Span(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex[:16],
        timestamp=time.time(),
        model=model,
        provider=provider,
        input_messages=json.dumps(input_messages),
        output=output_text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        latency_ms=latency_ms,
        status=status,
        framework=framework,
        tainted_source=tainted_source,
    )


def _emit_span(span: Span) -> None:
    """Emit a captured span via callback."""
    if _span_callback:
        try:
            _span_callback(span)
        except Exception:
            logger.debug("Span callback error", exc_info=True)


# ---------------------------------------------------------------------------
# Input/output extraction — Anthropic
# ---------------------------------------------------------------------------


def _build_anthropic_input(args: tuple, kwargs: dict) -> dict[str, Any]:
    """Extract model and messages from Anthropic Messages.create() call."""
    model = kwargs.get("model", args[0] if args else "unknown")
    messages = kwargs.get("messages", args[1] if len(args) > 1 else [])
    system = kwargs.get("system", "")
    return {
        "model": str(model),
        "messages": _safe_serialize(messages),
        "system": str(system) if system else "",
    }


def _extract_anthropic_response(response: Any) -> dict[str, Any]:
    """Extract text, tokens from an Anthropic Message response."""
    output_text = ""
    tokens_in = 0
    tokens_out = 0

    try:
        for block in response.content:
            if hasattr(block, "text"):
                output_text += block.text
        if hasattr(response, "usage"):
            tokens_in = getattr(response.usage, "input_tokens", 0)
            tokens_out = getattr(response.usage, "output_tokens", 0)
    except Exception:
        logger.debug("Failed to extract Anthropic response", exc_info=True)

    return {"output_text": output_text, "tokens_in": tokens_in, "tokens_out": tokens_out}


def _taint_anthropic_response(response: Any, span_id: str) -> None:
    """Inject TaintedStr into Anthropic response content blocks."""
    try:
        for block in response.content:
            if hasattr(block, "text") and isinstance(block.text, str):
                block.text = TaintedStr(
                    block.text,
                    taint_id=span_id,
                    span_id=span_id,
                    agent_id="",
                )
    except Exception:
        logger.debug("Failed to taint Anthropic response", exc_info=True)


# ---------------------------------------------------------------------------
# Input/output extraction — OpenAI
# ---------------------------------------------------------------------------


def _build_openai_input(args: tuple, kwargs: dict) -> dict[str, Any]:
    """Extract model and messages from OpenAI chat.completions.create() call."""
    model = kwargs.get("model", args[0] if args else "unknown")
    messages = kwargs.get("messages", args[1] if len(args) > 1 else [])
    return {
        "model": str(model),
        "messages": _safe_serialize(messages),
    }


def _extract_openai_response(response: Any) -> dict[str, Any]:
    """Extract text, tokens from an OpenAI ChatCompletion response."""
    output_text = ""
    tokens_in = 0
    tokens_out = 0

    try:
        if response.choices:
            msg = response.choices[0].message
            output_text = getattr(msg, "content", "") or ""
        if hasattr(response, "usage") and response.usage:
            tokens_in = getattr(response.usage, "prompt_tokens", 0)
            tokens_out = getattr(response.usage, "completion_tokens", 0)
    except Exception:
        logger.debug("Failed to extract OpenAI response", exc_info=True)

    return {"output_text": output_text, "tokens_in": tokens_in, "tokens_out": tokens_out}


def _taint_openai_response(response: Any, span_id: str) -> None:
    """Inject TaintedStr into OpenAI response choices."""
    try:
        if response.choices:
            msg = response.choices[0].message
            if hasattr(msg, "content") and isinstance(msg.content, str):
                msg.content = TaintedStr(
                    msg.content,
                    taint_id=span_id,
                    span_id=span_id,
                    agent_id="",
                )
    except Exception:
        logger.debug("Failed to taint OpenAI response", exc_info=True)


# ---------------------------------------------------------------------------
# Taint scanning
# ---------------------------------------------------------------------------


def _scan_tainted(args: tuple, kwargs: dict) -> str | None:
    """Scan call arguments for TaintedStr, return source span_id if found."""
    for container in (args, kwargs):
        found = find_tainted(container)
        if found is not None:
            return found.span_id
    return None


# ---------------------------------------------------------------------------
# Streaming wrappers
# ---------------------------------------------------------------------------


class AnthropicSyncStreamWrapper:
    """Wraps anthropic sync Stream to accumulate text and emit span on exit."""

    def __init__(self, stream: Any, *, model: str, input_data: dict, tainted_source: str | None, start_time: float) -> None:
        self._stream = stream
        self._model = model
        self._input_data = input_data
        self._tainted_source = tainted_source
        self._start = start_time
        self._text = ""
        self._tokens_in = 0
        self._tokens_out = 0

    def __iter__(self) -> AnthropicSyncStreamWrapper:
        return self

    def __next__(self) -> Any:
        event = next(self._stream)
        self._accumulate(event)
        return event

    def __enter__(self) -> AnthropicSyncStreamWrapper:
        return self

    def __exit__(self, *exc: Any) -> None:
        # Drain remaining events
        try:
            for event in self._stream:
                self._accumulate(event)
        except StopIteration:
            pass
        self._emit()

    def _accumulate(self, event: Any) -> None:
        if hasattr(event, "type"):
            if event.type == "content_block_delta" and hasattr(event, "delta"):
                delta = event.delta
                if hasattr(delta, "text"):
                    self._text += delta.text
            elif event.type == "message_delta" and hasattr(event, "usage"):
                self._tokens_out = getattr(event.usage, "output_tokens", self._tokens_out)
            elif event.type == "message_start" and hasattr(event, "message"):
                usage = getattr(event.message, "usage", None)
                if usage:
                    self._tokens_in = getattr(usage, "input_tokens", 0)

    def _emit(self) -> None:
        latency = (time.monotonic() - self._start) * 1000
        span = _build_span(
            model=self._model,
            provider="anthropic",
            input_messages=[self._input_data],
            output_text=self._text,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            latency_ms=latency,
            tainted_source=self._tainted_source,
        )
        _emit_span(span)


class AnthropicAsyncStreamWrapper:
    """Wraps anthropic async Stream to accumulate text and emit span on exit."""

    def __init__(self, stream: Any, *, model: str, input_data: dict, tainted_source: str | None, start_time: float) -> None:
        self._stream = stream
        self._model = model
        self._input_data = input_data
        self._tainted_source = tainted_source
        self._start = start_time
        self._text = ""
        self._tokens_in = 0
        self._tokens_out = 0

    def __aiter__(self) -> AnthropicAsyncStreamWrapper:
        return self

    async def __anext__(self) -> Any:
        event = await self._stream.__anext__()
        self._accumulate(event)
        return event

    async def __aenter__(self) -> AnthropicAsyncStreamWrapper:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        try:
            async for event in self._stream:
                self._accumulate(event)
        except StopAsyncIteration:
            pass
        self._emit()

    def _accumulate(self, event: Any) -> None:
        if hasattr(event, "type"):
            if event.type == "content_block_delta" and hasattr(event, "delta"):
                delta = event.delta
                if hasattr(delta, "text"):
                    self._text += delta.text
            elif event.type == "message_delta" and hasattr(event, "usage"):
                self._tokens_out = getattr(event.usage, "output_tokens", self._tokens_out)
            elif event.type == "message_start" and hasattr(event, "message"):
                usage = getattr(event.message, "usage", None)
                if usage:
                    self._tokens_in = getattr(usage, "input_tokens", 0)

    def _emit(self) -> None:
        latency = (time.monotonic() - self._start) * 1000
        span = _build_span(
            model=self._model,
            provider="anthropic",
            input_messages=[self._input_data],
            output_text=self._text,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            latency_ms=latency,
            tainted_source=self._tainted_source,
        )
        _emit_span(span)


class OpenAISyncStreamWrapper:
    """Wraps OpenAI sync stream to accumulate chunks and emit span."""

    def __init__(self, stream: Any, *, model: str, input_data: dict, tainted_source: str | None, start_time: float) -> None:
        self._stream = stream
        self._model = model
        self._input_data = input_data
        self._tainted_source = tainted_source
        self._start = start_time
        self._text = ""
        self._tokens_in = 0
        self._tokens_out = 0

    def __iter__(self) -> OpenAISyncStreamWrapper:
        return self

    def __next__(self) -> Any:
        chunk = next(self._stream)
        self._accumulate(chunk)
        return chunk

    def __enter__(self) -> OpenAISyncStreamWrapper:
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            for chunk in self._stream:
                self._accumulate(chunk)
        except StopIteration:
            pass
        self._emit()

    def _accumulate(self, chunk: Any) -> None:
        try:
            if chunk.choices and chunk.choices[0].delta:
                content = getattr(chunk.choices[0].delta, "content", None)
                if content:
                    self._text += content
            if hasattr(chunk, "usage") and chunk.usage:
                self._tokens_in = getattr(chunk.usage, "prompt_tokens", self._tokens_in)
                self._tokens_out = getattr(chunk.usage, "completion_tokens", self._tokens_out)
        except Exception:
            pass

    def _emit(self) -> None:
        latency = (time.monotonic() - self._start) * 1000
        span = _build_span(
            model=self._model,
            provider="openai",
            input_messages=[self._input_data],
            output_text=self._text,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            latency_ms=latency,
            tainted_source=self._tainted_source,
        )
        _emit_span(span)


# ---------------------------------------------------------------------------
# Patching — Anthropic
# ---------------------------------------------------------------------------


def _patch_anthropic() -> bool:
    """Patch anthropic SDK using wrapt."""
    if "anthropic" in _patched:
        return True
    try:
        import wrapt
        import anthropic  # noqa: F401

        @wrapt.patch_function_wrapper("anthropic.resources.messages", "Messages.create")
        def _wrap_sync(wrapped: Any, instance: Any, args: tuple, kwargs: dict) -> Any:
            input_data = _build_anthropic_input(args, kwargs)
            tainted_source = _scan_tainted(args, kwargs)
            stream = kwargs.get("stream", False)
            start = time.monotonic()

            result = wrapped(*args, **kwargs)

            if stream:
                return AnthropicSyncStreamWrapper(
                    result,
                    model=input_data["model"],
                    input_data=input_data,
                    tainted_source=tainted_source,
                    start_time=start,
                )

            latency = (time.monotonic() - start) * 1000
            extracted = _extract_anthropic_response(result)
            span = _build_span(
                model=input_data["model"],
                provider="anthropic",
                input_messages=[input_data],
                output_text=extracted["output_text"],
                tokens_in=extracted["tokens_in"],
                tokens_out=extracted["tokens_out"],
                latency_ms=latency,
                tainted_source=tainted_source,
            )
            _taint_anthropic_response(result, span.span_id)
            _emit_span(span)
            return result

        @wrapt.patch_function_wrapper("anthropic.resources.messages", "AsyncMessages.create")
        async def _wrap_async(wrapped: Any, instance: Any, args: tuple, kwargs: dict) -> Any:
            input_data = _build_anthropic_input(args, kwargs)
            tainted_source = _scan_tainted(args, kwargs)
            stream = kwargs.get("stream", False)
            start = time.monotonic()

            result = await wrapped(*args, **kwargs)

            if stream:
                return AnthropicAsyncStreamWrapper(
                    result,
                    model=input_data["model"],
                    input_data=input_data,
                    tainted_source=tainted_source,
                    start_time=start,
                )

            latency = (time.monotonic() - start) * 1000
            extracted = _extract_anthropic_response(result)
            span = _build_span(
                model=input_data["model"],
                provider="anthropic",
                input_messages=[input_data],
                output_text=extracted["output_text"],
                tokens_in=extracted["tokens_in"],
                tokens_out=extracted["tokens_out"],
                latency_ms=latency,
                tainted_source=tainted_source,
            )
            _taint_anthropic_response(result, span.span_id)
            _emit_span(span)
            return result

        _patched.add("anthropic")
        logger.info("Patched anthropic SDK")
        return True
    except ImportError:
        logger.debug("anthropic or wrapt not available")
        return False


# ---------------------------------------------------------------------------
# Patching — OpenAI
# ---------------------------------------------------------------------------


def _patch_openai() -> bool:
    """Patch openai SDK using wrapt."""
    if "openai" in _patched:
        return True
    try:
        import wrapt
        import openai  # noqa: F401

        @wrapt.patch_function_wrapper("openai.resources.chat.completions", "Completions.create")
        def _wrap_sync(wrapped: Any, instance: Any, args: tuple, kwargs: dict) -> Any:
            input_data = _build_openai_input(args, kwargs)
            tainted_source = _scan_tainted(args, kwargs)
            stream = kwargs.get("stream", False)
            start = time.monotonic()

            result = wrapped(*args, **kwargs)

            if stream:
                return OpenAISyncStreamWrapper(
                    result,
                    model=input_data["model"],
                    input_data=input_data,
                    tainted_source=tainted_source,
                    start_time=start,
                )

            latency = (time.monotonic() - start) * 1000
            extracted = _extract_openai_response(result)
            span = _build_span(
                model=input_data["model"],
                provider="openai",
                input_messages=[input_data],
                output_text=extracted["output_text"],
                tokens_in=extracted["tokens_in"],
                tokens_out=extracted["tokens_out"],
                latency_ms=latency,
                tainted_source=tainted_source,
            )
            _taint_openai_response(result, span.span_id)
            _emit_span(span)
            return result

        _patched.add("openai")
        logger.info("Patched openai SDK")
        return True
    except ImportError:
        logger.debug("openai or wrapt not available")
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_serialize(obj: Any) -> Any:
    """Serialize messages to JSON-safe format."""
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, str):
        return obj
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)

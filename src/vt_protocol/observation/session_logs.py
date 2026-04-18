"""Parse Claude Code session JSONL logs for LLM call telemetry.

Claude Code stores conversation history in ~/.claude/projects/ as JSONL files.
Each assistant message includes model, token usage, and timestamps. We parse
these to extract LLM call events for the Lattice dashboard.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Known pricing per million tokens (input, output) as of 2025
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-opus-4-20250514": (15.0, 75.0),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD from model name and token counts."""
    # Find best matching model key
    for key, (inp_rate, out_rate) in _PRICING.items():
        if key in model or model in key:
            return (input_tokens / 1_000_000 * inp_rate) + (output_tokens / 1_000_000 * out_rate)
    # Default to Sonnet pricing
    return (input_tokens / 1_000_000 * 3.0) + (output_tokens / 1_000_000 * 15.0)


def find_claude_session_dir(project_root: Path) -> Path | None:
    """Find the ~/.claude/projects/ directory for this project."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.is_dir():
        return None

    # Claude Code uses mangled path as directory name: /foo/bar → -foo-bar
    project_str = str(project_root.resolve())
    mangled = project_str.replace("/", "-")
    candidate = claude_dir / mangled
    if candidate.is_dir():
        return candidate

    return None


def find_latest_session_file(session_dir: Path) -> Path | None:
    """Find the most recently modified .jsonl session file."""
    jsonl_files = list(session_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)


def parse_session_jsonl(jsonl_path: Path) -> list[dict]:
    """Parse a Claude Code session JSONL file and extract LLM call events.

    Returns a list of event dicts suitable for writing to events.jsonl.
    """
    events: list[dict] = []

    try:
        lines = jsonl_path.read_text().splitlines()
    except Exception:
        logger.debug("Failed to read session file %s", jsonl_path, exc_info=True)
        return []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "assistant":
            continue

        msg = entry.get("message", {})
        usage = msg.get("usage", {})
        model = msg.get("model", "")

        if not model or not usage:
            continue

        input_tokens = usage.get("input_tokens", 0)
        cache_creation = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_input = input_tokens + cache_creation + cache_read

        timestamp = entry.get("timestamp", "")
        if not timestamp:
            continue

        # Extract prompt preview from message content
        content = msg.get("content", [])
        prompt_preview = ""
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    prompt_preview = block.get("text", "")[:200]
                    break
        elif isinstance(content, str):
            prompt_preview = content[:200]

        cost = _estimate_cost(model, total_input, output_tokens)

        events.append({
            "timestamp": timestamp,
            "type": "llm_call",
            "provider": "anthropic",
            "model": model,
            "input_tokens": total_input,
            "output_tokens": output_tokens,
            "latency_ms": 0,  # Not available from JSONL
            "prompt_preview": prompt_preview,
            "cost_usd": round(cost, 6),
            "agent": "claude-code",
        })

    return events


def sync_session_to_traces(project_root: Path) -> int:
    """Parse Claude Code session logs and append new LLM call events to .smm/traces/events.jsonl.

    Returns the number of new events appended.
    """
    session_dir = find_claude_session_dir(project_root)
    if session_dir is None:
        logger.debug("No Claude session directory found for %s", project_root)
        return 0

    session_file = find_latest_session_file(session_dir)
    if session_file is None:
        logger.debug("No session files found in %s", session_dir)
        return 0

    events = parse_session_jsonl(session_file)
    if not events:
        return 0

    traces_dir = project_root / ".smm" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    events_path = traces_dir / "events.jsonl"

    # Load existing timestamps to avoid duplicates
    existing_timestamps: set[str] = set()
    if events_path.exists():
        for line in events_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                existing = json.loads(line)
                if existing.get("type") == "llm_call":
                    existing_timestamps.add(existing.get("timestamp", ""))
            except json.JSONDecodeError:
                continue

    new_events = [e for e in events if e["timestamp"] not in existing_timestamps]
    if not new_events:
        return 0

    with open(events_path, "a") as f:
        for event in new_events:
            f.write(json.dumps(event) + "\n")

    return len(new_events)

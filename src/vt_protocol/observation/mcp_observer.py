"""MCP Gateway observer — logs every MCP tool call between agent and servers.

Sits between the agent and its downstream MCP servers. Records:
  - tool_name, arguments, response preview, timestamp, agent_id
  - Category: filesystem, shell, git, external
  - Duration and success/failure status

Produces ActivityEntry records for the unified timeline.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool category mapping
# ---------------------------------------------------------------------------

TOOL_CATEGORIES: dict[str, str] = {
    # Filesystem tools
    "read_file": "filesystem",
    "write_file": "filesystem",
    "list_directory": "filesystem",
    "search_files": "filesystem",
    "create_directory": "filesystem",
    "move_file": "filesystem",
    "delete_file": "filesystem",
    "get_file_info": "filesystem",
    # Shell tools
    "run_command": "shell",
    "execute_command": "shell",
    "bash": "shell",
    "terminal": "shell",
    # Git tools
    "git_commit": "git",
    "git_push": "git",
    "git_pull": "git",
    "git_branch": "git",
    "git_checkout": "git",
    "git_diff": "git",
    "git_log": "git",
    "git_status": "git",
    "git_add": "git",
    "git_reset": "git",
    "git_tag": "git",
    "git_merge": "git",
    "git_rebase": "git",
    "git_stash": "git",
}


def categorize_tool(tool_name: str) -> str:
    """Categorize an MCP tool call by its name."""
    name_lower = tool_name.lower().strip()
    if name_lower in TOOL_CATEGORIES:
        return TOOL_CATEGORIES[name_lower]
    # Heuristic fallbacks
    if any(k in name_lower for k in ("file", "read", "write", "directory", "path")):
        return "filesystem"
    if any(k in name_lower for k in ("shell", "exec", "command", "bash", "terminal")):
        return "shell"
    if "git" in name_lower:
        return "git"
    return "external"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MCPToolCall:
    """A single observed MCP tool invocation."""

    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    response_preview: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str = ""
    session_id: str = ""
    category: str = ""
    duration_ms: float = 0.0
    success: bool = True
    error: str | None = None
    server_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "response_preview": self.response_preview,
            "timestamp": self.timestamp.isoformat(),
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "category": self.category,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error": self.error,
            "server_name": self.server_name,
        }


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------


class MCPObserver:
    """Observes and logs MCP tool calls."""

    def __init__(self) -> None:
        self._calls: list[MCPToolCall] = []

    @property
    def calls(self) -> list[MCPToolCall]:
        return list(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def record(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        response: str = "",
        *,
        agent_id: str = "",
        session_id: str = "",
        duration_ms: float = 0.0,
        success: bool = True,
        error: str | None = None,
        server_name: str = "",
    ) -> MCPToolCall:
        """Record an MCP tool call and return the log entry."""
        category = categorize_tool(tool_name)
        preview = response[:500] if response else ""

        call = MCPToolCall(
            tool_name=tool_name,
            arguments=arguments or {},
            response_preview=preview,
            agent_id=agent_id,
            session_id=session_id,
            category=category,
            duration_ms=duration_ms,
            success=success,
            error=error,
            server_name=server_name,
        )
        self._calls.append(call)
        return call

    def calls_by_category(self) -> dict[str, int]:
        """Count calls grouped by category."""
        counts: dict[str, int] = {}
        for c in self._calls:
            counts[c.category] = counts.get(c.category, 0) + 1
        return counts

    def calls_by_agent(self) -> dict[str, int]:
        """Count calls grouped by agent_id."""
        counts: dict[str, int] = {}
        for c in self._calls:
            key = c.agent_id or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_activity_entries(self) -> list[dict[str, Any]]:
        """Convert all calls to unified ActivityEntry dicts."""
        entries = []
        for c in self._calls:
            entries.append({
                "entry_id": c.call_id,
                "timestamp": c.timestamp.timestamp(),
                "agent_id": c.agent_id,
                "session_id": c.session_id,
                "action_type": "mcp_tool",
                "tool_name": c.tool_name,
                "summary": f"MCP {c.category}: {c.tool_name}({_summarize_args(c.arguments)})",
                "severity": "warning" if not c.success else "info",
                "details": {
                    "category": c.category,
                    "arguments": c.arguments,
                    "response_preview": c.response_preview,
                    "success": c.success,
                    "error": c.error,
                    "server_name": c.server_name,
                },
                "duration_ms": c.duration_ms,
            })
        return entries

    def reset(self) -> None:
        """Clear all recorded calls."""
        self._calls.clear()


def _summarize_args(args: dict[str, Any]) -> str:
    """Create a brief summary of tool arguments."""
    parts = []
    for k, v in list(args.items())[:3]:
        val = str(v)
        if len(val) > 40:
            val = val[:37] + "..."
        parts.append(f"{k}={val}")
    return ", ".join(parts)

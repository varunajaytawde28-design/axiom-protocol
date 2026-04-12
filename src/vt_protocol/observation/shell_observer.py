"""Shell command observer — captures and classifies agent shell executions.

When an agent executes shell commands via MCP bash tool, captures:
  - command, exit_code, stdout/stderr preview, duration, timestamp
  - Flags dangerous commands with appropriate severity

Dangerous command patterns:
  - rm -rf /  (destructive deletion)
  - chmod 777 (world-writable permissions)
  - curl | bash (remote code execution)
  - pip install (unknown packages)
  - docker run --privileged (privileged container)
  - git push --force (force push)
  - dd if=/dev/zero (disk wipe)
  - mkfs (filesystem format)
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dangerous command patterns
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # (pattern, reason, severity)
    (re.compile(r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?-[a-zA-Z]*r|rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?-[a-zA-Z]*f|rm\s+-rf"),
     "Recursive force delete", "critical"),
    (re.compile(r"chmod\s+777"), "World-writable permissions", "critical"),
    (re.compile(r"curl\s+.*\|\s*(?:bash|sh|zsh)"), "Pipe remote script to shell", "critical"),
    (re.compile(r"wget\s+.*\|\s*(?:bash|sh|zsh)"), "Pipe remote script to shell", "critical"),
    (re.compile(r"docker\s+run\s+.*--privileged"), "Privileged Docker container", "critical"),
    (re.compile(r"git\s+push\s+.*--force(?:\s|$)"), "Git force push", "critical"),
    (re.compile(r"git\s+push\s+.*-f(?:\s|$)"), "Git force push", "critical"),
    (re.compile(r"git\s+reset\s+--hard"), "Git hard reset", "warning"),
    (re.compile(r"dd\s+.*if=/dev/zero"), "Disk write from /dev/zero", "critical"),
    (re.compile(r"mkfs\b"), "Filesystem format", "critical"),
    (re.compile(r"pip\s+install\s+(?!-r\s)(?!-e\s)(?!\.)"), "Package install", "warning"),
    (re.compile(r"npm\s+install\s+(?!--save-dev)"), "NPM package install", "info"),
    (re.compile(r"sudo\s+"), "Elevated privileges", "warning"),
    (re.compile(r"eval\s+"), "Shell eval", "warning"),
    (re.compile(r">\s*/dev/sd[a-z]"), "Direct device write", "critical"),
    (re.compile(r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;"), "Fork bomb", "critical"),
]


def check_dangerous(command: str) -> list[tuple[str, str]]:
    """Check if a command matches dangerous patterns.

    Returns list of (reason, severity) tuples for each match.
    """
    matches: list[tuple[str, str]] = []
    for pattern, reason, severity in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            matches.append((reason, severity))
    return matches


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ShellExecution:
    """A single observed shell command execution."""

    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    command: str = ""
    exit_code: int = 0
    stdout_preview: str = ""
    stderr_preview: str = ""
    duration_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str = ""
    session_id: str = ""
    dangerous: bool = False
    danger_reasons: list[str] = field(default_factory=list)
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "dangerous": self.dangerous,
            "danger_reasons": self.danger_reasons,
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------


class ShellObserver:
    """Observes and logs shell command executions."""

    def __init__(self) -> None:
        self._executions: list[ShellExecution] = []

    @property
    def executions(self) -> list[ShellExecution]:
        return list(self._executions)

    @property
    def dangerous_count(self) -> int:
        return sum(1 for e in self._executions if e.dangerous)

    def record(
        self,
        command: str,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        *,
        duration_ms: float = 0.0,
        agent_id: str = "",
        session_id: str = "",
    ) -> ShellExecution:
        """Record a shell command execution."""
        dangers = check_dangerous(command)
        is_dangerous = len(dangers) > 0

        # Determine highest severity from danger matches
        severity = "info"
        if is_dangerous:
            severities = [s for _, s in dangers]
            if "critical" in severities:
                severity = "critical"
            elif "warning" in severities:
                severity = "warning"
        elif exit_code != 0:
            severity = "warning"

        execution = ShellExecution(
            command=command,
            exit_code=exit_code,
            stdout_preview=stdout[:500] if stdout else "",
            stderr_preview=stderr[:500] if stderr else "",
            duration_ms=duration_ms,
            agent_id=agent_id,
            session_id=session_id,
            dangerous=is_dangerous,
            danger_reasons=[reason for reason, _ in dangers],
            severity=severity,
        )
        self._executions.append(execution)
        return execution

    def to_activity_entries(self) -> list[dict[str, Any]]:
        """Convert all executions to unified ActivityEntry dicts."""
        entries = []
        for e in self._executions:
            cmd_preview = e.command[:80] + "..." if len(e.command) > 80 else e.command
            summary = f"$ {cmd_preview}"
            if e.dangerous:
                summary = f"[DANGEROUS] {summary}"
            if e.exit_code != 0:
                summary += f" (exit {e.exit_code})"

            entries.append({
                "entry_id": e.execution_id,
                "timestamp": e.timestamp.timestamp(),
                "agent_id": e.agent_id,
                "session_id": e.session_id,
                "action_type": "shell_command",
                "tool_name": "bash",
                "summary": summary,
                "severity": e.severity,
                "details": {
                    "command": e.command,
                    "exit_code": e.exit_code,
                    "stdout_preview": e.stdout_preview,
                    "stderr_preview": e.stderr_preview,
                    "dangerous": e.dangerous,
                    "danger_reasons": e.danger_reasons,
                },
                "duration_ms": e.duration_ms,
            })
        return entries

    def reset(self) -> None:
        """Clear all recorded executions."""
        self._executions.clear()

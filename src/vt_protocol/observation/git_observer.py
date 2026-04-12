"""Git operation observer — tracks commits, branches, pushes, and force-pushes.

Captures:
  - Commits: message, files changed, author, branch
  - Branch operations: create, delete, checkout
  - Push operations: branch, remote, force flag (CRITICAL if force)
  - Tag operations: create, delete
  - Merge/rebase operations

Produces ActivityEntry records for the unified timeline.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Operation types and severity mapping
# ---------------------------------------------------------------------------

OPERATION_SEVERITY: dict[str, str] = {
    "commit": "info",
    "push": "info",
    "force_push": "critical",
    "branch_create": "info",
    "branch_delete": "warning",
    "checkout": "info",
    "tag_create": "info",
    "tag_delete": "warning",
    "merge": "info",
    "rebase": "warning",
    "reset_hard": "critical",
    "stash": "info",
    "cherry_pick": "info",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class GitOperation:
    """A single observed git operation."""

    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    operation: str = ""  # commit, push, force_push, branch_create, etc.
    message: str = ""
    files_changed: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str = ""
    session_id: str = ""
    author: str = ""
    branch: str = ""
    remote: str = ""
    severity: str = "info"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "operation": self.operation,
            "message": self.message,
            "files_changed": self.files_changed,
            "timestamp": self.timestamp.isoformat(),
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "author": self.author,
            "branch": self.branch,
            "remote": self.remote,
            "severity": self.severity,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------


class GitObserver:
    """Observes and logs git operations."""

    def __init__(self) -> None:
        self._operations: list[GitOperation] = []

    @property
    def operations(self) -> list[GitOperation]:
        return list(self._operations)

    @property
    def operation_count(self) -> int:
        return len(self._operations)

    @property
    def has_force_push(self) -> bool:
        return any(op.operation == "force_push" for op in self._operations)

    def record_commit(
        self,
        message: str,
        files_changed: list[str] | None = None,
        *,
        author: str = "",
        branch: str = "",
        agent_id: str = "",
        session_id: str = "",
    ) -> GitOperation:
        """Record a git commit."""
        op = GitOperation(
            operation="commit",
            message=message,
            files_changed=files_changed or [],
            author=author,
            branch=branch,
            agent_id=agent_id,
            session_id=session_id,
            severity=OPERATION_SEVERITY["commit"],
            details={"files_count": len(files_changed or [])},
        )
        self._operations.append(op)
        return op

    def record_push(
        self,
        branch: str = "",
        remote: str = "origin",
        *,
        force: bool = False,
        agent_id: str = "",
        session_id: str = "",
    ) -> GitOperation:
        """Record a git push. Force pushes are CRITICAL."""
        op_type = "force_push" if force else "push"
        severity = OPERATION_SEVERITY[op_type]
        message = f"{'Force push' if force else 'Push'} to {remote}/{branch}"

        op = GitOperation(
            operation=op_type,
            message=message,
            branch=branch,
            remote=remote,
            agent_id=agent_id,
            session_id=session_id,
            severity=severity,
            details={"force": force},
        )
        self._operations.append(op)
        return op

    def record_branch(
        self,
        branch_name: str,
        action: str = "create",
        *,
        agent_id: str = "",
        session_id: str = "",
    ) -> GitOperation:
        """Record a branch operation (create/delete)."""
        op_type = f"branch_{action}"
        severity = OPERATION_SEVERITY.get(op_type, "info")

        op = GitOperation(
            operation=op_type,
            message=f"Branch {action}: {branch_name}",
            branch=branch_name,
            agent_id=agent_id,
            session_id=session_id,
            severity=severity,
        )
        self._operations.append(op)
        return op

    def record_tag(
        self,
        tag_name: str,
        action: str = "create",
        *,
        message: str = "",
        agent_id: str = "",
        session_id: str = "",
    ) -> GitOperation:
        """Record a tag operation (create/delete)."""
        op_type = f"tag_{action}"
        severity = OPERATION_SEVERITY.get(op_type, "info")

        op = GitOperation(
            operation=op_type,
            message=message or f"Tag {action}: {tag_name}",
            agent_id=agent_id,
            session_id=session_id,
            severity=severity,
            details={"tag": tag_name},
        )
        self._operations.append(op)
        return op

    def record_operation(
        self,
        operation: str,
        *,
        message: str = "",
        branch: str = "",
        files_changed: list[str] | None = None,
        agent_id: str = "",
        session_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> GitOperation:
        """Record a generic git operation (merge, rebase, checkout, etc.)."""
        severity = OPERATION_SEVERITY.get(operation, "info")

        op = GitOperation(
            operation=operation,
            message=message or f"git {operation}",
            branch=branch,
            files_changed=files_changed or [],
            agent_id=agent_id,
            session_id=session_id,
            severity=severity,
            details=details or {},
        )
        self._operations.append(op)
        return op

    def to_activity_entries(self) -> list[dict[str, Any]]:
        """Convert all operations to unified ActivityEntry dicts."""
        entries = []
        for op in self._operations:
            summary = f"git {op.operation}"
            if op.message:
                msg_preview = op.message[:60] + "..." if len(op.message) > 60 else op.message
                summary += f": {msg_preview}"
            if op.branch:
                summary += f" ({op.branch})"

            entries.append({
                "entry_id": op.operation_id,
                "timestamp": op.timestamp.timestamp(),
                "agent_id": op.agent_id,
                "session_id": op.session_id,
                "action_type": "git_operation",
                "tool_name": f"git_{op.operation}",
                "summary": summary,
                "severity": op.severity,
                "details": {
                    "operation": op.operation,
                    "message": op.message,
                    "branch": op.branch,
                    "remote": op.remote,
                    "author": op.author,
                    "files_changed": op.files_changed,
                    **op.details,
                },
                "duration_ms": 0.0,
            })
        return entries

    def reset(self) -> None:
        """Clear all recorded operations."""
        self._operations.clear()

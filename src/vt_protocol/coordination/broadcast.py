"""Resolution broadcast — push updated context to active sessions.

When a decision is resolved, notify all active MCP sessions so agents
get fresh context on their next tool call.

From SPEC Sprint 19: "Multi-agent coordination — broadcast.py."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BroadcastMessage:
    """A message broadcast to all active sessions."""

    event_type: str = ""
    decision_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "decision_id": self.decision_id,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SessionInfo:
    """Minimal session info for broadcast targeting."""

    session_id: str = ""
    agent_id: str = ""
    active: bool = True
    pending_messages: list[BroadcastMessage] = field(default_factory=list)

    @property
    def pending_count(self) -> int:
        return len(self.pending_messages)


class BroadcastHub:
    """Manages broadcast of decision events to active MCP sessions.

    Sessions poll for pending messages on each tool call.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionInfo] = {}
        self._history: list[BroadcastMessage] = []

    @property
    def active_session_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.active)

    @property
    def total_broadcasts(self) -> int:
        return len(self._history)

    def register_session(self, session_id: str, agent_id: str = "") -> SessionInfo:
        """Register a new MCP session for broadcasts."""
        session = SessionInfo(session_id=session_id, agent_id=agent_id)
        self._sessions[session_id] = session
        return session

    def deregister_session(self, session_id: str) -> None:
        """Mark a session as inactive."""
        session = self._sessions.get(session_id)
        if session:
            session.active = False

    def broadcast(
        self,
        event_type: str,
        decision_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Broadcast a message to all active sessions.

        Returns the number of sessions notified.
        """
        msg = BroadcastMessage(
            event_type=event_type,
            decision_id=decision_id,
            payload=payload or {},
        )
        self._history.append(msg)

        count = 0
        for session in self._sessions.values():
            if session.active:
                session.pending_messages.append(msg)
                count += 1

        logger.debug("Broadcast %s to %d sessions", event_type, count)
        return count

    def poll(self, session_id: str) -> list[BroadcastMessage]:
        """Poll pending messages for a session. Clears the queue."""
        session = self._sessions.get(session_id)
        if not session or not session.active:
            return []
        messages = list(session.pending_messages)
        session.pending_messages.clear()
        return messages

    def get_session(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)

    def broadcast_resolution(
        self,
        decision_id: str,
        *,
        resolution_note: str = "",
        resolved_by: str = "",
    ) -> int:
        """Convenience: broadcast a decision resolution event."""
        return self.broadcast(
            event_type="decision_resolved",
            decision_id=decision_id,
            payload={
                "resolution_note": resolution_note,
                "resolved_by": resolved_by,
            },
        )

    def broadcast_contradiction(
        self,
        contradiction_id: str,
        *,
        decision_a_id: str = "",
        decision_b_id: str = "",
        verdict: str = "",
    ) -> int:
        """Convenience: broadcast a new contradiction detection."""
        return self.broadcast(
            event_type="contradiction_detected",
            decision_id=contradiction_id,
            payload={
                "decision_a_id": decision_a_id,
                "decision_b_id": decision_b_id,
                "verdict": verdict,
            },
        )

    def clear(self) -> None:
        self._sessions.clear()
        self._history.clear()

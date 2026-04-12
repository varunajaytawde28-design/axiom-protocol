"""Tests for resolution broadcast hub."""

from __future__ import annotations

import pytest

from vt_protocol.coordination.broadcast import (
    BroadcastHub,
    BroadcastMessage,
    SessionInfo,
)


class TestBroadcastMessage:
    def test_to_dict(self) -> None:
        msg = BroadcastMessage(
            event_type="decision_resolved",
            decision_id="d1",
            payload={"note": "approved"},
        )
        d = msg.to_dict()
        assert d["event_type"] == "decision_resolved"
        assert d["payload"]["note"] == "approved"


class TestSessionInfo:
    def test_pending_count(self) -> None:
        si = SessionInfo(
            session_id="s1",
            pending_messages=[BroadcastMessage(), BroadcastMessage()],
        )
        assert si.pending_count == 2

    def test_default_active(self) -> None:
        si = SessionInfo(session_id="s1")
        assert si.active is True


class TestBroadcastHub:
    def test_register_session(self) -> None:
        hub = BroadcastHub()
        session = hub.register_session("s1", "agent_a")
        assert session.session_id == "s1"
        assert hub.active_session_count == 1

    def test_deregister_session(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.deregister_session("s1")
        assert hub.active_session_count == 0

    def test_broadcast(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.register_session("s2")
        count = hub.broadcast("decision_resolved", "d1")
        assert count == 2
        assert hub.total_broadcasts == 1

    def test_broadcast_skips_inactive(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.register_session("s2")
        hub.deregister_session("s2")
        count = hub.broadcast("test_event")
        assert count == 1

    def test_poll(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.broadcast("event1", "d1")
        hub.broadcast("event2", "d2")
        messages = hub.poll("s1")
        assert len(messages) == 2
        assert messages[0].event_type == "event1"

    def test_poll_clears_queue(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.broadcast("event1")
        hub.poll("s1")
        messages = hub.poll("s1")
        assert len(messages) == 0

    def test_poll_nonexistent_session(self) -> None:
        hub = BroadcastHub()
        messages = hub.poll("nonexistent")
        assert len(messages) == 0

    def test_poll_inactive_session(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.deregister_session("s1")
        messages = hub.poll("s1")
        assert len(messages) == 0

    def test_get_session(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1", "agent_a")
        session = hub.get_session("s1")
        assert session is not None
        assert session.agent_id == "agent_a"

    def test_get_missing_session(self) -> None:
        hub = BroadcastHub()
        assert hub.get_session("nonexistent") is None

    def test_broadcast_resolution(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        count = hub.broadcast_resolution("d1", resolution_note="approved")
        assert count == 1
        messages = hub.poll("s1")
        assert messages[0].event_type == "decision_resolved"
        assert messages[0].payload["resolution_note"] == "approved"

    def test_broadcast_contradiction(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        count = hub.broadcast_contradiction("c1", decision_a_id="d1", verdict="tension")
        assert count == 1
        messages = hub.poll("s1")
        assert messages[0].event_type == "contradiction_detected"
        assert messages[0].payload["verdict"] == "tension"

    def test_clear(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.broadcast("event")
        hub.clear()
        assert hub.active_session_count == 0
        assert hub.total_broadcasts == 0

    def test_multiple_sessions_independent_queues(self) -> None:
        hub = BroadcastHub()
        hub.register_session("s1")
        hub.register_session("s2")
        hub.broadcast("event1")
        hub.poll("s1")  # Clear s1's queue
        hub.broadcast("event2")
        # s1 should only have event2, s2 should have event1+event2
        msgs1 = hub.poll("s1")
        msgs2 = hub.poll("s2")
        assert len(msgs1) == 1
        assert len(msgs2) == 2

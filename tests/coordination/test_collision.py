"""Tests for decision collision detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.coordination.collision import (
    Collision,
    CollisionDetector,
    CollisionType,
    DecisionEvent,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestDecisionEvent:
    def test_to_dict(self) -> None:
        ev = DecisionEvent(
            decision_id="d1",
            agent_id="agent_a",
            dimension="database",
            title="Use PostgreSQL",
        )
        d = ev.to_dict()
        assert d["decision_id"] == "d1"
        assert d["dimension"] == "database"


class TestCollision:
    def test_to_dict(self) -> None:
        ev_a = DecisionEvent(decision_id="d1", agent_id="a", dimension="db")
        ev_b = DecisionEvent(decision_id="d2", agent_id="b", dimension="db")
        c = Collision(
            collision_type=CollisionType.CONCURRENT_CONFLICT,
            event_a=ev_a,
            event_b=ev_b,
            dimension="db",
            time_gap_seconds=30.0,
        )
        d = c.to_dict()
        assert d["collision_type"] == "concurrent_conflict"
        assert d["time_gap_seconds"] == 30.0


class TestCollisionDetector:
    def test_no_collision_different_dimensions(self) -> None:
        detector = CollisionDetector()
        now = _now()
        collisions = detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="database", timestamp=now,
        ))
        assert len(collisions) == 0
        collisions = detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="auth", timestamp=now,
        ))
        assert len(collisions) == 0

    def test_no_collision_same_agent(self) -> None:
        detector = CollisionDetector()
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="database", timestamp=now,
        ))
        collisions = detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="a", dimension="database", timestamp=now,
        ))
        assert len(collisions) == 0

    def test_collision_detected(self) -> None:
        detector = CollisionDetector()
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="database", timestamp=now,
        ))
        collisions = detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="database",
            timestamp=now + timedelta(seconds=60),
        ))
        assert len(collisions) == 1
        assert collisions[0].collision_type == CollisionType.CONCURRENT_CONFLICT

    def test_no_collision_outside_window(self) -> None:
        detector = CollisionDetector(window_seconds=300)
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="database", timestamp=now,
        ))
        collisions = detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="database",
            timestamp=now + timedelta(seconds=600),  # 10 min > 5 min window
        ))
        assert len(collisions) == 0

    def test_causal_pair_suppresses_collision(self) -> None:
        detector = CollisionDetector()
        detector.add_causal_pair("d1", "d2")
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="database", timestamp=now,
        ))
        collisions = detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="database",
            timestamp=now + timedelta(seconds=30),
        ))
        assert len(collisions) == 0

    def test_collision_count(self) -> None:
        detector = CollisionDetector()
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="database", timestamp=now,
        ))
        detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="database",
            timestamp=now + timedelta(seconds=30),
        ))
        assert detector.collision_count == 1

    def test_get_collisions_for_dimension(self) -> None:
        detector = CollisionDetector()
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="database", timestamp=now,
        ))
        detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="database",
            timestamp=now + timedelta(seconds=30),
        ))
        db_collisions = detector.get_collisions_for_dimension("database")
        assert len(db_collisions) == 1
        auth_collisions = detector.get_collisions_for_dimension("auth")
        assert len(auth_collisions) == 0

    def test_cleanup_old_events(self) -> None:
        detector = CollisionDetector()
        old_time = _now() - timedelta(hours=1)
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="db", timestamp=old_time,
        ))
        removed = detector.cleanup_old_events(before=_now() - timedelta(minutes=30))
        assert removed == 1

    def test_clear(self) -> None:
        detector = CollisionDetector()
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="db", timestamp=now,
        ))
        detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="db",
            timestamp=now + timedelta(seconds=10),
        ))
        detector.clear()
        assert detector.collision_count == 0

    def test_multiple_collisions(self) -> None:
        detector = CollisionDetector()
        now = _now()
        detector.record_decision(DecisionEvent(
            decision_id="d1", agent_id="a", dimension="db", timestamp=now,
        ))
        detector.record_decision(DecisionEvent(
            decision_id="d2", agent_id="b", dimension="db",
            timestamp=now + timedelta(seconds=10),
        ))
        # Third agent also collides with both
        collisions = detector.record_decision(DecisionEvent(
            decision_id="d3", agent_id="c", dimension="db",
            timestamp=now + timedelta(seconds=20),
        ))
        assert len(collisions) == 2  # c collides with a and b

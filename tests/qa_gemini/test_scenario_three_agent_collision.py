"""Gemini Scenario 2: Three Agents, One Codebase.

Collision detection when Claude, Cursor, and Copilot mutate the same
architectural dimension concurrently. Uses real CollisionDetector.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.coordination.collision import (
    Collision,
    CollisionDetector,
    CollisionType,
    DecisionEvent,
    COLLISION_WINDOW_SECONDS,
)

pytestmark = pytest.mark.integration


class TestThreeAgentCollision:
    """Validates concurrent decision collision handling using CollisionDetector."""

    def test_three_agents_same_dimension_collide(self):
        """Three agents deciding on same dimension within window trigger collisions."""
        detector = CollisionDetector()
        now = datetime.now(timezone.utc)

        e1 = DecisionEvent(
            decision_id="d1", agent_id="claude", dimension="database",
            timestamp=now, title="Use PostgreSQL",
        )
        e2 = DecisionEvent(
            decision_id="d2", agent_id="cursor", dimension="database",
            timestamp=now + timedelta(seconds=10), title="Use SQLite",
        )
        e3 = DecisionEvent(
            decision_id="d3", agent_id="copilot", dimension="database",
            timestamp=now + timedelta(seconds=20), title="Use MongoDB",
        )

        c1 = detector.record_decision(e1)
        assert len(c1) == 0  # First event, no collision

        c2 = detector.record_decision(e2)
        assert len(c2) == 1  # Collision with e1
        assert c2[0].collision_type == CollisionType.CONCURRENT_CONFLICT

        c3 = detector.record_decision(e3)
        assert len(c3) == 2  # Collision with both e1 and e2

    def test_no_collision_outside_window(self):
        """Events outside the collision window don't trigger."""
        detector = CollisionDetector()
        now = datetime.now(timezone.utc)

        e1 = DecisionEvent(
            decision_id="d1", agent_id="claude", dimension="database",
            timestamp=now - timedelta(seconds=COLLISION_WINDOW_SECONDS + 10),
            title="Use PostgreSQL",
        )
        e2 = DecisionEvent(
            decision_id="d2", agent_id="cursor", dimension="database",
            timestamp=now, title="Use SQLite",
        )

        detector.record_decision(e1)
        c = detector.record_decision(e2)
        assert len(c) == 0

    def test_same_agent_no_collision(self):
        """Same agent deciding twice on same dimension is sequential, not collision."""
        detector = CollisionDetector()
        now = datetime.now(timezone.utc)

        e1 = DecisionEvent(
            decision_id="d1", agent_id="claude", dimension="database",
            timestamp=now, title="Use PostgreSQL",
        )
        e2 = DecisionEvent(
            decision_id="d2", agent_id="claude", dimension="database",
            timestamp=now + timedelta(seconds=30), title="Update to PostgreSQL 16",
        )

        detector.record_decision(e1)
        c = detector.record_decision(e2)
        # Same agent = sequential, not concurrent conflict (it's an override)
        for collision in c:
            assert collision.collision_type == CollisionType.SEQUENTIAL_OVERRIDE

    def test_different_dimensions_no_collision(self):
        """Different dimensions don't trigger collision."""
        detector = CollisionDetector()
        now = datetime.now(timezone.utc)

        e1 = DecisionEvent(
            decision_id="d1", agent_id="claude", dimension="database",
            timestamp=now, title="Use PostgreSQL",
        )
        e2 = DecisionEvent(
            decision_id="d2", agent_id="cursor", dimension="auth",
            timestamp=now + timedelta(seconds=5), title="Use JWT",
        )

        detector.record_decision(e1)
        c = detector.record_decision(e2)
        assert len(c) == 0

    def test_causal_link_suppresses_collision(self):
        """Causal link between decisions prevents false collision detection."""
        detector = CollisionDetector()
        # add_causal_pair takes decision IDs, not agent IDs
        detector.add_causal_pair("d1", "d2")
        now = datetime.now(timezone.utc)

        e1 = DecisionEvent(
            decision_id="d1", agent_id="claude", dimension="database",
            timestamp=now, title="Use PostgreSQL",
        )
        e2 = DecisionEvent(
            decision_id="d2", agent_id="cursor", dimension="database",
            timestamp=now + timedelta(seconds=5), title="Add read replica",
        )

        detector.record_decision(e1)
        c = detector.record_decision(e2)
        # Causal link suppresses the collision entirely — no collision returned
        assert len(c) == 0

    def test_get_collisions_for_dimension(self):
        """Can query collisions by dimension."""
        detector = CollisionDetector()
        now = datetime.now(timezone.utc)

        e1 = DecisionEvent(decision_id="d1", agent_id="claude", dimension="database", timestamp=now)
        e2 = DecisionEvent(decision_id="d2", agent_id="cursor", dimension="database", timestamp=now + timedelta(seconds=5))

        detector.record_decision(e1)
        detector.record_decision(e2)

        db_collisions = detector.get_collisions_for_dimension("database")
        auth_collisions = detector.get_collisions_for_dimension("auth")
        assert len(db_collisions) >= 1
        assert len(auth_collisions) == 0

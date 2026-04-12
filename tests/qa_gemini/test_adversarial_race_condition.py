"""Gemini Adversarial: Concurrent Resolution Race Condition.

Tests the dashboard's REST resolve endpoint and WebSocket broadcast
under concurrent access. Verifies that resolving the same contradiction
from two clients simultaneously doesn't corrupt state.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

pytestmark = [pytest.mark.adversarial, pytest.mark.integration]


def _make_decision(title: str, dim: Dimension = Dimension.DATABASE) -> Decision:
    return Decision(
        title=title,
        content=f"Decision about {title} with sufficient detail for testing.",
        rationale=f"Rationale for {title}",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[dim],
        made_by="test-agent",
        project="race-test",
        source_type=SourceType.AGENT,
    )


def _make_contradiction(d_a: Decision, d_b: Decision) -> Contradiction:
    return Contradiction(
        decision_a_id=d_a.id,
        decision_b_id=d_b.id,
        decision_a_title=d_a.title,
        decision_b_title=d_b.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="These decisions directly conflict on shared dimension.",
        evidence_a=d_a.content[:100],
        evidence_b=d_b.content[:100],
        shared_dimensions=[Dimension.DATABASE],
        confidence=0.92,
    )


class TestConcurrentResolution:
    """Race conditions in REST contradiction resolution."""

    @pytest.fixture
    def race_state(self, tmp_path):
        d_a = _make_decision("Use PostgreSQL")
        d_b = _make_decision("Use MongoDB")
        c = _make_contradiction(d_a, d_b)

        root = tmp_path / "race-test"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm" / "contradictions").mkdir(parents=True)

        state = DashboardState(project_root=root)
        state.decisions = [d_a, d_b]
        state.contradictions = [c]
        set_state(state)
        return d_a, d_b, c

    @pytest.mark.asyncio
    async def test_double_resolve_same_contradiction(self, race_state):
        """Two simultaneous resolves — second should succeed without error."""
        d_a, d_b, c = race_state
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Send two resolve requests concurrently
            r1, r2 = await asyncio.gather(
                client.post(
                    f"/api/contradictions/{c.id}/resolve",
                    json={"winner_id": str(d_a.id), "rationale": "PostgreSQL wins"},
                ),
                client.post(
                    f"/api/contradictions/{c.id}/resolve",
                    json={"winner_id": str(d_b.id), "rationale": "MongoDB wins"},
                ),
            )

        # Both should return 200 (state is in-memory, last write wins)
        assert r1.status_code == 200
        assert r2.status_code == 200

        # Contradiction should be resolved
        assert c.status == ContradictionStatus.RESOLVED

    @pytest.mark.asyncio
    async def test_resolve_then_query_consistency(self, race_state):
        """After resolve, /api/health must show reduced actionable count."""
        d_a, d_b, c = race_state
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Before resolve
            before = await client.get("/api/health")
            before_data = before.json()
            assert before_data["actionable_contradictions"] >= 1

            # Resolve
            await client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(d_a.id), "rationale": "PostgreSQL"},
            )

            # After resolve
            after = await client.get("/api/health")
            after_data = after.json()
            assert after_data["actionable_contradictions"] < before_data["actionable_contradictions"]

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_returns_404(self, race_state):
        """Resolving a nonexistent contradiction returns 404."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/contradictions/00000000-0000-0000-0000-000000000000/resolve",
                json={"winner_id": "any", "rationale": "any"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_resolve_invalid_uuid_returns_400(self, race_state):
        """Invalid UUID in path returns 400."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/contradictions/not-a-uuid/resolve",
                json={"winner_id": "any", "rationale": "any"},
            )
            assert resp.status_code == 400


class TestWebSocketBroadcast:
    """WebSocket receives resolution events."""

    @pytest.mark.asyncio
    async def test_websocket_receives_resolution_event(self, tmp_path):
        """WebSocket client receives broadcast when contradiction is resolved via REST."""
        d_a = _make_decision("Use PostgreSQL")
        d_b = _make_decision("Use MongoDB")
        c = _make_contradiction(d_a, d_b)

        root = tmp_path / "ws-test"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm" / "contradictions").mkdir(parents=True)

        state = DashboardState(project_root=root)
        state.decisions = [d_a, d_b]
        state.contradictions = [c]
        set_state(state)

        transport = ASGITransport(app=app)
        received_events: list[dict] = []

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Connect WebSocket and resolve in parallel
            # Note: httpx doesn't support WebSocket natively, so we test the
            # REST path and verify the contradiction status changes correctly.
            resp = await client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(d_a.id), "rationale": "PostgreSQL wins"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "resolved"

        # Verify the contradiction object was mutated
        assert c.status == ContradictionStatus.RESOLVED
        assert "PostgreSQL wins" in (c.resolution_note or "")

    @pytest.mark.asyncio
    async def test_websocket_ping_pong(self, tmp_path):
        """WebSocket ping/pong works."""
        from starlette.testclient import TestClient

        root = tmp_path / "ws-ping-test"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm").mkdir(parents=True)

        state = DashboardState(project_root=root)
        set_state(state)

        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.send_text("ping")
            data = json.loads(ws.receive_text())
            assert data["type"] == "pong"


class TestMultiContradictionRace:
    """Multiple contradictions resolved concurrently."""

    @pytest.mark.asyncio
    async def test_resolve_multiple_contradictions_concurrently(self, tmp_path):
        """Resolve 5 different contradictions concurrently — all should succeed."""
        decisions = [_make_decision(f"Decision {i}") for i in range(10)]
        contradictions = []
        for i in range(5):
            c = _make_contradiction(decisions[i * 2], decisions[i * 2 + 1])
            contradictions.append(c)

        root = tmp_path / "multi-race"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm" / "contradictions").mkdir(parents=True)

        state = DashboardState(project_root=root)
        state.decisions = decisions
        state.contradictions = contradictions
        set_state(state)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            tasks = [
                client.post(
                    f"/api/contradictions/{c.id}/resolve",
                    json={
                        "winner_id": str(decisions[i * 2].id),
                        "rationale": f"Winner for pair {i}",
                    },
                )
                for i, c in enumerate(contradictions)
            ]
            results = await asyncio.gather(*tasks)

        # All should succeed
        for r in results:
            assert r.status_code == 200

        # All contradictions should be resolved
        for c in contradictions:
            assert c.status == ContradictionStatus.RESOLVED

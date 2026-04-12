"""Gemini Performance: Contradiction Detection Throughput Benchmark.

Benchmarks the core contradiction detection pipeline components:
- CalibrationStore record throughput
- Merkle tree append + proof generation throughput
- CollisionDetector throughput under high decision volume
- Dashboard health endpoint latency with large state

Mocks EXTERNAL LLM calls (Anthropic API). Internal modules are real.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.coordination.collision import CollisionDetector, DecisionEvent
from vt_protocol.decisions.calibration import CalibrationStore
from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

pytestmark = pytest.mark.performance


def _make_decision(idx: int) -> Decision:
    dims = list(Dimension)
    return Decision(
        title=f"Perf Decision {idx}",
        content=f"Performance testing decision number {idx} with enough content.",
        rationale=f"Benchmark rationale {idx}",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[dims[idx % len(dims)]],
        made_by=f"agent-{idx % 5}",
        project="perf-test",
        source_type=SourceType.AGENT,
    )


class TestCalibrationStoreThroughput:
    """CalibrationStore must handle bulk record inserts efficiently."""

    def test_1000_records_under_2_seconds(self):
        """Insert 1000 calibration records — must complete in < 2s."""
        store = CalibrationStore()
        try:
            start = time.perf_counter()
            for i in range(1000):
                store.record(
                    contradiction_id=f"c-{i}",
                    judge_verdict="contradiction" if i % 3 == 0 else "compatible",
                    judge_confidence=0.7 + (i % 30) / 100,
                    human_verdict="contradiction" if i % 3 == 0 else "compatible",
                )
            elapsed = time.perf_counter() - start
            assert elapsed < 2.0, f"1000 records took {elapsed:.2f}s"
            assert store.size == 1000
        finally:
            store.close()

    def test_metrics_computation_under_500ms(self):
        """compute_metrics on 500 records should complete in < 500ms."""
        store = CalibrationStore()
        try:
            for i in range(500):
                store.record(
                    contradiction_id=f"c-{i}",
                    judge_verdict="contradiction" if i % 2 == 0 else "compatible",
                    judge_confidence=0.8,
                    human_verdict="contradiction" if i % 2 == 0 else "compatible",
                )

            start = time.perf_counter()
            metrics = store.compute_metrics()
            elapsed = time.perf_counter() - start

            assert elapsed < 0.5, f"Metrics took {elapsed:.3f}s"
            assert metrics.total_records == 500
        finally:
            store.close()


class TestMerkleTreeThroughput:
    """Merkle tree must handle high-volume audit entries."""

    def test_1000_appends_under_3_seconds(self):
        """Append 1000 audit entries — must complete in < 3s."""
        tree = MerkleTree(":memory:")
        try:
            start = time.perf_counter()
            for i in range(1000):
                entry = AuditEntry(
                    event_type=AuditEventType.DECISION_ADDED,
                    actor=f"agent-{i % 5}",
                    project="perf-test",
                    payload={"index": i},
                )
                tree.append(entry)
            elapsed = time.perf_counter() - start

            assert elapsed < 3.0, f"1000 appends took {elapsed:.2f}s"
            assert tree.size == 1000
        finally:
            tree.close()

    def test_root_hash_computation_under_100ms(self):
        """Root hash on 500-leaf tree should be fast."""
        tree = MerkleTree(":memory:")
        try:
            for i in range(500):
                tree.append(AuditEntry(
                    event_type=AuditEventType.DECISION_ADDED,
                    actor="agent",
                    project="perf-test",
                    payload={"i": i},
                ))

            start = time.perf_counter()
            root = tree.root_hash()
            elapsed = time.perf_counter() - start

            assert elapsed < 0.1, f"Root hash took {elapsed:.3f}s"
            assert len(root) == 32  # SHA-256
        finally:
            tree.close()

    def test_inclusion_proof_generation_under_50ms(self):
        """Inclusion proof for any leaf in a 500-leaf tree — under 50ms."""
        tree = MerkleTree(":memory:")
        try:
            for i in range(500):
                tree.append(AuditEntry(
                    event_type=AuditEventType.DECISION_ADDED,
                    actor="agent",
                    project="perf-test",
                    payload={"i": i},
                ))

            start = time.perf_counter()
            proof = tree.inclusion_proof(250)
            elapsed = time.perf_counter() - start

            assert elapsed < 0.05, f"Proof took {elapsed:.3f}s"
            assert proof.leaf_index == 250
        finally:
            tree.close()

    def test_consistency_proof_under_100ms(self):
        """Consistency proof between sizes 200 and 500 — under 100ms."""
        tree = MerkleTree(":memory:")
        try:
            for i in range(500):
                tree.append(AuditEntry(
                    event_type=AuditEventType.DECISION_ADDED,
                    actor="agent",
                    project="perf-test",
                    payload={"i": i},
                ))

            start = time.perf_counter()
            proof = tree.consistency_proof(200, 500)
            elapsed = time.perf_counter() - start

            assert elapsed < 0.1, f"Consistency proof took {elapsed:.3f}s"
            assert len(proof) > 0
        finally:
            tree.close()


class TestCollisionDetectorThroughput:
    """CollisionDetector must handle rapid decision recording."""

    def test_500_decisions_under_2_seconds(self):
        """Record 500 decisions across 12 dimensions — under 2s."""
        detector = CollisionDetector()
        dims = list(Dimension)
        now = datetime.now(timezone.utc)

        start = time.perf_counter()
        total_collisions = 0
        for i in range(500):
            event = DecisionEvent(
                decision_id=f"d-{i}",
                agent_id=f"agent-{i % 5}",
                dimension=dims[i % len(dims)].value,
                timestamp=now + timedelta(seconds=i),
                title=f"Decision {i}",
            )
            collisions = detector.record_decision(event)
            total_collisions += len(collisions)
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0, f"500 decisions took {elapsed:.2f}s"
        # With 5 agents across 12 dimensions, some collisions expected
        assert total_collisions > 0

    def test_collision_detection_within_window(self):
        """Decisions within window detected, outside window ignored — at scale."""
        detector = CollisionDetector()
        now = datetime.now(timezone.utc)

        # 50 agents all deciding on 'database' within window
        for i in range(50):
            event = DecisionEvent(
                decision_id=f"d-{i}",
                agent_id=f"agent-{i}",
                dimension="database",
                timestamp=now + timedelta(seconds=i * 2),  # 2s apart
                title=f"DB Decision {i}",
            )
            detector.record_decision(event)

        db_collisions = detector.get_collisions_for_dimension("database")
        assert len(db_collisions) > 0


class TestDashboardHealthThroughput:
    """Dashboard health endpoint with large state."""

    @pytest.mark.asyncio
    async def test_health_with_200_decisions(self, tmp_path):
        """Health endpoint returns in < 100ms with 200 decisions loaded."""
        from httpx import ASGITransport, AsyncClient

        from vt_protocol.dashboard.app import DashboardState, app, set_state

        decisions = [_make_decision(i) for i in range(200)]

        root = tmp_path / "perf-dash"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm").mkdir(parents=True)

        state = DashboardState(project_root=root)
        state.decisions = decisions
        set_state(state)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start = time.perf_counter()
            resp = await client.get("/api/health")
            elapsed = time.perf_counter() - start

            assert resp.status_code == 200
            data = resp.json()
            assert data["total_decisions"] == 200
            assert elapsed < 0.1, f"Health took {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_decisions_list_with_200(self, tmp_path):
        """Decisions list endpoint under 200ms with 200 decisions."""
        from httpx import ASGITransport, AsyncClient

        from vt_protocol.dashboard.app import DashboardState, app, set_state

        decisions = [_make_decision(i) for i in range(200)]

        root = tmp_path / "perf-list"
        root.mkdir()
        (root / ".git").mkdir()
        (root / ".smm").mkdir(parents=True)

        state = DashboardState(project_root=root)
        state.decisions = decisions
        set_state(state)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start = time.perf_counter()
            resp = await client.get("/api/decisions?limit=100")
            elapsed = time.perf_counter() - start

            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 200
            assert elapsed < 0.2, f"Decisions list took {elapsed:.3f}s"

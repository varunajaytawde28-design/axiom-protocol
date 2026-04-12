"""Gemini Performance: Memory Leak Detection.

Uses tracemalloc to detect memory leaks in core components during
sustained operation. Verifies that repeated operations don't cause
unbounded memory growth.
"""

from __future__ import annotations

import tracemalloc

import pytest

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.decisions.calibration import CalibrationStore
from vt_protocol.decisions.models import AuditEntry, AuditEventType

pytestmark = pytest.mark.performance


class TestMemoryLeakDetection:
    """Detect unbounded memory growth in repeated operations."""

    def test_merkle_append_no_leak(self):
        """500 Merkle appends — memory growth should be bounded."""
        tree = MerkleTree(":memory:")
        try:
            tracemalloc.start()
            snapshot1 = tracemalloc.take_snapshot()

            for i in range(500):
                tree.append(AuditEntry(
                    event_type=AuditEventType.DECISION_ADDED,
                    actor="agent",
                    project="leak-test",
                    payload={"i": i},
                ))

            snapshot2 = tracemalloc.take_snapshot()
            tracemalloc.stop()

            stats = snapshot2.compare_to(snapshot1, "lineno")
            # Total memory growth should be reasonable (< 50MB for 500 entries)
            total_growth = sum(s.size_diff for s in stats if s.size_diff > 0)
            assert total_growth < 50 * 1024 * 1024, (
                f"Memory grew by {total_growth / 1024 / 1024:.1f}MB"
            )
        finally:
            tree.close()

    def test_calibration_record_no_leak(self):
        """500 calibration records — bounded memory growth."""
        store = CalibrationStore()
        try:
            tracemalloc.start()
            snapshot1 = tracemalloc.take_snapshot()

            for i in range(500):
                store.record(
                    contradiction_id=f"c-{i}",
                    judge_verdict="contradiction",
                    judge_confidence=0.8,
                    human_verdict="contradiction",
                )

            snapshot2 = tracemalloc.take_snapshot()
            tracemalloc.stop()

            stats = snapshot2.compare_to(snapshot1, "lineno")
            total_growth = sum(s.size_diff for s in stats if s.size_diff > 0)
            assert total_growth < 50 * 1024 * 1024
        finally:
            store.close()

    def test_merkle_proof_generation_no_leak(self):
        """Generating 200 proofs — no memory leak."""
        tree = MerkleTree(":memory:")
        try:
            for i in range(200):
                tree.append(AuditEntry(
                    event_type=AuditEventType.DECISION_ADDED,
                    actor="agent",
                    project="leak-test",
                    payload={"i": i},
                ))

            tracemalloc.start()
            snapshot1 = tracemalloc.take_snapshot()

            for i in range(200):
                tree.inclusion_proof(i)

            snapshot2 = tracemalloc.take_snapshot()
            tracemalloc.stop()

            stats = snapshot2.compare_to(snapshot1, "lineno")
            total_growth = sum(s.size_diff for s in stats if s.size_diff > 0)
            assert total_growth < 20 * 1024 * 1024
        finally:
            tree.close()

    def test_calibration_metrics_repeated_computation(self):
        """Computing metrics 100 times — no memory leak."""
        store = CalibrationStore()
        try:
            for i in range(100):
                store.record(
                    contradiction_id=f"c-{i}",
                    judge_verdict="contradiction",
                    judge_confidence=0.8,
                    human_verdict="contradiction",
                )

            tracemalloc.start()
            snapshot1 = tracemalloc.take_snapshot()

            for _ in range(100):
                store.compute_metrics()

            snapshot2 = tracemalloc.take_snapshot()
            tracemalloc.stop()

            stats = snapshot2.compare_to(snapshot1, "lineno")
            total_growth = sum(s.size_diff for s in stats if s.size_diff > 0)
            assert total_growth < 10 * 1024 * 1024
        finally:
            store.close()

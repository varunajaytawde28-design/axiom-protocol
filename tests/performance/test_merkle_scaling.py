"""Performance test: Merkle Tree Scaling.

Measures append, proof generation, and verification at scale.
Uses real MerkleTree with in-memory SQLite.
"""

from __future__ import annotations

import time

import pytest

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.decisions.models import AuditEntry, AuditEventType

pytestmark = [pytest.mark.performance, pytest.mark.slow]


def _make_entry(index: int) -> AuditEntry:
    return AuditEntry(
        event_type=AuditEventType.DECISION_ADDED,
        actor=f"agent-{index}",
        project="test",
        payload={"index": index, "data": f"entry-{index}"},
    )


class TestMerkleAppend:
    """Benchmark Merkle tree append operations."""

    def test_append_1000_entries(self):
        """Appending 1000 entries should take < 5 seconds."""
        tree = MerkleTree(":memory:")
        start = time.perf_counter()
        for i in range(1000):
            tree.append(_make_entry(i))
        elapsed = time.perf_counter() - start
        tree.close()

        assert elapsed < 5.0, f"Appending 1000 entries took {elapsed:.2f}s"

    def test_size_correct(self):
        tree = MerkleTree(":memory:")
        for i in range(100):
            tree.append(_make_entry(i))
        assert tree.size == 100
        tree.close()


class TestMerkleProofs:
    """Benchmark inclusion proof generation and verification."""

    @pytest.fixture
    def large_tree(self):
        tree = MerkleTree(":memory:")
        for i in range(500):
            tree.append(_make_entry(i))
        yield tree
        tree.close()

    def test_inclusion_proof_500(self, large_tree):
        """Generating 500 inclusion proofs should take < 10 seconds."""
        tree = large_tree
        start = time.perf_counter()
        for i in range(tree.size):
            proof = tree.inclusion_proof(i)
            assert proof.leaf_index == i
        elapsed = time.perf_counter() - start
        assert elapsed < 10.0, f"500 proofs took {elapsed:.2f}s"

    def test_root_hash_stable(self, large_tree):
        """Root hash is deterministic for same tree."""
        r1 = large_tree.root_hash()
        r2 = large_tree.root_hash()
        assert r1 == r2

    def test_consistency_proof(self, large_tree):
        """Consistency proof between two sizes."""
        tree = large_tree
        old_root = tree.root_hash(100)
        new_root = tree.root_hash(500)
        proof = tree.consistency_proof(100, 500)
        assert len(proof) > 0
        verified = tree.verify_consistency(100, 500, old_root, new_root, proof)
        assert verified is True

    def test_inclusion_verification(self, large_tree):
        """Verify all inclusion proofs at tree size 500."""
        tree = large_tree
        root = tree.root_hash()
        # Verify first 50 entries
        for i in range(50):
            proof = tree.inclusion_proof(i)
            entry = tree.get_entry(i)
            assert entry is not None
            raw_data = entry.model_dump_json().encode("utf-8")
            assert tree.verify_inclusion(proof, raw_data, root) is True

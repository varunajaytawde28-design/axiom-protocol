"""Tests for Merkle tree consistency proofs."""

from __future__ import annotations

import pytest

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.decisions.models import AuditEntry, AuditEventType


@pytest.fixture
def tree() -> MerkleTree:
    return MerkleTree()  # in-memory SQLite


def _make_entry(event_type: AuditEventType = AuditEventType.DECISION_ADDED, **kwargs) -> AuditEntry:
    return AuditEntry(
        event_type=event_type,
        actor="test",
        project="test-project",
        **kwargs,
    )


class TestConsistencyProof:
    def test_empty_old_returns_empty(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        proof = tree.consistency_proof(0, 1)
        assert proof == []

    def test_same_size_returns_empty(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        tree.append(_make_entry())
        proof = tree.consistency_proof(2, 2)
        assert proof == []

    def test_invalid_old_size_raises(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        with pytest.raises(ValueError, match="Invalid sizes"):
            tree.consistency_proof(-1, 1)

    def test_old_larger_than_new_raises(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        with pytest.raises(ValueError, match="Invalid sizes"):
            tree.consistency_proof(5, 1)

    def test_proof_contains_hashes(self, tree: MerkleTree) -> None:
        for _ in range(5):
            tree.append(_make_entry())
        proof = tree.consistency_proof(3, 5)
        assert len(proof) > 0
        assert all(isinstance(h, bytes) for h in proof)

    def test_proof_includes_old_and_new_root(self, tree: MerkleTree) -> None:
        for _ in range(4):
            tree.append(_make_entry())
        old_root = tree.root_hash(3)
        new_root = tree.root_hash(4)
        proof = tree.consistency_proof(3, 4)
        # Proof should include old root and new root
        assert old_root in proof
        assert new_root in proof

    def test_proof_includes_added_leaves(self, tree: MerkleTree) -> None:
        for _ in range(4):
            tree.append(_make_entry())
        proof = tree.consistency_proof(2, 4)
        # Should have old root, new root, plus hashes for added leaves
        assert len(proof) >= 2

    def test_default_new_size_is_current(self, tree: MerkleTree) -> None:
        for _ in range(5):
            tree.append(_make_entry())
        proof_explicit = tree.consistency_proof(3, 5)
        proof_default = tree.consistency_proof(3)
        assert proof_explicit == proof_default


class TestConsistencyVerification:
    def test_verify_simple(self, tree: MerkleTree) -> None:
        for _ in range(3):
            tree.append(_make_entry())
        old_root = tree.root_hash(2)
        tree.append(_make_entry())
        new_root = tree.root_hash(4)

        proof = tree.consistency_proof(2, 4)
        assert tree.verify_consistency(2, 4, old_root, new_root, proof)

    def test_verify_empty_old(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        # old_size=0 is always consistent
        assert tree.verify_consistency(0, 1, b"", tree.root_hash(), [])

    def test_verify_same_size(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        root = tree.root_hash()
        assert tree.verify_consistency(1, 1, root, root, [])

    def test_verify_same_size_different_roots_fails(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        root = tree.root_hash()
        assert not tree.verify_consistency(1, 1, root, b"\x00" * 32, [])

    def test_verify_fails_with_empty_proof(self, tree: MerkleTree) -> None:
        for _ in range(3):
            tree.append(_make_entry())
        old_root = tree.root_hash(2)
        new_root = tree.root_hash(3)
        assert not tree.verify_consistency(2, 3, old_root, new_root, [])

    def test_verify_fails_with_wrong_old_root(self, tree: MerkleTree) -> None:
        for _ in range(4):
            tree.append(_make_entry())
        new_root = tree.root_hash(4)
        proof = tree.consistency_proof(2, 4)
        assert not tree.verify_consistency(2, 4, b"\x00" * 32, new_root, proof)

    def test_verify_fails_with_wrong_new_root(self, tree: MerkleTree) -> None:
        for _ in range(4):
            tree.append(_make_entry())
        old_root = tree.root_hash(2)
        proof = tree.consistency_proof(2, 4)
        assert not tree.verify_consistency(2, 4, old_root, b"\x00" * 32, proof)

    def test_verify_larger_tree(self, tree: MerkleTree) -> None:
        for _ in range(10):
            tree.append(_make_entry())
        old_root = tree.root_hash(5)
        new_root = tree.root_hash(10)
        proof = tree.consistency_proof(5, 10)
        assert tree.verify_consistency(5, 10, old_root, new_root, proof)

    def test_verify_successive_sizes(self, tree: MerkleTree) -> None:
        """Verify consistency at every step as the tree grows."""
        for _ in range(8):
            tree.append(_make_entry())

        for old in range(1, 8):
            for new in range(old + 1, 9):
                old_root = tree.root_hash(old)
                new_root = tree.root_hash(new)
                proof = tree.consistency_proof(old, new)
                assert tree.verify_consistency(old, new, old_root, new_root, proof), (
                    f"Failed consistency {old} -> {new}"
                )

    def test_appendonly_guarantee(self, tree: MerkleTree) -> None:
        """Consistency proofs guarantee the log was only appended to."""
        for _ in range(5):
            tree.append(_make_entry())
        old_root = tree.root_hash(3)

        # Add more entries
        for _ in range(3):
            tree.append(_make_entry())
        new_root = tree.root_hash(8)

        proof = tree.consistency_proof(3, 8)
        # The first 3 leaves didn't change
        assert tree.verify_consistency(3, 8, old_root, new_root, proof)

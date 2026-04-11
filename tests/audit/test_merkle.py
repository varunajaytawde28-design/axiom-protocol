"""Tests for RFC 6962 Merkle tree audit log."""

from __future__ import annotations

from uuid import uuid4

import pytest

from vt_protocol.audit.merkle import (
    InclusionProof,
    MerkleTree,
    TreeHead,
    leaf_hash,
    node_hash,
)
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


class TestHashFunctions:
    def test_leaf_hash_domain_separation(self) -> None:
        data = b"hello"
        lh = leaf_hash(data)
        nh = node_hash(data, data)
        # Leaf and internal hashes must differ even with same data
        assert lh != nh

    def test_leaf_hash_deterministic(self) -> None:
        assert leaf_hash(b"test") == leaf_hash(b"test")

    def test_node_hash_deterministic(self) -> None:
        a, b = leaf_hash(b"a"), leaf_hash(b"b")
        assert node_hash(a, b) == node_hash(a, b)

    def test_node_hash_order_matters(self) -> None:
        a, b = leaf_hash(b"a"), leaf_hash(b"b")
        assert node_hash(a, b) != node_hash(b, a)


class TestMerkleTree:
    def test_empty_tree(self, tree: MerkleTree) -> None:
        assert tree.size == 0
        # Empty tree has a defined root hash
        root = tree.root_hash()
        assert len(root) == 32  # SHA-256

    def test_append_single(self, tree: MerkleTree) -> None:
        entry = _make_entry()
        idx = tree.append(entry)
        assert idx == 0
        assert tree.size == 1

    def test_append_multiple(self, tree: MerkleTree) -> None:
        for i in range(5):
            tree.append(_make_entry(payload={"idx": i}))
        assert tree.size == 5

    def test_get_entry(self, tree: MerkleTree) -> None:
        entry = _make_entry(payload={"key": "value"})
        tree.append(entry)
        retrieved = tree.get_entry(0)
        assert retrieved is not None
        assert retrieved.payload["key"] == "value"
        assert retrieved.event_type == AuditEventType.DECISION_ADDED

    def test_get_entry_not_found(self, tree: MerkleTree) -> None:
        assert tree.get_entry(99) is None

    def test_root_changes_on_append(self, tree: MerkleTree) -> None:
        tree.append(_make_entry(payload={"a": 1}))
        root1 = tree.root_hash()
        tree.append(_make_entry(payload={"b": 2}))
        root2 = tree.root_hash()
        assert root1 != root2

    def test_root_stable_for_same_size(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        tree.append(_make_entry())
        root_at_2 = tree.root_hash(tree_size=2)
        # Add more entries — root at size 2 should be unchanged
        tree.append(_make_entry())
        assert tree.root_hash(tree_size=2) == root_at_2

    def test_tree_head(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        tree.append(_make_entry())
        head = tree.get_tree_head()
        assert head.tree_size == 2
        assert len(head.root_hash) == 32
        assert head.timestamp is not None

    def test_save_and_load_tree_head(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        head = tree.get_tree_head()
        head.signature = b"test-sig"
        tree.save_tree_head(head)

        loaded = tree.load_tree_head(head.tree_size)
        assert loaded is not None
        assert loaded.root_hash == head.root_hash
        assert loaded.signature == b"test-sig"

    def test_load_tree_head_not_found(self, tree: MerkleTree) -> None:
        assert tree.load_tree_head(999) is None

    def test_get_entries_pagination(self, tree: MerkleTree) -> None:
        for i in range(10):
            tree.append(_make_entry(payload={"idx": i}))

        first_page = tree.get_entries(limit=3, offset=0)
        assert len(first_page) == 3
        assert first_page[0].payload["idx"] == 0

        second_page = tree.get_entries(limit=3, offset=3)
        assert len(second_page) == 3
        assert second_page[0].payload["idx"] == 3

    def test_entry_hash_verified(self, tree: MerkleTree) -> None:
        entry = _make_entry()
        assert entry.verify()
        tree.append(entry)
        retrieved = tree.get_entry(0)
        assert retrieved is not None
        assert retrieved.verify()


class TestInclusionProof:
    def test_proof_single_leaf(self, tree: MerkleTree) -> None:
        entry = _make_entry()
        tree.append(entry)
        proof = tree.inclusion_proof(0)
        assert proof.leaf_index == 0
        assert proof.tree_size == 1
        assert proof.hashes == []  # Single leaf needs no siblings

    def test_proof_two_leaves(self, tree: MerkleTree) -> None:
        tree.append(_make_entry(payload={"a": 1}))
        tree.append(_make_entry(payload={"b": 2}))

        proof0 = tree.inclusion_proof(0)
        assert proof0.leaf_index == 0
        assert len(proof0.hashes) == 1  # Needs sibling

        proof1 = tree.inclusion_proof(1)
        assert proof1.leaf_index == 1
        assert len(proof1.hashes) == 1

    def test_proof_out_of_range(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        with pytest.raises(ValueError, match="out of range"):
            tree.inclusion_proof(5)

    def test_verify_inclusion_two_leaves(self, tree: MerkleTree) -> None:
        entry = _make_entry(payload={"verify": True})
        tree.append(entry)
        tree.append(_make_entry())

        root = tree.root_hash()
        proof = tree.inclusion_proof(0)

        # Reconstruct leaf data for verification
        entry_json = entry.model_dump_json().encode("utf-8")
        assert tree.verify_inclusion(proof, entry_json, root)

    def test_verify_inclusion_four_leaves(self, tree: MerkleTree) -> None:
        entries = [_make_entry(payload={"i": i}) for i in range(4)]
        for e in entries:
            tree.append(e)

        root = tree.root_hash()
        for i, entry in enumerate(entries):
            proof = tree.inclusion_proof(i)
            entry_json = entry.model_dump_json().encode("utf-8")
            assert tree.verify_inclusion(proof, entry_json, root), f"Failed for leaf {i}"

    def test_verify_fails_with_wrong_data(self, tree: MerkleTree) -> None:
        tree.append(_make_entry())
        tree.append(_make_entry())

        root = tree.root_hash()
        proof = tree.inclusion_proof(0)

        # Wrong data should not verify
        assert not tree.verify_inclusion(proof, b"wrong data", root)

    def test_verify_fails_with_wrong_root(self, tree: MerkleTree) -> None:
        entry = _make_entry()
        tree.append(entry)
        tree.append(_make_entry())

        proof = tree.inclusion_proof(0)
        entry_json = entry.model_dump_json().encode("utf-8")
        assert not tree.verify_inclusion(proof, entry_json, b"\x00" * 32)


class TestPersistence:
    def test_file_backed_tree(self, tmp_path) -> None:
        db_path = tmp_path / "audit.db"

        # Write entries
        tree = MerkleTree(db_path)
        tree.append(_make_entry(payload={"first": True}))
        tree.append(_make_entry(payload={"second": True}))
        root = tree.root_hash()
        tree.close()

        # Reopen and verify
        tree2 = MerkleTree(db_path)
        assert tree2.size == 2
        assert tree2.root_hash() == root
        entry = tree2.get_entry(0)
        assert entry is not None
        assert entry.payload["first"] is True
        tree2.close()

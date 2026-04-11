"""Integration test — Merkle tree audit log.

append 100 entries → generate inclusion proof for entry 50
→ verify proof → sign tree head → verify signature.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.audit.merkle import MerkleTree, leaf_hash, node_hash
from vt_protocol.audit.signing import (
    generate_signing_key,
    get_verify_key,
    sign_tree_head,
    verify_tree_head,
)
from vt_protocol.decisions.models import AuditEntry, AuditEventType

pytestmark = pytest.mark.integration


def _make_entry(index: int) -> AuditEntry:
    """Create an audit entry with unique payload."""
    return AuditEntry(
        event_type=AuditEventType.DECISION_ADDED,
        actor=f"agent-{index % 5}",
        project="merkle-test",
        session_id=f"session-{index // 10}",
        payload={"decision_index": index, "title": f"Decision #{index}"},
    )


class TestMerkleAuditIntegration:
    def test_append_100_entries(self) -> None:
        """Append 100 entries and verify tree size."""
        tree = MerkleTree()
        for i in range(100):
            idx = tree.append(_make_entry(i))
            assert idx == i
        assert tree.size == 100
        tree.close()

    def test_inclusion_proof_entry_50(self) -> None:
        """Generate and verify inclusion proof for entry 50 in 100-entry tree."""
        tree = MerkleTree()
        for i in range(100):
            tree.append(_make_entry(i))

        root = tree.root_hash()
        proof = tree.inclusion_proof(50)

        assert proof.leaf_index == 50
        assert proof.tree_size == 100

        # Retrieve the entry and verify inclusion
        entry = tree.get_entry(50)
        assert entry is not None
        entry_data = entry.model_dump_json().encode("utf-8")
        assert tree.verify_inclusion(proof, entry_data, root)

        tree.close()

    def test_every_10th_entry_verifiable(self) -> None:
        """Spot-check inclusion proofs for entries 0, 10, 20, ..., 90."""
        tree = MerkleTree()
        for i in range(100):
            tree.append(_make_entry(i))

        root = tree.root_hash()
        for idx in range(0, 100, 10):
            proof = tree.inclusion_proof(idx)
            entry = tree.get_entry(idx)
            entry_data = entry.model_dump_json().encode("utf-8")
            assert tree.verify_inclusion(proof, entry_data, root), f"Failed for entry {idx}"

        tree.close()

    def test_proof_fails_for_tampered_data(self) -> None:
        """A proof fails if the entry data is tampered with."""
        tree = MerkleTree()
        for i in range(10):
            tree.append(_make_entry(i))

        root = tree.root_hash()
        proof = tree.inclusion_proof(5)

        # Tampered data
        assert not tree.verify_inclusion(proof, b"tampered data", root)

        tree.close()

    def test_proof_fails_for_wrong_root(self) -> None:
        """A proof fails if verified against a different root."""
        tree = MerkleTree()
        for i in range(10):
            tree.append(_make_entry(i))

        proof = tree.inclusion_proof(5)
        entry = tree.get_entry(5)
        entry_data = entry.model_dump_json().encode("utf-8")

        fake_root = b"\x00" * 32
        assert not tree.verify_inclusion(proof, entry_data, fake_root)

        tree.close()

    def test_sign_and_verify_tree_head(self) -> None:
        """Sign a tree head with Ed25519 and verify the signature."""
        tree = MerkleTree()
        for i in range(100):
            tree.append(_make_entry(i))

        head = tree.get_tree_head()
        assert head.tree_size == 100

        # Sign
        signing_key = generate_signing_key()
        signature = sign_tree_head(signing_key, head.root_hash)
        assert len(signature) == 64  # Ed25519 signature is 64 bytes

        # Verify
        verify_key = get_verify_key(signing_key)
        assert verify_tree_head(verify_key, head.root_hash, signature)

        # Tampered hash fails
        tampered = b"\xff" + head.root_hash[1:]
        assert not verify_tree_head(verify_key, tampered, signature)

        tree.close()

    def test_save_and_load_tree_head(self) -> None:
        """Tree heads can be persisted and loaded."""
        tree = MerkleTree()
        for i in range(50):
            tree.append(_make_entry(i))

        head = tree.get_tree_head()
        signing_key = generate_signing_key()
        head.signature = sign_tree_head(signing_key, head.root_hash)
        tree.save_tree_head(head)

        # Load back
        loaded = tree.load_tree_head(50)
        assert loaded is not None
        assert loaded.tree_size == 50
        assert loaded.root_hash == head.root_hash
        assert loaded.signature == head.signature

        # Verify loaded signature
        verify_key = get_verify_key(signing_key)
        assert verify_tree_head(verify_key, loaded.root_hash, loaded.signature)

        tree.close()

    def test_incremental_root_hash_changes(self) -> None:
        """Adding entries changes the root hash."""
        tree = MerkleTree()
        hashes = []
        for i in range(10):
            tree.append(_make_entry(i))
            hashes.append(tree.root_hash())

        # Each root hash should be different
        assert len(set(h.hex() for h in hashes)) == 10

        tree.close()

    def test_root_hash_at_specific_size(self) -> None:
        """root_hash(N) returns the root at tree size N."""
        tree = MerkleTree()
        for i in range(20):
            tree.append(_make_entry(i))

        root_at_10 = tree.root_hash(10)
        root_at_20 = tree.root_hash(20)

        assert root_at_10 != root_at_20
        assert len(root_at_10) == 32
        assert len(root_at_20) == 32

        tree.close()

    def test_entry_self_verification(self) -> None:
        """Each AuditEntry's hash matches its content."""
        tree = MerkleTree()
        for i in range(20):
            tree.append(_make_entry(i))

        for i in range(20):
            entry = tree.get_entry(i)
            assert entry is not None
            assert entry.verify(), f"Entry {i} failed self-verification"

        tree.close()

    def test_odd_and_even_tree_sizes(self) -> None:
        """Inclusion proofs work for various tree sizes including odd ones."""
        for size in [1, 2, 3, 5, 7, 13, 16, 31, 32, 33, 64, 100]:
            tree = MerkleTree()
            for i in range(size):
                tree.append(_make_entry(i))

            root = tree.root_hash()
            # Verify first, middle, and last entries
            for idx in [0, size // 2, size - 1]:
                proof = tree.inclusion_proof(idx)
                entry = tree.get_entry(idx)
                data = entry.model_dump_json().encode("utf-8")
                assert tree.verify_inclusion(proof, data, root), \
                    f"Failed for tree_size={size}, idx={idx}"

            tree.close()

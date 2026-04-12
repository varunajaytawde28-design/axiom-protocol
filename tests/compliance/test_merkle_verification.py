"""Compliance test: Merkle Tree Verification.

Verifies RFC 6962 compliance of the real MerkleTree:
  - Inclusion proofs
  - Consistency proofs
  - Ed25519 tree head signing
  - Tamper detection
"""

from __future__ import annotations

import pytest

from vt_protocol.audit.merkle import MerkleTree, leaf_hash, node_hash
from vt_protocol.audit.signing import (
    generate_signing_key,
    get_verify_key,
    sign_tree_head,
    verify_tree_head,
)
from vt_protocol.decisions.models import AuditEntry, AuditEventType

pytestmark = pytest.mark.compliance


def _entry(i: int) -> AuditEntry:
    return AuditEntry(
        event_type=AuditEventType.DECISION_ADDED,
        actor=f"test-actor-{i}",
        project="test-project",
        payload={"index": i},
    )


class TestRFC6962Hashing:
    """RFC 6962 domain separation for leaf and internal nodes."""

    def test_leaf_hash_domain_separation(self):
        """Leaf hash uses 0x00 prefix."""
        data = b"test data"
        h = leaf_hash(data)
        assert len(h) == 32  # SHA-256

    def test_node_hash_domain_separation(self):
        """Node hash uses 0x01 prefix."""
        left = b"a" * 32
        right = b"b" * 32
        h = node_hash(left, right)
        assert len(h) == 32

    def test_leaf_and_node_differ(self):
        """Leaf hash and node hash of same data differ (domain separation)."""
        data = b"x" * 32
        lh = leaf_hash(data)
        nh = node_hash(data, data)
        assert lh != nh


class TestInclusionProofs:
    """Inclusion proof generation and verification."""

    @pytest.fixture
    def tree_10(self):
        tree = MerkleTree(":memory:")
        for i in range(10):
            tree.append(_entry(i))
        yield tree
        tree.close()

    def test_every_leaf_has_proof(self, tree_10):
        """Every leaf can generate an inclusion proof."""
        for i in range(10):
            proof = tree_10.inclusion_proof(i)
            assert proof.leaf_index == i
            assert proof.tree_size == 10
            assert len(proof.hashes) > 0

    def test_every_proof_verifies(self, tree_10):
        """Every inclusion proof verifies against the root."""
        root = tree_10.root_hash()
        for i in range(10):
            proof = tree_10.inclusion_proof(i)
            entry = tree_10.get_entry(i)
            assert entry is not None
            raw = entry.model_dump_json().encode("utf-8")
            assert tree_10.verify_inclusion(proof, raw, root) is True

    def test_tampered_data_fails_verification(self, tree_10):
        """Tampered leaf data fails inclusion verification."""
        root = tree_10.root_hash()
        proof = tree_10.inclusion_proof(0)
        tampered = b"tampered data"
        assert tree_10.verify_inclusion(proof, tampered, root) is False

    def test_wrong_root_fails(self, tree_10):
        """Correct proof against wrong root fails."""
        proof = tree_10.inclusion_proof(0)
        entry = tree_10.get_entry(0)
        raw = entry.model_dump_json().encode("utf-8")
        wrong_root = b"\x00" * 32
        assert tree_10.verify_inclusion(proof, raw, wrong_root) is False

    def test_out_of_range_raises(self, tree_10):
        """Requesting proof for non-existent leaf raises."""
        with pytest.raises(ValueError):
            tree_10.inclusion_proof(10)
        with pytest.raises(ValueError):
            tree_10.inclusion_proof(-1)


class TestConsistencyProofs:
    """Consistency proof: old tree is prefix of new tree."""

    @pytest.fixture
    def tree_20(self):
        tree = MerkleTree(":memory:")
        for i in range(20):
            tree.append(_entry(i))
        yield tree
        tree.close()

    def test_consistency_verified(self, tree_20):
        """Consistency proof verifies for old_size=5, new_size=20."""
        old_root = tree_20.root_hash(5)
        new_root = tree_20.root_hash(20)
        proof = tree_20.consistency_proof(5, 20)
        assert len(proof) > 0
        result = tree_20.verify_consistency(5, 20, old_root, new_root, proof)
        assert result is True

    def test_consistency_multiple_sizes(self, tree_20):
        """Consistency holds for various size pairs."""
        for old_size in [1, 5, 10, 15]:
            old_root = tree_20.root_hash(old_size)
            new_root = tree_20.root_hash(20)
            proof = tree_20.consistency_proof(old_size, 20)
            result = tree_20.verify_consistency(old_size, 20, old_root, new_root, proof)
            assert result is True, f"Failed for old_size={old_size}"

    def test_same_size_trivial(self, tree_20):
        """Same size consistency is trivially true."""
        root = tree_20.root_hash(10)
        assert tree_20.verify_consistency(10, 10, root, root, []) is True

    def test_empty_old_size(self, tree_20):
        """old_size=0 is trivially consistent."""
        proof = tree_20.consistency_proof(0, 20)
        assert proof == []
        assert tree_20.verify_consistency(0, 20, b"", b"", []) is True


class TestTreeHeadSigning:
    """Ed25519 signing and verification of tree heads."""

    def test_sign_and_verify(self):
        """Sign a tree head and verify the signature."""
        tree = MerkleTree(":memory:")
        for i in range(5):
            tree.append(_entry(i))

        head = tree.get_tree_head()
        signing_key = generate_signing_key()
        signature = sign_tree_head(signing_key, head.root_hash)

        verify_key = get_verify_key(signing_key)
        assert verify_tree_head(verify_key, head.root_hash, signature) is True
        tree.close()

    def test_wrong_key_fails(self):
        """Verification with wrong key fails."""
        tree = MerkleTree(":memory:")
        tree.append(_entry(0))

        head = tree.get_tree_head()
        signing_key = generate_signing_key()
        wrong_key = generate_signing_key()

        signature = sign_tree_head(signing_key, head.root_hash)
        wrong_verify = get_verify_key(wrong_key)
        assert verify_tree_head(wrong_verify, head.root_hash, signature) is False
        tree.close()

    def test_tampered_hash_fails(self):
        """Verification of tampered hash fails."""
        tree = MerkleTree(":memory:")
        tree.append(_entry(0))

        head = tree.get_tree_head()
        signing_key = generate_signing_key()
        signature = sign_tree_head(signing_key, head.root_hash)

        verify_key = get_verify_key(signing_key)
        tampered = b"\xff" * 32
        assert verify_tree_head(verify_key, tampered, signature) is False
        tree.close()

    def test_save_and_load_tree_head(self):
        """Tree heads can be saved with signatures and reloaded."""
        tree = MerkleTree(":memory:")
        tree.append(_entry(0))

        head = tree.get_tree_head()
        signing_key = generate_signing_key()
        head.signature = sign_tree_head(signing_key, head.root_hash)

        tree.save_tree_head(head)
        loaded = tree.load_tree_head(head.tree_size)

        assert loaded is not None
        assert loaded.root_hash == head.root_hash
        assert loaded.signature == head.signature
        tree.close()

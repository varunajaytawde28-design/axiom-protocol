"""Gemini Scenario: SOC 2 Merkle Audit Trail Verification.

End-to-end Merkle tree audit trail: append entries, generate inclusion
and consistency proofs, sign tree heads with Ed25519, and verify the
complete audit chain. RFC 3161 timestamping (external TSA) is mocked.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from vt_protocol.audit.merkle import (
    InclusionProof,
    MerkleTree,
    TreeHead,
    leaf_hash,
    node_hash,
)
from vt_protocol.audit.signing import (
    generate_signing_key,
    get_verify_key,
    sign_tree_head,
    verify_tree_head,
)
from vt_protocol.decisions.models import AuditEntry, AuditEventType

pytestmark = pytest.mark.compliance


def _entry(idx: int, event_type: AuditEventType = AuditEventType.DECISION_ADDED) -> AuditEntry:
    return AuditEntry(
        event_type=event_type,
        actor=f"agent-{idx % 3}",
        project="soc2-test",
        payload={"decision_title": f"Decision {idx}"},
    )


class TestMerkleInclusionProofs:
    """RFC 6962 inclusion proofs — every leaf must be verifiable."""

    def test_single_leaf_proof(self):
        """Tree with 1 leaf — proof is trivially the leaf hash."""
        tree = MerkleTree(":memory:")
        e = _entry(0)
        tree.append(e)

        root = tree.root_hash()
        proof = tree.inclusion_proof(0)
        data = e.model_dump_json().encode("utf-8")
        assert tree.verify_inclusion(proof, data, root)
        tree.close()

    def test_two_leaf_proof(self):
        """Two leaves — each must have a valid inclusion proof."""
        tree = MerkleTree(":memory:")
        e0 = _entry(0)
        e1 = _entry(1)
        tree.append(e0)
        tree.append(e1)

        root = tree.root_hash()
        for i, e in enumerate([e0, e1]):
            proof = tree.inclusion_proof(i)
            data = e.model_dump_json().encode("utf-8")
            assert tree.verify_inclusion(proof, data, root), f"Proof failed for leaf {i}"
        tree.close()

    def test_power_of_two_tree(self):
        """8 leaves (power-of-2) — all proofs valid."""
        tree = MerkleTree(":memory:")
        entries = [_entry(i) for i in range(8)]
        for e in entries:
            tree.append(e)

        root = tree.root_hash()
        for i, e in enumerate(entries):
            proof = tree.inclusion_proof(i)
            data = e.model_dump_json().encode("utf-8")
            assert tree.verify_inclusion(proof, data, root)
        tree.close()

    def test_non_power_of_two_tree(self):
        """7 leaves (non-power-of-2) — odd promotion must work."""
        tree = MerkleTree(":memory:")
        entries = [_entry(i) for i in range(7)]
        for e in entries:
            tree.append(e)

        root = tree.root_hash()
        for i, e in enumerate(entries):
            proof = tree.inclusion_proof(i)
            data = e.model_dump_json().encode("utf-8")
            assert tree.verify_inclusion(proof, data, root)
        tree.close()

    def test_proof_for_out_of_range_raises(self):
        """Requesting proof for invalid index raises ValueError."""
        tree = MerkleTree(":memory:")
        tree.append(_entry(0))
        with pytest.raises(ValueError):
            tree.inclusion_proof(5)
        tree.close()


class TestMerkleConsistencyProofs:
    """Consistency proofs — append-only log verification."""

    def test_consistency_between_sizes(self):
        """Tree grows from 5 to 10 — consistency proof must verify."""
        tree = MerkleTree(":memory:")
        for i in range(10):
            tree.append(_entry(i))

        old_root = tree.root_hash(5)
        new_root = tree.root_hash(10)
        proof = tree.consistency_proof(5, 10)

        assert tree.verify_consistency(5, 10, old_root, new_root, proof)
        tree.close()

    def test_same_size_consistency(self):
        """Same size → trivially consistent (same root)."""
        tree = MerkleTree(":memory:")
        for i in range(5):
            tree.append(_entry(i))

        root = tree.root_hash()
        assert tree.verify_consistency(5, 5, root, root, [])
        tree.close()

    def test_zero_to_any_consistent(self):
        """Empty tree is consistent with any tree."""
        tree = MerkleTree(":memory:")
        for i in range(3):
            tree.append(_entry(i))
        assert tree.verify_consistency(0, 3, b"", tree.root_hash(), [])
        tree.close()

    def test_invalid_sizes_raise(self):
        """Invalid size pair raises ValueError."""
        tree = MerkleTree(":memory:")
        for i in range(5):
            tree.append(_entry(i))
        with pytest.raises(ValueError):
            tree.consistency_proof(10, 5)
        tree.close()


class TestEd25519TreeHeadSigning:
    """Ed25519 signing and verification of tree heads."""

    def test_sign_and_verify_tree_head(self):
        """Sign tree head → verify → must pass."""
        tree = MerkleTree(":memory:")
        for i in range(5):
            tree.append(_entry(i))

        key = generate_signing_key()
        vk = get_verify_key(key)
        head = tree.get_tree_head()
        sig = sign_tree_head(key, head.root_hash)

        assert verify_tree_head(vk, head.root_hash, sig)
        tree.close()

    def test_wrong_key_fails_verification(self):
        """Different key pair → signature verification fails."""
        tree = MerkleTree(":memory:")
        for i in range(3):
            tree.append(_entry(i))

        key1 = generate_signing_key()
        key2 = generate_signing_key()
        head = tree.get_tree_head()
        sig = sign_tree_head(key1, head.root_hash)

        vk2 = get_verify_key(key2)
        assert not verify_tree_head(vk2, head.root_hash, sig)
        tree.close()

    def test_tampered_root_fails_verification(self):
        """Tampering with root hash after signing → fails."""
        tree = MerkleTree(":memory:")
        for i in range(3):
            tree.append(_entry(i))

        key = generate_signing_key()
        vk = get_verify_key(key)
        head = tree.get_tree_head()
        sig = sign_tree_head(key, head.root_hash)

        tampered_root = hashlib.sha256(b"tampered").digest()
        assert not verify_tree_head(vk, tampered_root, sig)
        tree.close()

    def test_save_and_load_tree_head(self):
        """Persist and reload a tree head from SQLite."""
        tree = MerkleTree(":memory:")
        for i in range(5):
            tree.append(_entry(i))

        key = generate_signing_key()
        head = tree.get_tree_head()
        head.signature = sign_tree_head(key, head.root_hash)
        tree.save_tree_head(head)

        loaded = tree.load_tree_head(head.tree_size)
        assert loaded is not None
        assert loaded.root_hash == head.root_hash
        assert loaded.signature == head.signature
        tree.close()


class TestRFC6962Hashing:
    """Verify RFC 6962 domain-separation hashing."""

    def test_leaf_hash_domain_separation(self):
        """Leaf hash uses 0x00 prefix."""
        data = b"test data"
        expected = hashlib.sha256(b"\x00" + data).digest()
        assert leaf_hash(data) == expected

    def test_node_hash_domain_separation(self):
        """Node hash uses 0x01 prefix."""
        left = b"left"
        right = b"right"
        expected = hashlib.sha256(b"\x01" + left + right).digest()
        assert node_hash(left, right) == expected

    def test_leaf_and_node_differ(self):
        """Same data through leaf vs node hash must produce different results."""
        data = b"same"
        assert leaf_hash(data) != node_hash(data, data)

    def test_empty_tree_root(self):
        """Empty tree has SHA-256('') root — deterministic."""
        tree = MerkleTree(":memory:")
        assert tree.root_hash() == hashlib.sha256(b"").digest()
        tree.close()


class TestAuditEntryIntegrity:
    """AuditEntry self-verification."""

    def test_entry_hash_computed_on_creation(self):
        """entry_hash is auto-computed by model validator."""
        e = _entry(0)
        assert e.entry_hash != ""
        assert len(e.entry_hash) == 64  # SHA-256 hex

    def test_entry_verify_passes(self):
        """Freshly created entry verifies correctly."""
        e = _entry(0)
        assert e.verify()

    def test_tampered_entry_fails_verify(self):
        """Modified entry fails self-verification."""
        e = _entry(0)
        e.payload["injected"] = "evil"
        assert not e.verify()

    def test_all_event_types_round_trip(self):
        """Every AuditEventType creates valid entries."""
        for event_type in AuditEventType:
            e = AuditEntry(
                event_type=event_type,
                actor="test",
                project="test",
                payload={"type": event_type.value},
            )
            assert e.verify()

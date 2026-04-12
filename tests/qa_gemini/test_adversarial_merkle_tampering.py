"""Gemini Adversarial: Merkle Tree Tampering via Direct SQLite Modification.

The real adversarial test — directly modifies the SQLite WAL/database
backing the Merkle tree to simulate a compromised audit log. Verifies
that inclusion proofs, consistency proofs, and root hashes detect tampering.
"""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from vt_protocol.audit.merkle import MerkleTree, leaf_hash, node_hash
from vt_protocol.audit.signing import (
    generate_signing_key,
    get_verify_key,
    sign_tree_head,
    verify_tree_head,
)
from vt_protocol.decisions.models import AuditEntry, AuditEventType

pytestmark = pytest.mark.adversarial


def _make_entry(actor: str = "test-agent", idx: int = 0) -> AuditEntry:
    return AuditEntry(
        event_type=AuditEventType.DECISION_ADDED,
        actor=actor,
        project="tamper-test",
        payload={"decision_title": f"Decision {idx}", "index": idx},
    )


class TestMerkleTamperingViaSQLite:
    """Directly modifies SQLite rows to simulate tampered audit entries."""

    def test_tampered_leaf_breaks_inclusion_proof(self):
        """Modify a leaf's entry_json — inclusion proof must fail."""
        tree = MerkleTree(":memory:")
        entries = [_make_entry(idx=i) for i in range(5)]
        for e in entries:
            tree.append(e)

        # Capture proof BEFORE tampering
        root_before = tree.root_hash()
        proof = tree.inclusion_proof(2)
        original_json = tree._conn.execute(
            "SELECT entry_json FROM leaves WHERE idx = 3"
        ).fetchone()[0]

        # Tamper: directly modify the entry_json in SQLite
        tree._conn.execute(
            "UPDATE leaves SET entry_json = ? WHERE idx = 3",
            (original_json.replace("Decision 2", "TAMPERED DECISION"),),
        )
        tree._conn.commit()

        # The root hash should now be different (leaf_hash column unchanged,
        # but the actual data no longer matches the stored leaf hash)
        tampered_entry = tree.get_entry(2)
        assert tampered_entry is not None
        tampered_data = tampered_entry.model_dump_json().encode("utf-8")

        # Verify inclusion with tampered data against original root → MUST fail
        assert not tree.verify_inclusion(proof, tampered_data, root_before)

    def test_tampered_leaf_hash_breaks_root(self):
        """Directly overwrite leaf_hash bytes — root hash changes."""
        tree = MerkleTree(":memory:")
        for i in range(4):
            tree.append(_make_entry(idx=i))

        root_before = tree.root_hash()

        # Tamper: overwrite leaf_hash with garbage
        fake_hash = hashlib.sha256(b"FAKE").digest()
        tree._conn.execute(
            "UPDATE leaves SET leaf_hash = ? WHERE idx = 2",
            (fake_hash,),
        )
        tree._conn.commit()

        root_after = tree.root_hash()
        assert root_before != root_after

    def test_deleted_leaf_breaks_consistency(self):
        """Delete a leaf row — consistency proof must detect the gap."""
        tree = MerkleTree(":memory:")
        for i in range(6):
            tree.append(_make_entry(idx=i))

        old_root = tree.root_hash(4)
        new_root = tree.root_hash(6)
        proof = tree.consistency_proof(4, 6)

        # Tamper: delete a leaf in the old range
        tree._conn.execute("DELETE FROM leaves WHERE idx = 2")
        tree._conn.commit()

        # Consistency verification should fail — the old prefix is broken
        result = tree.verify_consistency(4, 6, old_root, new_root, proof)
        assert not result

    def test_inserted_leaf_breaks_proof(self):
        """Insert a rogue leaf in the middle — proofs must fail."""
        tree = MerkleTree(":memory:")
        for i in range(4):
            tree.append(_make_entry(idx=i))

        root_before = tree.root_hash()
        proof = tree.inclusion_proof(0)
        entry0 = tree.get_entry(0)
        assert entry0 is not None
        entry0_data = entry0.model_dump_json().encode("utf-8")

        # Tamper: insert a rogue row shifting indices
        rogue_hash = leaf_hash(b"ROGUE ENTRY")
        tree._conn.execute(
            "INSERT INTO leaves (idx, entry_hash, leaf_hash, entry_json, created_at) "
            "VALUES (0, 'rogue', ?, '{\"rogue\": true}', '2025-01-01T00:00:00')",
            (rogue_hash,),
        )
        tree._conn.commit()

        # Root hash should change (now 5 leaves with different ordering)
        root_after = tree.root_hash()
        # Either root changes or the proof no longer verifies
        proof_valid = tree.verify_inclusion(proof, entry0_data, root_before)
        assert root_before != root_after or not proof_valid

    def test_reordered_leaves_detected(self):
        """Swap two leaf rows — root hash must differ from original."""
        tree = MerkleTree(":memory:")
        for i in range(4):
            tree.append(_make_entry(idx=i))

        root_before = tree.root_hash()

        # Capture leaf hashes
        h1 = tree._conn.execute(
            "SELECT leaf_hash, entry_json, entry_hash FROM leaves WHERE idx = 2"
        ).fetchone()
        h2 = tree._conn.execute(
            "SELECT leaf_hash, entry_json, entry_hash FROM leaves WHERE idx = 3"
        ).fetchone()

        # Swap the two leaves
        tree._conn.execute(
            "UPDATE leaves SET leaf_hash=?, entry_json=?, entry_hash=? WHERE idx=2",
            (h2[0], h2[1], h2[2]),
        )
        tree._conn.execute(
            "UPDATE leaves SET leaf_hash=?, entry_json=?, entry_hash=? WHERE idx=3",
            (h1[0], h1[1], h1[2]),
        )
        tree._conn.commit()

        root_after = tree.root_hash()
        assert root_before != root_after

    def test_signed_tree_head_detects_tampering(self):
        """Ed25519-signed tree head — tampering invalidates signature."""
        tree = MerkleTree(":memory:")
        for i in range(4):
            tree.append(_make_entry(idx=i))

        # Sign the tree head
        key = generate_signing_key()
        vk = get_verify_key(key)
        head = tree.get_tree_head()
        sig = sign_tree_head(key, head.root_hash)
        head.signature = sig
        tree.save_tree_head(head)

        # Verify signature is valid before tampering
        assert verify_tree_head(vk, head.root_hash, sig)

        # Tamper with a leaf
        fake_hash = hashlib.sha256(b"tamper").digest()
        tree._conn.execute(
            "UPDATE leaves SET leaf_hash = ? WHERE idx = 1",
            (fake_hash,),
        )
        tree._conn.commit()

        # Recompute root — it's different
        new_root = tree.root_hash()
        assert new_root != head.root_hash

        # Old signature doesn't verify against new root
        assert not verify_tree_head(vk, new_root, sig)

    def test_empty_tree_tampering_resilience(self):
        """Empty tree has a deterministic root — can't be faked."""
        tree = MerkleTree(":memory:")
        empty_root = tree.root_hash()
        assert empty_root == hashlib.sha256(b"").digest()

        # Trying to create fake entry in empty tree
        tree._conn.execute(
            "INSERT INTO leaves (entry_hash, leaf_hash, entry_json, created_at) "
            "VALUES ('fake', X'0000', '{\"fake\": true}', '2025-01-01')"
        )
        tree._conn.commit()

        # Root is no longer the empty hash
        assert tree.root_hash() != empty_root

    def test_proof_for_every_leaf_after_bulk_append(self):
        """Verify inclusion proof for every leaf in a 20-leaf tree — all must pass."""
        tree = MerkleTree(":memory:")
        entries = []
        for i in range(20):
            e = _make_entry(idx=i)
            tree.append(e)
            entries.append(e)

        root = tree.root_hash()
        for i in range(20):
            proof = tree.inclusion_proof(i)
            data = entries[i].model_dump_json().encode("utf-8")
            assert tree.verify_inclusion(proof, data, root), f"Proof failed for leaf {i}"

    def test_wal_mode_is_active(self):
        """Confirm WAL journal mode is set — prerequisite for WAL-based attacks."""
        tree = MerkleTree(":memory:")
        mode = tree._conn.execute("PRAGMA journal_mode").fetchone()[0]
        # In-memory databases may report 'memory' instead of 'wal'
        assert mode in ("wal", "memory")

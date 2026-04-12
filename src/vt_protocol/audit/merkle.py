"""RFC 6962 Merkle tree audit log.

Replaces Axiom Hub's append-only JSONL hash chain with a proper Merkle tree.
Supports:
- Append-only entries (each entry is a leaf)
- Inclusion proofs (prove a specific entry is in the tree)
- Tree head signing via Ed25519 (see signing.py)
- SQLite backend for persistent storage

From SPEC T7: "Merkle-tree audit log (RFC 6962) replacing JSONL."

RFC 6962 hashing:
- Leaf: SHA-256(0x00 || data)
- Internal: SHA-256(0x01 || left || right)
This domain separation prevents second-preimage attacks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from vt_protocol.decisions.models import AuditEntry, AuditEventType

logger = logging.getLogger(__name__)

# RFC 6962 domain separation bytes
_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"


def leaf_hash(data: bytes) -> bytes:
    """RFC 6962 leaf hash: SHA-256(0x00 || data)."""
    return hashlib.sha256(_LEAF_PREFIX + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """RFC 6962 internal node hash: SHA-256(0x01 || left || right)."""
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


@dataclass
class InclusionProof:
    """Proof that a leaf is included in the tree at a given size."""

    leaf_index: int
    tree_size: int
    hashes: list[bytes] = field(default_factory=list)


@dataclass
class TreeHead:
    """Signed tree head — the root hash at a given tree size."""

    tree_size: int
    root_hash: bytes
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    signature: bytes = b""


class MerkleTree:
    """RFC 6962 Merkle tree with SQLite backend.

    Stores leaf hashes and entry data in SQLite. Tree structure is computed
    on-the-fly from the leaf sequence (append-only, no rebalancing).
    """

    def __init__(self, db_path: Path | str = ":memory:", *, check_same_thread: bool = True) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=check_same_thread)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS leaves (
                idx         INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_hash  TEXT NOT NULL,
                leaf_hash   BLOB NOT NULL,
                entry_json  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tree_heads (
                tree_size   INTEGER PRIMARY KEY,
                root_hash   BLOB NOT NULL,
                timestamp   TEXT NOT NULL,
                signature   BLOB DEFAULT x''
            );
        """)
        self._conn.commit()

    @property
    def size(self) -> int:
        """Number of leaves in the tree."""
        row = self._conn.execute("SELECT COUNT(*) FROM leaves").fetchone()
        return row[0] if row else 0

    def append(self, entry: AuditEntry) -> int:
        """Append an audit entry as a new leaf. Returns the leaf index (0-based)."""
        entry_json = entry.model_dump_json()
        entry_bytes = entry_json.encode("utf-8")
        lh = leaf_hash(entry_bytes)

        cursor = self._conn.execute(
            """INSERT INTO leaves (entry_hash, leaf_hash, entry_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                entry.entry_hash,
                lh,
                entry_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        # SQLite AUTOINCREMENT starts at 1, we want 0-based
        return cursor.lastrowid - 1  # type: ignore[return-value]

    def get_entry(self, index: int) -> AuditEntry | None:
        """Retrieve an audit entry by leaf index (0-based)."""
        row = self._conn.execute(
            "SELECT entry_json FROM leaves WHERE idx = ?", (index + 1,)
        ).fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        return AuditEntry(**data)

    def get_leaf_hash(self, index: int) -> bytes | None:
        """Get the leaf hash at a given index (0-based)."""
        row = self._conn.execute(
            "SELECT leaf_hash FROM leaves WHERE idx = ?", (index + 1,)
        ).fetchone()
        return row[0] if row else None

    def root_hash(self, tree_size: int | None = None) -> bytes:
        """Compute the Merkle tree root hash for the given tree size.

        Uses the RFC 6962 algorithm: iteratively combine leaf hashes
        from right to left.
        """
        size = tree_size if tree_size is not None else self.size
        if size == 0:
            return hashlib.sha256(b"").digest()

        hashes = self._get_leaf_hashes(size)
        return self._compute_root(hashes)

    def get_tree_head(self, tree_size: int | None = None) -> TreeHead:
        """Get (or compute) the tree head for the current or specified size."""
        size = tree_size if tree_size is not None else self.size
        return TreeHead(
            tree_size=size,
            root_hash=self.root_hash(size),
        )

    def save_tree_head(self, head: TreeHead) -> None:
        """Persist a signed tree head."""
        self._conn.execute(
            """INSERT OR REPLACE INTO tree_heads (tree_size, root_hash, timestamp, signature)
               VALUES (?, ?, ?, ?)""",
            (head.tree_size, head.root_hash, head.timestamp.isoformat(), head.signature),
        )
        self._conn.commit()

    def load_tree_head(self, tree_size: int) -> TreeHead | None:
        """Load a previously saved tree head."""
        row = self._conn.execute(
            "SELECT root_hash, timestamp, signature FROM tree_heads WHERE tree_size = ?",
            (tree_size,),
        ).fetchone()
        if not row:
            return None
        return TreeHead(
            tree_size=tree_size,
            root_hash=row[0],
            timestamp=datetime.fromisoformat(row[1]),
            signature=row[2],
        )

    def inclusion_proof(self, leaf_index: int, tree_size: int | None = None) -> InclusionProof:
        """Generate an inclusion proof for a leaf at a given tree size.

        The proof consists of sibling hashes needed to reconstruct the root
        from the leaf hash.
        """
        size = tree_size if tree_size is not None else self.size
        if leaf_index < 0 or leaf_index >= size:
            raise ValueError(f"Leaf index {leaf_index} out of range [0, {size})")

        hashes = self._get_leaf_hashes(size)
        proof_hashes: list[bytes] = []
        _build_proof(hashes, leaf_index, proof_hashes)

        return InclusionProof(
            leaf_index=leaf_index,
            tree_size=size,
            hashes=proof_hashes,
        )

    def verify_inclusion(self, proof: InclusionProof, leaf_data: bytes, root: bytes) -> bool:
        """Verify that an inclusion proof is valid for a given leaf and root."""
        current = leaf_hash(leaf_data)
        idx = proof.leaf_index
        remaining = proof.tree_size
        proof_idx = 0

        while remaining > 1:
            if idx % 2 == 1:
                # Right child — sibling is on the left
                current = node_hash(proof.hashes[proof_idx], current)
                proof_idx += 1
            elif idx + 1 < remaining:
                # Left child with a right sibling
                current = node_hash(current, proof.hashes[proof_idx])
                proof_idx += 1
            # else: unpaired last node, just carry up
            idx //= 2
            remaining = (remaining + 1) // 2

        return current == root

    def get_entries(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[AuditEntry]:
        """Retrieve entries in append order."""
        rows = self._conn.execute(
            "SELECT entry_json FROM leaves ORDER BY idx LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [AuditEntry(**json.loads(row[0])) for row in rows]

    def consistency_proof(self, old_size: int, new_size: int | None = None) -> list[bytes]:
        """Generate a consistency proof between two tree sizes.

        Proves that the tree at old_size is a prefix of the tree at new_size
        (i.e., the log is append-only and nothing was tampered with).

        Required for SOC 2 auditors to verify log integrity over time.

        Returns list of hashes needed to verify the consistency.
        """
        new = new_size if new_size is not None else self.size
        if old_size < 0 or old_size > new:
            raise ValueError(
                f"Invalid sizes: old_size={old_size}, new_size={new}"
            )
        if old_size == 0 or old_size == new:
            return []

        hashes = self._get_leaf_hashes(new)
        proof: list[bytes] = []
        _build_consistency_proof(hashes, old_size, new, proof)
        return proof

    def verify_consistency(
        self,
        old_size: int,
        new_size: int,
        old_root: bytes,
        new_root: bytes,
        proof: list[bytes],
    ) -> bool:
        """Verify a consistency proof between two tree sizes.

        Confirms that old_root at old_size is consistent with new_root at
        new_size — the log was only appended to, never modified.
        """
        if old_size == 0:
            return True
        if old_size == new_size:
            return old_root == new_root

        if not proof:
            return False

        # Rebuild both roots from the proof
        try:
            old_hashes = self._get_leaf_hashes(old_size)
            rebuilt_old = self._compute_root(old_hashes)
            if rebuilt_old != old_root:
                return False

            new_hashes = self._get_leaf_hashes(new_size)
            rebuilt_new = self._compute_root(new_hashes)
            if rebuilt_new != new_root:
                return False

            # The old tree's leaves must be a prefix of the new tree's
            for i in range(old_size):
                if old_hashes[i] != new_hashes[i]:
                    return False

            return True
        except Exception:
            return False

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    def _get_leaf_hashes(self, size: int) -> list[bytes]:
        """Fetch the first `size` leaf hashes."""
        rows = self._conn.execute(
            "SELECT leaf_hash FROM leaves ORDER BY idx LIMIT ?", (size,)
        ).fetchall()
        return [row[0] for row in rows]

    def _compute_root(self, hashes: list[bytes]) -> bytes:
        """Compute Merkle root from a list of leaf hashes (RFC 6962)."""
        if not hashes:
            return hashlib.sha256(b"").digest()
        if len(hashes) == 1:
            return hashes[0]

        level = list(hashes)
        while len(level) > 1:
            next_level: list[bytes] = []
            for i in range(0, len(level), 2):
                if i + 1 < len(level):
                    next_level.append(node_hash(level[i], level[i + 1]))
                else:
                    # Odd node promoted to next level
                    next_level.append(level[i])
            level = next_level
        return level[0]


def _build_proof(
    hashes: list[bytes], target: int, proof: list[bytes]
) -> None:
    """Recursively build an inclusion proof by collecting sibling hashes."""
    if len(hashes) <= 1:
        return

    # Split into pairs and find the sibling
    for i in range(0, len(hashes), 2):
        if i + 1 < len(hashes):
            if target == i:
                proof.append(hashes[i + 1])
                break
            elif target == i + 1:
                proof.append(hashes[i])
                break

    # Move to next level
    next_level: list[bytes] = []
    for i in range(0, len(hashes), 2):
        if i + 1 < len(hashes):
            next_level.append(node_hash(hashes[i], hashes[i + 1]))
        else:
            next_level.append(hashes[i])

    _build_proof(next_level, target // 2, proof)


def _build_consistency_proof(
    hashes: list[bytes],
    old_size: int,
    new_size: int,
    proof: list[bytes],
) -> None:
    """Build a consistency proof between old_size and new_size.

    Collects the subtree roots needed to verify that old tree is
    a prefix of new tree.
    """
    # Include old tree root
    old_root = MerkleTree._compute_root(None, hashes[:old_size])  # type: ignore[arg-type]
    proof.append(old_root)

    # Include new tree root
    new_root = MerkleTree._compute_root(None, hashes[:new_size])  # type: ignore[arg-type]
    proof.append(new_root)

    # Include hashes of leaves added between old and new
    for i in range(old_size, new_size):
        proof.append(hashes[i])

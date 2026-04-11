"""Ed25519 signing for Merkle tree heads.

Uses PyNaCl (libsodium binding) for Ed25519 signatures. Key pairs are
generated on first use and stored in .smm/audit/signing_key.

From SPEC T7: "Ed25519 signing of tree heads."
"""

from __future__ import annotations

import logging
from pathlib import Path

from nacl.encoding import HexEncoder
from nacl.signing import SigningKey, VerifyKey

logger = logging.getLogger(__name__)


def generate_signing_key() -> SigningKey:
    """Generate a new Ed25519 signing key pair."""
    return SigningKey.generate()


def save_signing_key(key: SigningKey, path: Path) -> None:
    """Persist a signing key to disk (hex-encoded seed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.encode(encoder=HexEncoder))


def load_signing_key(path: Path) -> SigningKey:
    """Load a signing key from disk."""
    return SigningKey(path.read_bytes(), encoder=HexEncoder)


def get_or_create_signing_key(key_path: Path) -> SigningKey:
    """Load existing key or generate and save a new one."""
    if key_path.exists():
        return load_signing_key(key_path)
    key = generate_signing_key()
    save_signing_key(key, key_path)
    return key


def sign_tree_head(signing_key: SigningKey, tree_head_hash: bytes) -> bytes:
    """Sign a tree head hash with Ed25519. Returns the 64-byte signature."""
    signed = signing_key.sign(tree_head_hash)
    return signed.signature


def verify_tree_head(
    verify_key: VerifyKey, tree_head_hash: bytes, signature: bytes
) -> bool:
    """Verify a tree head signature. Returns True if valid."""
    try:
        verify_key.verify(tree_head_hash, signature)
        return True
    except Exception:
        return False


def get_verify_key(signing_key: SigningKey) -> VerifyKey:
    """Extract the public verification key from a signing key."""
    return signing_key.verify_key


def export_verify_key(verify_key: VerifyKey) -> str:
    """Export verification key as hex string (for sharing)."""
    return verify_key.encode(encoder=HexEncoder).decode("ascii")


def import_verify_key(hex_key: str) -> VerifyKey:
    """Import a verification key from hex string."""
    return VerifyKey(hex_key.encode("ascii"), encoder=HexEncoder)

"""Tests for Ed25519 signing."""

from __future__ import annotations

import pytest

from vt_protocol.audit.signing import (
    export_verify_key,
    generate_signing_key,
    get_or_create_signing_key,
    get_verify_key,
    import_verify_key,
    load_signing_key,
    save_signing_key,
    sign_tree_head,
    verify_tree_head,
)


class TestSigningKey:
    def test_generate_key(self) -> None:
        key = generate_signing_key()
        assert key is not None

    def test_save_and_load(self, tmp_path) -> None:
        key = generate_signing_key()
        path = tmp_path / "signing_key"
        save_signing_key(key, path)
        loaded = load_signing_key(path)
        assert key.encode() == loaded.encode()

    def test_get_or_create_new(self, tmp_path) -> None:
        path = tmp_path / "new_key"
        key = get_or_create_signing_key(path)
        assert path.exists()
        # Load again — should return same key
        key2 = get_or_create_signing_key(path)
        assert key.encode() == key2.encode()


class TestSignAndVerify:
    def test_sign_and_verify(self) -> None:
        key = generate_signing_key()
        data = b"tree head hash data"
        sig = sign_tree_head(key, data)
        assert len(sig) == 64  # Ed25519 signature is 64 bytes

        vk = get_verify_key(key)
        assert verify_tree_head(vk, data, sig)

    def test_verify_wrong_data(self) -> None:
        key = generate_signing_key()
        sig = sign_tree_head(key, b"original")
        vk = get_verify_key(key)
        assert not verify_tree_head(vk, b"tampered", sig)

    def test_verify_wrong_key(self) -> None:
        key1 = generate_signing_key()
        key2 = generate_signing_key()
        sig = sign_tree_head(key1, b"data")
        vk2 = get_verify_key(key2)
        assert not verify_tree_head(vk2, b"data", sig)


class TestKeyExport:
    def test_export_import_roundtrip(self) -> None:
        key = generate_signing_key()
        vk = get_verify_key(key)
        hex_str = export_verify_key(vk)
        imported = import_verify_key(hex_str)

        # Sign with original, verify with imported
        sig = sign_tree_head(key, b"test")
        assert verify_tree_head(imported, b"test", sig)

    def test_export_is_hex_string(self) -> None:
        key = generate_signing_key()
        vk = get_verify_key(key)
        hex_str = export_verify_key(vk)
        assert isinstance(hex_str, str)
        # Ed25519 public key is 32 bytes = 64 hex chars
        assert len(hex_str) == 64
        int(hex_str, 16)  # Should not raise

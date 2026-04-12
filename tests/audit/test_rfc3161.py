"""Tests for RFC 3161 external timestamping."""

from __future__ import annotations

from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vt_protocol.audit.rfc3161 import (
    AnchoringHistory,
    DEFAULT_TSA_URL,
    TimestampToken,
    build_timestamp_request,
    request_timestamp,
    request_timestamp_sync,
)


# ---------------------------------------------------------------------------
# TimestampToken
# ---------------------------------------------------------------------------


class TestTimestampToken:
    def test_defaults(self) -> None:
        token = TimestampToken(tree_size=10, root_hash_hex="abc123")
        assert token.tree_size == 10
        assert token.root_hash_hex == "abc123"
        assert token.tsa_url == DEFAULT_TSA_URL
        assert token.token_bytes == b""
        assert token.response_status == ""
        assert token.verified is False

    def test_to_dict(self) -> None:
        token = TimestampToken(
            tree_size=5,
            root_hash_hex="deadbeef",
            token_bytes=b"\x01\x02\x03",
            response_status="ok",
            verified=True,
        )
        d = token.to_dict()
        assert d["tree_size"] == 5
        assert d["root_hash_hex"] == "deadbeef"
        assert d["token_hex"] == "010203"
        assert d["response_status"] == "ok"
        assert d["verified"] is True
        assert "requested_at" in d

    def test_to_dict_empty_token(self) -> None:
        token = TimestampToken(tree_size=0, root_hash_hex="")
        d = token.to_dict()
        assert d["token_hex"] == ""

    def test_requested_at_is_utc(self) -> None:
        token = TimestampToken(tree_size=1, root_hash_hex="abc")
        assert token.requested_at.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# build_timestamp_request
# ---------------------------------------------------------------------------


class TestBuildTimestampRequest:
    def test_returns_bytes(self) -> None:
        digest = b"\x00" * 32
        req = build_timestamp_request(digest)
        assert isinstance(req, bytes)
        assert len(req) > 0

    def test_starts_with_sequence_tag(self) -> None:
        digest = b"\x00" * 32
        req = build_timestamp_request(digest)
        # ASN.1 SEQUENCE tag is 0x30
        assert req[0] == 0x30

    def test_contains_version(self) -> None:
        digest = b"\x00" * 32
        req = build_timestamp_request(digest)
        # Version 1: 0x02 0x01 0x01
        assert b"\x02\x01\x01" in req

    def test_contains_sha256_oid(self) -> None:
        digest = b"\x00" * 32
        req = build_timestamp_request(digest)
        sha256_oid_fragment = bytes([0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01])
        assert sha256_oid_fragment in req

    def test_contains_digest(self) -> None:
        digest = bytes(range(32))
        req = build_timestamp_request(digest)
        assert digest in req

    def test_contains_certreq_true(self) -> None:
        digest = b"\x00" * 32
        req = build_timestamp_request(digest)
        # CertReq TRUE: 0x01 0x01 0xFF
        assert b"\x01\x01\xff" in req

    def test_deterministic(self) -> None:
        digest = b"\xaa" * 32
        assert build_timestamp_request(digest) == build_timestamp_request(digest)


# ---------------------------------------------------------------------------
# request_timestamp (async)
# ---------------------------------------------------------------------------


class TestRequestTimestampAsync:
    @pytest.mark.asyncio
    async def test_success_response(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\x30\x82\x01\x00"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            token = await request_timestamp(b"\x00" * 32)

        assert token.response_status == "ok"
        assert token.verified is True
        assert token.token_bytes == b"\x30\x82\x01\x00"

    @pytest.mark.asyncio
    async def test_error_response(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            token = await request_timestamp(b"\x00" * 32)

        assert token.response_status == "error:500"
        assert token.verified is False

    @pytest.mark.asyncio
    async def test_network_exception(self) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("timeout"))

        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            token = await request_timestamp(b"\x00" * 32)

        assert "ConnectionError" in token.response_status
        assert token.verified is False

    @pytest.mark.asyncio
    async def test_custom_tsa_url(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"token"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        custom_url = "http://custom-tsa.example.com"
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            token = await request_timestamp(b"\x00" * 32, tsa_url=custom_url)

        assert token.tsa_url == custom_url
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == custom_url

    @pytest.mark.asyncio
    async def test_hash_hex_stored(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"token"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        tree_hash = b"\xab\xcd" * 16
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            token = await request_timestamp(tree_hash)

        assert token.root_hash_hex == tree_hash.hex()


# ---------------------------------------------------------------------------
# request_timestamp_sync
# ---------------------------------------------------------------------------


class TestRequestTimestampSync:
    def test_success_response(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\x30\x82\x01\x00"

        with patch("httpx.post", return_value=mock_response):
            token = request_timestamp_sync(b"\x00" * 32)

        assert token.response_status == "ok"
        assert token.verified is True
        assert token.token_bytes == b"\x30\x82\x01\x00"

    def test_error_response(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("httpx.post", return_value=mock_response):
            token = request_timestamp_sync(b"\x00" * 32)

        assert token.response_status == "error:403"
        assert token.verified is False

    def test_network_exception(self) -> None:
        with patch("httpx.post", side_effect=ConnectionError("refused")):
            token = request_timestamp_sync(b"\x00" * 32)

        assert "ConnectionError" in token.response_status

    def test_hash_hex_stored(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"token"

        tree_hash = b"\xff" * 32
        with patch("httpx.post", return_value=mock_response):
            token = request_timestamp_sync(tree_hash)

        assert token.root_hash_hex == tree_hash.hex()


# ---------------------------------------------------------------------------
# AnchoringHistory
# ---------------------------------------------------------------------------


class TestAnchoringHistory:
    def test_empty_history(self) -> None:
        h = AnchoringHistory()
        assert h.latest is None
        assert h.total_anchored == 0

    def test_latest_returns_last(self) -> None:
        tokens = [
            TimestampToken(tree_size=i, root_hash_hex=f"hash{i}")
            for i in range(3)
        ]
        h = AnchoringHistory(anchors=tokens)
        assert h.latest is not None
        assert h.latest.tree_size == 2

    def test_total_anchored_counts_ok(self) -> None:
        tokens = [
            TimestampToken(tree_size=1, root_hash_hex="a", response_status="ok"),
            TimestampToken(tree_size=2, root_hash_hex="b", response_status="error:500"),
            TimestampToken(tree_size=3, root_hash_hex="c", response_status="ok"),
        ]
        h = AnchoringHistory(anchors=tokens)
        assert h.total_anchored == 2

    def test_to_dict(self) -> None:
        tokens = [
            TimestampToken(tree_size=1, root_hash_hex="a", response_status="ok"),
        ]
        h = AnchoringHistory(anchors=tokens)
        d = h.to_dict()
        assert d["total"] == 1
        assert d["total_anchored"] == 1
        assert d["latest"]["tree_size"] == 1
        assert len(d["anchors"]) == 1

    def test_to_dict_empty(self) -> None:
        h = AnchoringHistory()
        d = h.to_dict()
        assert d["total"] == 0
        assert d["latest"] is None
        assert d["anchors"] == []

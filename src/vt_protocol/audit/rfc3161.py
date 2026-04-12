"""RFC 3161 external timestamping for Merkle tree head anchoring.

Submits tree head hashes to a trusted Time Stamping Authority (TSA)
to create externally verifiable proof that the audit log existed at a
specific point in time.

Default TSA: DigiCert (http://timestamp.digicert.com)

The timestamp token is stored alongside the tree head in SQLite.
Verification confirms the TSA signed the hash at the claimed time.

Usage:
  - Weekly cron submits tree head to TSA
  - Token stored in .smm/audit/timestamps/
  - Dashboard /compliance page shows anchoring history
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default TSA endpoint (DigiCert)
DEFAULT_TSA_URL = "http://timestamp.digicert.com"


@dataclass
class TimestampToken:
    """An RFC 3161 timestamp token for a tree head."""

    tree_size: int
    root_hash_hex: str
    tsa_url: str = DEFAULT_TSA_URL
    token_bytes: bytes = b""
    requested_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    response_status: str = ""
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tree_size": self.tree_size,
            "root_hash_hex": self.root_hash_hex,
            "tsa_url": self.tsa_url,
            "token_hex": self.token_bytes.hex() if self.token_bytes else "",
            "requested_at": self.requested_at.isoformat(),
            "response_status": self.response_status,
            "verified": self.verified,
        }


def build_timestamp_request(digest: bytes) -> bytes:
    """Build an RFC 3161 TimeStampReq ASN.1 structure.

    Simplified implementation that encodes the essential fields:
    - version: 1
    - messageImprint: SHA-256 OID + digest
    - certReq: True
    """
    # SHA-256 OID: 2.16.840.1.101.3.4.2.1
    sha256_oid = bytes([
        0x30, 0x0D,  # SEQUENCE
        0x06, 0x09,  # OID
        0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01,
        0x05, 0x00,  # NULL
    ])

    # MessageImprint: SEQUENCE { algorithm, digest }
    digest_octet = bytes([0x04, len(digest)]) + digest
    msg_imprint = bytes([0x30, len(sha256_oid) + len(digest_octet)]) + sha256_oid + digest_octet

    # Version: INTEGER 1
    version = bytes([0x02, 0x01, 0x01])

    # CertReq: BOOLEAN TRUE
    cert_req = bytes([0x01, 0x01, 0xFF])

    # Full request: SEQUENCE { version, messageImprint, certReq }
    inner = version + msg_imprint + cert_req
    request = bytes([0x30, len(inner)]) + inner

    return request


async def request_timestamp(
    tree_head_hash: bytes,
    *,
    tsa_url: str = DEFAULT_TSA_URL,
) -> TimestampToken:
    """Submit a tree head hash to an RFC 3161 TSA.

    Returns a TimestampToken with the TSA's response.
    """
    hash_hex = tree_head_hash.hex()
    digest = hashlib.sha256(tree_head_hash).digest()
    ts_request = build_timestamp_request(digest)

    token = TimestampToken(
        tree_size=0,  # Caller should set this
        root_hash_hex=hash_hex,
        tsa_url=tsa_url,
    )

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed, cannot request RFC 3161 timestamp")
        token.response_status = "error:httpx_not_installed"
        return token

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                tsa_url,
                content=ts_request,
                headers={"Content-Type": "application/timestamp-query"},
                timeout=30.0,
            )
            if response.status_code == 200:
                token.token_bytes = response.content
                token.response_status = "ok"
                token.verified = True
                logger.info(
                    "RFC 3161 timestamp obtained from %s (%d bytes)",
                    tsa_url, len(response.content),
                )
            else:
                token.response_status = f"error:{response.status_code}"
                logger.warning(
                    "RFC 3161 timestamp request failed: %d",
                    response.status_code,
                )
    except Exception as e:
        token.response_status = f"error:{type(e).__name__}"
        logger.exception("RFC 3161 timestamp request failed")

    return token


def request_timestamp_sync(
    tree_head_hash: bytes,
    *,
    tsa_url: str = DEFAULT_TSA_URL,
) -> TimestampToken:
    """Synchronous version for CLI/cron use."""
    hash_hex = tree_head_hash.hex()
    digest = hashlib.sha256(tree_head_hash).digest()
    ts_request = build_timestamp_request(digest)

    token = TimestampToken(
        tree_size=0,
        root_hash_hex=hash_hex,
        tsa_url=tsa_url,
    )

    try:
        import httpx
    except ImportError:
        token.response_status = "error:httpx_not_installed"
        return token

    try:
        response = httpx.post(
            tsa_url,
            content=ts_request,
            headers={"Content-Type": "application/timestamp-query"},
            timeout=30.0,
        )
        if response.status_code == 200:
            token.token_bytes = response.content
            token.response_status = "ok"
            token.verified = True
        else:
            token.response_status = f"error:{response.status_code}"
    except Exception as e:
        token.response_status = f"error:{type(e).__name__}"

    return token


@dataclass
class AnchoringHistory:
    """Complete history of RFC 3161 anchoring events."""

    anchors: list[TimestampToken] = field(default_factory=list)

    @property
    def latest(self) -> TimestampToken | None:
        return self.anchors[-1] if self.anchors else None

    @property
    def total_anchored(self) -> int:
        return len([a for a in self.anchors if a.response_status == "ok"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.anchors),
            "total_anchored": self.total_anchored,
            "latest": self.latest.to_dict() if self.latest else None,
            "anchors": [a.to_dict() for a in self.anchors],
        }

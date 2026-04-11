"""Secrets detection and redaction — hot-path scanner for agent prompts.

Detects:
  - AWS access keys (AKIA...)
  - Common API key patterns (sk-..., key-..., etc.)
  - Private keys (BEGIN RSA/EC/PRIVATE KEY)
  - Connection strings (postgres://, mongodb://, redis://)
  - .env variable assignments with secret-like values
  - JWT tokens (eyJ... three-segment base64)
  - Stripe, Twilio, SendGrid, GitHub tokens

Performance target: <1ms per prompt on typical inputs.
Redacts matches with [REDACTED:type] placeholder.
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SecretMatch:
    """A detected secret in text."""

    secret_type: str
    start: int
    end: int
    redacted: str  # e.g. "[REDACTED:aws_key]"
    original_preview: str = ""  # first 8 chars + "..."


@dataclass
class ScanResult:
    """Result of scanning text for secrets."""

    matches: list[SecretMatch] = field(default_factory=list)
    redacted_text: str = ""
    scan_time_ms: float = 0.0

    @property
    def has_secrets(self) -> bool:
        return len(self.matches) > 0

    @property
    def secret_types(self) -> list[str]:
        return list({m.secret_type for m in self.matches})


# ---------------------------------------------------------------------------
# Secret patterns — compiled regexes for hot-path performance
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # AWS Access Key ID (starts with AKIA, exactly 20 uppercase alphanumeric)
    ("aws_key", re.compile(r"(?<![A-Za-z0-9])(AKIA[0-9A-Z]{16})(?![A-Za-z0-9])")),

    # AWS Secret Access Key (40-char base64-ish after common prefixes)
    ("aws_secret", re.compile(
        r"(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY|secret_access_key)"
        r"[\s=:\"']+([A-Za-z0-9/+=]{40})"
    )),

    # Generic API keys: sk-..., key-..., api-..., token-... (32+ chars)
    ("api_key", re.compile(
        r"(?<![A-Za-z0-9_-])((?:sk|key|api|token)-[A-Za-z0-9_-]{32,})(?![A-Za-z0-9_-])"
    )),

    # OpenAI API key (sk-proj-... or sk-... pattern)
    ("openai_key", re.compile(r"(?<![A-Za-z0-9])(sk-(?:proj-)?[A-Za-z0-9]{20,})(?![A-Za-z0-9])")),

    # Anthropic API key
    ("anthropic_key", re.compile(r"(?<![A-Za-z0-9])(sk-ant-[A-Za-z0-9_-]{20,})(?![A-Za-z0-9])")),

    # Private keys (PEM format)
    ("private_key", re.compile(
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
        r"[\s\S]*?"
        r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
    )),

    # Connection strings
    ("connection_string", re.compile(
        r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)"
        r"://[^\s\"'`<>]{10,}"
    )),

    # JWT tokens (three base64url segments separated by dots)
    ("jwt_token", re.compile(
        r"(?<![A-Za-z0-9._-])(eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})(?![A-Za-z0-9._-])"
    )),

    # Stripe keys
    ("stripe_key", re.compile(
        r"(?<![A-Za-z0-9])((?:sk|pk|rk)_(?:test|live)_[A-Za-z0-9]{20,})(?![A-Za-z0-9])"
    )),

    # Twilio
    ("twilio_key", re.compile(r"(?<![A-Za-z0-9])(SK[0-9a-fA-F]{32})(?![A-Za-z0-9])")),

    # SendGrid
    ("sendgrid_key", re.compile(r"(?<![A-Za-z0-9.])(SG\.[A-Za-z0-9_-]{22,}\.[A-Za-z0-9_-]{22,})(?![A-Za-z0-9._-])")),

    # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    ("github_token", re.compile(
        r"(?<![A-Za-z0-9])(gh[pousr]_[A-Za-z0-9]{36,})(?![A-Za-z0-9])"
    )),

    # .env variable patterns with secret-like names
    ("env_secret", re.compile(
        r"(?:^|\n)\s*(?:export\s+)?"
        r"(?:(?:API_KEY|SECRET|PASSWORD|TOKEN|PRIVATE_KEY|ACCESS_KEY|AUTH_TOKEN|DB_PASS)"
        r"[A-Z_]*)"
        r"\s*=\s*[\"']?([^\s\"'#\n]{8,})[\"']?"
    )),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan(text: str) -> ScanResult:
    """Scan text for secrets. Returns matches and redacted text.

    Designed for hot-path use — target <1ms per prompt.
    """
    start = time.monotonic()
    matches: list[SecretMatch] = []

    for secret_type, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            # Use the capturing group if present, otherwise full match
            if m.lastindex:
                match_start = m.start(m.lastindex)
                match_end = m.end(m.lastindex)
                matched_text = m.group(m.lastindex)
            else:
                match_start = m.start()
                match_end = m.end()
                matched_text = m.group()

            preview = matched_text[:8] + "..." if len(matched_text) > 8 else matched_text
            matches.append(SecretMatch(
                secret_type=secret_type,
                start=match_start,
                end=match_end,
                redacted=f"[REDACTED:{secret_type}]",
                original_preview=preview,
            ))

    # Remove overlapping matches (keep longer/earlier)
    matches = _dedup_matches(matches)

    # Build redacted text
    redacted = _apply_redactions(text, matches)

    elapsed = (time.monotonic() - start) * 1000
    return ScanResult(matches=matches, redacted_text=redacted, scan_time_ms=elapsed)


def redact(text: str) -> str:
    """Convenience: scan and return only the redacted text."""
    return scan(text).redacted_text


def has_secrets(text: str) -> bool:
    """Quick check: does the text contain any detectable secrets?"""
    for _, pattern in _PATTERNS:
        if pattern.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dedup_matches(matches: list[SecretMatch]) -> list[SecretMatch]:
    """Remove overlapping matches, preferring longer matches."""
    if not matches:
        return matches

    # Sort by start position, then by length descending
    matches.sort(key=lambda m: (m.start, -(m.end - m.start)))

    deduped: list[SecretMatch] = [matches[0]]
    for m in matches[1:]:
        if m.start >= deduped[-1].end:
            deduped.append(m)
    return deduped


def _apply_redactions(text: str, matches: list[SecretMatch]) -> str:
    """Replace matched regions with redaction placeholders."""
    if not matches:
        return text

    parts: list[str] = []
    pos = 0
    for m in matches:
        parts.append(text[pos:m.start])
        parts.append(m.redacted)
        pos = m.end
    parts.append(text[pos:])
    return "".join(parts)

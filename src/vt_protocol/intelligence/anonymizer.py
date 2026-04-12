"""Cross-company architectural intelligence — anonymizer.

Strip identifying information from governance data before aggregation.
Hash company names, redact paths, normalize configs.

From SPEC Sprint 23: "Cross-company architectural intelligence — anonymizer."
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Patterns to redact
REDACTION_PATTERNS: dict[str, str] = {
    "email": r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
    "ip_address": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
    "url": r"https?://[^\s\"']+",
    "file_path": r"(?:/[a-zA-Z0-9._-]+){3,}",
    "api_key": r"\b(?:api[_-]?key|token|secret)[:\s]*['\"][a-zA-Z0-9_-]{20,}['\"]",
}


@dataclass
class AnonymizationConfig:
    """Configuration for anonymization."""

    hash_salt: str = "vt-protocol-anonymous"
    redact_emails: bool = True
    redact_ips: bool = True
    redact_urls: bool = True
    redact_paths: bool = True
    redact_keys: bool = True
    preserve_dimensions: bool = True
    preserve_statistics: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "hash_salt": "[REDACTED]",
            "redact_emails": self.redact_emails,
            "redact_ips": self.redact_ips,
            "redact_urls": self.redact_urls,
            "redact_paths": self.redact_paths,
            "redact_keys": self.redact_keys,
            "preserve_dimensions": self.preserve_dimensions,
            "preserve_statistics": self.preserve_statistics,
        }


@dataclass
class AnonymizationResult:
    """Result of anonymizing a governance dataset."""

    original_fields: int = 0
    redacted_fields: int = 0
    hashed_identifiers: int = 0
    anonymized_data: dict[str, Any] = field(default_factory=dict)

    @property
    def redaction_rate(self) -> float:
        return self.redacted_fields / max(self.original_fields, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_fields": self.original_fields,
            "redacted_fields": self.redacted_fields,
            "hashed_identifiers": self.hashed_identifiers,
            "redaction_rate": round(self.redaction_rate, 4),
        }


class Anonymizer:
    """Anonymize governance data for cross-company aggregation."""

    def __init__(self, config: AnonymizationConfig | None = None) -> None:
        self._config = config or AnonymizationConfig()

    @property
    def config(self) -> AnonymizationConfig:
        return self._config

    def hash_identifier(self, identifier: str) -> str:
        """One-way hash an identifier with salt."""
        salted = f"{self._config.hash_salt}:{identifier}"
        return hashlib.sha256(salted.encode()).hexdigest()[:16]

    def anonymize_company(self, company_name: str) -> str:
        """Hash a company name to an anonymous ID."""
        return f"org-{self.hash_identifier(company_name)}"

    def redact_text(self, text: str) -> str:
        """Redact sensitive patterns from text."""
        result = text
        if self._config.redact_emails:
            result = re.sub(REDACTION_PATTERNS["email"], "[EMAIL]", result)
        if self._config.redact_ips:
            result = re.sub(REDACTION_PATTERNS["ip_address"], "[IP]", result)
        if self._config.redact_urls:
            result = re.sub(REDACTION_PATTERNS["url"], "[URL]", result)
        if self._config.redact_paths:
            result = re.sub(REDACTION_PATTERNS["file_path"], "[PATH]", result)
        if self._config.redact_keys:
            result = re.sub(REDACTION_PATTERNS["api_key"], "[API_KEY]", result, flags=re.IGNORECASE)
        return result

    def anonymize_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Anonymize a single decision record."""
        anon = {}

        # Hash identifiers
        if "made_by" in decision:
            anon["made_by"] = self.hash_identifier(decision["made_by"])
        if "project" in decision:
            anon["project"] = self.anonymize_company(decision["project"])

        # Preserve dimensions and statistics
        if self._config.preserve_dimensions:
            anon["dimensions"] = decision.get("dimensions", [])

        if self._config.preserve_statistics:
            anon["confidence"] = decision.get("confidence", 0.0)
            anon["decision_type"] = decision.get("decision_type", "")
            anon["source_type"] = decision.get("source_type", "")

        # Redact text fields
        for text_field in ("title", "content", "rationale"):
            if text_field in decision:
                anon[text_field] = self.redact_text(decision[text_field])

        return anon

    def anonymize_governance_data(
        self, data: dict[str, Any], *, company_name: str = "",
    ) -> AnonymizationResult:
        """Anonymize a full governance dataset."""
        result = AnonymizationResult()
        anon_data: dict[str, Any] = {}

        # Hash company
        if company_name:
            anon_data["company_id"] = self.anonymize_company(company_name)
            result.hashed_identifiers += 1

        # Process decisions
        decisions = data.get("decisions", [])
        anon_decisions = []
        for d in decisions:
            result.original_fields += len(d)
            anon_d = self.anonymize_decision(d)
            result.redacted_fields += sum(1 for k in d if k not in anon_d)
            result.hashed_identifiers += sum(
                1 for k in ("made_by", "project") if k in d
            )
            anon_decisions.append(anon_d)
        anon_data["decisions"] = anon_decisions

        # Process config (preserve structure, redact secrets)
        config = data.get("governance_config", {})
        if config:
            anon_config = {}
            for key, value in config.items():
                result.original_fields += 1
                if isinstance(value, str):
                    anon_config[key] = self.redact_text(value)
                else:
                    anon_config[key] = value
            anon_data["governance_config"] = anon_config

        # Preserve aggregate stats
        if self._config.preserve_statistics:
            anon_data["statistics"] = {
                "decision_count": len(decisions),
                "dimension_distribution": _count_dimensions(decisions),
            }

        result.anonymized_data = anon_data
        return result


def _count_dimensions(decisions: list[dict[str, Any]]) -> dict[str, int]:
    """Count dimension occurrences across decisions."""
    counts: dict[str, int] = {}
    for d in decisions:
        for dim in d.get("dimensions", []):
            counts[dim] = counts.get(dim, 0) + 1
    return counts

"""Tests for cross-company intelligence — anonymizer."""

from __future__ import annotations

import pytest

from vt_protocol.intelligence.anonymizer import (
    REDACTION_PATTERNS,
    AnonymizationConfig,
    AnonymizationResult,
    Anonymizer,
)


# ---------------------------------------------------------------------------
# AnonymizationConfig
# ---------------------------------------------------------------------------


class TestAnonymizationConfig:
    def test_defaults(self):
        c = AnonymizationConfig()
        assert c.redact_emails is True
        assert c.redact_ips is True
        assert c.preserve_dimensions is True

    def test_to_dict_hides_salt(self):
        c = AnonymizationConfig(hash_salt="my-secret-salt")
        d = c.to_dict()
        assert d["hash_salt"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Anonymizer — hashing
# ---------------------------------------------------------------------------


class TestAnonymizerHashing:
    def test_hash_identifier_deterministic(self):
        a = Anonymizer()
        h1 = a.hash_identifier("test")
        h2 = a.hash_identifier("test")
        assert h1 == h2

    def test_hash_identifier_different_inputs(self):
        a = Anonymizer()
        h1 = a.hash_identifier("alice")
        h2 = a.hash_identifier("bob")
        assert h1 != h2

    def test_hash_identifier_length(self):
        a = Anonymizer()
        h = a.hash_identifier("test")
        assert len(h) == 16

    def test_anonymize_company(self):
        a = Anonymizer()
        anon = a.anonymize_company("Acme Corp")
        assert anon.startswith("org-")
        assert len(anon) == 20  # "org-" + 16 hex

    def test_anonymize_company_deterministic(self):
        a = Anonymizer()
        assert a.anonymize_company("X") == a.anonymize_company("X")


# ---------------------------------------------------------------------------
# Anonymizer — text redaction
# ---------------------------------------------------------------------------


class TestAnonymizerRedaction:
    def test_redact_email(self):
        a = Anonymizer()
        result = a.redact_text("Contact user@example.com for info")
        assert "[EMAIL]" in result
        assert "user@example.com" not in result

    def test_redact_ip(self):
        a = Anonymizer()
        result = a.redact_text("Server at 192.168.1.100")
        assert "[IP]" in result
        assert "192.168.1.100" not in result

    def test_redact_url(self):
        a = Anonymizer()
        result = a.redact_text("See https://internal.corp.com/docs")
        assert "[URL]" in result

    def test_redact_path(self):
        a = Anonymizer()
        result = a.redact_text("File at /home/user/project/secret.py")
        assert "[PATH]" in result

    def test_redact_disabled(self):
        config = AnonymizationConfig(redact_emails=False)
        a = Anonymizer(config)
        result = a.redact_text("Contact user@example.com")
        assert "user@example.com" in result

    def test_clean_text_unchanged(self):
        a = Anonymizer()
        text = "Use PostgreSQL for the database layer."
        assert a.redact_text(text) == text


# ---------------------------------------------------------------------------
# Anonymizer — decision anonymization
# ---------------------------------------------------------------------------


class TestAnonymizeDecision:
    def test_anonymize_decision(self):
        a = Anonymizer()
        decision = {
            "made_by": "alice",
            "project": "Acme Corp",
            "dimensions": ["database", "auth"],
            "confidence": 0.85,
            "title": "Use PostgreSQL",
            "decision_type": "technical",
            "source_type": "agent",
        }
        anon = a.anonymize_decision(decision)
        assert anon["made_by"] != "alice"
        assert anon["project"].startswith("org-")
        assert anon["dimensions"] == ["database", "auth"]
        assert anon["confidence"] == 0.85

    def test_anonymize_redacts_title(self):
        a = Anonymizer()
        decision = {
            "title": "Deploy to https://prod.example.com",
        }
        anon = a.anonymize_decision(decision)
        assert "[URL]" in anon["title"]


# ---------------------------------------------------------------------------
# Anonymizer — full governance data
# ---------------------------------------------------------------------------


class TestAnonymizeGovernanceData:
    def test_anonymize_full_dataset(self):
        a = Anonymizer()
        data = {
            "decisions": [
                {
                    "made_by": "agent-1",
                    "project": "MyProject",
                    "dimensions": ["database"],
                    "confidence": 0.8,
                    "title": "Use Redis for caching",
                    "decision_type": "technical",
                    "source_type": "agent",
                },
            ],
            "governance_config": {
                "extends": ["@vt/recommended"],
                "api_url": "https://internal.corp.com/api",
            },
        }
        result = a.anonymize_governance_data(data, company_name="Acme")
        assert result.hashed_identifiers > 0
        assert "company_id" in result.anonymized_data
        assert result.anonymized_data["company_id"].startswith("org-")

    def test_preserves_statistics(self):
        a = Anonymizer()
        data = {
            "decisions": [
                {"dimensions": ["database"], "confidence": 0.8, "made_by": "a", "project": "p"},
                {"dimensions": ["auth"], "confidence": 0.9, "made_by": "b", "project": "p"},
            ],
        }
        result = a.anonymize_governance_data(data)
        stats = result.anonymized_data.get("statistics", {})
        assert stats["decision_count"] == 2

    def test_redaction_rate(self):
        a = Anonymizer()
        data = {"decisions": [{"made_by": "x", "project": "y", "confidence": 0.5}]}
        result = a.anonymize_governance_data(data)
        assert result.redaction_rate >= 0.0

    def test_empty_data(self):
        a = Anonymizer()
        result = a.anonymize_governance_data({})
        assert result.original_fields == 0

"""Tests for secrets detection and redaction."""

from __future__ import annotations

import time

import pytest

from vt_protocol.observation.secrets import (
    ScanResult,
    SecretMatch,
    has_secrets,
    redact,
    scan,
)


class TestAWSKeys:
    def test_detects_access_key(self) -> None:
        result = scan("my key is AKIAIOSFODNN7EXAMPLE ok")
        assert result.has_secrets
        assert any(m.secret_type == "aws_key" for m in result.matches)

    def test_detects_secret_key(self) -> None:
        result = scan("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        assert result.has_secrets
        assert any(m.secret_type == "aws_secret" for m in result.matches)

    def test_redacts_access_key(self) -> None:
        text = "key: AKIAIOSFODNN7EXAMPLE"
        result = scan(text)
        assert "AKIA" not in result.redacted_text
        assert "[REDACTED:aws_key]" in result.redacted_text


class TestAPIKeys:
    def test_detects_generic_api_key(self) -> None:
        result = scan("token: sk-1234567890abcdef1234567890abcdef12")
        assert result.has_secrets

    def test_detects_openai_key(self) -> None:
        result = scan("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwx")
        assert result.has_secrets

    def test_detects_anthropic_key(self) -> None:
        result = scan("key is sk-ant-abcdefghijklmnopqrstuvwxyz")
        assert result.has_secrets
        assert any(m.secret_type == "anthropic_key" for m in result.matches)


class TestPrivateKeys:
    def test_detects_rsa_key(self) -> None:
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQ...\n-----END RSA PRIVATE KEY-----"
        result = scan(pem)
        assert result.has_secrets
        assert any(m.secret_type == "private_key" for m in result.matches)

    def test_detects_ec_key(self) -> None:
        pem = "-----BEGIN EC PRIVATE KEY-----\nMHQCAQEE...\n-----END EC PRIVATE KEY-----"
        result = scan(pem)
        assert result.has_secrets

    def test_redacts_private_key(self) -> None:
        pem = "before\n-----BEGIN PRIVATE KEY-----\ndata\n-----END PRIVATE KEY-----\nafter"
        result = scan(pem)
        assert "BEGIN PRIVATE KEY" not in result.redacted_text
        assert "[REDACTED:private_key]" in result.redacted_text


class TestConnectionStrings:
    def test_postgres(self) -> None:
        result = scan("DATABASE_URL=postgresql://user:pass@localhost:5432/db")
        assert result.has_secrets
        assert any(m.secret_type == "connection_string" for m in result.matches)

    def test_mongodb(self) -> None:
        result = scan("MONGO=mongodb+srv://user:pass@cluster0.example.net/db")
        assert result.has_secrets

    def test_redis(self) -> None:
        result = scan("REDIS_URL=redis://default:password@redis-host:6379")
        assert result.has_secrets


class TestJWT:
    def test_detects_jwt(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = scan(f"token: {jwt}")
        assert result.has_secrets
        assert any(m.secret_type == "jwt_token" for m in result.matches)


class TestVendorKeys:
    def test_stripe_key(self) -> None:
        result = scan("STRIPE_KEY=sk_test_4eC39HqLyjWDarjtT1zdp7dc")
        assert result.has_secrets
        assert any(m.secret_type == "stripe_key" for m in result.matches)

    def test_github_token(self) -> None:
        result = scan("GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz1234")
        assert result.has_secrets
        assert any(m.secret_type == "github_token" for m in result.matches)

    def test_sendgrid_key(self) -> None:
        result = scan("SG.abcdefghijklmnopqrstuv.wxyz1234567890abcdefghijk")
        assert result.has_secrets
        assert any(m.secret_type == "sendgrid_key" for m in result.matches)


class TestEnvSecrets:
    def test_env_pattern(self) -> None:
        result = scan("API_KEY=mysecretvalue12345678")
        assert result.has_secrets

    def test_export_pattern(self) -> None:
        result = scan("export SECRET_KEY=averylongsecretvalue123")
        assert result.has_secrets


class TestNoSecrets:
    def test_plain_text(self) -> None:
        result = scan("This is just normal text with no secrets.")
        assert not result.has_secrets
        assert result.redacted_text == "This is just normal text with no secrets."

    def test_empty_string(self) -> None:
        result = scan("")
        assert not result.has_secrets

    def test_code_without_secrets(self) -> None:
        code = "def hello():\n    return 'world'\n"
        result = scan(code)
        assert not result.has_secrets


class TestRedactFunction:
    def test_convenience_redact(self) -> None:
        text = "key: AKIAIOSFODNN7EXAMPLE"
        result = redact(text)
        assert "AKIA" not in result
        assert "[REDACTED:" in result

    def test_no_secrets_returns_original(self) -> None:
        text = "safe text"
        assert redact(text) == text


class TestHasSecrets:
    def test_true_for_secrets(self) -> None:
        assert has_secrets("AKIAIOSFODNN7EXAMPLE")

    def test_false_for_clean(self) -> None:
        assert not has_secrets("clean text here")


class TestPerformance:
    def test_under_1ms_for_typical_prompt(self) -> None:
        """Scan should complete in <1ms for typical LLM prompts."""
        prompt = "Please analyze this code:\n" + "x = 42\n" * 100
        result = scan(prompt)
        assert result.scan_time_ms < 5.0  # Allow some margin

    def test_reasonable_for_large_input(self) -> None:
        """Even large inputs should be fast."""
        text = "Some text without secrets. " * 1000
        result = scan(text)
        assert result.scan_time_ms < 50.0


class TestMultipleSecrets:
    def test_multiple_types(self) -> None:
        text = (
            "AWS: AKIAIOSFODNN7EXAMPLE\n"
            "DB: postgresql://user:pass@localhost:5432/db\n"
            "Token: ghp_1234567890abcdefghijklmnopqrstuvwxyz1234\n"
        )
        result = scan(text)
        assert len(result.matches) >= 3
        types = result.secret_types
        assert "aws_key" in types
        assert "connection_string" in types
        assert "github_token" in types

    def test_all_redacted(self) -> None:
        text = "AKIAIOSFODNN7EXAMPLE and postgresql://user:pass@host:5432/db"
        result = scan(text)
        assert "AKIA" not in result.redacted_text
        assert "postgresql://" not in result.redacted_text

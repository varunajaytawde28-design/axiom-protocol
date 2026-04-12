"""Tests for governance rule marketplace — publisher."""

from __future__ import annotations

import pytest

from vt_protocol.marketplace.publisher import (
    PackageBundle,
    PackageMetadata,
    ValidationResult,
    build_package,
    generate_readme,
    validate_governance_config,
    validate_version,
)


# ---------------------------------------------------------------------------
# PackageMetadata
# ---------------------------------------------------------------------------


class TestPackageMetadata:
    def test_defaults(self):
        m = PackageMetadata()
        assert m.version == "0.1.0"
        assert m.extends == []

    def test_to_dict(self):
        m = PackageMetadata(name="my-pkg", version="1.0.0")
        d = m.to_dict()
        assert d["name"] == "my-pkg"
        assert d["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# PackageBundle
# ---------------------------------------------------------------------------


class TestPackageBundle:
    def test_to_dict(self):
        m = PackageMetadata(name="test")
        b = PackageBundle(metadata=m, readme="# Test", checksum="abc123")
        d = b.to_dict()
        assert d["metadata"]["name"] == "test"
        assert d["readme"] == "# Test"
        assert d["checksum"] == "abc123"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidateGovernanceConfig:
    def test_valid_config(self):
        result = validate_governance_config({"rules": {"freeze_on_adopt": True}})
        assert result.valid is True

    def test_empty_config(self):
        result = validate_governance_config({})
        assert result.valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_no_rules_or_extends_warning(self):
        result = validate_governance_config({"agents": {"claude": True}})
        assert result.valid is True
        assert len(result.warnings) > 0

    def test_config_with_extends(self):
        result = validate_governance_config({"extends": ["@vt/recommended"]})
        assert result.valid is True
        assert len(result.warnings) == 0


class TestValidateVersion:
    def test_valid_versions(self):
        assert validate_version("1.0.0") is True
        assert validate_version("0.1.0") is True
        assert validate_version("12.34.56") is True

    def test_invalid_versions(self):
        assert validate_version("1.0") is False
        assert validate_version("v1.0.0") is False
        assert validate_version("1.0.0-beta") is False
        assert validate_version("") is False


# ---------------------------------------------------------------------------
# generate_readme
# ---------------------------------------------------------------------------


class TestGenerateReadme:
    def test_basic_readme(self):
        m = PackageMetadata(name="my-pkg", description="A test package")
        readme = generate_readme(m)
        assert "# my-pkg" in readme
        assert "smm install my-pkg" in readme
        assert "A test package" in readme

    def test_readme_with_dimensions(self):
        m = PackageMetadata(
            name="my-pkg",
            dimensions=["database", "auth"],
        )
        readme = generate_readme(m)
        assert "## Dimensions" in readme
        assert "- database" in readme
        assert "- auth" in readme

    def test_readme_with_frameworks(self):
        m = PackageMetadata(
            name="my-pkg",
            frameworks=["django", "flask"],
        )
        readme = generate_readme(m)
        assert "## Frameworks" in readme
        assert "- django" in readme

    def test_readme_default_description(self):
        m = PackageMetadata(name="my-pkg")
        readme = generate_readme(m)
        assert "VT Protocol" in readme


# ---------------------------------------------------------------------------
# build_package
# ---------------------------------------------------------------------------


class TestBuildPackage:
    def test_basic_build(self):
        config = {"rules": {"freeze_on_adopt": True}}
        bundle = build_package(config, name="my-pkg")
        assert bundle.metadata.name == "my-pkg"
        assert bundle.metadata.version == "0.1.0"
        assert bundle.checksum != ""
        assert bundle.readme != ""

    def test_build_with_options(self):
        config = {"rules": {}, "extends": ["@vt/security"]}
        bundle = build_package(
            config,
            name="sec-pkg",
            version="2.0.0",
            description="Security package",
            author="test-author",
            dimensions=["security", "auth"],
            languages=["python"],
            frameworks=["django"],
        )
        assert bundle.metadata.version == "2.0.0"
        assert bundle.metadata.author == "test-author"
        assert bundle.metadata.dimensions == ["security", "auth"]
        assert bundle.metadata.extends == ["@vt/security"]

    def test_build_checksum_deterministic(self):
        config = {"rules": {"freeze_on_adopt": True}}
        b1 = build_package(config, name="my-pkg")
        b2 = build_package(config, name="my-pkg")
        assert b1.checksum == b2.checksum

    def test_build_different_configs_different_checksums(self):
        c1 = {"rules": {"freeze_on_adopt": True}}
        c2 = {"rules": {"freeze_on_adopt": False}}
        b1 = build_package(c1, name="my-pkg")
        b2 = build_package(c2, name="my-pkg")
        assert b1.checksum != b2.checksum

    def test_build_with_templates(self):
        config = {"rules": {}}
        bundle = build_package(
            config,
            name="my-pkg",
            decision_templates=[{"title": "Use REST API"}],
            custom_dimensions=[{"name": "scalability"}],
        )
        assert len(bundle.decision_templates) == 1
        assert len(bundle.custom_dimensions) == 1

"""Governance rule marketplace — package publisher.

Package a local governance config for publishing to the registry.

From SPEC Sprint 22: "Governance rule marketplace — publisher.py."
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PackageMetadata:
    """Metadata for a publishable governance package."""

    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    dimensions: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "dimensions": self.dimensions,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "extends": self.extends,
        }


@dataclass
class PackageBundle:
    """A bundled package ready for publishing."""

    metadata: PackageMetadata
    governance_config: dict[str, Any] = field(default_factory=dict)
    decision_templates: list[dict[str, Any]] = field(default_factory=list)
    custom_dimensions: list[dict[str, Any]] = field(default_factory=list)
    readme: str = ""
    checksum: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "governance_config": self.governance_config,
            "decision_templates": self.decision_templates,
            "custom_dimensions": self.custom_dimensions,
            "readme": self.readme,
            "checksum": self.checksum,
        }


@dataclass
class ValidationResult:
    """Result of validating a governance config for publishing."""

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_governance_config(config: dict[str, Any]) -> ValidationResult:
    """Validate a governance config for publishing."""
    result = ValidationResult()

    if not config:
        result.valid = False
        result.errors.append("Empty governance config")
        return result

    if "extends" not in config and "rules" not in config:
        result.warnings.append("Config has no 'extends' or 'rules' — may be empty")

    return result


def validate_version(version: str) -> bool:
    """Validate semantic versioning format."""
    return bool(re.match(r"^\d+\.\d+\.\d+$", version))


def generate_readme(metadata: PackageMetadata) -> str:
    """Generate a README from package metadata."""
    lines = [
        f"# {metadata.name}",
        "",
        metadata.description or "A governance rule package for VT Protocol.",
        "",
        "## Installation",
        "",
        f"```bash",
        f"smm install {metadata.name}",
        f"```",
        "",
    ]
    if metadata.dimensions:
        lines.extend([
            "## Dimensions",
            "",
            *[f"- {d}" for d in metadata.dimensions],
            "",
        ])
    if metadata.frameworks:
        lines.extend([
            "## Frameworks",
            "",
            *[f"- {f}" for f in metadata.frameworks],
            "",
        ])
    return "\n".join(lines)


def build_package(
    config: dict[str, Any],
    *,
    name: str,
    version: str = "0.1.0",
    description: str = "",
    author: str = "",
    dimensions: list[str] | None = None,
    languages: list[str] | None = None,
    frameworks: list[str] | None = None,
    decision_templates: list[dict[str, Any]] | None = None,
    custom_dimensions: list[dict[str, Any]] | None = None,
) -> PackageBundle:
    """Build a publishable package from a governance config."""
    metadata = PackageMetadata(
        name=name,
        version=version,
        description=description,
        author=author,
        dimensions=dimensions or [],
        languages=languages or [],
        frameworks=frameworks or [],
        extends=config.get("extends", []),
    )

    readme = generate_readme(metadata)
    content = json.dumps({"metadata": metadata.to_dict(), "config": config}, sort_keys=True)
    checksum = hashlib.sha256(content.encode()).hexdigest()

    return PackageBundle(
        metadata=metadata,
        governance_config=config,
        decision_templates=decision_templates or [],
        custom_dimensions=custom_dimensions or [],
        readme=readme,
        checksum=checksum,
    )

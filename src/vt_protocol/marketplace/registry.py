"""Governance rule marketplace — registry client.

Client for a public registry API. Search, download, and rate
governance packages.

From SPEC Sprint 22: "Governance rule marketplace — registry.py."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PackageVersion:
    """A specific version of a governance package."""

    version: str = "0.1.0"
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checksum: str = ""


@dataclass
class PackageInfo:
    """Metadata for a governance package in the registry."""

    name: str = ""
    description: str = ""
    publisher: str = ""
    verified: bool = False
    dimensions: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    latest_version: str = "0.1.0"
    versions: list[PackageVersion] = field(default_factory=list)
    downloads: int = 0
    stars: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "publisher": self.publisher,
            "verified": self.verified,
            "dimensions": self.dimensions,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "latest_version": self.latest_version,
            "downloads": self.downloads,
            "stars": self.stars,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class SearchResult:
    """Result of a registry search."""

    packages: list[PackageInfo] = field(default_factory=list)
    total: int = 0
    query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "total": self.total,
            "packages": [p.to_dict() for p in self.packages],
        }


class RegistryClient:
    """Client for the governance package registry.

    Designed for a public API; uses an in-memory mock by default for testing.
    """

    def __init__(self, base_url: str = "https://registry.vtprotocol.dev/api") -> None:
        self._base_url = base_url
        self._packages: dict[str, PackageInfo] = {}

    @property
    def package_count(self) -> int:
        return len(self._packages)

    def register_package(self, package: PackageInfo) -> None:
        """Register a package (for mock/testing)."""
        self._packages[package.name] = package

    def search(
        self, query: str = "", *, dimension: str = "", language: str = "", framework: str = "",
    ) -> SearchResult:
        """Search for governance packages."""
        results: list[PackageInfo] = []
        q = query.lower()

        for pkg in self._packages.values():
            if q and q not in pkg.name.lower() and q not in pkg.description.lower():
                continue
            if dimension and dimension not in pkg.dimensions:
                continue
            if language and language not in pkg.languages:
                continue
            if framework and framework not in pkg.frameworks:
                continue
            results.append(pkg)

        return SearchResult(packages=results, total=len(results), query=query)

    def get_package(self, name: str) -> PackageInfo | None:
        """Get package details by name."""
        return self._packages.get(name)

    def download(self, name: str, version: str | None = None) -> dict[str, Any] | None:
        """Download package content. Returns governance config dict."""
        pkg = self._packages.get(name)
        if pkg is None:
            return None
        pkg.downloads += 1
        return {"name": pkg.name, "version": version or pkg.latest_version, "config": {}}

    def star(self, name: str) -> bool:
        """Star a package."""
        pkg = self._packages.get(name)
        if pkg is None:
            return False
        pkg.stars += 1
        return True

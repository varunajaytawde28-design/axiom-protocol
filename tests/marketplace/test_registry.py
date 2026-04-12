"""Tests for governance rule marketplace — registry client."""

from __future__ import annotations

import pytest

from vt_protocol.marketplace.registry import (
    PackageInfo,
    PackageVersion,
    RegistryClient,
    SearchResult,
)


def _make_package(name: str = "test-pkg", **kwargs) -> PackageInfo:
    return PackageInfo(
        name=name,
        description=kwargs.get("description", f"A {name} package"),
        publisher=kwargs.get("publisher", "test-org"),
        verified=kwargs.get("verified", False),
        dimensions=kwargs.get("dimensions", ["database"]),
        languages=kwargs.get("languages", ["python"]),
        frameworks=kwargs.get("frameworks", ["django"]),
        latest_version=kwargs.get("latest_version", "1.0.0"),
    )


# ---------------------------------------------------------------------------
# PackageInfo
# ---------------------------------------------------------------------------


class TestPackageInfo:
    def test_defaults(self):
        p = PackageInfo()
        assert p.name == ""
        assert p.downloads == 0
        assert p.stars == 0

    def test_to_dict(self):
        p = _make_package("my-pkg")
        d = p.to_dict()
        assert d["name"] == "my-pkg"
        assert "created_at" in d


# ---------------------------------------------------------------------------
# RegistryClient — basic operations
# ---------------------------------------------------------------------------


class TestRegistryClient:
    def test_empty_registry(self):
        client = RegistryClient()
        assert client.package_count == 0

    def test_register_package(self):
        client = RegistryClient()
        pkg = _make_package("my-pkg")
        client.register_package(pkg)
        assert client.package_count == 1

    def test_get_package(self):
        client = RegistryClient()
        client.register_package(_make_package("my-pkg"))
        pkg = client.get_package("my-pkg")
        assert pkg is not None
        assert pkg.name == "my-pkg"

    def test_get_package_not_found(self):
        client = RegistryClient()
        assert client.get_package("nonexistent") is None


# ---------------------------------------------------------------------------
# RegistryClient — search
# ---------------------------------------------------------------------------


class TestRegistrySearch:
    def _populated_client(self) -> RegistryClient:
        client = RegistryClient()
        client.register_package(_make_package(
            "django-security",
            dimensions=["security", "auth"],
            languages=["python"],
            frameworks=["django"],
        ))
        client.register_package(_make_package(
            "react-state",
            dimensions=["state-management"],
            languages=["typescript"],
            frameworks=["react"],
        ))
        client.register_package(_make_package(
            "postgres-perf",
            dimensions=["database"],
            languages=["python"],
            frameworks=["sqlalchemy"],
        ))
        return client

    def test_search_by_query(self):
        client = self._populated_client()
        result = client.search("django")
        assert result.total == 1
        assert result.packages[0].name == "django-security"

    def test_search_by_dimension(self):
        client = self._populated_client()
        result = client.search(dimension="security")
        assert result.total == 1

    def test_search_by_language(self):
        client = self._populated_client()
        result = client.search(language="typescript")
        assert result.total == 1
        assert result.packages[0].name == "react-state"

    def test_search_by_framework(self):
        client = self._populated_client()
        result = client.search(framework="react")
        assert result.total == 1

    def test_search_empty_query(self):
        client = self._populated_client()
        result = client.search()
        assert result.total == 3

    def test_search_no_results(self):
        client = self._populated_client()
        result = client.search("nonexistent")
        assert result.total == 0

    def test_search_case_insensitive(self):
        client = self._populated_client()
        result = client.search("DJANGO")
        assert result.total == 1


# ---------------------------------------------------------------------------
# RegistryClient — download & star
# ---------------------------------------------------------------------------


class TestRegistryDownloadStar:
    def test_download(self):
        client = RegistryClient()
        client.register_package(_make_package("my-pkg"))
        content = client.download("my-pkg")
        assert content is not None
        assert content["name"] == "my-pkg"

    def test_download_with_version(self):
        client = RegistryClient()
        client.register_package(_make_package("my-pkg"))
        content = client.download("my-pkg", version="2.0.0")
        assert content["version"] == "2.0.0"

    def test_download_increments_count(self):
        client = RegistryClient()
        client.register_package(_make_package("my-pkg"))
        client.download("my-pkg")
        client.download("my-pkg")
        pkg = client.get_package("my-pkg")
        assert pkg.downloads == 2

    def test_download_not_found(self):
        client = RegistryClient()
        assert client.download("nonexistent") is None

    def test_star(self):
        client = RegistryClient()
        client.register_package(_make_package("my-pkg"))
        assert client.star("my-pkg") is True
        pkg = client.get_package("my-pkg")
        assert pkg.stars == 1

    def test_star_not_found(self):
        client = RegistryClient()
        assert client.star("nonexistent") is False

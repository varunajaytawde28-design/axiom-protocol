"""Tests for governance rule marketplace — installer."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from vt_protocol.marketplace.installer import (
    InstallResult,
    LockEntry,
    LockFile,
    PackageInstaller,
)
from vt_protocol.marketplace.registry import PackageInfo, RegistryClient


# ---------------------------------------------------------------------------
# LockEntry
# ---------------------------------------------------------------------------


class TestLockEntry:
    def test_defaults(self):
        e = LockEntry()
        assert e.name == ""
        assert e.version == ""

    def test_to_dict(self):
        e = LockEntry(name="my-pkg", version="1.0.0", resolved_from="direct")
        d = e.to_dict()
        assert d["name"] == "my-pkg"
        assert d["resolved_from"] == "direct"


# ---------------------------------------------------------------------------
# LockFile
# ---------------------------------------------------------------------------


class TestLockFile:
    def test_empty(self):
        lf = LockFile()
        assert lf.package_count == 0
        assert not lf.has_package("anything")

    def test_has_package(self):
        lf = LockFile(entries=[LockEntry(name="my-pkg")])
        assert lf.has_package("my-pkg")
        assert not lf.has_package("other")

    def test_to_dict(self):
        lf = LockFile(entries=[LockEntry(name="a"), LockEntry(name="b")])
        d = lf.to_dict()
        assert d["lockfile_version"] == 1
        assert len(d["packages"]) == 2

    def test_save_and_load(self, tmp_path: Path):
        lf = LockFile(entries=[
            LockEntry(name="a", version="1.0.0"),
            LockEntry(name="b", version="2.0.0"),
        ])
        lock_path = tmp_path / ".smm" / "registry.lock"
        lf.save(lock_path)

        loaded = LockFile.load(lock_path)
        assert loaded.package_count == 2
        assert loaded.has_package("a")
        assert loaded.has_package("b")

    def test_load_nonexistent(self, tmp_path: Path):
        lock_path = tmp_path / "nonexistent.lock"
        loaded = LockFile.load(lock_path)
        assert loaded.package_count == 0


# ---------------------------------------------------------------------------
# InstallResult
# ---------------------------------------------------------------------------


class TestInstallResult:
    def test_success_when_no_failures(self):
        r = InstallResult(installed=["a", "b"])
        assert r.success

    def test_not_success_with_failures(self):
        r = InstallResult(failed=["a"])
        assert not r.success

    def test_to_dict(self):
        r = InstallResult(installed=["a"], already_installed=["b"])
        d = r.to_dict()
        assert d["success"] is True
        assert d["installed"] == ["a"]
        assert d["already_installed"] == ["b"]


# ---------------------------------------------------------------------------
# PackageInstaller
# ---------------------------------------------------------------------------


def _setup_installer(tmp_path: Path) -> tuple[PackageInstaller, RegistryClient]:
    client = RegistryClient()
    client.register_package(PackageInfo(
        name="base-pkg",
        description="Base package",
        latest_version="1.0.0",
    ))
    client.register_package(PackageInfo(
        name="ext-pkg",
        description="Extension package",
        latest_version="1.0.0",
    ))
    installer = PackageInstaller(registry=client, project_root=tmp_path)
    return installer, client


class TestPackageInstaller:
    def test_install_basic(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        result = installer.install("base-pkg")
        assert result.success
        assert "base-pkg" in result.installed
        assert result.lock_updated

    def test_install_already_installed(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        installer.install("base-pkg")
        result = installer.install("base-pkg")
        assert result.success
        assert "base-pkg" in result.already_installed
        assert "base-pkg" not in result.installed

    def test_install_not_found(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        result = installer.install("nonexistent")
        assert not result.success
        assert "nonexistent" in result.failed

    def test_install_creates_lock_file(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        installer.install("base-pkg")
        lock_path = tmp_path / ".smm" / "registry.lock"
        assert lock_path.exists()
        data = json.loads(lock_path.read_text())
        assert data["lockfile_version"] == 1

    def test_uninstall(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        installer.install("base-pkg")
        assert installer.uninstall("base-pkg") is True
        assert "base-pkg" not in installer.list_installed()

    def test_uninstall_not_installed(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        assert installer.uninstall("nonexistent") is False

    def test_list_installed(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        installer.install("base-pkg")
        installer.install("ext-pkg")
        installed = installer.list_installed()
        assert "base-pkg" in installed
        assert "ext-pkg" in installed

    def test_install_with_version(self, tmp_path: Path):
        installer, _ = _setup_installer(tmp_path)
        result = installer.install("base-pkg", version="2.0.0")
        assert result.success

    def test_lock_file_persistence(self, tmp_path: Path):
        installer, client = _setup_installer(tmp_path)
        installer.install("base-pkg")

        # Create a new installer pointing at the same root
        installer2 = PackageInstaller(registry=client, project_root=tmp_path)
        result = installer2.install("base-pkg")
        assert "base-pkg" in result.already_installed

"""Governance rule marketplace — package installer.

Install packages from the registry with dependency resolution and lock files.

From SPEC Sprint 22: "Governance rule marketplace — installer.py."
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vt_protocol.marketplace.registry import RegistryClient

logger = logging.getLogger(__name__)


@dataclass
class LockEntry:
    """A single entry in the lock file."""

    name: str = ""
    version: str = ""
    checksum: str = ""
    resolved_from: str = ""  # "direct" or parent package name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "checksum": self.checksum,
            "resolved_from": self.resolved_from,
        }


@dataclass
class LockFile:
    """Represents .smm/registry.lock for reproducible installs."""

    entries: list[LockEntry] = field(default_factory=list)

    @property
    def package_count(self) -> int:
        return len(self.entries)

    def has_package(self, name: str) -> bool:
        return any(e.name == name for e in self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lockfile_version": 1,
            "packages": [e.to_dict() for e in self.entries],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> LockFile:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        entries = [LockEntry(**e) for e in data.get("packages", [])]
        return cls(entries=entries)


@dataclass
class InstallResult:
    """Result of a package installation."""

    installed: list[str] = field(default_factory=list)
    already_installed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    lock_updated: bool = False

    @property
    def success(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "already_installed": self.already_installed,
            "failed": self.failed,
            "success": self.success,
            "lock_updated": self.lock_updated,
        }


class PackageInstaller:
    """Install governance packages from the registry."""

    def __init__(
        self,
        registry: RegistryClient,
        project_root: Path | None = None,
    ) -> None:
        self._registry = registry
        self._root = project_root or Path.cwd()
        self._lock_path = self._root / ".smm" / "registry.lock"

    @property
    def lock_file(self) -> LockFile:
        return LockFile.load(self._lock_path)

    def install(self, package_name: str, *, version: str | None = None) -> InstallResult:
        """Install a package and its dependencies."""
        result = InstallResult()
        lock = self.lock_file

        self._install_recursive(package_name, version, lock, result, resolved_from="direct")

        if result.installed:
            lock.save(self._lock_path)
            result.lock_updated = True

        return result

    def _install_recursive(
        self,
        name: str,
        version: str | None,
        lock: LockFile,
        result: InstallResult,
        resolved_from: str,
    ) -> None:
        if lock.has_package(name):
            result.already_installed.append(name)
            return

        pkg = self._registry.get_package(name)
        if pkg is None:
            result.failed.append(name)
            return

        # Download
        content = self._registry.download(name, version)
        if content is None:
            result.failed.append(name)
            return

        # Add to lock
        lock.entries.append(LockEntry(
            name=name,
            version=version or pkg.latest_version,
            checksum="",
            resolved_from=resolved_from,
        ))
        result.installed.append(name)

        # Resolve extends chain
        for dep in getattr(pkg, "extends", []) or []:
            if not lock.has_package(dep):
                self._install_recursive(dep, None, lock, result, resolved_from=name)

    def uninstall(self, package_name: str) -> bool:
        """Remove a package from the lock file."""
        lock = self.lock_file
        original = len(lock.entries)
        lock.entries = [e for e in lock.entries if e.name != package_name]
        if len(lock.entries) < original:
            lock.save(self._lock_path)
            return True
        return False

    def list_installed(self) -> list[str]:
        """List all installed packages."""
        return [e.name for e in self.lock_file.entries]

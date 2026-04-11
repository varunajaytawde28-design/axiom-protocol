"""Tests for git hooks integration."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from vt_protocol.integrations.git_hooks import (
    _HOOK_MARKER,
    create_mcp_json,
    install_hooks,
    is_installed,
    uninstall_hooks,
)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a fake git project with .git/hooks/."""
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    return tmp_path


class TestInstallHooks:
    def test_installs_both_hooks(self, git_project: Path) -> None:
        installed = install_hooks(git_project)
        assert "pre-commit" in installed
        assert "post-commit" in installed

    def test_hooks_are_executable(self, git_project: Path) -> None:
        install_hooks(git_project)
        for name in ("pre-commit", "post-commit"):
            hook = git_project / ".git" / "hooks" / name
            mode = hook.stat().st_mode
            assert mode & stat.S_IEXEC

    def test_hooks_contain_marker(self, git_project: Path) -> None:
        install_hooks(git_project)
        for name in ("pre-commit", "post-commit"):
            hook = git_project / ".git" / "hooks" / name
            assert _HOOK_MARKER in hook.read_text()

    def test_idempotent(self, git_project: Path) -> None:
        install_hooks(git_project)
        installed = install_hooks(git_project)
        assert installed == []  # Already installed

    def test_appends_to_existing_hook(self, git_project: Path) -> None:
        pre = git_project / ".git" / "hooks" / "pre-commit"
        pre.write_text("#!/bin/sh\necho 'existing hook'\n")
        pre.chmod(pre.stat().st_mode | stat.S_IEXEC)

        installed = install_hooks(git_project)
        assert "pre-commit" in installed
        content = pre.read_text()
        assert "existing hook" in content
        assert _HOOK_MARKER in content

    def test_no_git_dir(self, tmp_path: Path) -> None:
        installed = install_hooks(tmp_path)
        assert installed == []


class TestUninstallHooks:
    def test_removes_our_hooks(self, git_project: Path) -> None:
        install_hooks(git_project)
        removed = uninstall_hooks(git_project)
        assert "pre-commit" in removed
        assert "post-commit" in removed
        # Files should be gone (entirely ours)
        assert not (git_project / ".git" / "hooks" / "pre-commit").exists()
        assert not (git_project / ".git" / "hooks" / "post-commit").exists()

    def test_preserves_other_hook_content(self, git_project: Path) -> None:
        pre = git_project / ".git" / "hooks" / "pre-commit"
        pre.write_text("#!/bin/sh\necho 'other hook'\n")
        pre.chmod(pre.stat().st_mode | stat.S_IEXEC)
        install_hooks(git_project)

        removed = uninstall_hooks(git_project)
        assert "pre-commit" in removed
        assert pre.exists()
        content = pre.read_text()
        assert "other hook" in content
        assert _HOOK_MARKER not in content

    def test_no_hooks_to_remove(self, git_project: Path) -> None:
        removed = uninstall_hooks(git_project)
        assert removed == []


class TestIsInstalled:
    def test_not_installed(self, git_project: Path) -> None:
        result = is_installed(git_project)
        assert result["pre-commit"] is False
        assert result["post-commit"] is False

    def test_installed(self, git_project: Path) -> None:
        install_hooks(git_project)
        result = is_installed(git_project)
        assert result["pre-commit"] is True
        assert result["post-commit"] is True

    def test_no_git_dir(self, tmp_path: Path) -> None:
        result = is_installed(tmp_path)
        assert result["pre-commit"] is False


class TestCreateMcpJson:
    def test_creates_file(self, tmp_path: Path) -> None:
        path = create_mcp_json(tmp_path)
        assert path.exists()
        assert path.name == ".mcp.json"

    def test_valid_json(self, tmp_path: Path) -> None:
        create_mcp_json(tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        assert "mcpServers" in data
        assert "vt-protocol" in data["mcpServers"]

    def test_points_to_vt_serve(self, tmp_path: Path) -> None:
        create_mcp_json(tmp_path)
        data = json.loads((tmp_path / ".mcp.json").read_text())
        server = data["mcpServers"]["vt-protocol"]
        assert server["command"] == "vt"
        assert "--stdio" in server["args"]

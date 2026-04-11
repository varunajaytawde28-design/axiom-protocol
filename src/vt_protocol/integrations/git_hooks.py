"""Git hooks integration — pre-commit and post-commit hooks.

Pre-commit: run contradiction check, block on critical contradictions.
Post-commit: append to Merkle tree audit log, tag with decision metadata.

From SPEC T4: "Pre-commit: smm check runs automatically. Post-commit:
auto-tag commit with decision metadata via git trailers."
"""

from __future__ import annotations

import logging
import stat
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Hook marker so we can detect our hooks and avoid duplicates
_HOOK_MARKER = "# VT Protocol governance hook"

_PRE_COMMIT_HOOK = f"""\
#!/bin/sh
{_HOOK_MARKER}
# Runs `vt check` before each commit.
# Exit code 1 blocks the commit if critical contradictions exist.

if command -v vt >/dev/null 2>&1; then
    vt check --exit-code
    STATUS=$?
    if [ $STATUS -ne 0 ]; then
        echo ""
        echo "VT Protocol: commit blocked due to governance violations."
        echo "Run 'vt check' for details, or use --no-verify to skip."
        exit 1
    fi
fi
"""

_POST_COMMIT_HOOK = f"""\
#!/bin/sh
{_HOOK_MARKER}
# Appends commit metadata to the Merkle tree audit log.
# Runs in the background — never blocks.

if command -v vt >/dev/null 2>&1; then
    HASH=$(git rev-parse HEAD 2>/dev/null)
    MSG=$(git log -1 --pretty=%s 2>/dev/null)
    AUTHOR=$(git log -1 --pretty=%an 2>/dev/null)
    # Fire and forget — audit log append is best-effort
    vt audit-commit --hash "$HASH" --message "$MSG" --author "$AUTHOR" &
fi
"""


def install_hooks(project_root: Path) -> list[str]:
    """Install VT Protocol git hooks into .git/hooks/.

    Installs pre-commit and post-commit hooks. If a hook already exists
    and doesn't contain our marker, we append. If it already has our
    marker, we skip (idempotent).

    Returns list of hook names installed or updated.
    """
    git_hooks_dir = project_root / ".git" / "hooks"
    if not git_hooks_dir.is_dir():
        logger.warning("No .git/hooks/ directory found at %s", project_root)
        return []

    installed: list[str] = []

    for name, content in [("pre-commit", _PRE_COMMIT_HOOK), ("post-commit", _POST_COMMIT_HOOK)]:
        hook_path = git_hooks_dir / name
        installed_or_updated = _install_single_hook(hook_path, content)
        if installed_or_updated:
            installed.append(name)

    return installed


def uninstall_hooks(project_root: Path) -> list[str]:
    """Remove VT Protocol hooks from .git/hooks/.

    If the hook file contains only our content, removes the file.
    If it contains other content too, removes only our section.

    Returns list of hook names removed.
    """
    git_hooks_dir = project_root / ".git" / "hooks"
    if not git_hooks_dir.is_dir():
        return []

    removed: list[str] = []

    for name in ("pre-commit", "post-commit"):
        hook_path = git_hooks_dir / name
        if not hook_path.exists():
            continue

        content = hook_path.read_text()
        if _HOOK_MARKER not in content:
            continue

        # Check if the file is entirely ours
        lines = content.split("\n")
        our_lines = []
        other_lines = []
        in_our_section = False
        for line in lines:
            if _HOOK_MARKER in line:
                in_our_section = True
            if in_our_section:
                our_lines.append(line)
            else:
                other_lines.append(line)

        if not any(l.strip() for l in other_lines if not l.startswith("#!")):
            # Entirely ours — remove
            hook_path.unlink()
        else:
            # Has other content — remove only our section
            hook_path.write_text("\n".join(other_lines))
            _make_executable(hook_path)

        removed.append(name)

    return removed


def is_installed(project_root: Path) -> dict[str, bool]:
    """Check which VT Protocol hooks are installed."""
    git_hooks_dir = project_root / ".git" / "hooks"
    result = {"pre-commit": False, "post-commit": False}
    if not git_hooks_dir.is_dir():
        return result

    for name in result:
        hook_path = git_hooks_dir / name
        if hook_path.exists():
            content = hook_path.read_text()
            result[name] = _HOOK_MARKER in content

    return result


def create_mcp_json(project_root: Path) -> Path:
    """Create .mcp.json for Claude Code / Cursor MCP auto-discovery.

    Points to the VT Protocol MCP server so agents get governance
    tools automatically when they open the project.
    """
    import json

    mcp_config = {
        "mcpServers": {
            "vt-protocol": {
                "command": "vt",
                "args": ["serve", "--stdio"],
            }
        }
    }

    path = project_root / ".mcp.json"
    path.write_text(json.dumps(mcp_config, indent=2) + "\n")
    return path


def _install_single_hook(hook_path: Path, content: str) -> bool:
    """Install or append a single hook. Returns True if written."""
    if hook_path.exists():
        existing = hook_path.read_text()
        if _HOOK_MARKER in existing:
            return False  # Already installed
        # Append to existing hook
        hook_path.write_text(existing.rstrip() + "\n\n" + content)
    else:
        hook_path.write_text(content)

    _make_executable(hook_path)
    return True


def _make_executable(path: Path) -> None:
    """chmod +x a file."""
    current = path.stat().st_mode
    path.chmod(current | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

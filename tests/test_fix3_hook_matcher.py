"""Tests for Fix 3: Hook blocks ALL tools including reads.

Verifies that the hook matcher is set to 'Write|Edit' and not '.*'.
The session lock check should only apply to destructive operations,
not Read, Glob, Grep, or other read-only tools.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestHookMatcher:
    def test_project_settings_matcher_is_write_edit(self) -> None:
        """The project .claude/settings.json should match only Write|Edit."""
        settings_path = Path(__file__).parent.parent / ".claude" / "settings.json"
        if not settings_path.exists():
            pytest.skip("No .claude/settings.json in project root")

        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {}).get("PreToolUse", [])
        for hook_entry in hooks:
            matcher = hook_entry.get("matcher", "")
            # The VT validate hook should only match Write|Edit
            for h in hook_entry.get("hooks", []):
                cmd = h.get("command", "")
                if "vt-validate" in cmd:
                    assert matcher == "Write|Edit", (
                        f"VT validate hook matcher should be 'Write|Edit', got '{matcher}'"
                    )

    def test_install_claude_code_hook_sets_write_edit_matcher(self, tmp_path: Path) -> None:
        """install_claude_code_hook should set matcher to Write|Edit."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        settings_path = root / ".claude" / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())

        hooks = data["hooks"]["PreToolUse"]
        assert len(hooks) >= 1
        assert hooks[0]["matcher"] == "Write|Edit"

    def test_hook_matcher_does_not_match_read(self) -> None:
        """'Write|Edit' regex should not match Read, Glob, Grep."""
        import re

        pattern = re.compile("Write|Edit")
        # Should match
        assert pattern.search("Write")
        assert pattern.search("Edit")
        # Should NOT match (entire string)
        assert not pattern.fullmatch("Read")
        assert not pattern.fullmatch("Glob")
        assert not pattern.fullmatch("Grep")
        assert not pattern.fullmatch("Bash")
        assert not pattern.fullmatch("Agent")

    def test_hook_matcher_does_not_use_wildcard(self, tmp_path: Path) -> None:
        """Ensure the matcher is not '.*' which would block everything."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        settings_path = root / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())

        for hook_entry in data["hooks"]["PreToolUse"]:
            assert hook_entry["matcher"] != ".*", (
                "Matcher should not be '.*' — it would block all tools including reads"
            )

    def test_hook_script_only_validates_write_edit(self, tmp_path: Path) -> None:
        """The hook script itself should exit 0 for non-Write/Edit tools."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        hook_path = root / ".claude" / "hooks" / "vt-validate.sh"
        assert hook_path.exists()
        content = hook_path.read_text()
        # Should have case statement that exits 0 for non-Write/Edit
        assert "Write|Edit) ;;" in content
        assert "*) exit 0 ;;" in content

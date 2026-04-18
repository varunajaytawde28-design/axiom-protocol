"""Tests for Fix 5: Auto-run contradiction check after agent writes code.

Verifies:
- Post-commit hook includes vt infer + vt check + warning
- install_claude_code_hook creates PostToolUse hook script
- PostToolUse hook is registered in .claude/settings.json
- PostToolUse hook script has correct structure (infer then check)
- PostToolUse hook logs to events.jsonl on contradiction
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestPostCommitHookContent:
    def test_post_commit_hook_includes_vt_infer(self) -> None:
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "vt infer" in _POST_COMMIT_HOOK

    def test_post_commit_hook_includes_vt_check(self) -> None:
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "vt check" in _POST_COMMIT_HOOK

    def test_post_commit_hook_includes_contradiction_warning(self) -> None:
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "VT PROTOCOL" in _POST_COMMIT_HOOK
        assert "contradiction" in _POST_COMMIT_HOOK.lower()

    def test_post_commit_hook_warning_mentions_resolve(self) -> None:
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "--resolve" in _POST_COMMIT_HOOK

    def test_post_commit_hook_includes_audit_commit(self) -> None:
        """Original audit-commit call must still be present."""
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "audit-commit" in _POST_COMMIT_HOOK

    def test_post_commit_hook_runs_infer_before_check(self) -> None:
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        infer_pos = _POST_COMMIT_HOOK.find("vt infer")
        check_pos = _POST_COMMIT_HOOK.find("vt check")
        assert infer_pos != -1
        assert check_pos != -1
        assert infer_pos < check_pos, "vt infer must appear before vt check"


class TestPostToolUseHookInstall:
    def test_install_creates_post_write_script(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        post_hook = root / ".claude" / "hooks" / "vt-post-write.sh"
        assert post_hook.exists(), "vt-post-write.sh should be created"

    def test_post_write_script_is_executable(self, tmp_path: Path) -> None:
        import stat

        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        post_hook = root / ".claude" / "hooks" / "vt-post-write.sh"
        mode = post_hook.stat().st_mode
        assert mode & stat.S_IEXEC, "vt-post-write.sh must be executable"

    def test_settings_includes_post_tool_use(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        settings = json.loads((root / ".claude" / "settings.json").read_text())
        assert "PostToolUse" in settings["hooks"], "PostToolUse must be registered"

    def test_post_tool_use_matcher_is_write_edit(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        settings = json.loads((root / ".claude" / "settings.json").read_text())
        post_hooks = settings["hooks"]["PostToolUse"]
        matchers = [h.get("matcher") for h in post_hooks]
        assert "Write|Edit" in matchers

    def test_post_tool_use_command_points_to_script(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        settings = json.loads((root / ".claude" / "settings.json").read_text())
        post_hooks = settings["hooks"]["PostToolUse"]
        commands = [
            h2.get("command", "")
            for h in post_hooks
            for h2 in h.get("hooks", [])
        ]
        assert any("vt-post-write.sh" in c for c in commands)

    def test_post_write_script_runs_vt_infer(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        assert "vt infer" in content

    def test_post_write_script_runs_vt_check(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        assert "vt check" in content

    def test_post_write_script_logs_to_events_jsonl(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        assert "events.jsonl" in content

    def test_post_write_script_exits_0_for_non_write(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        assert "Write|Edit) ;;" in content
        assert "*) exit 0 ;;" in content

    def test_post_write_script_outputs_warning_on_contradiction(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        assert "VT PROTOCOL" in content
        assert "contradiction" in content.lower()

    def test_post_write_script_uses_additional_context_not_output_text(self, tmp_path: Path) -> None:
        """PostToolUse hooks must use 'additionalContext', not 'outputText'."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        assert "additionalContext" in content, "Must use additionalContext per Claude Code docs"
        assert "outputText" not in content, "outputText is not a valid Claude Code hook field"

    def test_post_write_script_uses_decision_block(self, tmp_path: Path) -> None:
        """PostToolUse hooks should output decision: block when contradictions found."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        assert '"block"' in content, "Must output decision: block for contradictions"

    def test_post_write_uses_grep_fallback(self, tmp_path: Path) -> None:
        """Hook must use grep-based FAIL detection instead of fragile jq JSON parsing."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        install_claude_code_hook(root)

        content = (root / ".claude" / "hooks" / "vt-post-write.sh").read_text()
        # Must use grep for FAIL detection (simpler than JSON parsing)
        assert 'grep' in content
        assert 'FAIL' in content

    def test_install_is_idempotent(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        result1 = install_claude_code_hook(root)
        result2 = install_claude_code_hook(root)
        assert result1 is True
        assert result2 is False  # already installed

    def test_merge_preserves_existing_settings(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        root = tmp_path / "proj"
        root.mkdir()
        settings_path = root / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({"theme": "dark", "hooks": {}}) + "\n")

        install_claude_code_hook(root)

        data = json.loads(settings_path.read_text())
        assert data["theme"] == "dark", "Existing settings must be preserved"
        assert "PreToolUse" in data["hooks"]
        assert "PostToolUse" in data["hooks"]

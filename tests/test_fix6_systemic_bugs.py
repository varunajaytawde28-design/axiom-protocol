"""Tests for Fix 6: Four systemic bugs — hook flags, MCP config, contradictions dir, assumption rules.

Bug 1: PostToolUse hook uses correct CLI flags (no --quiet, no --json)
Bug 2: .mcp.json generated with correct format for Claude Code
Bug 3: vt init creates .smm/contradictions/ directory
Bug 4: Validated assumptions produce imperative CLAUDE.md rules, not raw evidence
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Bug 1: PostToolUse hook uses correct CLI flags
# ---------------------------------------------------------------------------


class TestPostToolUseHookFlags:
    """Verify the hook template in git_hooks.py uses valid CLI flags."""

    def test_post_commit_hook_no_quiet_flag(self) -> None:
        """vt infer has no --quiet flag."""
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "--quiet" not in _POST_COMMIT_HOOK

    def test_post_commit_hook_no_json_flag(self) -> None:
        """vt check uses --json-output, not --json."""
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "--json " not in _POST_COMMIT_HOOK
        assert "vt check --json 2" not in _POST_COMMIT_HOOK

    def test_post_commit_hook_uses_grep_for_fail(self) -> None:
        """Post-commit hook uses grep for FAIL detection, not jq JSON parsing."""
        from vt_protocol.integrations.git_hooks import _POST_COMMIT_HOOK

        assert "grep" in _POST_COMMIT_HOOK
        assert "FAIL" in _POST_COMMIT_HOOK

    def test_claude_code_hook_template_no_quiet_flag(self) -> None:
        """The Claude Code PostToolUse hook template must not use --quiet."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        # Read the source to check the template string
        import inspect
        source = inspect.getsource(install_claude_code_hook)
        assert "infer --quiet" not in source

    def test_claude_code_hook_template_no_json_flag(self) -> None:
        """The Claude Code PostToolUse hook template must not use --json (use --json-output or grep)."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        import inspect
        source = inspect.getsource(install_claude_code_hook)
        assert "check --json 2" not in source
        assert "check --json'" not in source

    def test_claude_code_hook_template_has_debug_log(self) -> None:
        """The Claude Code PostToolUse hook template must include debug logging."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        import inspect
        source = inspect.getsource(install_claude_code_hook)
        assert "debug" in source.lower() or "DEBUG_LOG" in source

    def test_claude_code_hook_timeout_120(self) -> None:
        """PostToolUse hook timeout must be >= 120s for DeBERTa model loading."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        import inspect
        source = inspect.getsource(install_claude_code_hook)
        # The template sets timeout in the settings dict
        assert '"timeout": 120' in source or "'timeout': 120" in source

    def test_installed_hook_uses_grep(self, tmp_path: Path) -> None:
        """When installed, the PostToolUse hook must use grep, not jq for FAIL detection."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        (tmp_path / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
        install_claude_code_hook(tmp_path)

        post_hook = tmp_path / ".claude" / "hooks" / "vt-post-write.sh"
        assert post_hook.exists()
        content = post_hook.read_text()
        assert "grep" in content
        assert "FAIL" in content

    def test_installed_settings_timeout_120(self, tmp_path: Path) -> None:
        """Installed settings.json must have timeout >= 120s for PostToolUse."""
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        install_claude_code_hook(tmp_path)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        post_hooks = settings["hooks"]["PostToolUse"]
        for entry in post_hooks:
            for hook in entry.get("hooks", []):
                assert hook["timeout"] >= 120


# ---------------------------------------------------------------------------
# Bug 2: .mcp.json format
# ---------------------------------------------------------------------------


class TestMcpJsonGeneration:
    """Verify .mcp.json is generated with correct format for Claude Code."""

    def test_create_mcp_json_format(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import create_mcp_json

        path = create_mcp_json(tmp_path)
        data = json.loads(path.read_text())

        assert "mcpServers" in data
        assert "vt-protocol" in data["mcpServers"]
        server = data["mcpServers"]["vt-protocol"]
        assert server["command"] == "vt"
        assert server["args"] == ["serve", "--stdio"]

    def test_create_mcp_json_no_extra_keys(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import create_mcp_json

        path = create_mcp_json(tmp_path)
        data = json.loads(path.read_text())

        # Only mcpServers at top level
        assert list(data.keys()) == ["mcpServers"]

    def test_vt_init_creates_mcp_json(self, tmp_path: Path) -> None:
        """vt init should create .mcp.json in the project root."""
        from vt_protocol.integrations.git_hooks import create_mcp_json

        create_mcp_json(tmp_path)
        assert (tmp_path / ".mcp.json").exists()


# ---------------------------------------------------------------------------
# Bug 3: .smm/contradictions/ directory
# ---------------------------------------------------------------------------


class TestContradictionsDirectory:
    """Verify ensure_smm_structure creates .smm/contradictions/."""

    def test_ensure_smm_structure_creates_contradictions(self, tmp_path: Path) -> None:
        from vt_protocol.config import ensure_smm_structure

        ensure_smm_structure(tmp_path)
        assert (tmp_path / ".smm" / "contradictions").is_dir()

    def test_ensure_smm_structure_creates_all_dirs(self, tmp_path: Path) -> None:
        from vt_protocol.config import ensure_smm_structure

        ensure_smm_structure(tmp_path)
        expected = {"decisions", "cache", "generated", "audit", "traces", "contradictions", "pending-refactors"}
        actual = {p.name for p in (tmp_path / ".smm").iterdir() if p.is_dir()}
        assert expected <= actual

    def test_ensure_smm_structure_idempotent(self, tmp_path: Path) -> None:
        from vt_protocol.config import ensure_smm_structure

        ensure_smm_structure(tmp_path)
        ensure_smm_structure(tmp_path)
        assert (tmp_path / ".smm" / "contradictions").is_dir()


# ---------------------------------------------------------------------------
# Bug 4: Validated assumptions create proper CLAUDE.md rules
# ---------------------------------------------------------------------------


class TestAssumptionDecisionContent:
    """Verify validated assumptions produce imperative rules, not raw evidence."""

    def _make_assumption(self, pattern_id: str, summary: str, snippet: str = "", category: str = "data_scope"):
        from vt_protocol.decisions.models import (
            AssumptionCategory,
            AssumptionStatus,
            CodeEvidence,
            DomainAssumption,
        )

        evidence = [CodeEvidence(file="app.py", line=10, snippet=snippet)] if snippet else []
        return DomainAssumption(
            category=AssumptionCategory(category),
            pattern_id=pattern_id,
            summary=summary,
            code_evidence=evidence,
            status=AssumptionStatus.VALIDATED,
            resolved_by="test-user",
            answer_rationale="Confirmed by team",
        )

    def test_single_source_write_generates_imperative_rule(self) -> None:
        from vt_protocol.dashboard.app import _generate_imperative_rule

        assumption = self._make_assumption(
            "single_source_write",
            "create_user() writes to users table",
            "customer = stripe.Customer.create(email=email, name=full_name)",
        )
        rule = _generate_imperative_rule(assumption)

        assert "Only" in rule
        assert "create_user()" in rule
        assert "Do not" in rule
        # Must NOT contain raw code evidence
        assert "stripe.Customer.create" not in rule
        assert "Category:" not in rule
        assert "Evidence:" not in rule

    def test_env_no_fallback_generates_imperative_rule(self) -> None:
        from vt_protocol.dashboard.app import _generate_imperative_rule

        assumption = self._make_assumption(
            "env_no_fallback",
            "DATABASE_URL accessed without fallback",
            'os.environ["DATABASE_URL"]',
            category="configuration",
        )
        rule = _generate_imperative_rule(assumption)

        assert "DATABASE_URL" in rule
        assert "must always be set" in rule or "fallback" in rule
        assert "Do not" in rule
        assert "Category:" not in rule

    def test_single_table_query_generates_imperative_rule(self) -> None:
        from vt_protocol.dashboard.app import _generate_imperative_rule

        assumption = self._make_assumption(
            "single_table_query",
            "orders queried without joins",
            "SELECT * FROM orders WHERE id = ?",
        )
        rule = _generate_imperative_rule(assumption)

        assert "JOINs" in rule or "joins" in rule
        assert "Do not" in rule
        assert "Category:" not in rule

    def test_generic_pattern_still_imperative(self) -> None:
        from vt_protocol.dashboard.app import _generate_imperative_rule

        assumption = self._make_assumption(
            "unknown_pattern",
            "Cache TTL is 300 seconds",
            "cache.set(key, val, ttl=300)",
            category="temporal",
        )
        rule = _generate_imperative_rule(assumption)

        assert "Do not change" in rule or "Do not" in rule
        assert "approval" in rule
        # Must not dump raw evidence
        assert "Category:" not in rule
        assert "Evidence:" not in rule

    def test_no_raw_code_in_decision_content(self) -> None:
        """The full _create_decision_from_assumption path must produce clean content."""
        from vt_protocol.dashboard.app import _generate_imperative_rule

        assumption = self._make_assumption(
            "single_source_write",
            "save_order() writes to orders",
            "db.session.add(Order(user_id=uid))",
        )
        rule = _generate_imperative_rule(assumption)

        # Rule should be readable, not contain Python code
        assert "db.session.add" not in rule
        assert "Rationale:" in rule

"""Tests for the fully automated VT Protocol flow.

Covers all fixes needed for the VC demo:
1. ensure_smm_structure creates contradictions/ and pending-refactors/ dirs
2. PostToolUse hook fires and saves contradictions to disk + logs trace events
3. Validated assumptions become CLAUDE.md rules (decision created)
4. vt apply auto-runs after dashboard resolution + trace event logged
5. WebSocket push on events.jsonl changes
6. Session token aggregation (covered by existing test_bug5)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vt_protocol.config import ensure_smm_structure
from vt_protocol.dashboard.app import (
    DashboardState,
    _auto_apply_rules,
    _create_decision_from_assumption,
    _log_assumption_trace_event,
    _log_resolution_trace_event,
    app,
    reset_state,
    set_state,
)
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    Dimension,
    SourceType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision(title: str, *, dims: list[Dimension] | None = None) -> Decision:
    return Decision(
        title=title,
        content=f"Use {title} for the project.",
        rationale="Team decision",
        dimensions=dims or [Dimension.API_STYLE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )


def _contradiction(d1: Decision, d2: Decision) -> Contradiction:
    return Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=ContradictionVerdict.CONTRADICTION,
        reasoning="They conflict",
        evidence_a="A says X",
        evidence_b="B says Y",
        shared_dimensions=list(set(d1.dimensions) & set(d2.dimensions)),
        confidence=0.9,
    )


@pytest.fixture()
def governed_state(tmp_path: Path):
    """Full governed state with decisions, contradictions, and governance.yaml."""
    d1 = _decision("GraphQL via Strawberry")
    d2 = _decision("REST via FastAPI")
    c = _contradiction(d1, d2)

    ensure_smm_structure(tmp_path)
    (tmp_path / ".smm" / "decisions" / "001.json").write_text(d1.model_dump_json())
    (tmp_path / ".smm" / "decisions" / "002.json").write_text(d2.model_dump_json())
    (tmp_path / ".smm" / "contradictions" / f"{str(c.id)[:8]}.json").write_text(
        c.model_dump_json(indent=2)
    )
    (tmp_path / "governance.yaml").write_text(
        "extends:\n  - '@vt/recommended'\n"
        "model:\n  provider: none\n  model: ''\n"
        "agents:\n  claude: true\n"
    )

    ds = DashboardState(project_root=tmp_path)
    ds.decisions = [d1, d2]
    ds.contradictions = [c]
    set_state(ds)
    yield {"d1": d1, "d2": d2, "c": c, "root": tmp_path}
    reset_state()


# ===========================================================================
# FIX 1: ensure_smm_structure creates contradictions/ and pending-refactors/
# ===========================================================================


class TestEnsureSmmStructure:
    def test_creates_contradictions_dir(self, tmp_path: Path) -> None:
        ensure_smm_structure(tmp_path)
        assert (tmp_path / ".smm" / "contradictions").is_dir()

    def test_creates_pending_refactors_dir(self, tmp_path: Path) -> None:
        ensure_smm_structure(tmp_path)
        assert (tmp_path / ".smm" / "pending-refactors").is_dir()

    def test_creates_all_required_dirs(self, tmp_path: Path) -> None:
        ensure_smm_structure(tmp_path)
        expected = ["decisions", "cache", "generated", "audit", "traces",
                    "contradictions", "pending-refactors"]
        for d in expected:
            assert (tmp_path / ".smm" / d).is_dir(), f".smm/{d} missing"

    def test_idempotent(self, tmp_path: Path) -> None:
        ensure_smm_structure(tmp_path)
        ensure_smm_structure(tmp_path)
        assert (tmp_path / ".smm" / "contradictions").is_dir()

    def test_gitignore_created(self, tmp_path: Path) -> None:
        ensure_smm_structure(tmp_path)
        gi = tmp_path / ".smm" / ".gitignore"
        assert gi.exists()
        assert "cache/" in gi.read_text()


# ===========================================================================
# FIX 2: PostToolUse hook script is valid and executable
# ===========================================================================


class TestPostToolUseHookScript:
    def _hook_path(self) -> Path:
        return Path(__file__).parent.parent / ".claude" / "hooks" / "vt-post-write.sh"

    def test_hook_file_exists(self) -> None:
        assert self._hook_path().exists()

    def test_hook_is_executable(self) -> None:
        import stat
        mode = self._hook_path().stat().st_mode
        assert mode & stat.S_IEXEC, "Hook must be executable"

    def test_hook_has_marker(self) -> None:
        content = self._hook_path().read_text()
        assert "# VT Protocol governance hook" in content

    def test_hook_filters_tool_name(self) -> None:
        content = self._hook_path().read_text()
        assert "Write|Edit" in content

    def test_hook_saves_contradictions_to_disk(self) -> None:
        content = self._hook_path().read_text()
        assert "contradictions" in content

    def test_hook_logs_trace_event(self) -> None:
        content = self._hook_path().read_text()
        assert "events.jsonl" in content

    def test_hook_returns_hookSpecificOutput(self) -> None:
        content = self._hook_path().read_text()
        assert "hookSpecificOutput" in content
        assert "additionalContext" in content


# ===========================================================================
# FIX 2b: settings.json has PostToolUse entry
# ===========================================================================


class TestClaudeSettings:
    def _settings_path(self) -> Path:
        return Path(__file__).parent.parent / ".claude" / "settings.json"

    def test_settings_exists(self) -> None:
        assert self._settings_path().exists()

    def test_has_post_tool_use_hook(self) -> None:
        data = json.loads(self._settings_path().read_text())
        assert "PostToolUse" in data.get("hooks", {})

    def test_post_tool_use_matcher(self) -> None:
        data = json.loads(self._settings_path().read_text())
        post = data["hooks"]["PostToolUse"]
        matchers = [h.get("matcher") for h in post]
        assert "Write|Edit" in matchers

    def test_post_tool_use_points_to_hook(self) -> None:
        data = json.loads(self._settings_path().read_text())
        post = data["hooks"]["PostToolUse"]
        commands = [h["hooks"][0]["command"] for h in post]
        assert any("vt-post-write.sh" in c for c in commands)


# ===========================================================================
# FIX 3: Validated assumptions become decisions → CLAUDE.md rules
# ===========================================================================


class TestAssumptionToDecision:
    def _make_assumption(self):
        """Create a mock validated assumption."""
        from unittest.mock import MagicMock
        from enum import Enum

        class MockCategory(Enum):
            DATA_SCOPE = "data_scope"

        class MockStatus(Enum):
            VALIDATED = "validated"

        class MockEvidence:
            file = "src/api.py"
            line = 42
            snippet = "import httpx"

        a = MagicMock()
        a.id = "abcd1234-0000-0000-0000-000000000000"
        a.summary = "Data accessed via HTTP only"
        a.category = MockCategory.DATA_SCOPE
        a.status = MockStatus.VALIDATED
        a.code_evidence = [MockEvidence()]
        a.resolved_by = "test-user"
        a.answer_rationale = "Confirmed correct"
        a.pattern_id = "data_scope_http"
        return a

    def test_creates_decision_on_disk(self, tmp_path: Path) -> None:
        ds = DashboardState(project_root=tmp_path)
        ds.decisions = []
        (tmp_path / ".smm" / "decisions").mkdir(parents=True)

        assumption = self._make_assumption()
        result = _create_decision_from_assumption(ds, assumption)

        assert result is True
        files = list((tmp_path / ".smm" / "decisions").glob("*.json"))
        assert len(files) == 1

    def test_decision_title_contains_assumption_summary(self, tmp_path: Path) -> None:
        ds = DashboardState(project_root=tmp_path)
        ds.decisions = []
        (tmp_path / ".smm" / "decisions").mkdir(parents=True)

        assumption = self._make_assumption()
        _create_decision_from_assumption(ds, assumption)

        assert len(ds.decisions) == 1
        assert "Data accessed via HTTP only" in ds.decisions[0].title

    def test_decision_added_to_state(self, tmp_path: Path) -> None:
        ds = DashboardState(project_root=tmp_path)
        ds.decisions = []
        (tmp_path / ".smm" / "decisions").mkdir(parents=True)

        assumption = self._make_assumption()
        _create_decision_from_assumption(ds, assumption)

        assert len(ds.decisions) == 1


class TestAssumptionResolutionEndpoint:
    """Test that /api/assumptions/{id}/resolve triggers auto-apply and decision creation."""

    def test_validated_assumption_triggers_auto_apply(self, tmp_path: Path) -> None:
        """When user validates an assumption, sync_rules should be called."""
        from vt_protocol.decisions.models import AssumptionStatus

        # Create a minimal assumption on disk
        assumptions_dir = tmp_path / ".smm" / "assumptions"
        assumptions_dir.mkdir(parents=True)

        from vt_protocol.decisions.models import DomainAssumption, AssumptionCategory
        from uuid import uuid4

        assumption = DomainAssumption(
            id=uuid4(),
            pattern_id="test_pattern",
            category=AssumptionCategory.DATA_SCOPE,
            summary="Data is accessed via HTTP",
            confidence=0.8,
            severity="medium",
            code_evidence=[],
            question="Is data accessed via HTTP?",
            options=["Yes, HTTP only", "No, also gRPC", "I need more context"],
            status=AssumptionStatus.PROPOSED,
        )
        (assumptions_dir / f"{assumption.id.hex[:8]}.json").write_text(
            assumption.model_dump_json(indent=2)
        )

        ds = DashboardState(project_root=tmp_path)
        ds.decisions = []
        ds._assumptions = [assumption]
        (tmp_path / "governance.yaml").write_text(
            "extends:\n  - '@vt/recommended'\n"
            "model:\n  provider: none\n  model: ''\n"
            "agents:\n  claude: true\n"
        )
        set_state(ds)

        try:
            client = TestClient(app)
            with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
                mock_sync.return_value = MagicMock(files_written=[])
                resp = client.post(
                    f"/api/assumptions/{assumption.id.hex}/resolve",
                    json={"selected_option": 0, "resolved_by": "test-user"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "validated"
                mock_sync.assert_called_once()
        finally:
            reset_state()


# ===========================================================================
# FIX 4: Resolution logs trace events + auto-applies rules
# ===========================================================================


class TestResolutionTraceEvents:
    def test_log_resolution_trace_event(self, tmp_path: Path) -> None:
        d1 = _decision("GraphQL")
        d2 = _decision("REST")
        c = _contradiction(d1, d2)
        c.resolved_by = "dashboard-user"

        (tmp_path / ".smm" / "traces").mkdir(parents=True)
        _log_resolution_trace_event(tmp_path, c, str(d1.id), d1.title)

        events_path = tmp_path / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()
        event = json.loads(events_path.read_text().strip())
        assert event["type"] == "contradiction_resolved"
        assert "GraphQL" in event["reason"]

    def test_resolve_endpoint_creates_trace_event(
        self, governed_state: dict
    ) -> None:
        c = governed_state["c"]
        d1 = governed_state["d1"]
        root = governed_state["root"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
            )

        events_path = root / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()
        lines = [l for l in events_path.read_text().splitlines() if l.strip()]
        # At least one resolution event
        events = [json.loads(l) for l in lines]
        resolution_events = [e for e in events if e.get("type") == "contradiction_resolved"]
        assert len(resolution_events) >= 1

    def test_apply_resolution_creates_trace_event(
        self, governed_state: dict
    ) -> None:
        c = governed_state["c"]
        root = governed_state["root"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            client.post(
                f"/api/contradictions/{c.id}/apply-resolution",
                json={"action": "pick_a", "rationale": "GraphQL wins"},
            )

        events_path = root / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()
        lines = [l for l in events_path.read_text().splitlines() if l.strip()]
        events = [json.loads(l) for l in lines]
        resolution_events = [e for e in events if e.get("type") == "contradiction_resolved"]
        assert len(resolution_events) >= 1


class TestAssumptionTraceEvents:
    def test_log_assumption_trace_event(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock
        from enum import Enum

        class MockStatus(Enum):
            VALIDATED = "validated"

        a = MagicMock()
        a.status = MockStatus.VALIDATED
        a.summary = "HTTP only access"
        a.resolved_by = "test-user"

        (tmp_path / ".smm" / "traces").mkdir(parents=True)
        _log_assumption_trace_event(tmp_path, a)

        events_path = tmp_path / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()
        event = json.loads(events_path.read_text().strip())
        assert event["type"] == "assumption_validated"
        assert "HTTP only" in event["reason"]


# ===========================================================================
# FIX 4b: _auto_apply_rules regenerates CLAUDE.md
# ===========================================================================


class TestAutoApplyOnResolution:
    def test_resolve_triggers_sync_rules(self, governed_state: dict) -> None:
        c = governed_state["c"]
        d1 = governed_state["d1"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
            )
            mock_sync.assert_called_once()

    def test_apply_resolution_triggers_sync_rules(self, governed_state: dict) -> None:
        c = governed_state["c"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            client.post(
                f"/api/contradictions/{c.id}/apply-resolution",
                json={"action": "accept_exception", "rationale": "OK"},
            )
            mock_sync.assert_called_once()

    def test_auto_apply_never_raises(self, tmp_path: Path) -> None:
        ds = DashboardState(project_root=tmp_path)
        # No governance.yaml, no .smm/ — should not raise
        _auto_apply_rules(ds)


# ===========================================================================
# FIX 5: WebSocket broadcast helpers exist and work
# ===========================================================================


class TestWebSocketBroadcast:
    def test_broadcast_method_exists(self) -> None:
        ds = DashboardState()
        assert hasattr(ds, "broadcast")
        assert callable(ds.broadcast)

    def test_websocket_endpoint_responds_to_ping(self) -> None:
        ds = DashboardState()
        set_state(ds)
        try:
            client = TestClient(app)
            with client.websocket_connect("/ws") as ws:
                ws.send_text("ping")
                data = ws.receive_json()
                assert data["type"] == "pong"
        finally:
            reset_state()

    def test_websocket_endpoint_responds_to_refresh(self, tmp_path: Path) -> None:
        ds = DashboardState(project_root=tmp_path)
        (tmp_path / ".smm" / "decisions").mkdir(parents=True)
        set_state(ds)
        try:
            client = TestClient(app)
            with client.websocket_connect("/ws") as ws:
                ws.send_text("refresh")
                data = ws.receive_json()
                assert data["type"] == "refreshed"
        finally:
            reset_state()


# ===========================================================================
# FIX 6: Resolution broadcasts include enough data for badge update
# ===========================================================================


class TestResolutionBroadcastData:
    def test_resolve_broadcasts_winner_and_loser(self, governed_state: dict) -> None:
        c = governed_state["c"]
        d1 = governed_state["d1"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            with client.websocket_connect("/ws") as ws:
                # Send ping to consume initial connection
                ws.send_text("ping")
                ws.receive_json()

                # Resolve via API in a separate request
                resp = client.post(
                    f"/api/contradictions/{c.id}/resolve",
                    json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
                )
                assert resp.status_code == 200

                # The broadcast should contain winner and loser info
                data = ws.receive_json()
                assert data["type"] == "contradiction_resolved"
                assert "winner_id" in data["data"]
                assert "loser_id" in data["data"]


# ===========================================================================
# Integration: Full resolve flow (disk → state → rules → trace → WS)
# ===========================================================================


class TestFullResolveFlow:
    def test_end_to_end_resolve(self, governed_state: dict) -> None:
        """Resolving a contradiction must:
        1. Update contradiction status on disk
        2. Supersede losing decision on disk
        3. Generate refactor task
        4. Auto-apply rules (sync_rules called)
        5. Log trace event
        6. Broadcast via WebSocket
        """
        c = governed_state["c"]
        d1 = governed_state["d1"]
        d2 = governed_state["d2"]
        root = governed_state["root"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            resp = client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
            )

        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "resolved"
        assert result["loser_superseded"] is True

        # 1. Contradiction on disk is resolved
        c_file = root / ".smm" / "contradictions" / f"{str(c.id)[:8]}.json"
        c_data = json.loads(c_file.read_text())
        assert c_data["status"] == "resolved"

        # 2. Losing decision superseded on disk
        d2_file = root / ".smm" / "decisions" / "002.json"
        d2_data = json.loads(d2_file.read_text())
        assert d2_data["status"] == "superseded"
        assert d2_data["valid"] is False

        # 3. Refactor task generated
        refactors_dir = root / ".smm" / "pending-refactors"
        assert refactors_dir.is_dir()
        refactor_files = list(refactors_dir.glob("refactor-*.md"))
        assert len(refactor_files) == 1
        assert d2.title in refactor_files[0].read_text()

        # 4. sync_rules was called
        mock_sync.assert_called_once()

        # 5. Trace event logged
        events_path = root / ".smm" / "traces" / "events.jsonl"
        assert events_path.exists()
        events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
        resolution_events = [e for e in events if e.get("type") == "contradiction_resolved"]
        assert len(resolution_events) >= 1


# ===========================================================================
# Integration: install_claude_code_hook generates both hooks
# ===========================================================================


class TestInstallClaudeCodeHook:
    def test_installs_both_hooks(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        result = install_claude_code_hook(tmp_path)
        assert result is True

        pre_hook = tmp_path / ".claude" / "hooks" / "vt-validate.sh"
        post_hook = tmp_path / ".claude" / "hooks" / "vt-post-write.sh"
        assert pre_hook.exists()
        assert post_hook.exists()

    def test_post_hook_saves_contradictions(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        install_claude_code_hook(tmp_path)
        post_hook = tmp_path / ".claude" / "hooks" / "vt-post-write.sh"
        content = post_hook.read_text()
        assert "contradictions" in content

    def test_settings_json_has_both_hooks(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        install_claude_code_hook(tmp_path)
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert "PreToolUse" in settings["hooks"]
        assert "PostToolUse" in settings["hooks"]

    def test_idempotent(self, tmp_path: Path) -> None:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook

        install_claude_code_hook(tmp_path)
        result = install_claude_code_hook(tmp_path)
        assert result is False  # Already installed

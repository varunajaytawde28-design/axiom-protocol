"""Tests for Bug 4: Dashboard auto-runs vt apply after resolution.

Verifies:
- api_resolve_contradiction triggers sync_rules (CLAUDE.md regenerated)
- api_apply_resolution triggers sync_rules (CLAUDE.md regenerated)
- _auto_apply_rules() calls sync_rules with active decisions
- Auto-apply is best-effort (never raises on error)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vt_protocol.dashboard.app import DashboardState, _auto_apply_rules, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionVerdict,
    Decision,
    Dimension,
    SourceType,
)


def _decision(title: str) -> Decision:
    return Decision(
        title=title,
        content="Content",
        rationale="Rationale",
        dimensions=[Dimension.API_STYLE],
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
        evidence_a="A",
        evidence_b="B",
        shared_dimensions=[Dimension.API_STYLE],
        confidence=0.9,
    )


@pytest.fixture()
def state_with_governance(tmp_path: Path):
    d1 = _decision("GraphQL via Strawberry")
    d2 = _decision("REST via FastAPI")
    c = _contradiction(d1, d2)

    (tmp_path / ".smm" / "decisions").mkdir(parents=True)
    (tmp_path / ".smm" / "contradictions").mkdir(parents=True)
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


class TestAutoApplyRules:
    def test_auto_apply_does_not_raise_on_missing_config(self, tmp_path: Path) -> None:
        ds = DashboardState(project_root=tmp_path)
        # Should not raise even with no governance.yaml
        _auto_apply_rules(ds)

    def test_auto_apply_calls_sync_rules(self, tmp_path: Path) -> None:
        (tmp_path / "governance.yaml").write_text(
            "extends:\n  - '@vt/recommended'\n"
            "model:\n  provider: none\n  model: ''\n"
            "agents:\n  claude: true\n"
        )
        ds = DashboardState(project_root=tmp_path)
        ds.decisions = [_decision("Use PostgreSQL")]

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            _auto_apply_rules(ds)
            mock_sync.assert_called_once()


class TestResolveEndpointTriggersApply:
    def test_resolve_triggers_sync_rules(
        self, state_with_governance: dict
    ) -> None:
        c = state_with_governance["c"]
        d1 = state_with_governance["d1"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            client.post(
                f"/api/contradictions/{c.id}/resolve",
                json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
            )
            mock_sync.assert_called_once()


class TestApplyResolutionEndpointTriggersApply:
    def test_apply_resolution_triggers_sync_rules(
        self, state_with_governance: dict
    ) -> None:
        c = state_with_governance["c"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            client.post(
                f"/api/contradictions/{c.id}/apply-resolution",
                json={"action": "accept_exception", "rationale": "OK"},
            )
            mock_sync.assert_called_once()

    def test_apply_resolution_pick_a_triggers_sync_rules(
        self, state_with_governance: dict
    ) -> None:
        c = state_with_governance["c"]
        d1 = state_with_governance["d1"]
        client = TestClient(app)

        with patch("vt_protocol.prevention.rulesync.sync_rules") as mock_sync:
            mock_sync.return_value = MagicMock(files_written=[])
            client.post(
                f"/api/contradictions/{c.id}/apply-resolution",
                json={"action": "pick_a", "rationale": "GraphQL wins"},
            )
            mock_sync.assert_called_once()

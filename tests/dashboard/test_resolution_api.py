"""Tests for resolution paths and calibration dashboard endpoints."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.dashboard.app import DashboardState, app, reset_state, set_state
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    Dimension,
    SourceType,
)


def _decision(title: str, *, dims: list[Dimension] | None = None) -> Decision:
    return Decision(
        title=title,
        content=f"Content for {title}",
        rationale="Good rationale",
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="test",
        source_type=SourceType.MANUAL,
    )


def _contradiction(
    d1: Decision, d2: Decision,
    *, verdict: ContradictionVerdict = ContradictionVerdict.CONTRADICTION,
) -> Contradiction:
    return Contradiction(
        decision_a_id=d1.id,
        decision_b_id=d2.id,
        decision_a_title=d1.title,
        decision_b_title=d2.title,
        verdict=verdict,
        reasoning="They conflict",
        evidence_a="A says X",
        evidence_b="B says Y",
        shared_dimensions=list(set(d1.dimensions) & set(d2.dimensions)),
        confidence=0.85,
    )


@pytest.fixture()
def setup_state(tmp_path: Path):
    d1 = _decision("Use PostgreSQL")
    d2 = _decision("Use MongoDB")
    d3 = _decision("Use REST", dims=[Dimension.API_STYLE])

    c1 = _contradiction(d1, d2, verdict=ContradictionVerdict.CONTRADICTION)
    c2 = _contradiction(d1, d3, verdict=ContradictionVerdict.TENSION)

    ds = DashboardState(project_root=tmp_path)
    ds.decisions = [d1, d2, d3]
    ds.contradictions = [c1, c2]
    ds._merkle = MerkleTree(check_same_thread=False)

    # Create .smm directory for persistence
    (tmp_path / ".smm" / "contradictions").mkdir(parents=True, exist_ok=True)

    set_state(ds)
    yield {"d1": d1, "d2": d2, "d3": d3, "c1": c1, "c2": c2}
    reset_state()


@pytest.fixture()
def client(setup_state) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Resolution paths endpoint
# ---------------------------------------------------------------------------


class TestResolutionPathsEndpoint:
    def test_contradiction_paths(self, client: TestClient, setup_state: dict) -> None:
        c1 = setup_state["c1"]
        resp = client.get(f"/api/contradictions/{c1.id}/resolution-paths")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["paths"]) == 3
        actions = {p["action"] for p in data["paths"]}
        assert "pick_a" in actions
        assert "pick_b" in actions
        assert "accept_exception" in actions

    def test_tension_paths(self, client: TestClient, setup_state: dict) -> None:
        c2 = setup_state["c2"]
        resp = client.get(f"/api/contradictions/{c2.id}/resolution-paths")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["paths"]) == 3
        actions = {p["action"] for p in data["paths"]}
        assert "accept_exception" in actions
        assert "update_decision" in actions
        assert "defer" in actions

    def test_not_found(self, client: TestClient) -> None:
        resp = client.get(f"/api/contradictions/{uuid4()}/resolution-paths")
        assert resp.status_code == 404

    def test_invalid_uuid(self, client: TestClient) -> None:
        resp = client.get("/api/contradictions/not-a-uuid/resolution-paths")
        assert resp.status_code == 400

    def test_path_has_labels(self, client: TestClient, setup_state: dict) -> None:
        c1 = setup_state["c1"]
        resp = client.get(f"/api/contradictions/{c1.id}/resolution-paths")
        data = resp.json()
        for p in data["paths"]:
            assert p["label"]
            assert p["description"]
            assert p["impact"] in ("low", "medium", "high")


# ---------------------------------------------------------------------------
# Apply resolution endpoint
# ---------------------------------------------------------------------------


class TestApplyResolutionEndpoint:
    def test_pick_a(self, client: TestClient, setup_state: dict) -> None:
        c1 = setup_state["c1"]
        resp = client.post(
            f"/api/contradictions/{c1.id}/apply-resolution",
            json={"action": "pick_a", "rationale": "PostgreSQL is better"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"
        assert data["winner_id"] == str(setup_state["d1"].id)

    def test_accept_exception(self, client: TestClient, setup_state: dict) -> None:
        c1 = setup_state["c1"]
        resp = client.post(
            f"/api/contradictions/{c1.id}/apply-resolution",
            json={"action": "accept_exception", "rationale": "Known trade-off"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"

    def test_defer(self, client: TestClient, setup_state: dict) -> None:
        c2 = setup_state["c2"]
        resp = client.post(
            f"/api/contradictions/{c2.id}/apply-resolution",
            json={"action": "defer", "rationale": "Will revisit next sprint"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deferred"

    def test_not_found(self, client: TestClient) -> None:
        resp = client.post(
            f"/api/contradictions/{uuid4()}/apply-resolution",
            json={"action": "pick_a", "rationale": "test"},
        )
        assert resp.status_code == 404

    def test_invalid_uuid(self, client: TestClient) -> None:
        resp = client.post(
            "/api/contradictions/not-a-uuid/apply-resolution",
            json={"action": "pick_a", "rationale": "test"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Calibration endpoint
# ---------------------------------------------------------------------------


class TestCalibrationEndpoint:
    def test_no_calibration_data(self, client: TestClient) -> None:
        resp = client.get("/api/calibration")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"] is None
        assert "No calibration data" in data["message"]

    def test_with_calibration_data(self, client: TestClient, setup_state: dict, tmp_path: Path) -> None:
        from vt_protocol.decisions.calibration import CalibrationStore

        # Create calibration data
        db_path = tmp_path / ".smm" / "calibration.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = CalibrationStore(db_path)
        for i in range(10):
            store.record(f"c{i}", "contradiction", 0.85, "contradiction")
        store.close()

        resp = client.get("/api/calibration")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metrics"] is not None
        assert data["metrics"]["total_records"] == 10
        assert data["metrics"]["accuracy"] == 1.0

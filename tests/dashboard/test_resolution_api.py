"""Tests for resolution paths and calibration dashboard endpoints."""

from __future__ import annotations

import json
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
    DecisionStatus,
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


@pytest.fixture()
def disk_state(tmp_path: Path):
    """State with decision files on disk using vt-init naming convention."""
    d1 = _decision("Detected: GraphQL API", dims=[Dimension.API_STYLE])
    d2 = _decision("Detected: REST API", dims=[Dimension.API_STYLE])

    c1 = _contradiction(d1, d2, verdict=ContradictionVerdict.CONTRADICTION)

    # Write decisions to disk with vt-init naming (no UUID in filename)
    decisions_dir = tmp_path / ".smm" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    (decisions_dir / "001-api-graphql.json").write_text(d1.model_dump_json(indent=2))
    (decisions_dir / "002-api-rest.json").write_text(d2.model_dump_json(indent=2))

    # Write contradiction to disk
    contradictions_dir = tmp_path / ".smm" / "contradictions"
    contradictions_dir.mkdir(parents=True, exist_ok=True)
    (contradictions_dir / f"{str(c1.id)[:8]}.json").write_text(
        c1.model_dump_json(indent=2)
    )

    ds = DashboardState(project_root=tmp_path)
    ds.decisions = [d1, d2]
    ds.contradictions = [c1]
    ds._merkle = MerkleTree(check_same_thread=False)

    set_state(ds)
    yield {"d1": d1, "d2": d2, "c1": c1, "tmp_path": tmp_path}
    reset_state()


@pytest.fixture()
def disk_client(disk_state) -> TestClient:
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
# Defer endpoint
# ---------------------------------------------------------------------------


class TestDeferEndpoint:
    def test_defer_marks_status_deferred(self, client: TestClient, setup_state: dict) -> None:
        c1 = setup_state["c1"]
        resp = client.post(f"/api/contradictions/{c1.id}/defer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deferred"
        assert data["id"] == str(c1.id)

    def test_defer_updates_contradiction_in_state(self, client: TestClient, setup_state: dict) -> None:
        c1 = setup_state["c1"]
        client.post(f"/api/contradictions/{c1.id}/defer")
        assert c1.status.value == "deferred"
        assert c1.resolved_by == "dashboard-user"
        assert c1.resolved_at is None  # No resolved_at for deferred

    def test_defer_persists_to_disk(self, client: TestClient, disk_state: dict) -> None:
        c1 = disk_state["c1"]
        client.post(f"/api/contradictions/{c1.id}/defer")

        # _save_contradiction writes to canonical contradiction-{uuid[:8]}.json
        canonical = disk_state["tmp_path"] / ".smm" / "contradictions" / f"contradiction-{str(c1.id)[:8]}.json"
        c_data = json.loads(canonical.read_text())
        assert c_data["status"] == "deferred"

    def test_defer_deletes_lock(self, client: TestClient, setup_state: dict) -> None:
        from vt_protocol.dashboard.app import get_state
        state = get_state()
        lock = state.project_root / ".smm" / "contradiction.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('{"contradiction_id": "abc"}')

        c1 = setup_state["c1"]
        client.post(f"/api/contradictions/{c1.id}/defer")

        assert not lock.exists()

    def test_defer_does_not_supersede_either_decision(self, client: TestClient, setup_state: dict) -> None:
        c1 = setup_state["c1"]
        d1 = setup_state["d1"]
        d2 = setup_state["d2"]
        client.post(f"/api/contradictions/{c1.id}/defer")
        assert d1.valid is True
        assert d2.valid is True

    def test_defer_not_found(self, client: TestClient) -> None:
        resp = client.post(f"/api/contradictions/{uuid4()}/defer")
        assert resp.status_code == 404

    def test_defer_invalid_uuid(self, client: TestClient) -> None:
        resp = client.post("/api/contradictions/not-a-uuid/defer")
        assert resp.status_code == 400

    def test_deferred_visible_via_status_filter(self, client: TestClient, setup_state: dict) -> None:
        """After deferring, GET /contradictions?status=deferred returns it."""
        c1 = setup_state["c1"]
        client.post(f"/api/contradictions/{c1.id}/defer")

        resp = client.get("/api/contradictions?status=deferred")
        assert resp.status_code == 200
        data = resp.json()
        ids = [c["id"] for c in data["contradictions"]]
        assert str(c1.id) in ids

    def test_deferred_not_in_unresolved_list(self, client: TestClient, setup_state: dict) -> None:
        """After deferring, the contradiction no longer appears in the default unresolved list."""
        c1 = setup_state["c1"]
        client.post(f"/api/contradictions/{c1.id}/defer")

        resp = client.get("/api/contradictions")  # default = unresolved
        data = resp.json()
        ids = [c["id"] for c in data["contradictions"]]
        assert str(c1.id) not in ids


# ---------------------------------------------------------------------------
# Bug 1: Losing decision must be deactivated on resolve
# ---------------------------------------------------------------------------


class TestResolveDeactivatesLoser:
    """When Accept A / Accept B is clicked, the losing decision must be
    updated to status=superseded, valid=false in both memory and on disk."""

    def test_resolve_endpoint_supersedes_loser(self, client: TestClient, setup_state: dict) -> None:
        """POST /resolve with winner_id should set loser to superseded."""
        c1 = setup_state["c1"]
        d1 = setup_state["d1"]  # winner
        d2 = setup_state["d2"]  # loser

        resp = client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "PostgreSQL is better"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["loser_id"] == str(d2.id)
        assert data["loser_superseded"] is True

        # In-memory: loser should be superseded
        assert d2.status == DecisionStatus.SUPERSEDED
        assert d2.valid is False

        # Winner should still be active
        assert d1.status == DecisionStatus.ACTIVE
        assert d1.valid is True

    def test_resolve_endpoint_persists_loser_to_disk(self, client: TestClient, setup_state: dict, tmp_path: Path) -> None:
        """Losing decision JSON file should be updated on disk."""
        c1 = setup_state["c1"]
        d1 = setup_state["d1"]
        d2 = setup_state["d2"]

        # Write decision files to disk first
        decisions_dir = tmp_path / ".smm" / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        d2_file = decisions_dir / f"{str(d2.id)[:8]}.json"
        d2_file.write_text(d2.model_dump_json(indent=2))

        resp = client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "PostgreSQL is better"},
        )
        assert resp.status_code == 200

        # Check disk
        saved = json.loads(d2_file.read_text())
        assert saved["status"] == "superseded"
        assert saved["valid"] is False

    def test_resolve_accept_b_supersedes_a(self, client: TestClient, setup_state: dict) -> None:
        """Picking B as winner should supersede A."""
        c1 = setup_state["c1"]
        d1 = setup_state["d1"]
        d2 = setup_state["d2"]

        resp = client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d2.id), "rationale": "MongoDB is better"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["loser_id"] == str(d1.id)

        # d1 is the loser now
        assert d1.status == DecisionStatus.SUPERSEDED
        assert d1.valid is False
        # d2 is the winner
        assert d2.status == DecisionStatus.ACTIVE

    def test_apply_resolution_pick_a_supersedes_b(self, client: TestClient, setup_state: dict, tmp_path: Path) -> None:
        """apply-resolution with pick_a should persist loser to disk."""
        c1 = setup_state["c1"]
        d2 = setup_state["d2"]

        # Write decision file
        decisions_dir = tmp_path / ".smm" / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        d2_file = decisions_dir / f"{str(d2.id)[:8]}.json"
        d2_file.write_text(d2.model_dump_json(indent=2))

        resp = client.post(
            f"/api/contradictions/{c1.id}/apply-resolution",
            json={"action": "pick_a", "rationale": "PostgreSQL wins"},
        )
        assert resp.status_code == 200

        # Check in-memory
        assert d2.valid is False

        # Check disk
        saved = json.loads(d2_file.read_text())
        assert saved["status"] == "superseded"
        assert saved["valid"] is False

    def test_apply_resolution_pick_b_supersedes_a(self, client: TestClient, setup_state: dict, tmp_path: Path) -> None:
        """apply-resolution with pick_b should persist loser to disk."""
        c1 = setup_state["c1"]
        d1 = setup_state["d1"]

        decisions_dir = tmp_path / ".smm" / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        d1_file = decisions_dir / f"{str(d1.id)[:8]}.json"
        d1_file.write_text(d1.model_dump_json(indent=2))

        resp = client.post(
            f"/api/contradictions/{c1.id}/apply-resolution",
            json={"action": "pick_b", "rationale": "MongoDB wins"},
        )
        assert resp.status_code == 200

        saved = json.loads(d1_file.read_text())
        assert saved["status"] == "superseded"
        assert saved["valid"] is False

    def test_accept_exception_does_not_supersede(self, client: TestClient, setup_state: dict) -> None:
        """accept_exception should NOT supersede either decision."""
        c1 = setup_state["c1"]
        d1 = setup_state["d1"]
        d2 = setup_state["d2"]

        resp = client.post(
            f"/api/contradictions/{c1.id}/apply-resolution",
            json={"action": "accept_exception", "rationale": "Known trade-off"},
        )
        assert resp.status_code == 200

        # Both should remain active
        assert d1.status == DecisionStatus.ACTIVE
        assert d1.valid is True
        assert d2.status == DecisionStatus.ACTIVE
        assert d2.valid is True


# ---------------------------------------------------------------------------
# Exact user flow: vt-init filenames on disk
# ---------------------------------------------------------------------------


class TestResolveWithVtInitFilenames:
    """Reproduces the exact bug: decision files named 001-api-graphql.json
    (no UUID in filename) were not updated by _save_decision()."""

    def test_accept_a_updates_loser_on_disk(self, disk_client: TestClient, disk_state: dict) -> None:
        """Accept A (GraphQL wins) must update 002-api-rest.json on disk."""
        c1 = disk_state["c1"]
        d1 = disk_state["d1"]  # GraphQL — winner
        d2 = disk_state["d2"]  # REST — loser
        tmp_path = disk_state["tmp_path"]

        resp = disk_client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["loser_superseded"] is True

        # THE CRITICAL CHECK: the original file must be updated
        rest_file = tmp_path / ".smm" / "decisions" / "002-api-rest.json"
        saved = json.loads(rest_file.read_text())
        assert saved["status"] == "superseded", (
            f"002-api-rest.json still has status={saved['status']!r} — "
            "_save_decision() failed to find the file by ID"
        )
        assert saved["valid"] is False

    def test_accept_b_updates_loser_on_disk(self, disk_client: TestClient, disk_state: dict) -> None:
        """Accept B (REST wins) must update 001-api-graphql.json on disk."""
        c1 = disk_state["c1"]
        d1 = disk_state["d1"]  # GraphQL — loser
        d2 = disk_state["d2"]  # REST — winner
        tmp_path = disk_state["tmp_path"]

        resp = disk_client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d2.id), "rationale": "REST wins"},
        )
        assert resp.status_code == 200

        graphql_file = tmp_path / ".smm" / "decisions" / "001-api-graphql.json"
        saved = json.loads(graphql_file.read_text())
        assert saved["status"] == "superseded"
        assert saved["valid"] is False

    def test_winner_file_unchanged(self, disk_client: TestClient, disk_state: dict) -> None:
        """Winner's file should NOT be modified."""
        c1 = disk_state["c1"]
        d1 = disk_state["d1"]  # GraphQL — winner
        tmp_path = disk_state["tmp_path"]

        disk_client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
        )

        graphql_file = tmp_path / ".smm" / "decisions" / "001-api-graphql.json"
        saved = json.loads(graphql_file.read_text())
        assert saved["status"] == "active"
        assert saved["valid"] is True

    def test_no_duplicate_file_created(self, disk_client: TestClient, disk_state: dict) -> None:
        """_save_decision must overwrite in place, not create a new UUID-named file."""
        c1 = disk_state["c1"]
        d1 = disk_state["d1"]
        tmp_path = disk_state["tmp_path"]

        disk_client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
        )

        decisions_dir = tmp_path / ".smm" / "decisions"
        files = list(decisions_dir.glob("*.json"))
        assert len(files) == 2, (
            f"Expected 2 decision files, got {len(files)}: "
            f"{[f.name for f in files]}"
        )

    def test_vt_apply_excludes_superseded(self, disk_client: TestClient, disk_state: dict) -> None:
        """After resolve, _load_local_decisions should see the loser as superseded."""
        c1 = disk_state["c1"]
        d1 = disk_state["d1"]
        tmp_path = disk_state["tmp_path"]

        disk_client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "GraphQL wins"},
        )

        # Simulate what vt apply does: reload decisions from disk
        from vt_protocol.cli.commands import _load_local_decisions

        decisions = _load_local_decisions(tmp_path)
        active = [d for d in decisions if d.valid]
        assert len(active) == 1
        assert active[0].title == "Detected: GraphQL API"


# ---------------------------------------------------------------------------
# Bug 1 regression: full disk round-trip with realistic filenames
# ---------------------------------------------------------------------------


class TestResolveFullDiskRoundTrip:
    """End-to-end: contradiction + decision files on disk with realistic
    naming conventions.  Verifies that clicking Accept A persists both the
    contradiction status AND the loser decision status to the ORIGINAL files
    (no duplicate files created)."""

    @pytest.fixture()
    def full_disk(self, tmp_path: Path):
        d1 = _decision("Use PostgreSQL")
        d2 = _decision("Use MongoDB")
        c1 = _contradiction(d1, d2)

        # Write decisions with vt-init naming
        decisions_dir = tmp_path / ".smm" / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        (decisions_dir / "001-database-postgresql.json").write_text(
            d1.model_dump_json(indent=2)
        )
        (decisions_dir / "001-database-nosql.json").write_text(
            d2.model_dump_json(indent=2)
        )

        # Write contradiction with realistic naming (contradiction-NNN-<id>.json)
        contradictions_dir = tmp_path / ".smm" / "contradictions"
        contradictions_dir.mkdir(parents=True, exist_ok=True)
        contra_filename = f"contradiction-001-{str(c1.id)[:8]}.json"
        (contradictions_dir / contra_filename).write_text(
            c1.model_dump_json(indent=2)
        )

        ds = DashboardState(project_root=tmp_path)
        ds.decisions = [d1, d2]
        ds.contradictions = [c1]
        ds._merkle = MerkleTree(check_same_thread=False)

        set_state(ds)
        yield {
            "d1": d1, "d2": d2, "c1": c1,
            "tmp_path": tmp_path,
            "contra_filename": contra_filename,
        }
        reset_state()

    def test_resolve_persists_loser_decision_to_disk(self, full_disk: dict) -> None:
        """POST /resolve must write status=superseded to the loser's JSON file."""
        client = TestClient(app)
        c1 = full_disk["c1"]
        d1 = full_disk["d1"]  # winner
        d2 = full_disk["d2"]  # loser
        tmp_path = full_disk["tmp_path"]

        resp = client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "PostgreSQL wins"},
        )
        assert resp.status_code == 200

        # Loser file must have status=superseded, valid=false
        loser_file = tmp_path / ".smm" / "decisions" / "001-database-nosql.json"
        saved = json.loads(loser_file.read_text())
        assert saved["status"] == "superseded", (
            f"Loser file still has status={saved['status']!r} — "
            "_save_decision failed to update the original file"
        )
        assert saved["valid"] is False

    def test_resolve_persists_contradiction_to_original_file(self, full_disk: dict) -> None:
        """The original contradiction file must be updated in place (not a new file)."""
        client = TestClient(app)
        c1 = full_disk["c1"]
        d1 = full_disk["d1"]
        tmp_path = full_disk["tmp_path"]
        contra_filename = full_disk["contra_filename"]

        resp = client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "PostgreSQL wins"},
        )
        assert resp.status_code == 200

        # The ORIGINAL file must be updated
        contra_file = tmp_path / ".smm" / "contradictions" / contra_filename
        saved = json.loads(contra_file.read_text())
        assert saved["status"] == "resolved", (
            f"Original contradiction file still has status={saved['status']!r} — "
            "_save_contradiction wrote to a different filename"
        )

    def test_no_duplicate_contradiction_file(self, full_disk: dict) -> None:
        """_save_contradiction must NOT create a second file alongside the original."""
        client = TestClient(app)
        c1 = full_disk["c1"]
        d1 = full_disk["d1"]
        tmp_path = full_disk["tmp_path"]

        client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "PostgreSQL wins"},
        )

        contra_dir = tmp_path / ".smm" / "contradictions"
        files = list(contra_dir.glob("*.json"))
        assert len(files) == 1, (
            f"Expected 1 contradiction file, got {len(files)}: "
            f"{[f.name for f in files]} — _save_contradiction created a duplicate"
        )

    def test_reload_after_resolve_shows_no_unresolved(self, full_disk: dict) -> None:
        """After resolve, reloading contradictions from disk must show zero unresolved."""
        client = TestClient(app)
        c1 = full_disk["c1"]
        d1 = full_disk["d1"]

        client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "PostgreSQL wins"},
        )

        # This endpoint reloads from disk
        resp = client.get("/api/contradictions?status=unresolved")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0, (
            f"Still {data['total']} unresolved contradictions after resolve — "
            "stale file not updated on disk"
        )

    def test_no_duplicate_decision_file(self, full_disk: dict) -> None:
        """_save_decision must overwrite in place, not create a new UUID-named file."""
        client = TestClient(app)
        c1 = full_disk["c1"]
        d1 = full_disk["d1"]
        tmp_path = full_disk["tmp_path"]

        client.post(
            f"/api/contradictions/{c1.id}/resolve",
            json={"winner_id": str(d1.id), "rationale": "PostgreSQL wins"},
        )

        decisions_dir = tmp_path / ".smm" / "decisions"
        files = list(decisions_dir.glob("*.json"))
        assert len(files) == 2, (
            f"Expected 2 decision files, got {len(files)}: "
            f"{[f.name for f in files]}"
        )


# ---------------------------------------------------------------------------
# Bug 2: Decision Ledger must show all dimensions
# ---------------------------------------------------------------------------


class TestGraphShowsAllDimensions:
    """The /api/graph endpoint must reload decisions from disk and return
    ALL dimensions, not just a hardcoded subset."""

    @pytest.fixture()
    def multi_dim_disk(self, tmp_path: Path):
        """Create decisions spanning many dimensions on disk."""
        decisions = []
        specs = [
            ("Use PostgreSQL", [Dimension.DATABASE]),
            ("Use REST", [Dimension.API_STYLE]),
            ("Use threading", [Dimension.CONCURRENCY]),
            ("Use Redis caching", [Dimension.CACHING]),
            ("Use RabbitMQ", [Dimension.MESSAGING]),
            ("Use pytest", [Dimension.TESTING]),
            ("Use OAuth", [Dimension.AUTH]),
            ("Use TLS", [Dimension.SECURITY]),
        ]
        decisions_dir = tmp_path / ".smm" / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        for i, (title, dims) in enumerate(specs, 1):
            d = _decision(title, dims=dims)
            decisions.append(d)
            fname = f"{i:03d}-{dims[0].value}-{title.split()[-1].lower()}.json"
            (decisions_dir / fname).write_text(d.model_dump_json(indent=2))

        ds = DashboardState(project_root=tmp_path)
        ds.decisions = []  # deliberately empty — must reload from disk
        ds.contradictions = []
        ds._merkle = MerkleTree(check_same_thread=False)

        set_state(ds)
        yield {"decisions": decisions, "tmp_path": tmp_path}
        reset_state()

    def test_graph_reloads_from_disk(self, multi_dim_disk: dict) -> None:
        """Graph must show decisions loaded from disk, not stale in-memory state."""
        client = TestClient(app)
        resp = client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 8, (
            f"Expected 8 nodes, got {len(data['nodes'])} — "
            "graph is not reloading decisions from disk"
        )

    def test_graph_includes_all_dimensions(self, multi_dim_disk: dict) -> None:
        """All dimension values present in decisions must appear in graph nodes."""
        client = TestClient(app)
        resp = client.get("/api/graph")
        data = resp.json()
        all_dims = set()
        for node in data["nodes"]:
            all_dims.update(node["data"]["dimensions"])
        expected = {"database", "api-style", "concurrency", "caching",
                    "messaging", "testing", "auth", "security"}
        assert expected <= all_dims, (
            f"Missing dimensions in graph: {expected - all_dims}"
        )

    def test_decisions_api_reloads_from_disk(self, multi_dim_disk: dict) -> None:
        """/api/decisions must return freshly loaded decisions."""
        client = TestClient(app)
        resp = client.get("/api/decisions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 8, (
            f"Expected 8 decisions, got {data['total']} — "
            "/api/decisions not reloading from disk"
        )


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

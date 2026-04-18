"""Test that duplicate contradiction files are handled correctly.

Bug: vt check saves contradiction-001-{id[:8]}.json while the PostToolUse
hook (via vt check again) or _save_contradiction_file creates {id[:8]}.json.
When the dashboard resolves, only ONE file gets updated, leaving the other
"unresolved" and the dashboard keeps showing it.

Fix: canonical filename is contradiction-{id[:8]}.json everywhere.
Save/load functions deduplicate by id and clean up stale duplicates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.decision_factory import make_conflicting_pair, make_contradiction
from vt_protocol.decisions.models import ContradictionStatus


@pytest.fixture
def smm_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".smm" / "contradictions"
    d.mkdir(parents=True)
    return tmp_path


def _write_json(path: Path, contradiction) -> None:
    path.write_text(contradiction.model_dump_json(indent=2))


class TestCanonicalFilename:
    """_save_contradictions (bulk) must use contradiction-{id[:8]}.json."""

    def test_save_contradictions_uses_canonical_name(self, smm_dir: Path):
        from vt_protocol.cli.commands import _save_contradictions

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)

        _save_contradictions(smm_dir, [c])

        files = list((smm_dir / ".smm" / "contradictions").glob("*.json"))
        assert len(files) == 1
        assert files[0].name == f"contradiction-{str(c.id)[:8]}.json"


class TestSaveContradictionFileDedup:
    """_save_contradiction_file must update ALL matching files and remove dupes."""

    def test_updates_both_and_removes_duplicate(self, smm_dir: Path):
        from vt_protocol.cli.commands import _save_contradiction_file

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)
        cdir = smm_dir / ".smm" / "contradictions"

        # Simulate the bug: two files for the same contradiction
        old_numbered = cdir / f"contradiction-001-{str(c.id)[:8]}.json"
        old_bare = cdir / f"{str(c.id)[:8]}.json"
        _write_json(old_numbered, c)
        _write_json(old_bare, c)
        assert len(list(cdir.glob("*.json"))) == 2

        # Now resolve and save
        c.status = ContradictionStatus.RESOLVED
        c.resolved_by = "test"
        c.resolution_note = "test resolution"
        _save_contradiction_file(smm_dir, c)

        # Should have exactly ONE canonical file
        files = list(cdir.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == f"contradiction-{str(c.id)[:8]}.json"
        data = json.loads(files[0].read_text())
        assert data["status"] == "resolved"

    def test_creates_canonical_name_when_no_existing(self, smm_dir: Path):
        from vt_protocol.cli.commands import _save_contradiction_file

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)

        _save_contradiction_file(smm_dir, c)

        files = list((smm_dir / ".smm" / "contradictions").glob("*.json"))
        assert len(files) == 1
        assert files[0].name == f"contradiction-{str(c.id)[:8]}.json"


class TestDashboardSaveContradiction:
    """dashboard _save_contradiction must update all matching files and remove dupes."""

    def test_resolves_both_files(self, smm_dir: Path):
        from vt_protocol.dashboard.app import _save_contradiction

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)
        cdir = smm_dir / ".smm" / "contradictions"

        # Two files: numbered + bare
        _write_json(cdir / f"contradiction-001-{str(c.id)[:8]}.json", c)
        _write_json(cdir / f"{str(c.id)[:8]}.json", c)

        # Resolve
        c.status = ContradictionStatus.RESOLVED
        c.resolved_by = "dashboard"
        _save_contradiction(smm_dir, c)

        files = list(cdir.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == f"contradiction-{str(c.id)[:8]}.json"
        data = json.loads(files[0].read_text())
        assert data["status"] == "resolved"


class TestLoadContradictionsDedup:
    """_load_contradictions must deduplicate by id field."""

    def test_dedup_returns_one_entry(self, smm_dir: Path):
        from vt_protocol.dashboard.app import _load_contradictions

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)
        cdir = smm_dir / ".smm" / "contradictions"

        # Write two files with same id
        _write_json(cdir / f"contradiction-001-{str(c.id)[:8]}.json", c)
        _write_json(cdir / f"{str(c.id)[:8]}.json", c)

        loaded = _load_contradictions(smm_dir)
        assert len(loaded) == 1
        assert str(loaded[0].id) == str(c.id)

    def test_dedup_prefers_resolved(self, smm_dir: Path):
        from vt_protocol.dashboard.app import _load_contradictions

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)
        cdir = smm_dir / ".smm" / "contradictions"

        # Unresolved version
        _write_json(cdir / f"aaa-{str(c.id)[:8]}.json", c)

        # Resolved version (same id)
        c.status = ContradictionStatus.RESOLVED
        c.resolved_by = "test"
        _write_json(cdir / f"zzz-{str(c.id)[:8]}.json", c)

        loaded = _load_contradictions(smm_dir)
        assert len(loaded) == 1
        assert loaded[0].status == ContradictionStatus.RESOLVED


class TestLoadLocalContradictionsDedup:
    """_load_local_contradictions (cli) must also deduplicate by id field."""

    def test_dedup_returns_one_entry(self, smm_dir: Path):
        from vt_protocol.cli.commands import _load_local_contradictions

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)
        cdir = smm_dir / ".smm" / "contradictions"

        _write_json(cdir / f"contradiction-001-{str(c.id)[:8]}.json", c)
        _write_json(cdir / f"{str(c.id)[:8]}.json", c)

        loaded = _load_local_contradictions(smm_dir)
        assert len(loaded) == 1
        assert str(loaded[0].id) == str(c.id)

    def test_dedup_prefers_resolved(self, smm_dir: Path):
        from vt_protocol.cli.commands import _load_local_contradictions

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)
        cdir = smm_dir / ".smm" / "contradictions"

        _write_json(cdir / f"aaa-{str(c.id)[:8]}.json", c)

        c.status = ContradictionStatus.RESOLVED
        c.resolved_by = "test"
        _write_json(cdir / f"zzz-{str(c.id)[:8]}.json", c)

        loaded = _load_local_contradictions(smm_dir)
        assert len(loaded) == 1
        assert loaded[0].status == ContradictionStatus.RESOLVED


class TestEndToEndFlow:
    """Full flow: vt check creates file, hook creates dupe, dashboard resolves, all clear."""

    def test_full_duplicate_resolve_flow(self, smm_dir: Path):
        from vt_protocol.cli.commands import _save_contradictions
        from vt_protocol.dashboard.app import _load_contradictions, _save_contradiction

        d_a, d_b = make_conflicting_pair()
        c = make_contradiction(d_a, d_b)
        cdir = smm_dir / ".smm" / "contradictions"

        # Step 1: vt check creates canonical file
        _save_contradictions(smm_dir, [c])
        files = list(cdir.glob("*.json"))
        assert len(files) == 1

        # Step 2: Simulate hook also creating a bare-name file (the old bug)
        bare_file = cdir / f"{str(c.id)[:8]}.json"
        _write_json(bare_file, c)
        assert len(list(cdir.glob("*.json"))) == 2

        # Step 3: Dashboard loads — should see exactly 1
        loaded = _load_contradictions(smm_dir)
        assert len(loaded) == 1

        # Step 4: Dashboard resolves
        resolved = loaded[0]
        resolved.status = ContradictionStatus.RESOLVED
        resolved.resolved_by = "dashboard"
        resolved.resolution_note = "Chose PostgreSQL"
        _save_contradiction(smm_dir, resolved)

        # Step 5: Only ONE file remains, and it's resolved
        files = list(cdir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["status"] == "resolved"

        # Step 6: Dashboard shows "All Clear"
        reloaded = _load_contradictions(smm_dir)
        actionable = [c for c in reloaded if c.is_actionable]
        assert actionable == [], "Dashboard should show All Clear"

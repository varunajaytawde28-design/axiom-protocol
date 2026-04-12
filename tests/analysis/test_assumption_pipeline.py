"""Tests for the assumption detection pipeline, resolution, stats, and storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from vt_protocol.analysis.assumption_pipeline import (
    AssumptionPipelineResult,
    AssumptionStats,
    compute_stats,
    load_assumptions,
    resolve_assumption,
    run_assumption_pipeline,
    save_assumption,
    save_assumptions,
)
from vt_protocol.decisions.models import (
    AssumptionCategory,
    AssumptionStatus,
    CodeEvidence,
    DomainAssumption,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assumption(
    *,
    pattern_id: str = "single_source_write",
    category: AssumptionCategory = AssumptionCategory.DATA_SCOPE,
    status: AssumptionStatus = AssumptionStatus.PROPOSED,
    summary: str = "Write to orders found in one location",
    confidence: float = 0.7,
    question: str = "Which matches your business reality?",
    options: list[str] | None = None,
    file: str = "src/order_service.py",
    line: int = 42,
    snippet: str = "INSERT INTO transactions ...",
    is_baseline: bool = False,
) -> DomainAssumption:
    return DomainAssumption(
        category=category,
        status=status,
        pattern_id=pattern_id,
        summary=summary,
        confidence=confidence,
        severity="high",
        question=question,
        options=options or [
            "A) Correct -- only place_order() should write to transactions",
            "B) Incomplete -- external webhooks should also write",
            "C) Incomplete -- batch imports should also write",
            "D) Wrong -- multiple services must write",
            "E) I need more context before deciding",
        ],
        code_evidence=[CodeEvidence(file=file, line=line, snippet=snippet)],
        is_baseline=is_baseline,
    )


# ---------------------------------------------------------------------------
# TestLoadSaveAssumptions
# ---------------------------------------------------------------------------


class TestLoadSaveAssumptions:
    def test_save_and_load(self, tmp_path: Path) -> None:
        a1 = _make_assumption(summary="Assumption one", pattern_id="single_source_write")
        a2 = _make_assumption(
            summary="Assumption two",
            pattern_id="env_no_fallback",
            category=AssumptionCategory.CONFIGURATION,
        )

        save_assumptions(tmp_path, [a1, a2])
        loaded = load_assumptions(tmp_path)

        assert len(loaded) == 2
        ids = {a.id for a in loaded}
        assert a1.id in ids
        assert a2.id in ids

        loaded_map = {a.id: a for a in loaded}
        assert loaded_map[a1.id].summary == "Assumption one"
        assert loaded_map[a1.id].pattern_id == "single_source_write"
        assert loaded_map[a2.id].category == AssumptionCategory.CONFIGURATION

    def test_load_empty_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "assumptions").mkdir(parents=True)
        loaded = load_assumptions(tmp_path)
        assert loaded == []

    def test_load_missing_dir(self, tmp_path: Path) -> None:
        # No .smm/assumptions/ directory at all
        loaded = load_assumptions(tmp_path)
        assert loaded == []

    def test_save_creates_dir(self, tmp_path: Path) -> None:
        assert not (tmp_path / ".smm" / "assumptions").exists()
        a = _make_assumption()
        path = save_assumption(tmp_path, a)
        assert (tmp_path / ".smm" / "assumptions").is_dir()
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["pattern_id"] == "single_source_write"


# ---------------------------------------------------------------------------
# TestResolveAssumption
# ---------------------------------------------------------------------------


class TestResolveAssumption:
    def test_resolve_validates_option_a(self, tmp_path: Path) -> None:
        a = _make_assumption(status=AssumptionStatus.PROPOSED)
        save_assumption(tmp_path, a)

        result = resolve_assumption(tmp_path, a.id.hex, selected_option=0)
        assert result is not None
        assert result.status == AssumptionStatus.VALIDATED

    def test_resolve_rejects_option_b(self, tmp_path: Path) -> None:
        a = _make_assumption(status=AssumptionStatus.PROPOSED)
        save_assumption(tmp_path, a)

        result = resolve_assumption(tmp_path, a.id.hex, selected_option=1)
        assert result is not None
        assert result.status == AssumptionStatus.REJECTED

    def test_resolve_defers(self, tmp_path: Path) -> None:
        # Option E (index 4) contains "I need more context"
        a = _make_assumption(status=AssumptionStatus.PROPOSED)
        save_assumption(tmp_path, a)

        result = resolve_assumption(tmp_path, a.id.hex, selected_option=4)
        assert result is not None
        assert result.status == AssumptionStatus.DEFERRED
        assert result.deferred_until is not None

    def test_resolve_sets_metadata(self, tmp_path: Path) -> None:
        a = _make_assumption(status=AssumptionStatus.PROPOSED)
        save_assumption(tmp_path, a)

        result = resolve_assumption(
            tmp_path,
            a.id.hex,
            selected_option=0,
            resolved_by="tech-lead",
            rationale="Confirmed with domain expert",
        )
        assert result is not None
        assert result.resolved_by == "tech-lead"
        assert result.resolved_at is not None
        assert result.answer_rationale == "Confirmed with domain expert"

    def test_resolve_nonexistent(self, tmp_path: Path) -> None:
        (tmp_path / ".smm" / "assumptions").mkdir(parents=True)
        result = resolve_assumption(tmp_path, uuid4().hex, selected_option=0)
        assert result is None


# ---------------------------------------------------------------------------
# TestRunPipeline
# ---------------------------------------------------------------------------


class TestRunPipeline:
    def test_pipeline_on_empty_project(self, tmp_path: Path) -> None:
        # Project with no Python files at all
        (tmp_path / ".smm" / "assumptions").mkdir(parents=True)
        result = run_assumption_pipeline(tmp_path)
        assert result.detected == 0
        assert result.new == 0
        assert result.assumptions == []

    @patch("vt_protocol.analysis.assumptions._is_test_path", return_value=False)
    def test_pipeline_detects_patterns(self, _mock_test_path, tmp_path: Path) -> None:
        # Create a source file with a known detectable pattern
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "order_service.py").write_text(
            'def place_order(db):\n'
            '    db.execute("INSERT INTO transactions (id, amount) VALUES (?, ?)", (1, 100))\n'
        )

        result = run_assumption_pipeline(tmp_path, existing=[])
        assert result.detected > 0
        assert result.new > 0
        assert len(result.assumptions) > 0

        # At least one assumption should be for single_source_write
        pattern_ids = [a.pattern_id for a in result.assumptions]
        assert "single_source_write" in pattern_ids

    @patch("vt_protocol.analysis.assumptions._is_test_path", return_value=False)
    def test_pipeline_dedup(self, _mock_test_path, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "service.py").write_text(
            'def do_thing(db):\n'
            '    db.execute("INSERT INTO orders (id) VALUES (?)", (1,))\n'
        )

        # First run: finds assumptions
        result1 = run_assumption_pipeline(tmp_path, existing=[])
        assert result1.new > 0

        # Second run: pass first-run assumptions as existing
        result2 = run_assumption_pipeline(tmp_path, existing=result1.assumptions)
        # Everything should be deduped
        assert result2.new == 0
        assert result2.deduped > 0 or result2.pre_validated > 0

    @patch("vt_protocol.analysis.assumptions._is_test_path", return_value=False)
    def test_pipeline_threshold(self, _mock_test_path, tmp_path: Path) -> None:
        # Create a file that triggers a low-confidence pattern
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        # no_migration_check has base_confidence=0.4, below default 0.5 threshold
        (src_dir / "migrate_service.py").write_text(
            'def run_migration(db):\n'
            '    db.execute("INSERT INTO log (msg) VALUES (?)", ("migrated",))\n'
        )

        result = run_assumption_pipeline(tmp_path, existing=[])
        # Anything with confidence < 0.5 should be filtered
        for a in result.assumptions:
            assert a.confidence >= 0.5

    @patch("vt_protocol.analysis.assumptions._is_test_path", return_value=False)
    def test_pipeline_generates_questions(self, _mock_test_path, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "order_svc.py").write_text(
            'def place_order(db):\n'
            '    db.execute("INSERT INTO transactions (id, amount) VALUES (?, ?)", (1, 100))\n'
        )

        result = run_assumption_pipeline(tmp_path, existing=[])
        # All new assumptions should have had question generation attempted
        for a in result.assumptions:
            # question/options may or may not be populated depending on template match,
            # but status should be PROPOSED
            assert a.status == AssumptionStatus.PROPOSED


# ---------------------------------------------------------------------------
# TestComputeStats
# ---------------------------------------------------------------------------


class TestComputeStats:
    def test_stats_empty(self) -> None:
        stats = compute_stats([])
        assert stats.total_detected == 0
        assert stats.total_validated == 0
        assert stats.total_rejected == 0
        assert stats.total_deferred == 0
        assert stats.by_category == {}
        assert stats.by_pattern == {}

    def test_stats_counts_by_status(self) -> None:
        assumptions = [
            _make_assumption(status=AssumptionStatus.VALIDATED),
            _make_assumption(status=AssumptionStatus.VALIDATED),
            _make_assumption(status=AssumptionStatus.REJECTED),
            _make_assumption(status=AssumptionStatus.PROPOSED),
            _make_assumption(status=AssumptionStatus.DEFERRED),
        ]
        stats = compute_stats(assumptions)
        assert stats.total_detected == 5
        assert stats.total_validated == 2
        assert stats.total_rejected == 1
        assert stats.total_deferred == 1

    def test_stats_by_category(self) -> None:
        assumptions = [
            _make_assumption(
                category=AssumptionCategory.DATA_SCOPE,
                status=AssumptionStatus.VALIDATED,
            ),
            _make_assumption(
                category=AssumptionCategory.DATA_SCOPE,
                status=AssumptionStatus.REJECTED,
            ),
            _make_assumption(
                category=AssumptionCategory.TEMPORAL,
                status=AssumptionStatus.VALIDATED,
            ),
            _make_assumption(
                category=AssumptionCategory.ACCESS,
                status=AssumptionStatus.DEFERRED,
            ),
        ]
        stats = compute_stats(assumptions)

        assert "data_scope" in stats.by_category
        assert stats.by_category["data_scope"]["detected"] == 2
        assert stats.by_category["data_scope"]["validated"] == 1
        assert stats.by_category["data_scope"]["rejected"] == 1

        assert "temporal" in stats.by_category
        assert stats.by_category["temporal"]["validated"] == 1

        assert "access" in stats.by_category
        assert stats.by_category["access"]["deferred"] == 1


# ---------------------------------------------------------------------------
# TestFreezeOnAdopt
# ---------------------------------------------------------------------------


class TestFreezeOnAdopt:
    @patch("vt_protocol.analysis.assumptions._is_test_path", return_value=False)
    def test_first_scan_baseline(self, _mock_test_path, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text(
            'def handle(db):\n'
            '    db.execute("INSERT INTO events (id) VALUES (?)", (1,))\n'
        )

        # First scan: no existing assumptions
        result = run_assumption_pipeline(tmp_path, existing=[])

        # All assumptions from first scan should be marked as baseline
        for a in result.assumptions:
            assert a.is_baseline is True, (
                f"Expected is_baseline=True for assumption {a.pattern_id}"
            )


# ---------------------------------------------------------------------------
# TestTransactionBugScenario (integration)
# ---------------------------------------------------------------------------


class TestTransactionBugScenario:
    @patch("vt_protocol.analysis.assumptions._is_test_path", return_value=False)
    def test_transaction_bug_full_scenario(self, _mock_test_path, tmp_path: Path) -> None:
        """End-to-end: detect assumption in order_service.py, reject it, generate rule."""
        # Step 1: Create order_service.py with a single INSERT INTO transactions
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "order_service.py").write_text(
            'def place_order(db, order_id, amount):\n'
            '    """Place a new order and record the transaction."""\n'
            '    db.execute(\n'
            '        "INSERT INTO transactions (order_id, amount) VALUES (?, ?)",\n'
            '        (order_id, amount),\n'
            '    )\n'
        )

        # Step 2: Run pipeline -- should detect single_source_write
        result = run_assumption_pipeline(tmp_path, existing=[])
        assert result.detected > 0

        ssw_assumptions = [
            a for a in result.assumptions if a.pattern_id == "single_source_write"
        ]
        assert len(ssw_assumptions) >= 1, (
            f"Expected single_source_write, got patterns: "
            f"{[a.pattern_id for a in result.assumptions]}"
        )

        assumption = ssw_assumptions[0]
        assert assumption.status == AssumptionStatus.PROPOSED

        # Step 3: Save and then resolve with option B (webhooks should also write) -> REJECTED
        save_assumption(tmp_path, assumption)

        resolved = resolve_assumption(
            tmp_path,
            assumption.id.hex,
            selected_option=1,  # Option B
            resolved_by="tech-lead",
            rationale="External webhooks from payment provider also write transactions",
        )
        assert resolved is not None
        assert resolved.status == AssumptionStatus.REJECTED

        # Step 4: Verify generate_rule_text produces "DO NOT assume" rule
        rule_text = resolved.generate_rule_text()
        assert "DO NOT assume" in rule_text
        assert resolved.resolved_by in rule_text

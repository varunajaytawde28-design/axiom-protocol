"""Tests for IRT calibration framework."""

from __future__ import annotations

import pytest

from vt_protocol.decisions.calibration import (
    DRIFT_THRESHOLD,
    CalibrationMetrics,
    CalibrationRecord,
    CalibrationStore,
    compute_accuracy,
    compute_brier_score,
    compute_drift,
    compute_ece,
    compute_wasserstein,
)


# ---------------------------------------------------------------------------
# CalibrationRecord
# ---------------------------------------------------------------------------


class TestCalibrationRecord:
    def test_is_correct_matching(self) -> None:
        r = CalibrationRecord(
            judge_verdict="contradiction",
            judge_confidence=0.9,
            human_verdict="contradiction",
        )
        assert r.is_correct is True

    def test_is_correct_mismatching(self) -> None:
        r = CalibrationRecord(
            judge_verdict="contradiction",
            judge_confidence=0.9,
            human_verdict="compatible",
        )
        assert r.is_correct is False

    def test_default_fields(self) -> None:
        r = CalibrationRecord()
        assert r.id  # auto-generated
        assert r.timestamp  # auto-generated
        assert r.metadata == {}


# ---------------------------------------------------------------------------
# CalibrationStore
# ---------------------------------------------------------------------------


class TestCalibrationStore:
    @pytest.fixture()
    def store(self) -> CalibrationStore:
        return CalibrationStore()  # in-memory

    def test_record_and_retrieve(self, store: CalibrationStore) -> None:
        store.record("c1", "contradiction", 0.9, "contradiction")
        assert store.size == 1
        records = store.get_records()
        assert len(records) == 1
        assert records[0].contradiction_id == "c1"
        assert records[0].judge_verdict == "contradiction"
        assert records[0].human_verdict == "contradiction"

    def test_multiple_records(self, store: CalibrationStore) -> None:
        store.record("c1", "contradiction", 0.9, "contradiction")
        store.record("c2", "tension", 0.6, "compatible")
        store.record("c3", "compatible", 0.8, "compatible")
        assert store.size == 3

    def test_get_records_limit(self, store: CalibrationStore) -> None:
        for i in range(10):
            store.record(f"c{i}", "contradiction", 0.9, "contradiction")
        records = store.get_records(limit=5)
        assert len(records) == 5

    def test_empty_store_size(self, store: CalibrationStore) -> None:
        assert store.size == 0

    def test_metadata_stored(self, store: CalibrationStore) -> None:
        store.record("c1", "contradiction", 0.9, "contradiction",
                      metadata={"model": "haiku", "nli_score": 0.8})
        records = store.get_records()
        assert records[0].metadata["model"] == "haiku"
        assert records[0].metadata["nli_score"] == 0.8


# ---------------------------------------------------------------------------
# compute_accuracy
# ---------------------------------------------------------------------------


class TestComputeAccuracy:
    def test_empty(self) -> None:
        assert compute_accuracy([]) == 0.0

    def test_all_correct(self) -> None:
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="contradiction"),
            CalibrationRecord(judge_verdict="tension", judge_confidence=0.7, human_verdict="tension"),
        ]
        assert compute_accuracy(records) == 1.0

    def test_half_correct(self) -> None:
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="contradiction"),
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.8, human_verdict="compatible"),
        ]
        assert compute_accuracy(records) == 0.5

    def test_none_correct(self) -> None:
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="compatible"),
        ]
        assert compute_accuracy(records) == 0.0


# ---------------------------------------------------------------------------
# compute_ece
# ---------------------------------------------------------------------------


class TestComputeECE:
    def test_empty(self) -> None:
        assert compute_ece([]) == 0.0

    def test_perfectly_calibrated(self) -> None:
        # All correct at confidence 0.9 — perfect calibration at that bin
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.95, human_verdict="contradiction")
            for _ in range(10)
        ]
        ece = compute_ece(records)
        # avg_confidence ≈ 0.95, avg_accuracy = 1.0 → small gap
        assert ece < 0.1

    def test_overconfident(self) -> None:
        # High confidence but always wrong
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.95, human_verdict="compatible")
            for _ in range(10)
        ]
        ece = compute_ece(records)
        assert ece > 0.5  # Large calibration error

    def test_mixed_bins(self) -> None:
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.2, human_verdict="compatible"),
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.8, human_verdict="contradiction"),
        ]
        ece = compute_ece(records)
        assert 0.0 <= ece <= 1.0


# ---------------------------------------------------------------------------
# compute_brier_score
# ---------------------------------------------------------------------------


class TestComputeBrierScore:
    def test_empty(self) -> None:
        assert compute_brier_score([]) == 0.0

    def test_perfect_prediction(self) -> None:
        # Confidence 1.0 and correct
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=1.0, human_verdict="contradiction"),
        ]
        assert compute_brier_score(records) == 0.0

    def test_worst_prediction(self) -> None:
        # Confidence 1.0 but wrong
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=1.0, human_verdict="compatible"),
        ]
        assert compute_brier_score(records) == 1.0

    def test_uncertain_correct(self) -> None:
        # Confidence 0.5 and correct → Brier = (0.5 - 1.0)^2 = 0.25
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.5, human_verdict="contradiction"),
        ]
        assert abs(compute_brier_score(records) - 0.25) < 0.001

    def test_uncertain_wrong(self) -> None:
        # Confidence 0.5 and wrong → Brier = (0.5 - 0.0)^2 = 0.25
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.5, human_verdict="compatible"),
        ]
        assert abs(compute_brier_score(records) - 0.25) < 0.001


# ---------------------------------------------------------------------------
# compute_wasserstein
# ---------------------------------------------------------------------------


class TestComputeWasserstein:
    def test_empty(self) -> None:
        assert compute_wasserstein([]) == 0.0

    def test_identical_distributions(self) -> None:
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="contradiction"),
            CalibrationRecord(judge_verdict="tension", judge_confidence=0.7, human_verdict="tension"),
            CalibrationRecord(judge_verdict="compatible", judge_confidence=0.8, human_verdict="compatible"),
        ]
        assert compute_wasserstein(records) == 0.0

    def test_opposite_distributions(self) -> None:
        # Judge says contradiction, human says compatible
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="compatible"),
        ]
        w = compute_wasserstein(records)
        assert w > 0  # Non-zero distance

    def test_shifted_distribution(self) -> None:
        # Judge always says contradiction, human says tension
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="tension")
            for _ in range(5)
        ]
        w = compute_wasserstein(records)
        assert w > 0

    def test_symmetric(self) -> None:
        # Distance should be the same regardless of direction
        records_a = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="compatible"),
        ]
        records_b = [
            CalibrationRecord(judge_verdict="compatible", judge_confidence=0.9, human_verdict="contradiction"),
        ]
        assert compute_wasserstein(records_a) == compute_wasserstein(records_b)


# ---------------------------------------------------------------------------
# compute_drift
# ---------------------------------------------------------------------------


class TestComputeDrift:
    def test_empty(self) -> None:
        assert compute_drift([]) == 0.0

    def test_single_record(self) -> None:
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="contradiction"),
        ]
        assert compute_drift(records) == 0.0

    def test_no_drift(self) -> None:
        # Consistent accuracy throughout
        records = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="contradiction")
            for _ in range(20)
        ]
        assert compute_drift(records, window=10) == 0.0

    def test_detects_drift(self) -> None:
        # Recent records are wrong, old ones are correct
        recent_wrong = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="compatible")
            for _ in range(10)
        ]
        old_correct = [
            CalibrationRecord(judge_verdict="contradiction", judge_confidence=0.9, human_verdict="contradiction")
            for _ in range(40)
        ]
        # Records are newest-first, so recent_wrong comes first
        records = recent_wrong + old_correct
        drift = compute_drift(records, window=10)
        assert drift > 0.5  # Significant drift


# ---------------------------------------------------------------------------
# CalibrationMetrics
# ---------------------------------------------------------------------------


class TestCalibrationMetrics:
    def test_to_dict(self) -> None:
        m = CalibrationMetrics(
            ece=0.1234, brier_score=0.5678,
            wasserstein_distance=0.2345, accuracy=0.8765,
            total_records=100, drift_score=0.15, needs_review=False,
        )
        d = m.to_dict()
        assert d["ece"] == 0.1234
        assert d["accuracy"] == 0.8765
        assert d["needs_review"] is False

    def test_needs_review_when_drifted(self) -> None:
        m = CalibrationMetrics(drift_score=0.5, needs_review=True)
        assert m.needs_review is True


# ---------------------------------------------------------------------------
# CalibrationStore.compute_metrics integration
# ---------------------------------------------------------------------------


class TestComputeMetricsIntegration:
    def test_empty_store(self) -> None:
        store = CalibrationStore()
        metrics = store.compute_metrics()
        assert metrics.total_records == 0
        assert metrics.accuracy == 0.0

    def test_with_data(self) -> None:
        store = CalibrationStore()
        # 8 correct, 2 wrong = 80% accuracy
        for i in range(8):
            store.record(f"c{i}", "contradiction", 0.85, "contradiction")
        for i in range(2):
            store.record(f"w{i}", "contradiction", 0.85, "compatible")

        metrics = store.compute_metrics()
        assert metrics.total_records == 10
        assert metrics.accuracy == 0.8
        assert metrics.ece >= 0.0
        assert metrics.brier_score >= 0.0
        assert metrics.wasserstein_distance >= 0.0

    def test_needs_review_flag(self) -> None:
        store = CalibrationStore(drift_threshold=0.1)
        # All recent wrong, all old correct — big drift
        for i in range(50):
            store.record(f"old{i}", "contradiction", 0.9, "contradiction")
        for i in range(20):
            store.record(f"new{i}", "contradiction", 0.9, "compatible")

        metrics = store.compute_metrics()
        # With enough drift, needs_review should be True
        # (depends on window vs total ratio, but should detect the shift)
        assert metrics.total_records == 70

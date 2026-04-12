"""Gemini Scenario: IRT Calibration Drift Detection.

Tests the CalibrationStore's ability to track judge accuracy over time,
detect drift via rolling window comparison, compute ECE/Brier/Wasserstein
metrics, and trigger human review when drift exceeds threshold.
"""

from __future__ import annotations

import pytest

from vt_protocol.decisions.calibration import (
    DRIFT_THRESHOLD,
    DRIFT_WINDOW,
    ECE_BINS,
    CalibrationMetrics,
    CalibrationRecord,
    CalibrationStore,
    compute_accuracy,
    compute_brier_score,
    compute_drift,
    compute_ece,
    compute_wasserstein,
)

pytestmark = pytest.mark.integration


class TestIRTCalibrationDrift:
    """IRT-based LLM judge calibration with drift detection."""

    @pytest.fixture
    def store(self):
        s = CalibrationStore()
        yield s
        s.close()

    def test_perfect_judge_has_zero_drift(self, store: CalibrationStore):
        """Judge that always matches human → drift = 0, needs_review = False."""
        for i in range(100):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="contradiction",
                judge_confidence=0.9,
                human_verdict="contradiction",
            )

        metrics = store.compute_metrics()
        assert metrics.accuracy == 1.0
        assert metrics.drift_score == 0.0
        assert not metrics.needs_review

    def test_gradual_drift_triggers_review(self, store: CalibrationStore):
        """Judge starts accurate then drifts — needs_review should flip True."""
        # First 80 records: judge is correct
        for i in range(80):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="contradiction",
                judge_confidence=0.85,
                human_verdict="contradiction",
            )

        # Next 50 records: judge is WRONG (drift period)
        for i in range(80, 130):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="compatible",
                judge_confidence=0.80,
                human_verdict="contradiction",
            )

        metrics = store.compute_metrics()
        # Recent accuracy is 0% while overall has a mix → drift > 0
        assert metrics.drift_score > 0.0
        # Drift should exceed threshold given 50 consecutive wrong
        assert metrics.needs_review is True

    def test_brier_score_bounds(self, store: CalibrationStore):
        """Brier score is in [0, 1] — perfect judge gets near-zero."""
        for i in range(50):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="tension",
                judge_confidence=1.0,
                human_verdict="tension",
            )
        metrics = store.compute_metrics()
        assert 0.0 <= metrics.brier_score <= 1.0
        # Perfect with confidence=1.0 → Brier ≈ 0
        assert metrics.brier_score < 0.01

    def test_overconfident_wrong_judge_high_brier(self, store: CalibrationStore):
        """Judge says 'contradiction' with 0.95 confidence, human says 'compatible'."""
        for i in range(50):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="contradiction",
                judge_confidence=0.95,
                human_verdict="compatible",
            )
        metrics = store.compute_metrics()
        # Brier score = (0.95 - 0)^2 = 0.9025 for each
        assert metrics.brier_score > 0.8

    def test_ece_calibrated_judge(self, store: CalibrationStore):
        """Well-calibrated judge: 80% confidence, actually correct 80% of time."""
        for i in range(100):
            # 80 correct, 20 wrong — matches 0.80 confidence
            human = "contradiction" if i < 80 else "compatible"
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="contradiction",
                judge_confidence=0.80,
                human_verdict=human,
            )
        metrics = store.compute_metrics()
        # ECE should be low for well-calibrated judge
        assert metrics.ece < 0.15

    def test_ece_miscalibrated_judge(self, store: CalibrationStore):
        """Miscalibrated: says 0.95 confidence but only right 50% of time."""
        for i in range(100):
            human = "contradiction" if i < 50 else "compatible"
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="contradiction",
                judge_confidence=0.95,
                human_verdict=human,
            )
        metrics = store.compute_metrics()
        # ECE should be high — gap between 0.95 confidence and 0.50 accuracy
        assert metrics.ece > 0.3

    def test_wasserstein_identical_distributions(self, store: CalibrationStore):
        """Identical judge/human distributions → Wasserstein = 0."""
        for i in range(30):
            store.record(
                contradiction_id=f"c-{i}", judge_verdict="contradiction",
                judge_confidence=0.9, human_verdict="contradiction",
            )
        for i in range(30, 60):
            store.record(
                contradiction_id=f"c-{i}", judge_verdict="tension",
                judge_confidence=0.7, human_verdict="tension",
            )
        for i in range(60, 90):
            store.record(
                contradiction_id=f"c-{i}", judge_verdict="compatible",
                judge_confidence=0.8, human_verdict="compatible",
            )
        metrics = store.compute_metrics()
        assert metrics.wasserstein_distance == 0.0

    def test_wasserstein_maximally_different(self, store: CalibrationStore):
        """Judge says all compatible, human says all contradiction → max distance."""
        for i in range(100):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="compatible",
                judge_confidence=0.9,
                human_verdict="contradiction",
            )
        metrics = store.compute_metrics()
        assert metrics.wasserstein_distance > 1.0

    def test_record_count_matches(self, store: CalibrationStore):
        """CalibrationStore.size tracks total records accurately."""
        assert store.size == 0
        for i in range(25):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="contradiction",
                judge_confidence=0.8,
                human_verdict="contradiction",
            )
        assert store.size == 25

    def test_get_records_ordered_newest_first(self, store: CalibrationStore):
        """Records are returned newest-first by timestamp."""
        for i in range(10):
            store.record(
                contradiction_id=f"c-{i}",
                judge_verdict="contradiction",
                judge_confidence=0.8,
                human_verdict="contradiction",
            )
        records = store.get_records(limit=10)
        assert len(records) == 10
        # Newest first — later records have higher contradiction_ids
        assert records[0].contradiction_id == "c-9"

    def test_empty_store_returns_empty_metrics(self, store: CalibrationStore):
        """No records → zeroed metrics, no review needed."""
        metrics = store.compute_metrics()
        assert metrics.total_records == 0
        assert metrics.accuracy == 0.0
        assert metrics.drift_score == 0.0
        assert not metrics.needs_review

    def test_drift_with_small_window(self):
        """Custom drift threshold — lower threshold triggers review sooner."""
        store = CalibrationStore(drift_threshold=0.1)
        try:
            # 100 correct (historical baseline)
            for i in range(100):
                store.record(
                    contradiction_id=f"c-{i}",
                    judge_verdict="contradiction",
                    judge_confidence=0.9,
                    human_verdict="contradiction",
                )
            # 50 wrong (recent drift) — get_records returns newest first,
            # so the DRIFT_WINDOW captures these wrong ones
            for i in range(100, 150):
                store.record(
                    contradiction_id=f"c-{i}",
                    judge_verdict="compatible",
                    judge_confidence=0.9,
                    human_verdict="contradiction",
                )
            metrics = store.compute_metrics()
            # Recent accuracy (last 50) = 0%, overall = 100/150 = 0.667
            # Drift = |0.667 - 0.0| = 0.667 > threshold 0.1
            assert metrics.drift_score > 0.1
            assert metrics.needs_review is True
        finally:
            store.close()

    def test_metadata_preserved(self, store: CalibrationStore):
        """Metadata dict survives round-trip through SQLite."""
        store.record(
            contradiction_id="c-meta",
            judge_verdict="tension",
            judge_confidence=0.7,
            human_verdict="tension",
            metadata={"model": "haiku-4.5", "latency_ms": 142},
        )
        records = store.get_records(limit=1)
        assert len(records) == 1
        assert records[0].metadata["model"] == "haiku-4.5"
        assert records[0].metadata["latency_ms"] == 142


class TestCalibrationPureFunctions:
    """Test the pure metric computation functions directly."""

    def test_compute_accuracy_empty(self):
        assert compute_accuracy([]) == 0.0

    def test_compute_accuracy_all_correct(self):
        records = [
            CalibrationRecord(judge_verdict="contradiction", human_verdict="contradiction")
            for _ in range(10)
        ]
        assert compute_accuracy(records) == 1.0

    def test_compute_accuracy_half_correct(self):
        records = [
            CalibrationRecord(
                judge_verdict="contradiction",
                human_verdict="contradiction" if i < 5 else "compatible",
            )
            for i in range(10)
        ]
        assert compute_accuracy(records) == 0.5

    def test_compute_drift_few_records(self):
        """Fewer than 2 records → drift = 0."""
        records = [CalibrationRecord(judge_verdict="contradiction", human_verdict="contradiction")]
        assert compute_drift(records) == 0.0

    def test_compute_drift_no_drift(self):
        """Consistent accuracy → drift ≈ 0."""
        records = [
            CalibrationRecord(judge_verdict="contradiction", human_verdict="contradiction")
            for _ in range(100)
        ]
        assert compute_drift(records) == 0.0

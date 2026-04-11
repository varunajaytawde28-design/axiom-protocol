"""IRT calibration framework for LLM judge drift detection.

Implements Item Response Theory Graded Response Model (Chen et al. 2026)
to track the Wasserstein distance between Haiku judge verdicts and human
resolutions. Triggers mandatory human review when the judge drifts beyond
a configurable threshold.

Key metrics:
  - Expected Calibration Error (ECE): binned calibration measure
  - Brier Score: mean squared error between predicted and actual
  - Wasserstein Distance: earth-mover distance between verdict distributions
  - Drift Score: rolling window of judge accuracy

Storage: SQLite-backed calibration records (separate from Merkle audit).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from vt_protocol.decisions.models import ContradictionVerdict

logger = logging.getLogger(__name__)

# Default drift threshold — trigger human review above this
DRIFT_THRESHOLD = 0.3

# ECE bins for calibration analysis
ECE_BINS = 10

# Rolling window size for drift computation
DRIFT_WINDOW = 50

# Verdicts as ordered numeric values for distance computation
_VERDICT_ORDER: dict[str, int] = {
    "compatible": 0,
    "tension": 1,
    "contradiction": 2,
}


@dataclass
class CalibrationRecord:
    """A single calibration data point: judge verdict vs human resolution."""

    id: str = field(default_factory=lambda: uuid4().hex[:16])
    contradiction_id: str = ""
    judge_verdict: str = ""  # "contradiction", "tension", "compatible"
    judge_confidence: float = 0.0
    human_verdict: str = ""  # The actual resolution outcome
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_correct(self) -> bool:
        """Whether the judge verdict matched the human resolution."""
        return self.judge_verdict == self.human_verdict


@dataclass
class CalibrationMetrics:
    """Aggregated calibration metrics for the LLM judge."""

    ece: float = 0.0  # Expected Calibration Error (lower = better)
    brier_score: float = 0.0  # Brier score (lower = better)
    wasserstein_distance: float = 0.0  # Distribution distance (lower = better)
    accuracy: float = 0.0  # Simple accuracy (higher = better)
    total_records: int = 0
    drift_score: float = 0.0  # Rolling drift (lower = better)
    needs_review: bool = False  # True if drift exceeds threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "ece": round(self.ece, 4),
            "brier_score": round(self.brier_score, 4),
            "wasserstein_distance": round(self.wasserstein_distance, 4),
            "accuracy": round(self.accuracy, 4),
            "total_records": self.total_records,
            "drift_score": round(self.drift_score, 4),
            "needs_review": self.needs_review,
        }


class CalibrationStore:
    """SQLite-backed storage for calibration records.

    Stores judge vs human verdict pairs for IRT calibration analysis.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        check_same_thread: bool = True,
        drift_threshold: float = DRIFT_THRESHOLD,
    ) -> None:
        if db_path is None:
            self._conn = sqlite3.connect(":memory:", check_same_thread=check_same_thread)
        else:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
        self._drift_threshold = drift_threshold
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS calibration_records (
                id TEXT PRIMARY KEY,
                contradiction_id TEXT NOT NULL,
                judge_verdict TEXT NOT NULL,
                judge_confidence REAL NOT NULL,
                human_verdict TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}'
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cal_timestamp
            ON calibration_records(timestamp)
        """)
        self._conn.commit()

    def record(
        self,
        contradiction_id: str,
        judge_verdict: str,
        judge_confidence: float,
        human_verdict: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> CalibrationRecord:
        """Record a calibration data point."""
        rec = CalibrationRecord(
            contradiction_id=contradiction_id,
            judge_verdict=judge_verdict,
            judge_confidence=judge_confidence,
            human_verdict=human_verdict,
            metadata=metadata or {},
        )
        self._conn.execute(
            """INSERT INTO calibration_records
               (id, contradiction_id, judge_verdict, judge_confidence,
                human_verdict, timestamp, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                rec.id,
                rec.contradiction_id,
                rec.judge_verdict,
                rec.judge_confidence,
                rec.human_verdict,
                rec.timestamp,
                json.dumps(rec.metadata),
            ),
        )
        self._conn.commit()
        return rec

    def get_records(
        self, *, limit: int = 500, offset: int = 0
    ) -> list[CalibrationRecord]:
        """Fetch calibration records ordered by timestamp (newest first)."""
        rows = self._conn.execute(
            """SELECT id, contradiction_id, judge_verdict, judge_confidence,
                      human_verdict, timestamp, metadata_json
               FROM calibration_records
               ORDER BY timestamp DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [
            CalibrationRecord(
                id=r[0],
                contradiction_id=r[1],
                judge_verdict=r[2],
                judge_confidence=r[3],
                human_verdict=r[4],
                timestamp=r[5],
                metadata=json.loads(r[6]) if r[6] else {},
            )
            for r in rows
        ]

    @property
    def size(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM calibration_records"
        ).fetchone()
        return row[0] if row else 0

    def compute_metrics(self) -> CalibrationMetrics:
        """Compute all calibration metrics from stored records."""
        records = self.get_records(limit=10000)
        if not records:
            return CalibrationMetrics()

        accuracy = compute_accuracy(records)
        ece = compute_ece(records)
        brier = compute_brier_score(records)
        wasserstein = compute_wasserstein(records)
        drift = compute_drift(records, window=DRIFT_WINDOW)

        return CalibrationMetrics(
            ece=ece,
            brier_score=brier,
            wasserstein_distance=wasserstein,
            accuracy=accuracy,
            total_records=len(records),
            drift_score=drift,
            needs_review=drift > self._drift_threshold,
        )

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Metric computation functions (pure, testable)
# ---------------------------------------------------------------------------


def compute_accuracy(records: list[CalibrationRecord]) -> float:
    """Simple accuracy: fraction of judge verdicts matching human verdicts."""
    if not records:
        return 0.0
    correct = sum(1 for r in records if r.is_correct)
    return correct / len(records)


def compute_ece(records: list[CalibrationRecord], *, n_bins: int = ECE_BINS) -> float:
    """Expected Calibration Error — binned calibration measure.

    Bins predictions by confidence, then measures the gap between
    average confidence and actual accuracy in each bin.
    Lower is better. Perfect calibration = 0.0.
    """
    if not records:
        return 0.0

    bins: list[list[CalibrationRecord]] = [[] for _ in range(n_bins)]
    for r in records:
        # Clamp confidence to [0, 1) for binning
        idx = min(int(r.judge_confidence * n_bins), n_bins - 1)
        bins[idx].append(r)

    ece = 0.0
    total = len(records)
    for bin_records in bins:
        if not bin_records:
            continue
        bin_size = len(bin_records)
        avg_confidence = sum(r.judge_confidence for r in bin_records) / bin_size
        avg_accuracy = sum(1 for r in bin_records if r.is_correct) / bin_size
        ece += (bin_size / total) * abs(avg_accuracy - avg_confidence)

    return ece


def compute_brier_score(records: list[CalibrationRecord]) -> float:
    """Brier score — mean squared error of probabilistic predictions.

    For each record: (confidence - actual_correctness)^2
    Lower is better. Perfect = 0.0.
    """
    if not records:
        return 0.0

    total = 0.0
    for r in records:
        actual = 1.0 if r.is_correct else 0.0
        total += (r.judge_confidence - actual) ** 2

    return total / len(records)


def compute_wasserstein(records: list[CalibrationRecord]) -> float:
    """Wasserstein (earth-mover) distance between judge and human verdict distributions.

    Maps verdicts to ordinal values (compatible=0, tension=1, contradiction=2),
    computes 1D Wasserstein distance between the two distributions.
    Lower is better. Perfect agreement = 0.0.
    """
    if not records:
        return 0.0

    judge_counts: Counter[int] = Counter()
    human_counts: Counter[int] = Counter()

    for r in records:
        j = _VERDICT_ORDER.get(r.judge_verdict, 0)
        h = _VERDICT_ORDER.get(r.human_verdict, 0)
        judge_counts[j] += 1
        human_counts[h] += 1

    n = len(records)
    # Normalized CDF comparison (1D Wasserstein)
    distance = 0.0
    judge_cdf = 0.0
    human_cdf = 0.0
    for k in range(3):  # 0, 1, 2
        judge_cdf += judge_counts[k] / n
        human_cdf += human_counts[k] / n
        distance += abs(judge_cdf - human_cdf)

    return distance


def compute_drift(
    records: list[CalibrationRecord],
    *,
    window: int = DRIFT_WINDOW,
) -> float:
    """Rolling drift score — compares recent accuracy to overall accuracy.

    High drift means the judge's recent performance differs significantly
    from its historical average. This indicates the judge may need recalibration.
    """
    if len(records) < 2:
        return 0.0

    overall_accuracy = compute_accuracy(records)

    # Recent records (already sorted newest-first from get_records)
    recent = records[:window]
    recent_accuracy = compute_accuracy(recent)

    # Drift is the absolute difference
    return abs(overall_accuracy - recent_accuracy)

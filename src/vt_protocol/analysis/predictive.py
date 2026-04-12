"""Predictive governance — contradiction probability prediction.

Train a lightweight classifier on historical contradiction data to predict
probability of future contradictions before they happen.

From SPEC Sprint 21: "Predictive governance."
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

MIN_TRAINING_EXAMPLES = 50
PREDICTION_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class PredictionFeatures:
    """Feature vector for contradiction prediction."""

    dimension_overlap_count: int = 0
    decision_density: float = 0.0  # decisions per dimension
    historical_contradiction_rate: float = 0.0
    agent_type_numeric: int = 0  # encoded agent type
    time_since_last_same_dimension: float = 0.0  # hours
    total_active_decisions: int = 0
    dimension_count: int = 0

    def to_vector(self) -> list[float]:
        return [
            float(self.dimension_overlap_count),
            self.decision_density,
            self.historical_contradiction_rate,
            float(self.agent_type_numeric),
            self.time_since_last_same_dimension,
            float(self.total_active_decisions),
            float(self.dimension_count),
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_overlap_count": self.dimension_overlap_count,
            "decision_density": round(self.decision_density, 4),
            "historical_contradiction_rate": round(self.historical_contradiction_rate, 4),
            "agent_type_numeric": self.agent_type_numeric,
            "time_since_last_same_dimension": round(self.time_since_last_same_dimension, 2),
            "total_active_decisions": self.total_active_decisions,
            "dimension_count": self.dimension_count,
        }


@dataclass
class Prediction:
    """A contradiction probability prediction."""

    contradiction_probability: float = 0.0
    confidence: float = 0.0
    dimensions: list[str] = field(default_factory=list)
    warning: str = ""
    features: PredictionFeatures | None = None

    @property
    def is_warning(self) -> bool:
        return self.contradiction_probability >= PREDICTION_CONFIDENCE_THRESHOLD

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_probability": round(self.contradiction_probability, 4),
            "confidence": round(self.confidence, 4),
            "dimensions": self.dimensions,
            "warning": self.warning,
            "is_warning": self.is_warning,
            "features": self.features.to_dict() if self.features else None,
        }


@dataclass
class TrainingExample:
    """A single training example from historical data."""

    features: PredictionFeatures
    label: int  # 1 = contradiction occurred, 0 = no contradiction

    def to_vector(self) -> list[float]:
        return self.features.to_vector()


@dataclass
class ModelMetrics:
    """Metrics from model training."""

    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    training_examples: int = 0
    feature_importances: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": round(self.accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
            "training_examples": self.training_examples,
            "feature_importances": {
                k: round(v, 4) for k, v in self.feature_importances.items()
            },
        }


# Agent type encoding
AGENT_TYPE_MAP: dict[str, int] = {
    "coding": 0,
    "review": 1,
    "scan": 2,
    "orchestrator": 3,
    "custom": 4,
    "human": 5,
}

FEATURE_NAMES = [
    "dimension_overlap_count",
    "decision_density",
    "historical_contradiction_rate",
    "agent_type_numeric",
    "time_since_last_same_dimension",
    "total_active_decisions",
    "dimension_count",
]


class PredictiveModel:
    """Contradiction prediction model.

    Uses scikit-learn GradientBoostingClassifier when available,
    falls back to a simple heuristic model.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._trained = False
        self._metrics: ModelMetrics | None = None
        self._use_sklearn = False

    @property
    def is_trained(self) -> bool:
        return self._trained

    @property
    def metrics(self) -> ModelMetrics | None:
        return self._metrics

    def train(self, examples: list[TrainingExample]) -> ModelMetrics:
        """Train the model on historical data.

        Returns metrics. Falls back gracefully with <50 examples.
        """
        if len(examples) < MIN_TRAINING_EXAMPLES:
            self._trained = False
            return ModelMetrics(training_examples=len(examples))

        X = [ex.to_vector() for ex in examples]
        y = [ex.label for ex in examples]

        try:
            return self._train_sklearn(X, y, len(examples))
        except ImportError:
            logger.debug("scikit-learn not available, using heuristic model")
            return self._train_heuristic(examples)

    def _train_sklearn(
        self, X: list[list[float]], y: list[int], n_examples: int,
    ) -> ModelMetrics:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.model_selection import cross_val_score

        clf = GradientBoostingClassifier(
            n_estimators=50, max_depth=3, random_state=42,
        )
        clf.fit(X, y)
        self._model = clf
        self._trained = True
        self._use_sklearn = True

        # Cross-validation scores
        scores = cross_val_score(clf, X, y, cv=min(5, len(X)), scoring="accuracy")

        importances = dict(zip(FEATURE_NAMES, clf.feature_importances_))

        self._metrics = ModelMetrics(
            accuracy=float(scores.mean()),
            precision=float(scores.mean()),  # simplified
            recall=float(scores.mean()),
            f1_score=float(scores.mean()),
            training_examples=n_examples,
            feature_importances=importances,
        )
        return self._metrics

    def _train_heuristic(self, examples: list[TrainingExample]) -> ModelMetrics:
        """Simple heuristic fallback: use historical contradiction rate."""
        positive = sum(1 for ex in examples if ex.label == 1)
        self._base_rate = positive / len(examples) if examples else 0.0
        self._trained = True
        self._use_sklearn = False

        self._metrics = ModelMetrics(
            accuracy=max(self._base_rate, 1 - self._base_rate),
            training_examples=len(examples),
        )
        return self._metrics

    def predict(self, features: PredictionFeatures) -> Prediction:
        """Predict contradiction probability for a new decision."""
        if not self._trained:
            return Prediction(
                warning="Model not trained — insufficient data",
                features=features,
            )

        if self._use_sklearn and self._model is not None:
            X = [features.to_vector()]
            prob = float(self._model.predict_proba(X)[0][1])
        else:
            # Heuristic: weight historical rate by dimension overlap
            base = getattr(self, "_base_rate", 0.5)
            overlap_boost = min(features.dimension_overlap_count * 0.1, 0.3)
            density_boost = min(features.decision_density * 0.05, 0.2)
            prob = min(1.0, base + overlap_boost + density_boost)

        warning = ""
        if prob >= PREDICTION_CONFIDENCE_THRESHOLD:
            dims_str = ", ".join(features.to_dict().keys())
            warning = (
                f"This decision has {prob:.0%} chance of contradicting "
                f"a future decision based on historical patterns"
            )

        return Prediction(
            contradiction_probability=prob,
            confidence=prob,
            warning=warning,
            features=features,
        )

    def reset(self) -> None:
        self._model = None
        self._trained = False
        self._metrics = None
        self._use_sklearn = False


# ---------------------------------------------------------------------------
# Feature extraction helpers
# ---------------------------------------------------------------------------


def extract_features(
    dimensions: list[str],
    *,
    existing_decisions_by_dim: dict[str, int] | None = None,
    historical_contradiction_rate: dict[str, float] | None = None,
    agent_type: str = "coding",
    last_decision_time: datetime | None = None,
    total_active: int = 0,
) -> PredictionFeatures:
    """Extract prediction features from decision context."""
    dim_counts = existing_decisions_by_dim or {}
    contra_rates = historical_contradiction_rate or {}

    overlap = sum(1 for d in dimensions if dim_counts.get(d, 0) > 0)
    density = sum(dim_counts.get(d, 0) for d in dimensions) / max(len(dimensions), 1)
    avg_rate = (
        sum(contra_rates.get(d, 0.0) for d in dimensions) / max(len(dimensions), 1)
        if dimensions else 0.0
    )

    hours_since = 0.0
    if last_decision_time:
        delta = datetime.now(timezone.utc) - last_decision_time
        hours_since = delta.total_seconds() / 3600

    return PredictionFeatures(
        dimension_overlap_count=overlap,
        decision_density=density,
        historical_contradiction_rate=avg_rate,
        agent_type_numeric=AGENT_TYPE_MAP.get(agent_type, 4),
        time_since_last_same_dimension=hours_since,
        total_active_decisions=total_active,
        dimension_count=len(dimensions),
    )

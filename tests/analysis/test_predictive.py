"""Tests for predictive governance — contradiction probability prediction."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from vt_protocol.analysis.predictive import (
    AGENT_TYPE_MAP,
    FEATURE_NAMES,
    MIN_TRAINING_EXAMPLES,
    PREDICTION_CONFIDENCE_THRESHOLD,
    ModelMetrics,
    Prediction,
    PredictionFeatures,
    PredictiveModel,
    TrainingExample,
    extract_features,
)


# ---------------------------------------------------------------------------
# PredictionFeatures
# ---------------------------------------------------------------------------


class TestPredictionFeatures:
    def test_default_features(self):
        f = PredictionFeatures()
        assert f.dimension_overlap_count == 0
        assert f.decision_density == 0.0
        assert f.historical_contradiction_rate == 0.0

    def test_to_vector_length(self):
        f = PredictionFeatures()
        vec = f.to_vector()
        assert len(vec) == 7

    def test_to_vector_values(self):
        f = PredictionFeatures(
            dimension_overlap_count=3,
            decision_density=2.5,
            historical_contradiction_rate=0.3,
            agent_type_numeric=1,
            time_since_last_same_dimension=12.0,
            total_active_decisions=10,
            dimension_count=4,
        )
        vec = f.to_vector()
        assert vec[0] == 3.0
        assert vec[1] == 2.5
        assert vec[2] == 0.3
        assert vec[3] == 1.0

    def test_to_dict(self):
        f = PredictionFeatures(dimension_overlap_count=2)
        d = f.to_dict()
        assert d["dimension_overlap_count"] == 2
        assert "decision_density" in d


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------


class TestPrediction:
    def test_default_not_warning(self):
        p = Prediction()
        assert not p.is_warning
        assert p.contradiction_probability == 0.0

    def test_is_warning_above_threshold(self):
        p = Prediction(contradiction_probability=0.8)
        assert p.is_warning

    def test_is_warning_at_threshold(self):
        p = Prediction(contradiction_probability=PREDICTION_CONFIDENCE_THRESHOLD)
        assert p.is_warning

    def test_not_warning_below_threshold(self):
        p = Prediction(contradiction_probability=0.5)
        assert not p.is_warning

    def test_to_dict(self):
        p = Prediction(contradiction_probability=0.75, warning="test")
        d = p.to_dict()
        assert d["contradiction_probability"] == 0.75
        assert d["warning"] == "test"
        assert d["is_warning"] is True


# ---------------------------------------------------------------------------
# TrainingExample
# ---------------------------------------------------------------------------


class TestTrainingExample:
    def test_basic(self):
        f = PredictionFeatures(dimension_overlap_count=1)
        ex = TrainingExample(features=f, label=1)
        assert ex.label == 1
        assert ex.to_vector()[0] == 1.0

    def test_label_zero(self):
        f = PredictionFeatures()
        ex = TrainingExample(features=f, label=0)
        assert ex.label == 0


# ---------------------------------------------------------------------------
# ModelMetrics
# ---------------------------------------------------------------------------


class TestModelMetrics:
    def test_default(self):
        m = ModelMetrics()
        assert m.accuracy == 0.0
        assert m.training_examples == 0

    def test_to_dict(self):
        m = ModelMetrics(accuracy=0.85, precision=0.8, recall=0.9, f1_score=0.85)
        d = m.to_dict()
        assert d["accuracy"] == 0.85


# ---------------------------------------------------------------------------
# PredictiveModel — heuristic fallback
# ---------------------------------------------------------------------------


def _make_examples(n: int, positive_rate: float = 0.3) -> list[TrainingExample]:
    """Generate synthetic training examples."""
    examples = []
    for i in range(n):
        label = 1 if i < int(n * positive_rate) else 0
        f = PredictionFeatures(
            dimension_overlap_count=i % 5,
            decision_density=float(i % 10) / 10,
            historical_contradiction_rate=positive_rate,
            agent_type_numeric=i % 4,
            time_since_last_same_dimension=float(i),
            total_active_decisions=i,
            dimension_count=i % 8 + 1,
        )
        examples.append(TrainingExample(features=f, label=label))
    return examples


class TestPredictiveModelHeuristic:
    def test_not_trained_initially(self):
        model = PredictiveModel()
        assert not model.is_trained
        assert model.metrics is None

    def test_insufficient_examples(self):
        model = PredictiveModel()
        metrics = model.train([])
        assert not model.is_trained
        assert metrics.training_examples == 0

    def test_insufficient_examples_below_min(self):
        model = PredictiveModel()
        examples = _make_examples(10)
        metrics = model.train(examples)
        assert not model.is_trained
        assert metrics.training_examples == 10

    def test_heuristic_training(self):
        model = PredictiveModel()
        examples = _make_examples(MIN_TRAINING_EXAMPLES)
        metrics = model.train(examples)
        assert model.is_trained
        assert metrics.training_examples == MIN_TRAINING_EXAMPLES
        assert metrics.accuracy > 0

    def test_heuristic_prediction(self):
        model = PredictiveModel()
        examples = _make_examples(MIN_TRAINING_EXAMPLES, positive_rate=0.4)
        model.train(examples)

        features = PredictionFeatures(
            dimension_overlap_count=3,
            decision_density=0.5,
        )
        pred = model.predict(features)
        assert 0.0 <= pred.contradiction_probability <= 1.0
        assert pred.features is not None

    def test_predict_untrained_returns_warning(self):
        model = PredictiveModel()
        pred = model.predict(PredictionFeatures())
        assert "not trained" in pred.warning.lower()

    def test_predict_high_risk(self):
        model = PredictiveModel()
        examples = _make_examples(MIN_TRAINING_EXAMPLES, positive_rate=0.8)
        model.train(examples)

        features = PredictionFeatures(
            dimension_overlap_count=5,
            decision_density=1.0,
        )
        pred = model.predict(features)
        assert pred.contradiction_probability > 0

    def test_reset(self):
        model = PredictiveModel()
        model.train(_make_examples(MIN_TRAINING_EXAMPLES))
        assert model.is_trained
        model.reset()
        assert not model.is_trained
        assert model.metrics is None


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


class TestExtractFeatures:
    def test_basic_extraction(self):
        f = extract_features(["database", "auth"])
        assert f.dimension_count == 2
        assert f.dimension_overlap_count == 0

    def test_with_existing_decisions(self):
        f = extract_features(
            ["database", "auth"],
            existing_decisions_by_dim={"database": 3, "auth": 1},
        )
        assert f.dimension_overlap_count == 2
        assert f.decision_density > 0

    def test_with_contradiction_rate(self):
        f = extract_features(
            ["database"],
            historical_contradiction_rate={"database": 0.25},
        )
        assert f.historical_contradiction_rate == 0.25

    def test_agent_type_encoding(self):
        f = extract_features(["database"], agent_type="review")
        assert f.agent_type_numeric == AGENT_TYPE_MAP["review"]

    def test_unknown_agent_type(self):
        f = extract_features(["database"], agent_type="unknown")
        assert f.agent_type_numeric == 4  # "custom" default

    def test_time_since_last(self):
        past = datetime.now(timezone.utc) - timedelta(hours=5)
        f = extract_features(
            ["database"],
            last_decision_time=past,
        )
        assert f.time_since_last_same_dimension >= 4.9

    def test_total_active(self):
        f = extract_features(["database"], total_active=42)
        assert f.total_active_decisions == 42

    def test_empty_dimensions(self):
        f = extract_features([])
        assert f.dimension_count == 0
        assert f.dimension_overlap_count == 0
        assert f.decision_density == 0.0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_agent_type_map(self):
        assert "coding" in AGENT_TYPE_MAP
        assert "human" in AGENT_TYPE_MAP
        assert len(AGENT_TYPE_MAP) == 6

    def test_feature_names(self):
        assert len(FEATURE_NAMES) == 7
        assert "dimension_overlap_count" in FEATURE_NAMES

    def test_min_training(self):
        assert MIN_TRAINING_EXAMPLES == 50

    def test_threshold(self):
        assert PREDICTION_CONFIDENCE_THRESHOLD == 0.7

"""Tests for architecture DNA fingerprinting."""

from __future__ import annotations

import pytest

from vt_protocol.intelligence.fingerprint import (
    COMPATIBILITY_THRESHOLD,
    FINGERPRINT_DIMENSIONS,
    FINGERPRINT_FEATURES,
    VECTOR_LENGTH,
    ArchFingerprint,
    SimilarityResult,
    compare_fingerprints,
    cosine_similarity,
    generate_fingerprint,
)


# ---------------------------------------------------------------------------
# ArchFingerprint
# ---------------------------------------------------------------------------


class TestArchFingerprint:
    def test_default_vector_length(self):
        fp = ArchFingerprint()
        assert fp.vector_length == VECTOR_LENGTH

    def test_default_all_zeros(self):
        fp = ArchFingerprint()
        assert all(v == 0.0 for v in fp.vector)

    def test_to_dict(self):
        fp = ArchFingerprint(project_id="test")
        d = fp.to_dict()
        assert d["project_id"] == "test"
        assert len(d["vector"]) == VECTOR_LENGTH


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 1.0]
        assert cosine_similarity(a, b) == 0.0

    def test_different_lengths(self):
        a = [1.0, 2.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == 0.0


# ---------------------------------------------------------------------------
# generate_fingerprint
# ---------------------------------------------------------------------------


class TestGenerateFingerprint:
    def test_basic(self):
        decisions = [
            {"dimensions": ["database"], "confidence": 0.8, "source_type": "agent"},
            {"dimensions": ["auth"], "confidence": 0.9, "source_type": "manual"},
        ]
        fp = generate_fingerprint(project_id="test", decisions=decisions)
        assert fp.project_id == "test"
        assert fp.decision_count == 2
        assert fp.fingerprint_hash != ""

    def test_dimension_scores(self):
        decisions = [
            {"dimensions": ["database"], "confidence": 0.8},
        ]
        fp = generate_fingerprint(project_id="test", decisions=decisions)
        assert fp.dimension_scores.get("database", 0.0) > 0.0
        assert fp.dimension_scores.get("auth", 0.0) == 0.0

    def test_feature_scores(self):
        decisions = [
            {"dimensions": ["database"], "confidence": 0.8},
        ]
        fp = generate_fingerprint(project_id="test", decisions=decisions)
        assert "decision_count" in fp.feature_scores
        assert "avg_confidence" in fp.feature_scores
        assert fp.feature_scores["avg_confidence"] == pytest.approx(0.8)

    def test_empty_decisions(self):
        fp = generate_fingerprint(project_id="test", decisions=[])
        assert fp.decision_count == 0
        assert all(v == 0.0 for v in fp.vector[:len(FINGERPRINT_DIMENSIONS)])

    def test_with_contradictions(self):
        decisions = [
            {"dimensions": ["database"], "confidence": 0.8},
        ]
        contradictions = [
            {"status": "resolved"},
            {"status": "unresolved"},
        ]
        fp = generate_fingerprint(
            project_id="test",
            decisions=decisions,
            contradictions=contradictions,
        )
        assert fp.feature_scores["contradiction_rate"] > 0.0
        assert fp.feature_scores["resolution_rate"] == pytest.approx(0.5)

    def test_fingerprint_hash_deterministic(self):
        decisions = [{"dimensions": ["database"], "confidence": 0.8}]
        fp1 = generate_fingerprint(project_id="a", decisions=decisions)
        fp2 = generate_fingerprint(project_id="a", decisions=decisions)
        assert fp1.fingerprint_hash == fp2.fingerprint_hash

    def test_different_inputs_different_hash(self):
        fp1 = generate_fingerprint(
            project_id="a",
            decisions=[{"dimensions": ["database"], "confidence": 0.8}],
        )
        fp2 = generate_fingerprint(
            project_id="b",
            decisions=[{"dimensions": ["auth"], "confidence": 0.5}],
        )
        assert fp1.fingerprint_hash != fp2.fingerprint_hash

    def test_dimension_coverage_score(self):
        decisions = [
            {"dimensions": ["database", "auth", "caching"], "confidence": 0.9},
        ]
        fp = generate_fingerprint(project_id="test", decisions=decisions)
        assert fp.feature_scores["dimension_coverage"] == pytest.approx(
            3 / len(FINGERPRINT_DIMENSIONS)
        )


# ---------------------------------------------------------------------------
# compare_fingerprints
# ---------------------------------------------------------------------------


class TestCompareFingerprints:
    def test_identical_fingerprints(self):
        decisions = [{"dimensions": ["database"], "confidence": 0.8}]
        fp = generate_fingerprint(project_id="a", decisions=decisions)
        result = compare_fingerprints(fp, fp)
        assert result.cosine_similarity == pytest.approx(1.0)
        assert result.compatible

    def test_different_fingerprints(self):
        fp_a = generate_fingerprint(
            project_id="a",
            decisions=[{"dimensions": ["database"], "confidence": 0.9}],
        )
        fp_b = generate_fingerprint(
            project_id="b",
            decisions=[{"dimensions": ["auth"], "confidence": 0.9}],
        )
        result = compare_fingerprints(fp_a, fp_b)
        assert result.cosine_similarity < 1.0
        assert result.project_a == "a"
        assert result.project_b == "b"

    def test_dimension_similarities(self):
        fp_a = generate_fingerprint(
            project_id="a",
            decisions=[{"dimensions": ["database", "auth"], "confidence": 0.8}],
        )
        fp_b = generate_fingerprint(
            project_id="b",
            decisions=[{"dimensions": ["database"], "confidence": 0.8}],
        )
        result = compare_fingerprints(fp_a, fp_b)
        assert "database" in result.dimension_similarities

    def test_compatibility_threshold(self):
        fp_a = generate_fingerprint(
            project_id="a",
            decisions=[{"dimensions": ["database"], "confidence": 0.8}],
        )
        fp_b = generate_fingerprint(
            project_id="b",
            decisions=[{"dimensions": ["database"], "confidence": 0.8}],
        )
        result = compare_fingerprints(fp_a, fp_b)
        assert result.compatible is True

    def test_custom_threshold(self):
        fp_a = generate_fingerprint(
            project_id="a",
            decisions=[{"dimensions": ["database"], "confidence": 0.8}],
        )
        fp_b = generate_fingerprint(
            project_id="b",
            decisions=[{"dimensions": ["auth"], "confidence": 0.8}],
        )
        result = compare_fingerprints(fp_a, fp_b, threshold=0.99)
        assert result.compatible is False

    def test_to_dict(self):
        fp = generate_fingerprint(
            project_id="a",
            decisions=[{"dimensions": ["database"], "confidence": 0.8}],
        )
        result = compare_fingerprints(fp, fp)
        d = result.to_dict()
        assert "cosine_similarity" in d
        assert "compatible" in d


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_vector_length(self):
        assert VECTOR_LENGTH == len(FINGERPRINT_DIMENSIONS) + len(FINGERPRINT_FEATURES)

    def test_fingerprint_dimensions_count(self):
        assert len(FINGERPRINT_DIMENSIONS) == 12

    def test_compatibility_threshold(self):
        assert COMPATIBILITY_THRESHOLD == 0.7

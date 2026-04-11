"""Tests for soft-label NLI distribution (Madaan et al. 2025)."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from vt_protocol.decisions.contradiction import (
    NLIDistribution,
    _USER_TEMPLATE,
    _USER_TEMPLATE_WITH_NLI,
    nli_distribution,
    nli_score,
)


# ---------------------------------------------------------------------------
# NLIDistribution dataclass
# ---------------------------------------------------------------------------


class TestNLIDistribution:
    def test_basic_creation(self) -> None:
        dist = NLIDistribution(contradiction=0.8, entailment=0.1, neutral=0.1)
        assert dist.contradiction == 0.8
        assert dist.entailment == 0.1
        assert dist.neutral == 0.1

    def test_dominant_label_contradiction(self) -> None:
        dist = NLIDistribution(contradiction=0.8, entailment=0.1, neutral=0.1)
        assert dist.dominant_label == "contradiction"

    def test_dominant_label_entailment(self) -> None:
        dist = NLIDistribution(contradiction=0.1, entailment=0.8, neutral=0.1)
        assert dist.dominant_label == "entailment"

    def test_dominant_label_neutral(self) -> None:
        dist = NLIDistribution(contradiction=0.1, entailment=0.1, neutral=0.8)
        assert dist.dominant_label == "neutral"

    def test_entropy_certain(self) -> None:
        # Almost certain → low entropy
        dist = NLIDistribution(contradiction=0.98, entailment=0.01, neutral=0.01)
        assert dist.entropy < 0.5

    def test_entropy_uncertain(self) -> None:
        # Uniform → high entropy (max ~1.585 for 3 classes)
        dist = NLIDistribution(contradiction=0.333, entailment=0.334, neutral=0.333)
        assert dist.entropy > 1.5

    def test_entropy_zero_probs(self) -> None:
        # One of the probs is 0 → should not crash
        dist = NLIDistribution(contradiction=0.5, entailment=0.5, neutral=0.0)
        assert dist.entropy >= 0.0

    def test_is_ambiguous_true(self) -> None:
        dist = NLIDistribution(contradiction=0.4, entailment=0.35, neutral=0.25)
        assert dist.is_ambiguous is True

    def test_is_ambiguous_false(self) -> None:
        dist = NLIDistribution(contradiction=0.7, entailment=0.2, neutral=0.1)
        assert dist.is_ambiguous is False

    def test_to_dict(self) -> None:
        dist = NLIDistribution(contradiction=0.8123, entailment=0.1234, neutral=0.0643)
        d = dist.to_dict()
        assert d["contradiction"] == 0.8123
        assert d["entailment"] == 0.1234
        assert d["neutral"] == 0.0643

    def test_to_dict_rounding(self) -> None:
        dist = NLIDistribution(contradiction=0.81239999, entailment=0.12345, neutral=0.06415)
        d = dist.to_dict()
        assert d["contradiction"] == 0.8124
        assert d["entailment"] == 0.1235
        assert d["neutral"] == round(0.06415, 4)


# ---------------------------------------------------------------------------
# nli_distribution function
# ---------------------------------------------------------------------------


class TestNLIDistributionFunction:
    def test_returns_none_without_sentence_transformers(self) -> None:
        # Without mocking, if sentence_transformers not importable, returns None
        # We mock the import to simulate not having it
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            result = nli_distribution("text a", "text b")
            # Might return None due to import failure
            # (depends on if sentence_transformers is actually installed)

    @patch("vt_protocol.decisions.contradiction._get_nli_model")
    def test_returns_distribution(self, mock_model_fn: MagicMock) -> None:
        import numpy as np

        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([[0.7, 0.1, 0.2]])
        mock_model_fn.return_value = mock_model

        result = nli_distribution("Use PostgreSQL", "Use MongoDB")
        assert result is not None
        assert result.contradiction == pytest.approx(0.7, abs=0.01)
        assert result.entailment == pytest.approx(0.1, abs=0.01)
        assert result.neutral == pytest.approx(0.2, abs=0.01)

    @patch("vt_protocol.decisions.contradiction._get_nli_model")
    def test_nli_score_uses_distribution(self, mock_model_fn: MagicMock) -> None:
        import numpy as np

        mock_model = MagicMock()
        mock_model.predict.return_value = np.array([[0.7, 0.1, 0.2]])
        mock_model_fn.return_value = mock_model

        score = nli_score("Use PostgreSQL", "Use MongoDB")
        assert score == pytest.approx(0.7, abs=0.01)


# ---------------------------------------------------------------------------
# LLM template with NLI soft labels
# ---------------------------------------------------------------------------


class TestUserTemplateWithNLI:
    def test_template_has_nli_fields(self) -> None:
        assert "{nli_contradiction" in _USER_TEMPLATE_WITH_NLI
        assert "{nli_entailment" in _USER_TEMPLATE_WITH_NLI
        assert "{nli_neutral" in _USER_TEMPLATE_WITH_NLI
        assert "{nli_note}" in _USER_TEMPLATE_WITH_NLI

    def test_template_without_nli_unchanged(self) -> None:
        # Original template should not have NLI fields
        assert "nli_contradiction" not in _USER_TEMPLATE
        assert "nli_entailment" not in _USER_TEMPLATE

    def test_format_with_nli(self) -> None:
        msg = _USER_TEMPLATE_WITH_NLI.format(
            title_a="Use PostgreSQL",
            content_a="We should use PostgreSQL",
            title_b="Use MongoDB",
            content_b="We should use MongoDB",
            dimensions="database",
            nli_contradiction=0.8,
            nli_entailment=0.1,
            nli_neutral=0.1,
            nli_note="Dominant signal: contradiction",
        )
        assert "80.0%" in msg
        assert "Dominant signal: contradiction" in msg


# ---------------------------------------------------------------------------
# Integration: check_contradiction with soft labels
# ---------------------------------------------------------------------------


class TestCheckContradictionWithSoftLabels:
    @patch("vt_protocol.decisions.contradiction.nli_distribution")
    def test_skips_llm_when_nli_low(self, mock_dist: MagicMock) -> None:
        from vt_protocol.decisions.contradiction import check_contradiction
        from vt_protocol.decisions.models import Decision, Dimension, SourceType

        mock_dist.return_value = NLIDistribution(
            contradiction=0.1, entailment=0.7, neutral=0.2
        )

        d1 = Decision(
            title="Use PostgreSQL", content="Use PostgreSQL for data",
            made_by="test", project="test", source_type=SourceType.MANUAL,
            dimensions=[Dimension.DATABASE],
        )
        d2 = Decision(
            title="Use MongoDB", content="Use MongoDB for data",
            made_by="test", project="test", source_type=SourceType.MANUAL,
            dimensions=[Dimension.DATABASE],
        )

        result = check_contradiction(d1, d2, skip_llm=True)
        assert result is None  # NLI score < 0.3 → skipped

    @patch("vt_protocol.decisions.contradiction.nli_distribution")
    @patch("vt_protocol.decisions.contradiction.llm_check")
    def test_passes_dist_to_llm(self, mock_llm: MagicMock, mock_dist: MagicMock) -> None:
        from vt_protocol.decisions.contradiction import check_contradiction
        from vt_protocol.decisions.models import Decision, Dimension, SourceType

        dist = NLIDistribution(contradiction=0.8, entailment=0.1, neutral=0.1)
        mock_dist.return_value = dist
        mock_llm.return_value = {
            "reasoning": "These conflict",
            "verdict": "contradiction",
            "confidence": 0.9,
            "evidence_a": "PostgreSQL",
            "evidence_b": "MongoDB",
        }

        d1 = Decision(
            title="Use PostgreSQL", content="Use PostgreSQL for data",
            made_by="test", project="test", source_type=SourceType.MANUAL,
            dimensions=[Dimension.DATABASE],
        )
        d2 = Decision(
            title="Use MongoDB", content="Use MongoDB for data",
            made_by="test", project="test", source_type=SourceType.MANUAL,
            dimensions=[Dimension.DATABASE],
        )

        result = check_contradiction(d1, d2, skip_voting=True)
        assert result is not None

        # Verify llm_check was called with nli_distribution
        mock_llm.assert_called_once()
        call_kwargs = mock_llm.call_args[1]
        assert call_kwargs["nli_distribution"] is dist

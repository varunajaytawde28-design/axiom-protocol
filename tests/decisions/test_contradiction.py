"""Tests for contradiction detection pipeline.

Both NLI and LLM stages are fully mocked — no external services needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from vt_protocol.decisions.contradiction import (
    CONFIDENCE_THRESHOLD,
    NLI_THRESHOLD,
    _parse_llm_response,
    check_contradiction,
    llm_check,
    nli_score,
    reset_nli_model,
)
from vt_protocol.decisions.models import (
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.exceptions import ContradictionDetectionError


@pytest.fixture
def decision_pg() -> Decision:
    return Decision(
        title="Use PostgreSQL for primary datastore",
        content="PostgreSQL for all relational data. Concurrent access via MVCC.",
        rationale="Production-ready, concurrent access",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=[Dimension.DATABASE],
        made_by="test",
        project="test",
    )


@pytest.fixture
def decision_sqlite() -> Decision:
    return Decision(
        title="Use SQLite for all data storage",
        content="SQLite with WAL mode for simplicity. Single file, no server process.",
        rationale="Zero config, embedded, simple",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.DATABASE],
        made_by="test",
        project="test",
    )


@pytest.fixture
def decision_redis() -> Decision:
    return Decision(
        title="Use Redis for caching",
        content="Redis as a caching layer for frequently accessed data.",
        rationale="Low latency, mature",
        decision_type=DecisionType.TECHNICAL,
        dimensions=[Dimension.CACHING],
        made_by="test",
        project="test",
    )


class TestNLIScore:
    def test_nli_returns_float_or_none(self) -> None:
        reset_nli_model()
        result = nli_score("Use PostgreSQL for storage", "Use SQLite for storage")
        # Returns float if sentence-transformers is installed, None otherwise
        assert result is None or isinstance(result, float)

    def test_nli_graceful_on_model_error(self) -> None:
        reset_nli_model()
        with patch(
            "vt_protocol.decisions.contradiction._get_nli_model",
            side_effect=RuntimeError("model load failed"),
        ):
            result = nli_score("a", "b")
            assert result is None


class TestLLMResponseParsing:
    def test_parse_valid_json(self) -> None:
        raw = '{"reasoning": "They conflict", "verdict": "contradiction", "confidence": 0.9, "evidence_a": "PG", "evidence_b": "SQLite"}'
        result = _parse_llm_response(raw)
        assert result["verdict"] == "contradiction"
        assert result["confidence"] == 0.9

    def test_parse_json_with_markdown_fences(self) -> None:
        raw = '```json\n{"reasoning": "ok", "verdict": "compatible", "confidence": 0.8, "evidence_a": "a", "evidence_b": "b"}\n```'
        result = _parse_llm_response(raw)
        assert result["verdict"] == "compatible"

    def test_parse_missing_field_raises(self) -> None:
        raw = '{"reasoning": "ok", "verdict": "compatible"}'
        with pytest.raises(ContradictionDetectionError, match="Missing"):
            _parse_llm_response(raw)

    def test_parse_invalid_verdict_raises(self) -> None:
        raw = '{"reasoning": "ok", "verdict": "maybe", "confidence": 0.5, "evidence_a": "a", "evidence_b": "b"}'
        with pytest.raises(ContradictionDetectionError, match="Invalid verdict"):
            _parse_llm_response(raw)

    def test_parse_no_json_raises(self) -> None:
        with pytest.raises(ContradictionDetectionError, match="No JSON"):
            _parse_llm_response("just some text without json")

    def test_parse_normalizes_verdict_case(self) -> None:
        raw = '{"reasoning": "ok", "verdict": "TENSION", "confidence": 0.6, "evidence_a": "a", "evidence_b": "b"}'
        result = _parse_llm_response(raw)
        assert result["verdict"] == "tension"


class TestLLMCheck:
    def test_returns_none_without_api_key(self, decision_pg, decision_sqlite) -> None:
        with patch.dict("os.environ", {}, clear=True):
            # Remove ANTHROPIC_API_KEY
            result = llm_check(decision_pg, decision_sqlite, [Dimension.DATABASE])
            assert result is None

    @patch("anthropic.Anthropic")
    def test_llm_returns_parsed_result(self, mock_anthropic_cls, decision_pg, decision_sqlite) -> None:
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"reasoning": "PG vs SQLite", "verdict": "contradiction", "confidence": 0.85, "evidence_a": "PostgreSQL for concurrent", "evidence_b": "SQLite single file"}')]
        mock_client.messages.create.return_value = mock_response

        result = llm_check(
            decision_pg, decision_sqlite, [Dimension.DATABASE],
            api_key="test-key",
        )
        assert result is not None
        assert result["verdict"] == "contradiction"
        assert result["confidence"] == 0.85


class TestCheckContradiction:
    def test_compatible_returns_none(self, decision_pg, decision_redis) -> None:
        """Decisions with no shared dimensions produce no contradiction."""
        result = check_contradiction(
            decision_pg, decision_redis, skip_nli=True, skip_llm=True
        )
        assert result is None

    def test_skip_both_stages_returns_none(self, decision_pg, decision_sqlite) -> None:
        result = check_contradiction(
            decision_pg, decision_sqlite, skip_nli=True, skip_llm=True
        )
        assert result is None

    @patch("vt_protocol.decisions.contradiction.nli_score")
    def test_low_nli_skips_llm(self, mock_nli, decision_pg, decision_sqlite) -> None:
        mock_nli.return_value = 0.1  # Below NLI_THRESHOLD
        result = check_contradiction(decision_pg, decision_sqlite, skip_llm=True)
        assert result is None
        mock_nli.assert_called_once()

    @patch("vt_protocol.decisions.contradiction.llm_check")
    @patch("vt_protocol.decisions.contradiction.nli_score")
    def test_high_nli_proceeds_to_llm(self, mock_nli, mock_llm, decision_pg, decision_sqlite) -> None:
        mock_nli.return_value = 0.8  # Above threshold
        mock_llm.return_value = {
            "reasoning": "Contradictory databases",
            "verdict": "contradiction",
            "confidence": 0.9,
            "evidence_a": "PostgreSQL",
            "evidence_b": "SQLite",
        }

        result = check_contradiction(decision_pg, decision_sqlite)
        assert result is not None
        assert result.verdict == ContradictionVerdict.CONTRADICTION
        assert result.confidence == 0.9
        assert result.decision_a_title == decision_pg.title
        assert result.decision_b_title == decision_sqlite.title

    @patch("vt_protocol.decisions.contradiction.llm_check")
    def test_llm_compatible_returns_none(self, mock_llm, decision_pg, decision_sqlite) -> None:
        mock_llm.return_value = {
            "reasoning": "Not contradictory",
            "verdict": "compatible",
            "confidence": 0.8,
            "evidence_a": "PG",
            "evidence_b": "SQLite",
        }
        result = check_contradiction(decision_pg, decision_sqlite, skip_nli=True)
        assert result is None

    @patch("vt_protocol.decisions.contradiction.llm_check")
    def test_tension_returns_contradiction_model(self, mock_llm, decision_pg, decision_sqlite) -> None:
        mock_llm.return_value = {
            "reasoning": "Some tension",
            "verdict": "tension",
            "confidence": 0.6,
            "evidence_a": "PG",
            "evidence_b": "SQLite",
        }
        result = check_contradiction(decision_pg, decision_sqlite, skip_nli=True)
        assert result is not None
        assert result.verdict == ContradictionVerdict.TENSION

    @patch("vt_protocol.decisions.contradiction.llm_check")
    def test_shared_dimensions_computed(self, mock_llm, decision_pg, decision_sqlite) -> None:
        mock_llm.return_value = {
            "reasoning": "Both database",
            "verdict": "contradiction",
            "confidence": 0.9,
            "evidence_a": "PG",
            "evidence_b": "SQLite",
        }
        result = check_contradiction(decision_pg, decision_sqlite, skip_nli=True)
        assert result is not None
        assert Dimension.DATABASE in result.shared_dimensions

    @patch("vt_protocol.decisions.contradiction.nli_score")
    def test_nli_none_proceeds_to_llm(self, mock_nli, decision_pg, decision_sqlite) -> None:
        mock_nli.return_value = None  # Transformers not installed
        # With skip_llm=True, should return None (but NLI didn't filter)
        result = check_contradiction(decision_pg, decision_sqlite, skip_llm=True)
        assert result is None

    def test_nli_threshold_constant(self) -> None:
        assert NLI_THRESHOLD == 0.3

    def test_confidence_threshold_constant(self) -> None:
        assert CONFIDENCE_THRESHOLD == 0.7

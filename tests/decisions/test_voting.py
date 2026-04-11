"""Tests for self-consistency voting in contradiction detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vt_protocol.decisions.contradiction import (
    VOTING_ROUNDS,
    VOTING_TEMPERATURE,
    VOTING_THRESHOLD,
    _self_consistency_vote,
    check_contradiction,
)
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


def _make_decision(title: str = "Test decision", dims: list[Dimension] | None = None) -> Decision:
    return Decision(
        title=title,
        content=f"Detailed content about {title}.",
        rationale="Good reasons.",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )


def _make_llm_result(
    verdict: str = "contradiction",
    confidence: float = 0.5,
) -> dict:
    return {
        "reasoning": "Step-by-step analysis of the conflict.",
        "verdict": verdict,
        "confidence": confidence,
        "evidence_a": "Evidence from A",
        "evidence_b": "Evidence from B",
    }


class TestVotingConstants:
    def test_threshold_is_reasonable(self) -> None:
        assert 0.0 < VOTING_THRESHOLD < 1.0
        assert VOTING_THRESHOLD == 0.6

    def test_rounds_is_positive(self) -> None:
        assert VOTING_ROUNDS >= 2

    def test_temperature_is_elevated(self) -> None:
        assert VOTING_TEMPERATURE > 0.0


class TestSelfConsistencyVote:
    def test_majority_vote_picks_winner(self) -> None:
        """When 3/4 calls say contradiction, result is contradiction."""
        initial = _make_llm_result("contradiction", 0.5)
        d_a = _make_decision("A")
        d_b = _make_decision("B")
        dims = [Dimension.DATABASE]

        vote_results = [
            _make_llm_result("contradiction", 0.6),
            _make_llm_result("compatible", 0.7),
            _make_llm_result("contradiction", 0.55),
        ]

        with patch("vt_protocol.decisions.contradiction.llm_check", side_effect=vote_results):
            result = _self_consistency_vote(
                d_a, d_b, dims,
                initial_result=initial,
                api_key="test-key",
            )
            assert result is not None
            assert result["verdict"] == "contradiction"
            assert "_voting" in result
            assert result["_voting"]["total_calls"] == 4

    def test_compatible_majority_overrides(self) -> None:
        """When majority says compatible, initial contradiction is overridden."""
        initial = _make_llm_result("contradiction", 0.4)
        d_a = _make_decision("A")
        d_b = _make_decision("B")

        vote_results = [
            _make_llm_result("compatible", 0.8),
            _make_llm_result("compatible", 0.7),
            _make_llm_result("compatible", 0.75),
        ]

        with patch("vt_protocol.decisions.contradiction.llm_check", side_effect=vote_results):
            result = _self_consistency_vote(
                d_a, d_b, [Dimension.DATABASE],
                initial_result=initial,
                api_key="test-key",
            )
            assert result is not None
            assert result["verdict"] == "compatible"

    def test_agreement_ratio_in_result(self) -> None:
        initial = _make_llm_result("tension", 0.5)

        vote_results = [
            _make_llm_result("tension", 0.6),
            _make_llm_result("tension", 0.55),
            _make_llm_result("contradiction", 0.4),
        ]

        with patch("vt_protocol.decisions.contradiction.llm_check", side_effect=vote_results):
            result = _self_consistency_vote(
                _make_decision("A"), _make_decision("B"), [Dimension.DATABASE],
                initial_result=initial,
                api_key="test-key",
            )
            assert result is not None
            assert result["_voting"]["agreement"] == 0.75  # 3/4

    def test_all_calls_fail_returns_none(self) -> None:
        """When all voting calls fail, returns None (not enough data to vote)."""
        initial = _make_llm_result("contradiction", 0.4)

        with patch("vt_protocol.decisions.contradiction.llm_check", return_value=None):
            result = _self_consistency_vote(
                _make_decision("A"), _make_decision("B"), [Dimension.DATABASE],
                initial_result=initial,
                api_key="test-key",
            )
            # Only initial result (len=1), need >=2 to vote
            assert result is None

    def test_picks_highest_confidence_winner(self) -> None:
        """Among winning verdict results, pick the one with highest confidence."""
        initial = _make_llm_result("contradiction", 0.3)

        vote_results = [
            _make_llm_result("contradiction", 0.8),
            _make_llm_result("contradiction", 0.5),
            _make_llm_result("compatible", 0.9),
        ]

        with patch("vt_protocol.decisions.contradiction.llm_check", side_effect=vote_results):
            result = _self_consistency_vote(
                _make_decision("A"), _make_decision("B"), [Dimension.DATABASE],
                initial_result=initial,
                api_key="test-key",
            )
            assert result is not None
            # Highest confidence among "contradiction" results is 0.8
            # Agreement is 3/4 = 0.75
            # Final confidence = 0.8 * 0.75 = 0.6
            assert result["confidence"] == 0.6


class TestCheckContradictionWithVoting:
    def test_skip_voting_flag(self) -> None:
        """skip_voting=True should not trigger voting even on low confidence."""
        d_a = _make_decision("A")
        d_b = _make_decision("B")
        low_conf = _make_llm_result("contradiction", 0.3)

        with patch("vt_protocol.decisions.contradiction.nli_score", return_value=0.8):
            with patch("vt_protocol.decisions.contradiction.llm_check", return_value=low_conf) as mock_llm:
                result = check_contradiction(
                    d_a, d_b,
                    skip_voting=True,
                    api_key="test-key",
                )
                assert result is not None
                # Only 1 call — no voting
                assert mock_llm.call_count == 1

    def test_voting_triggered_on_low_confidence(self) -> None:
        """When confidence < 0.6, voting should trigger additional calls."""
        d_a = _make_decision("A")
        d_b = _make_decision("B")
        initial = _make_llm_result("contradiction", 0.4)
        vote = _make_llm_result("contradiction", 0.6)

        with patch("vt_protocol.decisions.contradiction.nli_score", return_value=0.8):
            with patch("vt_protocol.decisions.contradiction.llm_check", side_effect=[initial, vote, vote, vote]) as mock_llm:
                result = check_contradiction(d_a, d_b, api_key="test-key")
                assert result is not None
                # 1 initial + 3 voting calls = 4 total
                assert mock_llm.call_count == 4

    def test_no_voting_on_high_confidence(self) -> None:
        """When confidence >= 0.6, no voting is triggered."""
        d_a = _make_decision("A")
        d_b = _make_decision("B")
        high_conf = _make_llm_result("contradiction", 0.85)

        with patch("vt_protocol.decisions.contradiction.nli_score", return_value=0.8):
            with patch("vt_protocol.decisions.contradiction.llm_check", return_value=high_conf) as mock_llm:
                result = check_contradiction(d_a, d_b, api_key="test-key")
                assert result is not None
                assert mock_llm.call_count == 1

"""Tests for ContraGen synthetic contradiction data pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vt_protocol.decisions.contragen import (
    PAIR_TYPES,
    DatasetStats,
    _parse_pairs,
    generate_all_pairs,
    generate_dataset,
    generate_pairs,
    load_dataset,
    validate_pair,
)
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.exceptions import ContradictionDetectionError


def _make_decision(
    title: str = "Use PostgreSQL",
    dims: list[Dimension] | None = None,
) -> Decision:
    return Decision(
        title=title,
        content=f"We chose {title} for our architecture. Full details here.",
        rationale=f"Because {title} is the best.",
        decision_type=DecisionType.ARCHITECTURAL,
        dimensions=dims or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )


def _make_pair(
    label: str = "contradiction",
    title_a: str = "Use PostgreSQL",
    title_b: str = "Use SQLite",
) -> dict:
    return {
        "decision_a": {"title": title_a, "content": f"Details about {title_a}."},
        "decision_b": {"title": title_b, "content": f"Details about {title_b}."},
        "label": label,
        "label_rationale": f"These are a {label} because of their database choices.",
        "shared_dimensions": ["database"],
    }


def _mock_anthropic_response(pairs: list[dict]) -> MagicMock:
    """Create a mock Anthropic response with JSON array."""
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps(pairs))]
    return mock_resp


class TestPairTypes:
    def test_all_types_defined(self) -> None:
        types = {t for t, _ in PAIR_TYPES}
        assert types == {"contradiction", "tension", "hard_negative", "compatible"}

    def test_counts_sum_to_eight(self) -> None:
        total = sum(count for _, count in PAIR_TYPES)
        assert total == 8


class TestValidatePair:
    def test_valid_pair(self) -> None:
        pair = _make_pair()
        errors = validate_pair(pair)
        assert errors == []

    def test_missing_label(self) -> None:
        pair = _make_pair()
        del pair["label"]
        errors = validate_pair(pair)
        assert any("label" in e for e in errors)

    def test_invalid_label(self) -> None:
        pair = _make_pair(label="bogus")
        errors = validate_pair(pair)
        assert any("Invalid label" in e for e in errors)

    def test_missing_decision_a(self) -> None:
        pair = _make_pair()
        del pair["decision_a"]
        errors = validate_pair(pair)
        assert any("decision_a" in e for e in errors)

    def test_missing_title_in_decision(self) -> None:
        pair = _make_pair()
        del pair["decision_a"]["title"]
        errors = validate_pair(pair)
        assert any("decision_a.title" in e for e in errors)

    def test_decision_not_dict(self) -> None:
        pair = _make_pair()
        pair["decision_a"] = "not a dict"
        errors = validate_pair(pair)
        assert any("must be a dict" in e for e in errors)


class TestParsePairs:
    def test_parses_valid_json_array(self) -> None:
        raw = json.dumps([_make_pair(), _make_pair(label="tension")])
        pairs = _parse_pairs(raw)
        assert len(pairs) == 2

    def test_strips_markdown_fences(self) -> None:
        raw = f"```json\n{json.dumps([_make_pair()])}\n```"
        pairs = _parse_pairs(raw)
        assert len(pairs) == 1

    def test_skips_invalid_pairs(self) -> None:
        raw = json.dumps([_make_pair(), {"label": "bad"}])
        pairs = _parse_pairs(raw)
        assert len(pairs) == 1

    def test_raises_on_no_json(self) -> None:
        with pytest.raises(ContradictionDetectionError, match="No JSON array"):
            _parse_pairs("This is not JSON at all")

    def test_handles_extra_text(self) -> None:
        raw = f"Here are the pairs:\n{json.dumps([_make_pair()])}\nDone!"
        pairs = _parse_pairs(raw)
        assert len(pairs) == 1


class TestGeneratePairs:
    def test_returns_empty_without_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = generate_pairs(_make_decision(), api_key=None)
            assert result == []

    def test_generates_with_mock_api(self) -> None:
        mock_pairs = [_make_pair(), _make_pair()]
        mock_resp = _mock_anthropic_response(mock_pairs)

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            result = generate_pairs(
                _make_decision(),
                pair_type="contradiction",
                count=2,
                api_key="test-key",
            )
            assert len(result) == 2
            assert result[0]["seed_decision_id"] is not None

    def test_adds_metadata(self) -> None:
        mock_resp = _mock_anthropic_response([_make_pair()])

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.return_value = mock_resp

            result = generate_pairs(_make_decision(), api_key="test-key")
            assert result[0]["seed_decision_id"]
            assert result[0]["seed_dimensions"] == ["database"]
            assert "model" in result[0]

    def test_handles_api_error(self) -> None:
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.messages.create.side_effect = Exception("API error")

            result = generate_pairs(_make_decision(), api_key="test-key")
            assert result == []


class TestGenerateAllPairs:
    def test_calls_all_pair_types(self) -> None:
        calls = []

        def mock_generate(decision, *, pair_type, count, model, api_key):
            calls.append(pair_type)
            return [_make_pair(label=pair_type)] * count

        with patch("vt_protocol.decisions.contragen.generate_pairs", side_effect=mock_generate):
            result = generate_all_pairs(_make_decision(), api_key="test-key")
            assert set(calls) == {"contradiction", "tension", "hard_negative", "compatible"}
            assert len(result) == 8


class TestGenerateDataset:
    def test_writes_jsonl(self, tmp_path: Path) -> None:
        output = tmp_path / "dataset.jsonl"

        def mock_all(decision, *, model, api_key):
            return [_make_pair("contradiction"), _make_pair("tension")]

        with patch("vt_protocol.decisions.contragen.generate_all_pairs", side_effect=mock_all):
            stats = generate_dataset(
                [_make_decision("A"), _make_decision("B")],
                output,
                api_key="test-key",
            )
            assert output.exists()
            lines = output.read_text().strip().split("\n")
            assert len(lines) == 4  # 2 decisions x 2 pairs each
            assert stats.total == 4
            assert stats.seed_decisions == 2

    def test_stats_by_type(self, tmp_path: Path) -> None:
        output = tmp_path / "dataset.jsonl"

        def mock_all(decision, *, model, api_key):
            return [_make_pair("contradiction"), _make_pair("compatible")]

        with patch("vt_protocol.decisions.contragen.generate_all_pairs", side_effect=mock_all):
            stats = generate_dataset([_make_decision()], output, api_key="test-key")
            assert stats.by_type["contradiction"] == 1
            assert stats.by_type["compatible"] == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        output = tmp_path / "deep" / "nested" / "dataset.jsonl"

        with patch("vt_protocol.decisions.contragen.generate_all_pairs", return_value=[]):
            generate_dataset([], output, api_key="test-key")
            assert output.parent.is_dir()


class TestLoadDataset:
    def test_load_valid_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "data.jsonl"
        pairs = [_make_pair("contradiction"), _make_pair("tension")]
        path.write_text("\n".join(json.dumps(p) for p in pairs))

        loaded = load_dataset(path)
        assert len(loaded) == 2
        assert loaded[0]["label"] == "contradiction"

    def test_load_nonexistent(self, tmp_path: Path) -> None:
        loaded = load_dataset(tmp_path / "missing.jsonl")
        assert loaded == []

    def test_load_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        loaded = load_dataset(path)
        assert loaded == []


class TestDatasetStats:
    def test_to_dict(self) -> None:
        stats = DatasetStats()
        stats.total = 10
        stats.seed_decisions = 3
        stats.by_type = {"contradiction": 4, "tension": 6}
        d = stats.to_dict()
        assert d["total_pairs"] == 10
        assert d["seed_decisions"] == 3
        assert d["by_type"]["contradiction"] == 4

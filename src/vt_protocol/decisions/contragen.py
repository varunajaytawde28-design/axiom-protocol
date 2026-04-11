"""ContraGen — synthetic contradiction data pipeline for fine-tuning.

Takes real architectural decisions and generates synthetic pairs:
  - 2 direct contradictions per decision
  - 2 subtle tensions
  - 2 hard negatives (look contradictory but aren't)
  - 2 compatible pairs

Uses Claude Sonnet for higher-quality generation. Outputs labeled JSONL.

From SPEC: "ContraGen: Generate labeled pairs for fine-tuning the
contradiction classifier. Real decisions are the seed."
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from vt_protocol.decisions.models import Decision, Dimension
from vt_protocol.exceptions import ContradictionDetectionError

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"

PAIR_TYPES = [
    ("contradiction", 2),
    ("tension", 2),
    ("hard_negative", 2),
    ("compatible", 2),
]

_SYSTEM_PROMPT = """\
You are an expert at generating synthetic architectural decision pairs for \
training a contradiction detection classifier. You understand software \
architecture deeply and can generate realistic, nuanced examples.

PAIR TYPES:
- contradiction: Decisions that directly conflict — cannot both be implemented
- tension: Decisions that pull in different directions but could coexist with tradeoffs
- hard_negative: Decisions that LOOK contradictory but aren't (different scopes, \
timeframes, complementary layers) — these are the hardest to classify correctly
- compatible: Decisions that naturally work together

RULES:
1. Generated decisions must be realistic and technically sound
2. Hard negatives must be genuinely tricky — not obviously compatible
3. Include dimension-specific patterns (database contradictions differ from auth ones)
4. Each generated decision needs title + content (2-4 sentences)
5. Provide a brief label_rationale explaining why this pair has this label

Respond with valid JSON only (no markdown fences)."""

_USER_TEMPLATE = """\
Given this real architectural decision as a seed:

Title: "{title}"
Content: {content}
Dimensions: {dimensions}

Generate {count} synthetic decision pairs of type "{pair_type}".

Each pair should contain:
- The original decision (decision_a) with title and content
- A synthetic counter-decision (decision_b) with title and content
- The label ("{pair_type}")
- label_rationale explaining why this pair has this label
- shared_dimensions relevant to the pair

Return as a JSON array:
[
  {{
    "decision_a": {{"title": "...", "content": "..."}},
    "decision_b": {{"title": "...", "content": "..."}},
    "label": "{pair_type}",
    "label_rationale": "...",
    "shared_dimensions": ["..."]
  }},
  ...
]"""


def generate_pairs(
    decision: Decision,
    *,
    pair_type: str = "contradiction",
    count: int = 2,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Generate synthetic decision pairs from a seed decision.

    Args:
        decision: Real decision to use as seed
        pair_type: One of "contradiction", "tension", "hard_negative", "compatible"
        count: Number of pairs to generate
        model: Claude model to use
        api_key: Anthropic API key (falls back to env var)

    Returns:
        List of pair dicts with decision_a, decision_b, label, label_rationale,
        shared_dimensions.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.warning("No ANTHROPIC_API_KEY, cannot generate synthetic pairs")
        return []

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed")
        return []

    client = anthropic.Anthropic(api_key=key)

    user_msg = _USER_TEMPLATE.format(
        title=decision.title,
        content=decision.content,
        dimensions=", ".join(d.value for d in decision.dimensions),
        pair_type=pair_type,
        count=count,
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text
        pairs = _parse_pairs(raw)

        # Attach metadata
        for p in pairs:
            p["seed_decision_id"] = str(decision.id)
            p["seed_dimensions"] = [d.value for d in decision.dimensions]
            p["model"] = model

        return pairs
    except Exception:
        logger.exception("ContraGen generation failed for %s", decision.title)
        return []


def generate_all_pairs(
    decision: Decision,
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    """Generate all pair types for a single seed decision.

    Produces 8 total pairs: 2 contradictions + 2 tensions + 2 hard negatives
    + 2 compatible pairs.
    """
    all_pairs: list[dict[str, Any]] = []
    for pair_type, count in PAIR_TYPES:
        pairs = generate_pairs(
            decision,
            pair_type=pair_type,
            count=count,
            model=model,
            api_key=api_key,
        )
        all_pairs.extend(pairs)
    return all_pairs


def generate_dataset(
    decisions: list[Decision],
    output_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> DatasetStats:
    """Generate a full synthetic dataset from multiple seed decisions.

    Writes labeled JSONL to output_path. Each line is a pair dict.
    Returns stats about what was generated.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stats = DatasetStats()
    with open(output_path, "w") as f:
        for decision in decisions:
            pairs = generate_all_pairs(decision, model=model, api_key=api_key)
            for pair in pairs:
                f.write(json.dumps(pair) + "\n")
                stats.total += 1
                label = pair.get("label", "unknown")
                stats.by_type[label] = stats.by_type.get(label, 0) + 1

    stats.seed_decisions = len(decisions)
    logger.info(
        "ContraGen: wrote %d pairs from %d seeds to %s",
        stats.total, stats.seed_decisions, output_path,
    )
    return stats


class DatasetStats:
    """Statistics from a ContraGen run."""

    def __init__(self) -> None:
        self.total: int = 0
        self.seed_decisions: int = 0
        self.by_type: dict[str, int] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_pairs": self.total,
            "seed_decisions": self.seed_decisions,
            "by_type": self.by_type,
        }


def validate_pair(pair: dict[str, Any]) -> list[str]:
    """Validate a generated pair for structural correctness.

    Returns list of error strings (empty = valid).
    """
    errors: list[str] = []

    for key in ("decision_a", "decision_b", "label", "label_rationale"):
        if key not in pair:
            errors.append(f"Missing required field: {key}")

    if "label" in pair:
        valid_labels = {"contradiction", "tension", "hard_negative", "compatible"}
        if pair["label"] not in valid_labels:
            errors.append(f"Invalid label: {pair['label']}")

    for side in ("decision_a", "decision_b"):
        if side in pair and isinstance(pair[side], dict):
            for field in ("title", "content"):
                if field not in pair[side]:
                    errors.append(f"Missing {side}.{field}")
        elif side in pair:
            errors.append(f"{side} must be a dict")

    return errors


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL dataset file."""
    if not path.exists():
        return []
    pairs = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_pairs(raw: str) -> list[dict[str, Any]]:
    """Parse LLM JSON response into list of pair dicts."""
    import re

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)

    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        raise ContradictionDetectionError(f"No JSON array in ContraGen response: {raw[:200]}")

    pairs = json.loads(match.group(0))
    if not isinstance(pairs, list):
        raise ContradictionDetectionError("ContraGen response is not a JSON array")

    # Validate each pair
    valid_pairs = []
    for pair in pairs:
        errors = validate_pair(pair)
        if errors:
            logger.warning("Skipping invalid pair: %s", errors)
            continue
        valid_pairs.append(pair)

    return valid_pairs

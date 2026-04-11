"""Contradiction detection pipeline.

Two-stage pipeline:
  Stage 1: NLI cross-encoder pre-filter (skip LLM if score < 0.3)
  Stage 2: LLM call to Claude Haiku 4.5 for structured judgment

Both stages are optional with graceful fallback:
  - No torch/sentence-transformers → skip NLI, go straight to LLM
  - No ANTHROPIC_API_KEY → skip LLM, return COMPATIBLE with low confidence

From SPEC T7: "Single LLM call with structured output (reasoning before
verdict, ternary judgment). NLI cross-encoder pre-filter cuts 60-80% of
LLM calls. ~$0.002/check blended cost."
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionVerdict,
    Decision,
    Dimension,
)
from vt_protocol.exceptions import ContradictionDetectionError

logger = logging.getLogger(__name__)

# NLI score below this threshold → skip LLM (assume COMPATIBLE)
NLI_THRESHOLD = 0.3

# LLM confidence below this → route to human review
CONFIDENCE_THRESHOLD = 0.7

# Default model for contradiction detection
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Self-consistency voting: trigger when initial confidence < this threshold
VOTING_THRESHOLD = 0.6

# Number of additional voting calls
VOTING_ROUNDS = 3

# Temperature for voting calls (higher for diversity)
VOTING_TEMPERATURE = 0.7

@dataclass
class NLIDistribution:
    """Full softmax distribution from NLI cross-encoder.

    Instead of a hard 0.3 cutoff, the full distribution allows:
    - Passing soft labels to LLM context for nuanced judgment
    - Graceful degradation for vague/ambiguous constraints
    - Better calibration by preserving uncertainty information

    From Madaan et al. 2025: "Full softmax distribution instead of hard
    threshold enables graceful degradation for vague constraints."
    """

    contradiction: float = 0.0
    entailment: float = 0.0
    neutral: float = 0.0

    @property
    def dominant_label(self) -> str:
        """Return the label with highest probability."""
        labels = {
            "contradiction": self.contradiction,
            "entailment": self.entailment,
            "neutral": self.neutral,
        }
        return max(labels, key=labels.get)  # type: ignore[arg-type]

    @property
    def entropy(self) -> float:
        """Shannon entropy of the distribution — high = uncertain."""
        import math

        total = 0.0
        for p in (self.contradiction, self.entailment, self.neutral):
            if p > 0:
                total -= p * math.log2(p)
        return total

    @property
    def is_ambiguous(self) -> bool:
        """True if no label has >0.5 probability — highly uncertain."""
        return max(self.contradiction, self.entailment, self.neutral) < 0.5

    def to_dict(self) -> dict[str, float]:
        return {
            "contradiction": round(self.contradiction, 4),
            "entailment": round(self.entailment, 4),
            "neutral": round(self.neutral, 4),
        }


_SYSTEM_PROMPT = """\
You are an architectural decision contradiction detector. You compare two \
software decisions and determine if they contradict each other.

CRITICAL INSTRUCTIONS:
1. Default to COMPATIBLE — only flag genuine contradictions
2. Tension means "these pull in different directions but could coexist"
3. Contradiction means "these cannot both be true simultaneously"
4. You MUST provide reasoning BEFORE your verdict
5. You MUST cite specific evidence from each decision

Respond with valid JSON only (no markdown fences):
{
  "reasoning": "Step-by-step analysis of whether these decisions conflict...",
  "verdict": "compatible" | "tension" | "contradiction",
  "confidence": 0.0-1.0,
  "evidence_a": "Specific quote or paraphrase from Decision A",
  "evidence_b": "Specific quote or paraphrase from Decision B"
}"""

_USER_TEMPLATE = """\
Decision A: "{title_a}"
{content_a}

Decision B: "{title_b}"
{content_b}

Shared dimensions: {dimensions}

Are these decisions contradictory, in tension, or compatible?"""

_USER_TEMPLATE_WITH_NLI = """\
Decision A: "{title_a}"
{content_a}

Decision B: "{title_b}"
{content_b}

Shared dimensions: {dimensions}

NLI pre-analysis (automated):
  Contradiction probability: {nli_contradiction:.1%}
  Entailment probability: {nli_entailment:.1%}
  Neutral probability: {nli_neutral:.1%}
  Note: {nli_note}

Use the NLI pre-analysis as a signal, but make your own independent judgment.
Are these decisions contradictory, in tension, or compatible?"""


# ---------------------------------------------------------------------------
# Stage 1: NLI cross-encoder pre-filter
# ---------------------------------------------------------------------------


def nli_score(text_a: str, text_b: str) -> float | None:
    """Compute NLI contradiction score using cross-encoder.

    Returns the contradiction probability (0-1), or None if the model
    is not available (no torch/sentence-transformers installed).

    Uses cross-encoder/nli-deberta-v3-base.
    """
    dist = nli_distribution(text_a, text_b)
    if dist is None:
        return None
    return dist.contradiction


def nli_distribution(text_a: str, text_b: str) -> NLIDistribution | None:
    """Compute full NLI softmax distribution using cross-encoder.

    Returns NLIDistribution with contradiction, entailment, neutral
    probabilities, or None if the model is not available.

    From Madaan et al. 2025: full distribution instead of hard cutoff
    enables graceful degradation for vague constraints.
    """
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        logger.debug("sentence-transformers not installed, skipping NLI")
        return None

    try:
        model = _get_nli_model()
        # CrossEncoder.predict returns [contradiction, entailment, neutral]
        scores = model.predict([(text_a, text_b)])
        return NLIDistribution(
            contradiction=float(scores[0][0]),
            entailment=float(scores[0][1]),
            neutral=float(scores[0][2]),
        )
    except Exception:
        logger.exception("NLI scoring failed")
        return None


_nli_model: Any = None


def _get_nli_model() -> Any:
    """Lazy-load the NLI cross-encoder model (singleton)."""
    global _nli_model
    if _nli_model is None:
        from sentence_transformers import CrossEncoder
        _nli_model = CrossEncoder("cross-encoder/nli-deberta-v3-base")
    return _nli_model


def reset_nli_model() -> None:
    """Reset the cached NLI model (for testing)."""
    global _nli_model
    _nli_model = None


# ---------------------------------------------------------------------------
# Stage 2: LLM contradiction judgment
# ---------------------------------------------------------------------------


def llm_check(
    decision_a: Decision,
    decision_b: Decision,
    shared_dimensions: list[Dimension],
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
    temperature: float | None = None,
    nli_distribution: NLIDistribution | None = None,
) -> dict[str, Any] | None:
    """Call Claude for structured contradiction judgment.

    Returns parsed JSON dict with reasoning, verdict, confidence, evidence_a,
    evidence_b. Returns None if the API key is missing or the call fails.

    When nli_distribution is provided, soft labels are included in the prompt
    to give the LLM additional context (Madaan et al. 2025).
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.debug("No ANTHROPIC_API_KEY, skipping LLM contradiction check")
        return None

    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed, skipping LLM check")
        return None

    client = anthropic.Anthropic(api_key=key)

    dims_str = ", ".join(d.value for d in shared_dimensions) or "none"

    # Use soft-label template when NLI distribution is available
    if nli_distribution is not None:
        nli_note = (
            "High uncertainty — consider both possibilities"
            if nli_distribution.is_ambiguous
            else f"Dominant signal: {nli_distribution.dominant_label}"
        )
        user_msg = _USER_TEMPLATE_WITH_NLI.format(
            title_a=decision_a.title,
            content_a=decision_a.content,
            title_b=decision_b.title,
            content_b=decision_b.content,
            dimensions=dims_str,
            nli_contradiction=nli_distribution.contradiction,
            nli_entailment=nli_distribution.entailment,
            nli_neutral=nli_distribution.neutral,
            nli_note=nli_note,
        )
    else:
        user_msg = _USER_TEMPLATE.format(
            title_a=decision_a.title,
            content_a=decision_a.content,
            title_b=decision_b.title,
            content_b=decision_b.content,
            dimensions=dims_str,
        )

    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 1024,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_msg}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = client.messages.create(**kwargs)
        raw = response.content[0].text
        return _parse_llm_response(raw)
    except Exception:
        logger.exception("LLM contradiction check failed")
        return None


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """Parse LLM JSON response, stripping markdown fences if present."""
    import re

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ContradictionDetectionError(f"No JSON found in LLM response: {raw[:200]}")

    data = json.loads(match.group(0))

    # Validate required fields
    for field in ("reasoning", "verdict", "confidence", "evidence_a", "evidence_b"):
        if field not in data:
            raise ContradictionDetectionError(f"Missing '{field}' in LLM response")

    # Normalize verdict
    verdict_raw = data["verdict"].lower().strip()
    if verdict_raw not in ("contradiction", "tension", "compatible"):
        raise ContradictionDetectionError(f"Invalid verdict: {verdict_raw}")
    data["verdict"] = verdict_raw

    return data


# ---------------------------------------------------------------------------
# Combined pipeline
# ---------------------------------------------------------------------------


def check_contradiction(
    decision_a: Decision,
    decision_b: Decision,
    *,
    skip_nli: bool = False,
    skip_llm: bool = False,
    skip_voting: bool = False,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> Contradiction | None:
    """Run the full contradiction detection pipeline.

    Stage 1 (NLI): If available and score < NLI_THRESHOLD, return None
        (assumed compatible, no LLM call needed).
    Stage 2 (LLM): Get structured judgment with reasoning, verdict,
        evidence citations. NLI soft labels are passed to the LLM
        when available (Madaan et al. 2025).

    Returns a Contradiction model if a contradiction or tension is detected,
    or None if the decisions are compatible.

    Both stages degrade gracefully:
    - No torch → skip NLI, proceed to LLM
    - No API key → return None (assume compatible)
    """
    shared_dims = list(set(decision_a.dimensions) & set(decision_b.dimensions))

    # Stage 1: NLI pre-filter with soft labels
    nli_dist: NLIDistribution | None = None
    if not skip_nli:
        text_a = f"{decision_a.title}. {decision_a.content}"
        text_b = f"{decision_b.title}. {decision_b.content}"
        nli_dist = nli_distribution(text_a, text_b)
        if nli_dist is not None and nli_dist.contradiction < NLI_THRESHOLD:
            logger.debug(
                "NLI score %.3f < %.3f for (%s, %s) — skipping LLM",
                nli_dist.contradiction, NLI_THRESHOLD,
                decision_a.title, decision_b.title,
            )
            return None

    # Stage 2: LLM judgment (with soft NLI labels when available)
    if skip_llm:
        return None

    result = llm_check(
        decision_a, decision_b, shared_dims,
        model=model, api_key=api_key,
        nli_distribution=nli_dist,
    )
    if result is None:
        return None

    verdict = ContradictionVerdict(result["verdict"])
    if verdict == ContradictionVerdict.COMPATIBLE:
        return None

    confidence = float(result["confidence"])

    # Self-consistency voting: if low confidence, run 3 more calls and majority-vote
    if confidence < VOTING_THRESHOLD and not skip_voting:
        voted = _self_consistency_vote(
            decision_a, decision_b, shared_dims,
            initial_result=result,
            model=model,
            api_key=api_key,
        )
        if voted is not None:
            result = voted
            verdict = ContradictionVerdict(result["verdict"])
            confidence = float(result["confidence"])
            if verdict == ContradictionVerdict.COMPATIBLE:
                return None

    return Contradiction(
        decision_a_id=decision_a.id,
        decision_b_id=decision_b.id,
        decision_a_title=decision_a.title,
        decision_b_title=decision_b.title,
        verdict=verdict,
        reasoning=result["reasoning"],
        evidence_a=result["evidence_a"],
        evidence_b=result["evidence_b"],
        shared_dimensions=shared_dims,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Self-consistency voting
# ---------------------------------------------------------------------------


def _self_consistency_vote(
    decision_a: Decision,
    decision_b: Decision,
    shared_dims: list[Dimension],
    *,
    initial_result: dict[str, Any],
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> dict[str, Any] | None:
    """Run multiple LLM calls at higher temperature and majority-vote on verdict.

    Only triggered for low-confidence initial calls (< VOTING_THRESHOLD).
    Runs VOTING_ROUNDS additional calls at VOTING_TEMPERATURE, then picks
    the majority verdict. Returns the winning result dict, or None on failure.
    """
    all_results = [initial_result]

    for _ in range(VOTING_ROUNDS):
        r = llm_check(
            decision_a, decision_b, shared_dims,
            model=model,
            api_key=api_key,
            temperature=VOTING_TEMPERATURE,
        )
        if r is not None:
            all_results.append(r)

    if len(all_results) < 2:
        return None

    # Count verdicts
    from collections import Counter
    verdict_counts = Counter(r["verdict"] for r in all_results)
    winner_verdict, winner_count = verdict_counts.most_common(1)[0]

    # Pick the result with highest confidence among the winning verdict
    winning_results = [r for r in all_results if r["verdict"] == winner_verdict]
    best = max(winning_results, key=lambda r: float(r["confidence"]))

    # Adjust confidence based on agreement ratio
    agreement = winner_count / len(all_results)
    best = dict(best)
    best["confidence"] = round(float(best["confidence"]) * agreement, 3)
    best["_voting"] = {
        "total_calls": len(all_results),
        "verdict_counts": dict(verdict_counts),
        "agreement": agreement,
    }

    logger.info(
        "Self-consistency vote: %s (agreement %.0f%%, %d/%d calls)",
        winner_verdict, agreement * 100, winner_count, len(all_results),
    )
    return best

"""Architecture DNA fingerprinting.

Generate a fixed-length vector encoding of a codebase's architectural
decisions. Compare fingerprints via cosine similarity for M&A due diligence,
tech debt assessment, and compatibility analysis.

From SPEC Sprint 24: "Architecture DNA fingerprinting."
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Fingerprint vector dimensions (order matters — fixed positions)
FINGERPRINT_DIMENSIONS: list[str] = [
    "database",
    "auth",
    "caching",
    "api-style",
    "deployment",
    "concurrency",
    "logging",
    "testing",
    "error-handling",
    "state-management",
    "messaging",
    "security",
]

# Additional fingerprint features beyond core dimensions
FINGERPRINT_FEATURES: list[str] = [
    "decision_count",
    "contradiction_rate",
    "avg_confidence",
    "dimension_coverage",
    "governance_maturity",
    "agent_diversity",
    "decision_velocity",
    "resolution_rate",
]

VECTOR_LENGTH = len(FINGERPRINT_DIMENSIONS) + len(FINGERPRINT_FEATURES)


@dataclass
class ArchFingerprint:
    """A fixed-length vector encoding of architectural decisions."""

    project_id: str = ""
    vector: list[float] = field(default_factory=lambda: [0.0] * VECTOR_LENGTH)
    dimension_scores: dict[str, float] = field(default_factory=dict)
    feature_scores: dict[str, float] = field(default_factory=dict)
    decision_count: int = 0
    fingerprint_hash: str = ""

    @property
    def vector_length(self) -> int:
        return len(self.vector)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "vector": [round(v, 4) for v in self.vector],
            "dimension_scores": {k: round(v, 4) for k, v in self.dimension_scores.items()},
            "feature_scores": {k: round(v, 4) for k, v in self.feature_scores.items()},
            "decision_count": self.decision_count,
            "fingerprint_hash": self.fingerprint_hash,
        }


@dataclass
class SimilarityResult:
    """Result of comparing two fingerprints."""

    project_a: str = ""
    project_b: str = ""
    cosine_similarity: float = 0.0
    dimension_similarities: dict[str, float] = field(default_factory=dict)
    compatible: bool = False  # similarity > threshold
    compatibility_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_a": self.project_a,
            "project_b": self.project_b,
            "cosine_similarity": round(self.cosine_similarity, 4),
            "dimension_similarities": {
                k: round(v, 4) for k, v in self.dimension_similarities.items()
            },
            "compatible": self.compatible,
            "compatibility_notes": self.compatibility_notes,
        }


COMPATIBILITY_THRESHOLD = 0.7


def generate_fingerprint(
    *,
    project_id: str,
    decisions: list[dict[str, Any]],
    contradictions: list[dict[str, Any]] | None = None,
) -> ArchFingerprint:
    """Generate an architecture DNA fingerprint from decisions."""
    fp = ArchFingerprint(project_id=project_id, decision_count=len(decisions))
    contradictions = contradictions or []

    # Score each core dimension
    dim_counts: dict[str, int] = {}
    dim_confidences: dict[str, list[float]] = {}
    agent_types: set[str] = set()

    for d in decisions:
        for dim in d.get("dimensions", []):
            dim_counts[dim] = dim_counts.get(dim, 0) + 1
            dim_confidences.setdefault(dim, []).append(d.get("confidence", 0.0))
        agent_types.add(d.get("source_type", "unknown"))

    total_decisions = max(len(decisions), 1)

    # Fill dimension scores (positions 0..11)
    for i, dim in enumerate(FINGERPRINT_DIMENSIONS):
        count = dim_counts.get(dim, 0)
        confs = dim_confidences.get(dim, [])
        avg_conf = sum(confs) / max(len(confs), 1)
        # Score = normalized count * average confidence
        score = min(1.0, count / total_decisions) * avg_conf
        fp.vector[i] = score
        fp.dimension_scores[dim] = score

    # Fill feature scores (positions 12..19)
    base = len(FINGERPRINT_DIMENSIONS)

    # decision_count (normalized: log scale, cap at 1000)
    fp.vector[base + 0] = min(1.0, math.log1p(len(decisions)) / math.log1p(1000))
    fp.feature_scores["decision_count"] = fp.vector[base + 0]

    # contradiction_rate
    contra_rate = len(contradictions) / total_decisions if decisions else 0.0
    fp.vector[base + 1] = min(1.0, contra_rate)
    fp.feature_scores["contradiction_rate"] = fp.vector[base + 1]

    # avg_confidence
    all_confs = [d.get("confidence", 0.0) for d in decisions]
    avg_conf = sum(all_confs) / max(len(all_confs), 1)
    fp.vector[base + 2] = avg_conf
    fp.feature_scores["avg_confidence"] = avg_conf

    # dimension_coverage (fraction of 12 dimensions used)
    dims_used = len([d for d in FINGERPRINT_DIMENSIONS if dim_counts.get(d, 0) > 0])
    fp.vector[base + 3] = dims_used / len(FINGERPRINT_DIMENSIONS)
    fp.feature_scores["dimension_coverage"] = fp.vector[base + 3]

    # governance_maturity (heuristic: coverage * confidence * resolution)
    resolution_rate = 0.0
    if contradictions:
        resolved = sum(1 for c in contradictions if c.get("status") == "resolved")
        resolution_rate = resolved / len(contradictions)
    maturity = fp.vector[base + 3] * avg_conf * (0.5 + 0.5 * resolution_rate)
    fp.vector[base + 4] = maturity
    fp.feature_scores["governance_maturity"] = maturity

    # agent_diversity
    fp.vector[base + 5] = min(1.0, len(agent_types) / 5.0)
    fp.feature_scores["agent_diversity"] = fp.vector[base + 5]

    # decision_velocity (decisions per dimension)
    fp.vector[base + 6] = min(1.0, total_decisions / max(dims_used * 5, 1))
    fp.feature_scores["decision_velocity"] = fp.vector[base + 6]

    # resolution_rate
    fp.vector[base + 7] = resolution_rate
    fp.feature_scores["resolution_rate"] = resolution_rate

    # Compute hash
    vec_str = ",".join(f"{v:.6f}" for v in fp.vector)
    fp.fingerprint_hash = hashlib.sha256(vec_str.encode()).hexdigest()[:16]

    return fp


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def compare_fingerprints(
    fp_a: ArchFingerprint,
    fp_b: ArchFingerprint,
    *,
    threshold: float = COMPATIBILITY_THRESHOLD,
) -> SimilarityResult:
    """Compare two architecture fingerprints."""
    sim = cosine_similarity(fp_a.vector, fp_b.vector)

    # Per-dimension similarities
    dim_sims: dict[str, float] = {}
    for dim in FINGERPRINT_DIMENSIONS:
        score_a = fp_a.dimension_scores.get(dim, 0.0)
        score_b = fp_b.dimension_scores.get(dim, 0.0)
        if score_a == 0.0 and score_b == 0.0:
            dim_sims[dim] = 1.0  # both absent = compatible
        else:
            max_score = max(score_a, score_b)
            dim_sims[dim] = 1.0 - abs(score_a - score_b) / max(max_score, 0.001)

    # Compatibility notes
    notes: list[str] = []
    for dim, dim_sim in dim_sims.items():
        if dim_sim < 0.3:
            notes.append(f"Major divergence in '{dim}' — review required")
        elif dim_sim < 0.6:
            notes.append(f"Moderate difference in '{dim}'")

    return SimilarityResult(
        project_a=fp_a.project_id,
        project_b=fp_b.project_id,
        cosine_similarity=sim,
        dimension_similarities=dim_sims,
        compatible=sim >= threshold,
        compatibility_notes=notes,
    )

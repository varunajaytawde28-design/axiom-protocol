"""PM view — Living Specifications.

Upload/paste a PRD, map requirements to decisions via embedding cosine
similarity, track coverage status (green/yellow/red/orange).

From SPEC Sprint 16: "PM view — Living Specifications."
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from vt_protocol.decisions.models import Decision

logger = logging.getLogger(__name__)


class CoverageStatus(str, Enum):
    """Coverage status for a single requirement."""

    IMPLEMENTED = "implemented"  # green — matched decision exists
    PARTIAL = "partial"  # yellow — weak match
    NOT_STARTED = "not_started"  # red — no match
    DIVERGED = "diverged"  # orange — contradicts a decision


@dataclass
class Requirement:
    """A single requirement extracted from a PRD."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    text: str = ""
    section: str = ""
    index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "section": self.section,
            "index": self.index,
        }


@dataclass
class RequirementCoverage:
    """Coverage mapping for a single requirement."""

    requirement: Requirement
    status: CoverageStatus = CoverageStatus.NOT_STARTED
    matched_decision_id: str | None = None
    matched_decision_title: str | None = None
    similarity_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement.to_dict(),
            "status": self.status.value,
            "matched_decision_id": self.matched_decision_id,
            "matched_decision_title": self.matched_decision_title,
            "similarity_score": round(self.similarity_score, 4),
        }


@dataclass
class Specification:
    """A product specification (PRD)."""

    id: str = field(default_factory=lambda: uuid4().hex[:16])
    title: str = ""
    raw_text: str = ""
    requirements: list[Requirement] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def requirement_count(self) -> int:
        return len(self.requirements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "requirement_count": self.requirement_count,
            "requirements": [r.to_dict() for r in self.requirements],
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CoverageReport:
    """Full coverage report for a specification."""

    spec_id: str = ""
    coverages: list[RequirementCoverage] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.coverages)

    @property
    def implemented_count(self) -> int:
        return sum(1 for c in self.coverages if c.status == CoverageStatus.IMPLEMENTED)

    @property
    def partial_count(self) -> int:
        return sum(1 for c in self.coverages if c.status == CoverageStatus.PARTIAL)

    @property
    def not_started_count(self) -> int:
        return sum(1 for c in self.coverages if c.status == CoverageStatus.NOT_STARTED)

    @property
    def diverged_count(self) -> int:
        return sum(1 for c in self.coverages if c.status == CoverageStatus.DIVERGED)

    @property
    def coverage_percent(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.implemented_count + 0.5 * self.partial_count) / self.total * 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "total": self.total,
            "implemented": self.implemented_count,
            "partial": self.partial_count,
            "not_started": self.not_started_count,
            "diverged": self.diverged_count,
            "coverage_percent": round(self.coverage_percent, 1),
            "coverages": [c.to_dict() for c in self.coverages],
        }


# ---------------------------------------------------------------------------
# Requirement extraction
# ---------------------------------------------------------------------------


def extract_requirements(text: str, *, title: str = "") -> Specification:
    """Extract requirements from PRD text (plain text or Markdown).

    Splits on:
    - Numbered items (1. ... or 1) ...)
    - Bullet points (- or * at line start)
    - Sentences containing modal verbs (must, shall, should, will)
    """
    spec = Specification(title=title or "Untitled Spec", raw_text=text)

    lines = text.strip().split("\n")
    current_section = ""
    raw_items: list[tuple[str, str]] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Detect markdown headers as section names
        header_match = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if header_match:
            current_section = header_match.group(1).strip()
            continue

        # Numbered or bulleted items
        item_match = re.match(r"^(?:\d+[.)]\s+|[-*]\s+)(.+)$", stripped)
        if item_match:
            raw_items.append((item_match.group(1).strip(), current_section))
            continue

        # Sentences with modal verbs
        if re.search(r"\b(must|shall|should|will|need to|require)\b", stripped, re.IGNORECASE):
            # Split on sentence boundaries
            sentences = re.split(r"(?<=[.!?])\s+", stripped)
            for sent in sentences:
                if re.search(r"\b(must|shall|should|will|need to|require)\b", sent, re.IGNORECASE):
                    raw_items.append((sent.strip(), current_section))

    for i, (text_item, section) in enumerate(raw_items):
        if len(text_item) < 5:
            continue
        spec.requirements.append(Requirement(
            text=text_item,
            section=section,
            index=i,
        ))

    return spec


# ---------------------------------------------------------------------------
# Similarity and coverage computation
# ---------------------------------------------------------------------------

# Similarity thresholds
IMPLEMENTED_THRESHOLD = 0.6
PARTIAL_THRESHOLD = 0.3


def _keyword_similarity(text_a: str, text_b: str) -> float:
    """Simple keyword overlap similarity as fallback when embeddings unavailable."""
    words_a = set(re.findall(r"\w{3,}", text_a.lower()))
    words_b = set(re.findall(r"\w{3,}", text_b.lower()))
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def compute_coverage(
    spec: Specification,
    decisions: list[Decision],
    *,
    similarity_fn: Any | None = None,
) -> CoverageReport:
    """Map each requirement to the best-matching decision.

    Uses cosine similarity via embeddings when available,
    falls back to keyword overlap.
    """
    sim_fn = similarity_fn or _keyword_similarity

    report = CoverageReport(spec_id=spec.id)

    for req in spec.requirements:
        best_score = 0.0
        best_decision: Decision | None = None

        for decision in decisions:
            decision_text = f"{decision.title}. {decision.content}"
            score = sim_fn(req.text, decision_text)
            if score > best_score:
                best_score = score
                best_decision = decision

        if best_score >= IMPLEMENTED_THRESHOLD and best_decision:
            status = CoverageStatus.IMPLEMENTED
        elif best_score >= PARTIAL_THRESHOLD and best_decision:
            status = CoverageStatus.PARTIAL
        else:
            status = CoverageStatus.NOT_STARTED

        coverage = RequirementCoverage(
            requirement=req,
            status=status,
            matched_decision_id=str(best_decision.id) if best_decision else None,
            matched_decision_title=best_decision.title if best_decision else None,
            similarity_score=best_score,
        )
        report.coverages.append(coverage)

    return report


# ---------------------------------------------------------------------------
# In-memory spec store
# ---------------------------------------------------------------------------


class SpecStore:
    """Simple in-memory store for specifications."""

    def __init__(self) -> None:
        self._specs: dict[str, Specification] = {}

    def add(self, spec: Specification) -> str:
        self._specs[spec.id] = spec
        return spec.id

    def get(self, spec_id: str) -> Specification | None:
        return self._specs.get(spec_id)

    def list_specs(self) -> list[Specification]:
        return list(self._specs.values())

    @property
    def count(self) -> int:
        return len(self._specs)

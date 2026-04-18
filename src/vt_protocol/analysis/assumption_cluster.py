"""Unsupervised Alert Clustering for Domain Assumptions.

Groups semantically similar assumptions to reduce noise. Instead of showing
15 null-check warnings individually, this module collapses them into 1 cluster
with a single representative question (the most severe member's question).

Clustering algorithm:
1. Group by (pattern_id, file_path) for exact file-level clusters.
2. If a file group has >10 members (auto-cluster threshold), collapse immediately.
3. For remaining small groups, merge groups sharing (pattern_id, directory).
4. Singletons (count=1) remain as individual assumptions.
5. Sort: severity desc (critical>high>medium>low), then confidence desc.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

from vt_protocol.decisions.models import DomainAssumption, AssumptionStatus  # noqa: F401

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_ORDER: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

AUTO_CLUSTER_THRESHOLD: int = 10

# ---------------------------------------------------------------------------
# Cluster dataclass
# ---------------------------------------------------------------------------


@dataclass
class AssumptionCluster:
    """A group of semantically similar assumptions collapsed into one alert.

    Attributes:
        cluster_id: Unique identifier for the cluster (pattern_id::file_or_dir).
        assumptions: All assumptions belonging to this cluster.
        representative: The highest-severity member (ties broken by confidence).
        severity: Max severity across all members.
        confidence: Average confidence across all members.
        file_pattern: Common file path or directory shared by members.
        pattern_id: The shared detection rule ID.
        count: Number of assumptions in the cluster.
        question: The representative assumption's question.
        options: The representative assumption's options.
    """

    cluster_id: str
    assumptions: list[DomainAssumption]
    representative: DomainAssumption
    severity: str
    confidence: float
    file_pattern: str
    pattern_id: str
    count: int
    question: str
    options: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _severity_rank(severity: str) -> int:
    """Return numeric rank for a severity string (higher = more severe)."""
    return SEVERITY_ORDER.get(severity.lower(), 0)


def _max_severity(assumptions: list[DomainAssumption]) -> str:
    """Return the highest severity string from a list of assumptions."""
    if not assumptions:
        return "low"
    return max(assumptions, key=lambda a: _severity_rank(a.severity)).severity


def _avg_confidence(assumptions: list[DomainAssumption]) -> float:
    """Return the average confidence across assumptions."""
    if not assumptions:
        return 0.0
    return sum(a.confidence for a in assumptions) / len(assumptions)


def _pick_representative(assumptions: list[DomainAssumption]) -> DomainAssumption:
    """Pick the single most important assumption as the cluster representative.

    Selection order:
    1. Highest severity rank.
    2. Tie-break by highest confidence.
    3. Further tie-break by earliest detection time.
    """
    return max(
        assumptions,
        key=lambda a: (
            _severity_rank(a.severity),
            a.confidence,
            # Earlier detected_at should win ties, so negate the timestamp.
            -(a.detected_at.timestamp() if a.detected_at else 0),
        ),
    )


def _primary_file(assumption: DomainAssumption) -> str:
    """Return the first file from code_evidence, or empty string."""
    if assumption.code_evidence:
        return assumption.code_evidence[0].file
    return ""


def _file_directory(file_path: str) -> str:
    """Return the directory portion of a file path."""
    if not file_path:
        return ""
    return os.path.dirname(file_path)


def _build_cluster(
    cluster_id: str,
    assumptions: list[DomainAssumption],
    file_pattern: str,
    pattern_id: str,
) -> AssumptionCluster:
    """Construct an AssumptionCluster from a list of assumptions."""
    rep = _pick_representative(assumptions)
    return AssumptionCluster(
        cluster_id=cluster_id,
        assumptions=list(assumptions),
        representative=rep,
        severity=_max_severity(assumptions),
        confidence=round(_avg_confidence(assumptions), 4),
        file_pattern=file_pattern,
        pattern_id=pattern_id,
        count=len(assumptions),
        question=rep.question,
        options=list(rep.options),
    )


def _sort_clusters(clusters: list[AssumptionCluster]) -> list[AssumptionCluster]:
    """Sort clusters by severity descending, then confidence descending."""
    return sorted(
        clusters,
        key=lambda c: (_severity_rank(c.severity), c.confidence),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cluster_assumptions(
    assumptions: list[DomainAssumption],
) -> list[AssumptionCluster]:
    """Group semantically similar assumptions into clusters.

    Algorithm:
    1. Group by (pattern_id, primary_file) to form exact file-level groups.
    2. Any file group exceeding AUTO_CLUSTER_THRESHOLD (10) is immediately
       collapsed into a single cluster.
    3. Remaining small file groups are merged if they share the same
       pattern_id AND the same parent directory.
    4. Groups with count == 1 stay as singleton clusters.
    5. Results are sorted by severity (critical > high > medium > low),
       then by average confidence descending.

    Args:
        assumptions: Flat list of DomainAssumption instances.

    Returns:
        Sorted list of AssumptionCluster instances.
    """
    if not assumptions:
        return []

    # ---- Step 1: Group by (pattern_id, file) ----
    file_groups: dict[tuple[str, str], list[DomainAssumption]] = defaultdict(list)
    for a in assumptions:
        key = (a.pattern_id, _primary_file(a))
        file_groups[key].append(a)

    # ---- Step 2: Auto-cluster large file groups (>10) ----
    clusters: list[AssumptionCluster] = []
    remaining_groups: dict[tuple[str, str], list[DomainAssumption]] = {}

    for (pid, fpath), group in file_groups.items():
        if len(group) > AUTO_CLUSTER_THRESHOLD:
            cid = f"{pid}::{fpath}"
            clusters.append(_build_cluster(cid, group, fpath, pid))
        else:
            remaining_groups[(pid, fpath)] = group

    # ---- Step 3: Merge small groups by (pattern_id, directory) ----
    dir_buckets: dict[tuple[str, str], list[tuple[str, list[DomainAssumption]]]] = defaultdict(list)
    for (pid, fpath), group in remaining_groups.items():
        dkey = (pid, _file_directory(fpath))
        dir_buckets[dkey].append((fpath, group))

    for (pid, dirpath), file_entries in dir_buckets.items():
        # Merge all file entries in this directory for this pattern_id
        merged: list[DomainAssumption] = []
        file_paths: list[str] = []
        for fpath, group in file_entries:
            merged.extend(group)
            if fpath:
                file_paths.append(fpath)

        if len(file_entries) > 1 and len(merged) > 1:
            # Multiple files in the same directory with the same pattern — merge
            common = dirpath if dirpath else (file_paths[0] if file_paths else "")
            cid = f"{pid}::{common}/*"
            clusters.append(_build_cluster(cid, merged, common, pid))
        else:
            # Single file group (or singleton) — keep as-is
            for fpath, group in file_entries:
                if len(group) == 1:
                    # Singleton: still wrap in a cluster for uniform interface
                    a = group[0]
                    cid = f"{pid}::{fpath}"
                    clusters.append(_build_cluster(cid, group, fpath, pid))
                else:
                    # Small multi-member group in a single file
                    cid = f"{pid}::{fpath}"
                    clusters.append(_build_cluster(cid, group, fpath, pid))

    # ---- Step 5: Sort ----
    return _sort_clusters(clusters)


def flatten_clusters(
    clusters: list[AssumptionCluster],
) -> list[DomainAssumption]:
    """Extract the representative assumption from each cluster.

    Use this to get one assumption per cluster for display purposes. The
    returned list preserves the cluster sort order (severity desc, confidence
    desc).

    Args:
        clusters: List of AssumptionCluster instances (typically from
            ``cluster_assumptions``).

    Returns:
        List of DomainAssumption instances, one per cluster.
    """
    return [c.representative for c in clusters]

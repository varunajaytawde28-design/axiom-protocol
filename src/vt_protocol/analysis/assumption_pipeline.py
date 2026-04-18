"""Pipeline orchestrator for domain assumption detection and resolution.

Runs the full assumption lifecycle:
  1. Scan code for implicit domain assumptions
  2. Filter by tiered confidence thresholds (architectural vs implementation)
  3. Filter shadow-mode patterns (statistical auto-disable)
  4. Deduplicate against existing assumptions
  5. Freeze-on-adopt baseline tagging (first scan)
  6. Cluster similar assumptions (unsupervised grouping)
  7. Prioritize by graph centrality + churn
  8. Generate bounded multiple-choice questions
  9. Transition DETECTED -> PROPOSED
 10. Persist to .smm/assumptions/

Also provides resolution, storage, adaptive-learning stats, and metrics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vt_protocol.analysis.assumptions import (
    PATTERNS,
    get_pattern_mode,
    get_tiered_threshold,
    load_pattern_stats,
    save_pattern_stats,
    scan_changed_files,
    scan_directory,
    update_pattern_stats,
)
from vt_protocol.analysis.assumption_questions import generate_question
from vt_protocol.decisions.models import (
    AssumptionStatus,
    DomainAssumption,
    GovernanceConfig,
)

logger = logging.getLogger(__name__)

# Default confidence threshold when not specified in governance config.
_DEFAULT_CONFIDENCE_THRESHOLD = 0.5

# Pattern tier lookup — built from PATTERNS registry
_PATTERN_TIERS: dict[str, str] = {p.pattern_id: p.tier for p in PATTERNS}


# ---------------------------------------------------------------------------
# Pipeline Result
# ---------------------------------------------------------------------------


@dataclass
class AssumptionPipelineResult:
    """Result of running the assumption pipeline."""

    detected: int = 0
    new: int = 0
    pre_validated: int = 0
    deduped: int = 0
    below_threshold: int = 0
    shadowed: int = 0
    clusters_formed: int = 0
    cluster_compression: float = 0.0
    assumptions: list[DomainAssumption] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core Pipeline
# ---------------------------------------------------------------------------


def run_assumption_pipeline(
    root: Path,
    *,
    changed_files: list[str] | None = None,
    config: GovernanceConfig | None = None,
    existing: list[DomainAssumption] | None = None,
) -> AssumptionPipelineResult:
    """Run the full assumption detection-to-proposal pipeline.

    Enhanced pipeline with tiered thresholds, shadow mode, clustering,
    and priority scoring.

    Args:
        root: Project root directory.
        changed_files: If provided, only scan these files (incremental mode).
            Otherwise scan the full directory tree.
        config: Governance configuration. Used to read threshold from
            ``config.rules``. Falls back to defaults if *None*.
        existing: Pre-loaded existing assumptions. If *None*, they are loaded
            from ``{root}/.smm/assumptions/``.

    Returns:
        An ``AssumptionPipelineResult`` containing counts and the list of
        new assumptions ready for human review.
    """
    result = AssumptionPipelineResult()

    # ── Stage 1: Scan ──────────────────────────────────────────────────
    if changed_files is not None:
        raw_assumptions = scan_changed_files(root, changed_files)
    else:
        raw_assumptions = scan_directory(root)

    result.detected = len(raw_assumptions)
    logger.info("Scan detected %d raw assumptions", result.detected)

    if not raw_assumptions:
        return result

    # ── Stage 2: Tiered threshold filter ───────────────────────────────
    # Each pattern has a tier (architectural vs implementation) with
    # different confidence thresholds.
    pattern_stats = load_pattern_stats(root)

    above_threshold: list[DomainAssumption] = []
    for assumption in raw_assumptions:
        # Update triggered count
        update_pattern_stats(pattern_stats, assumption.pattern_id, "triggered")

        # Check shadow mode (statistical auto-disable)
        mode = get_pattern_mode(pattern_stats, assumption.pattern_id)
        if mode == "shadow":
            result.shadowed += 1
            logger.debug("Shadow mode: %s (%s)", assumption.pattern_id, assumption.summary)
            continue

        # Use tiered threshold
        tier = _PATTERN_TIERS.get(assumption.pattern_id, "implementation")
        threshold = get_tiered_threshold(tier)

        if assumption.confidence < threshold:
            result.below_threshold += 1
            logger.debug(
                "Below %s threshold (%.2f < %.2f): %s",
                tier,
                assumption.confidence,
                threshold,
                assumption.summary,
            )
        else:
            above_threshold.append(assumption)

    # Save updated pattern stats
    save_pattern_stats(root, pattern_stats)

    if not above_threshold:
        return result

    # ── Stage 3: Deduplicate ───────────────────────────────────────────
    if existing is None:
        existing = load_assumptions(root)

    existing_keys: dict[str, DomainAssumption] = {
        a.dedup_key: a for a in existing
    }

    deduplicated: list[DomainAssumption] = []
    for assumption in above_threshold:
        key = assumption.dedup_key
        prev = existing_keys.get(key)
        if prev is not None:
            if prev.status == AssumptionStatus.VALIDATED:
                result.pre_validated += 1
                logger.debug("Already validated: %s", key)
            else:
                result.deduped += 1
                logger.debug("Dedup (status=%s): %s", prev.status, key)
        else:
            deduplicated.append(assumption)

    if not deduplicated:
        result.new = 0
        return result

    # ── Stage 4: Freeze-on-adopt ───────────────────────────────────────
    is_first_scan = len(existing) == 0
    if is_first_scan:
        logger.info(
            "First scan — marking %d assumptions as baseline",
            len(deduplicated),
        )
        for assumption in deduplicated:
            assumption.is_baseline = True

    # ── Stage 5: Cluster similar assumptions ───────────────────────────
    try:
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions
        clusters = cluster_assumptions(deduplicated)
        result.clusters_formed = len(clusters)
        if result.clusters_formed > 0 and len(deduplicated) > 0:
            result.cluster_compression = len(deduplicated) / result.clusters_formed

        # Use cluster representatives for display, preserving all for storage
        clustered = []
        for cluster in clusters:
            rep = cluster.representative
            # Annotate the representative with cluster info
            if cluster.count > 1:
                rep.summary = f"[{cluster.count}x] {rep.summary}"
            clustered.append(rep)
        deduplicated = clustered
        logger.info(
            "Clustered %d assumptions into %d groups (%.1fx compression)",
            result.detected - result.below_threshold - result.shadowed,
            result.clusters_formed,
            result.cluster_compression,
        )
    except Exception:
        logger.debug("Clustering unavailable, skipping", exc_info=True)

    # ── Stage 6: Priority scoring ──────────────────────────────────────
    try:
        from vt_protocol.analysis.assumption_priority import prioritize_assumptions
        prioritized = prioritize_assumptions(deduplicated, root)
        # Sort by priority score descending
        deduplicated = [p.assumption for p in prioritized]
        logger.info("Prioritized %d assumptions by centrality + churn", len(deduplicated))
    except Exception:
        logger.debug("Priority scoring unavailable, skipping", exc_info=True)

    # ── Stage 7: Generate questions ────────────────────────────────────
    for assumption in deduplicated:
        if assumption.question:
            continue  # Already has a question (from cluster representative)
        try:
            question_data = generate_question(assumption)
            if question_data is not None:
                if isinstance(question_data, dict):
                    if "question" in question_data:
                        assumption.question = question_data["question"]
                    if "options" in question_data:
                        assumption.options = question_data["options"]
                # generate_question may mutate the assumption directly
        except Exception:
            logger.warning(
                "Failed to generate question for %s (%s)",
                assumption.pattern_id,
                assumption.summary,
                exc_info=True,
            )

    # ── Stage 8: Set status to PROPOSED ────────────────────────────────
    for assumption in deduplicated:
        if assumption.status == AssumptionStatus.DETECTED:
            assumption.status = AssumptionStatus.PROPOSED

    # ── Stage 9: Return ────────────────────────────────────────────────
    result.new = len(deduplicated)
    result.assumptions = deduplicated
    logger.info(
        "Pipeline complete: %d detected, %d new, %d deduped, "
        "%d pre-validated, %d below threshold, %d shadowed, "
        "%d clusters (%.1fx compression)",
        result.detected,
        result.new,
        result.deduped,
        result.pre_validated,
        result.below_threshold,
        result.shadowed,
        result.clusters_formed,
        result.cluster_compression,
    )
    return result


# ---------------------------------------------------------------------------
# Storage Functions
# ---------------------------------------------------------------------------


def load_assumptions(root: Path) -> list[DomainAssumption]:
    """Load assumptions from ``.smm/assumptions/*.json``."""
    assumptions_dir = root / ".smm" / "assumptions"
    if not assumptions_dir.is_dir():
        return []
    result: list[DomainAssumption] = []
    for f in sorted(assumptions_dir.glob("*.json")):
        if f.name == "stats.json":
            continue
        try:
            data = json.loads(f.read_text())
            result.append(DomainAssumption(**data))
        except Exception:
            logger.warning("Failed to load assumption from %s", f)
    return result


def save_assumption(root: Path, assumption: DomainAssumption) -> Path:
    """Save a single assumption to ``.smm/assumptions/``."""
    assumptions_dir = root / ".smm" / "assumptions"
    assumptions_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{assumption.pattern_id}-{assumption.id.hex[:8]}.json"
    filepath = assumptions_dir / filename
    filepath.write_text(assumption.model_dump_json(indent=2))
    return filepath


def save_assumptions(root: Path, assumptions: list[DomainAssumption]) -> list[Path]:
    """Save multiple assumptions."""
    return [save_assumption(root, a) for a in assumptions]


# ---------------------------------------------------------------------------
# Resolution Functions
# ---------------------------------------------------------------------------


def resolve_assumption(
    root: Path,
    assumption_id: str,
    selected_option: int,
    *,
    resolved_by: str = "human",
    rationale: str = "",
) -> DomainAssumption | None:
    """Resolve a PROPOSED assumption by selecting an option.

    Resolution rules:
    - Option 0 (A) -> VALIDATED  (current code is correct)
    - Option matching "I need more context" -> DEFERRED
    - Any other option -> REJECTED  (code needs to change)

    Args:
        root: Project root directory.
        assumption_id: Hex string of the assumption UUID (full or prefix).
        selected_option: 0-based index of the chosen option.
        resolved_by: Who resolved this (e.g. "human", agent ID).
        rationale: Free-text explanation for the choice.

    Returns:
        Updated ``DomainAssumption`` or *None* if not found.
    """
    assumptions_dir = root / ".smm" / "assumptions"
    if not assumptions_dir.is_dir():
        return None

    # Find the assumption file by ID prefix match
    target_file: Path | None = None
    target_assumption: DomainAssumption | None = None

    for f in sorted(assumptions_dir.glob("*.json")):
        if f.name == "stats.json":
            continue
        try:
            data = json.loads(f.read_text())
            assumption = DomainAssumption(**data)
        except Exception:
            continue

        if assumption.id.hex == assumption_id or str(assumption.id) == assumption_id:
            target_file = f
            target_assumption = assumption
            break

    if target_assumption is None or target_file is None:
        logger.warning("Assumption not found: %s", assumption_id)
        return None

    if target_assumption.status not in (
        AssumptionStatus.DETECTED,
        AssumptionStatus.PROPOSED,
    ):
        logger.warning(
            "Assumption %s is already resolved (status=%s)",
            assumption_id,
            target_assumption.status,
        )
        return target_assumption

    # Validate option index
    if not target_assumption.options or selected_option < 0 or selected_option >= len(target_assumption.options):
        logger.warning(
            "Invalid option index %d for assumption %s (has %d options)",
            selected_option,
            assumption_id,
            len(target_assumption.options) if target_assumption.options else 0,
        )
        return None

    # Determine new status
    chosen_text = target_assumption.options[selected_option].lower()
    if selected_option == 0:
        new_status = AssumptionStatus.VALIDATED
    elif "i need more context" in chosen_text or "more context" in chosen_text:
        new_status = AssumptionStatus.DEFERRED
    else:
        new_status = AssumptionStatus.REJECTED

    # Update assumption
    target_assumption.status = new_status
    target_assumption.selected_option = selected_option
    target_assumption.resolved_by = resolved_by
    target_assumption.answer_rationale = rationale
    target_assumption.resolved_at = datetime.now(timezone.utc)

    if new_status == AssumptionStatus.DEFERRED:
        # Re-surface after 7 days
        from datetime import timedelta

        target_assumption.deferred_until = datetime.now(timezone.utc) + timedelta(days=7)

    # Save back
    target_file.write_text(target_assumption.model_dump_json(indent=2))

    # Update pattern stats for auto-disable tracking
    try:
        pattern_stats = load_pattern_stats(root)
        event = new_status.value  # "validated", "rejected", or "deferred"
        update_pattern_stats(pattern_stats, target_assumption.pattern_id, event)
        save_pattern_stats(root, pattern_stats)
    except Exception:
        logger.debug("Failed to update pattern stats", exc_info=True)

    logger.info(
        "Resolved assumption %s -> %s (option %d: %s)",
        assumption_id[:8],
        new_status.value,
        selected_option,
        target_assumption.options[selected_option],
    )
    return target_assumption


# ---------------------------------------------------------------------------
# Stats Functions (adaptive learning)
# ---------------------------------------------------------------------------


@dataclass
class AssumptionStats:
    """Aggregate statistics for adaptive learning feedback loops."""

    total_detected: int = 0
    total_validated: int = 0
    total_rejected: int = 0
    total_deferred: int = 0
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    by_pattern: dict[str, dict[str, int]] = field(default_factory=dict)


def compute_stats(assumptions: list[DomainAssumption]) -> AssumptionStats:
    """Compute acceptance/rejection rates from resolved assumptions.

    Groups counts by category and pattern_id so the scanner can learn
    which patterns produce useful assumptions versus noise.
    """
    stats = AssumptionStats()
    stats.total_detected = len(assumptions)

    for a in assumptions:
        # Overall counts
        if a.status == AssumptionStatus.VALIDATED:
            stats.total_validated += 1
        elif a.status == AssumptionStatus.REJECTED:
            stats.total_rejected += 1
        elif a.status == AssumptionStatus.DEFERRED:
            stats.total_deferred += 1

        # Per-category breakdown
        cat = a.category.value
        if cat not in stats.by_category:
            stats.by_category[cat] = {
                "detected": 0,
                "validated": 0,
                "rejected": 0,
                "deferred": 0,
            }
        stats.by_category[cat]["detected"] += 1
        if a.status == AssumptionStatus.VALIDATED:
            stats.by_category[cat]["validated"] += 1
        elif a.status == AssumptionStatus.REJECTED:
            stats.by_category[cat]["rejected"] += 1
        elif a.status == AssumptionStatus.DEFERRED:
            stats.by_category[cat]["deferred"] += 1

        # Per-pattern breakdown
        pid = a.pattern_id
        if pid not in stats.by_pattern:
            stats.by_pattern[pid] = {
                "detected": 0,
                "validated": 0,
                "rejected": 0,
                "deferred": 0,
            }
        stats.by_pattern[pid]["detected"] += 1
        if a.status == AssumptionStatus.VALIDATED:
            stats.by_pattern[pid]["validated"] += 1
        elif a.status == AssumptionStatus.REJECTED:
            stats.by_pattern[pid]["rejected"] += 1
        elif a.status == AssumptionStatus.DEFERRED:
            stats.by_pattern[pid]["deferred"] += 1

    return stats


def save_stats(root: Path, stats: AssumptionStats) -> None:
    """Save stats to ``.smm/assumptions/stats.json``."""
    assumptions_dir = root / ".smm" / "assumptions"
    assumptions_dir.mkdir(parents=True, exist_ok=True)
    filepath = assumptions_dir / "stats.json"
    data = {
        "total_detected": stats.total_detected,
        "total_validated": stats.total_validated,
        "total_rejected": stats.total_rejected,
        "total_deferred": stats.total_deferred,
        "by_category": stats.by_category,
        "by_pattern": stats.by_pattern,
    }
    filepath.write_text(json.dumps(data, indent=2))
    logger.debug("Saved assumption stats to %s", filepath)


def load_stats(root: Path) -> AssumptionStats:
    """Load stats from ``.smm/assumptions/stats.json``."""
    filepath = root / ".smm" / "assumptions" / "stats.json"
    if not filepath.is_file():
        return AssumptionStats()
    try:
        data = json.loads(filepath.read_text())
        return AssumptionStats(
            total_detected=data.get("total_detected", 0),
            total_validated=data.get("total_validated", 0),
            total_rejected=data.get("total_rejected", 0),
            total_deferred=data.get("total_deferred", 0),
            by_category=data.get("by_category", {}),
            by_pattern=data.get("by_pattern", {}),
        )
    except Exception:
        logger.warning("Failed to load stats from %s", filepath)
        return AssumptionStats()


# ---------------------------------------------------------------------------
# Tracking Metrics (Improvement #6)
# ---------------------------------------------------------------------------


@dataclass
class AssumptionMetrics:
    """Tracking metrics for assumption detection quality."""

    # TTFVA: Time to First Valuable Assumption (seconds)
    # Time from first scan to first VALIDATED assumption
    ttfva_seconds: float | None = None

    # Cognitive Value Ratio: architectural validations / implementation validations
    cognitive_value_ratio: float = 0.0

    # Cluster efficiency: raw assumptions / clusters shown (higher = better batching)
    cluster_efficiency: float = 1.0

    # Rule ROI per pattern
    rule_roi: dict[str, float] = field(default_factory=dict)

    # Summary counts
    total_architectural: int = 0
    total_implementation: int = 0
    validated_architectural: int = 0
    validated_implementation: int = 0


def compute_metrics(
    root: Path,
    assumptions: list[DomainAssumption] | None = None,
) -> AssumptionMetrics:
    """Compute tracking metrics for the assumption detection system.

    Args:
        root: Project root.
        assumptions: Pre-loaded assumptions. If None, loads from disk.

    Returns:
        AssumptionMetrics with all computed values.
    """
    from vt_protocol.analysis.assumptions import PATTERNS, compute_rule_roi, load_pattern_stats

    if assumptions is None:
        assumptions = load_assumptions(root)

    metrics = AssumptionMetrics()

    # Build tier lookup
    tier_map = {p.pattern_id: p.tier for p in PATTERNS}

    # Count by tier
    first_scan_time: datetime | None = None
    first_validated_time: datetime | None = None

    for a in assumptions:
        tier = tier_map.get(a.pattern_id, "implementation")
        if tier == "architectural":
            metrics.total_architectural += 1
            if a.status == AssumptionStatus.VALIDATED:
                metrics.validated_architectural += 1
        else:
            metrics.total_implementation += 1
            if a.status == AssumptionStatus.VALIDATED:
                metrics.validated_implementation += 1

        # Track times for TTFVA
        if first_scan_time is None or a.detected_at < first_scan_time:
            first_scan_time = a.detected_at
        if a.status == AssumptionStatus.VALIDATED and a.resolved_at:
            if first_validated_time is None or a.resolved_at < first_validated_time:
                first_validated_time = a.resolved_at

    # TTFVA
    if first_scan_time and first_validated_time:
        delta = first_validated_time - first_scan_time
        metrics.ttfva_seconds = delta.total_seconds()

    # Cognitive Value Ratio
    if metrics.validated_implementation > 0:
        metrics.cognitive_value_ratio = (
            metrics.validated_architectural / metrics.validated_implementation
        )
    elif metrics.validated_architectural > 0:
        metrics.cognitive_value_ratio = float("inf")

    # Rule ROI from pattern stats
    pattern_stats = load_pattern_stats(root)
    for pid in pattern_stats:
        metrics.rule_roi[pid] = compute_rule_roi(pattern_stats, pid)

    return metrics


def format_metrics_summary(metrics: AssumptionMetrics) -> str:
    """Format metrics into a human-readable summary string."""
    lines = []

    if metrics.ttfva_seconds is not None:
        if metrics.ttfva_seconds < 60:
            lines.append(f"TTFVA: {metrics.ttfva_seconds:.0f}s")
        elif metrics.ttfva_seconds < 3600:
            lines.append(f"TTFVA: {metrics.ttfva_seconds / 60:.1f}min")
        else:
            lines.append(f"TTFVA: {metrics.ttfva_seconds / 3600:.1f}h")

    lines.append(
        f"Cognitive Value: {metrics.cognitive_value_ratio:.1f}x "
        f"({metrics.validated_architectural} arch / {metrics.validated_implementation} impl)"
    )

    if metrics.cluster_efficiency > 1.0:
        lines.append(f"Cluster Efficiency: {metrics.cluster_efficiency:.1f}x compression")

    # Top 3 Rule ROI
    if metrics.rule_roi:
        sorted_roi = sorted(metrics.rule_roi.items(), key=lambda x: x[1], reverse=True)
        top = sorted_roi[:3]
        roi_parts = [f"{pid}: {roi:.1f}" for pid, roi in top]
        lines.append(f"Top Rule ROI: {', '.join(roi_parts)}")

    return " | ".join(lines)

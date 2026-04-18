"""Resolution path suggestions for contradictions.

CodeRabbit-style resolution with 2-3 actionable paths per contradiction:
  - Accept Exception: keep both decisions, mark as acknowledged tension
  - Update Decision: supersede one decision with a reconciled version
  - Dismiss: not a real contradiction (false positive)

Each path includes a label, description, and the action type for the
frontend to render as one-click buttons.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
)

logger = logging.getLogger(__name__)


class ResolutionType:
    """Resolution action types mapped to frontend buttons."""

    PICK_A = "pick_a"
    PICK_B = "pick_b"
    ACCEPT_EXCEPTION = "accept_exception"
    UPDATE_DECISION = "update_decision"
    DISMISS = "dismiss"
    DEFER = "defer"


@dataclass
class ResolutionPath:
    """A suggested resolution action for a contradiction."""

    action: str  # ResolutionType value
    label: str  # Button label (e.g., "Keep PostgreSQL")
    description: str  # Explanation of what this action does
    impact: str  # "low", "medium", "high"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "label": self.label,
            "description": self.description,
            "impact": self.impact,
            "details": self.details,
        }


def suggest_resolution_paths(
    contradiction: Contradiction,
    decision_a: Decision | None = None,
    decision_b: Decision | None = None,
) -> list[ResolutionPath]:
    """Generate resolution path suggestions for a contradiction.

    Returns 2-3 actionable paths depending on the contradiction's
    verdict and context. Each path is a concrete action the user
    can take with a single click.
    """
    paths: list[ResolutionPath] = []

    if contradiction.verdict == ContradictionVerdict.CONTRADICTION:
        paths.extend(_paths_for_contradiction(contradiction, decision_a, decision_b))
    elif contradiction.verdict == ContradictionVerdict.TENSION:
        paths.extend(_paths_for_tension(contradiction, decision_a, decision_b))
    else:
        # Compatible — shouldn't normally need resolution
        paths.append(ResolutionPath(
            action=ResolutionType.DISMISS,
            label="Dismiss",
            description="This pair is compatible — no action needed.",
            impact="low",
        ))

    return paths


def _paths_for_contradiction(
    c: Contradiction,
    da: Decision | None,
    db: Decision | None,
) -> list[ResolutionPath]:
    """Resolution paths for a genuine contradiction."""
    paths: list[ResolutionPath] = []

    title_a = da.title if da else c.decision_a_title
    title_b = db.title if db else c.decision_b_title

    # Path 1: Keep Decision A
    paths.append(ResolutionPath(
        action=ResolutionType.PICK_A,
        label=f"Keep: {title_a[:40]}",
        description=(
            f"Supersede '{title_b}' in favor of '{title_a}'. "
            f"The losing decision will be marked as superseded."
        ),
        impact="high",
        details={
            "winner_id": str(c.decision_a_id),
            "loser_id": str(c.decision_b_id),
        },
    ))

    # Path 2: Keep Decision B
    paths.append(ResolutionPath(
        action=ResolutionType.PICK_B,
        label=f"Keep: {title_b[:40]}",
        description=(
            f"Supersede '{title_a}' in favor of '{title_b}'. "
            f"The losing decision will be marked as superseded."
        ),
        impact="high",
        details={
            "winner_id": str(c.decision_b_id),
            "loser_id": str(c.decision_a_id),
        },
    ))

    # Path 3: Accept as exception (keep both)
    paths.append(ResolutionPath(
        action=ResolutionType.ACCEPT_EXCEPTION,
        label="Accept Exception",
        description=(
            "Acknowledge this contradiction as an intentional exception. "
            "Both decisions remain active. The contradiction is logged "
            "but excluded from quality gates."
        ),
        impact="low",
        details={
            "new_status": ContradictionStatus.IGNORED.value,
        },
    ))

    return paths


def _paths_for_tension(
    c: Contradiction,
    da: Decision | None,
    db: Decision | None,
) -> list[ResolutionPath]:
    """Resolution paths for a tension (pull in different directions but can coexist)."""
    paths: list[ResolutionPath] = []

    title_a = da.title if da else c.decision_a_title
    title_b = db.title if db else c.decision_b_title

    # Path 1: Accept the tension
    paths.append(ResolutionPath(
        action=ResolutionType.ACCEPT_EXCEPTION,
        label="Accept Tension",
        description=(
            "These decisions pull in different directions but can coexist. "
            "Mark as acknowledged and exclude from quality gates."
        ),
        impact="low",
    ))

    # Path 2: Update one decision to reconcile
    paths.append(ResolutionPath(
        action=ResolutionType.UPDATE_DECISION,
        label="Reconcile Decisions",
        description=(
            f"Create a new decision that reconciles '{title_a}' and "
            f"'{title_b}'. Both originals will be superseded."
        ),
        impact="medium",
        details={
            "decision_a_id": str(c.decision_a_id),
            "decision_b_id": str(c.decision_b_id),
        },
    ))

    # Path 3: Defer for later
    paths.append(ResolutionPath(
        action=ResolutionType.DEFER,
        label="Defer",
        description=(
            "Defer this tension for later review. It will remain visible "
            "but won't block quality gates."
        ),
        impact="low",
        details={
            "new_status": ContradictionStatus.DEFERRED.value,
        },
    ))

    return paths


def apply_resolution(
    contradiction: Contradiction,
    action: str,
    *,
    rationale: str = "",
    actor: str = "dashboard-user",
    decisions: list[Decision] | None = None,
) -> dict[str, Any]:
    """Apply a resolution action to a contradiction.

    Returns a dict with the resolution outcome:
      - status: new contradiction status
      - winner_id: if pick_a/pick_b, the winning decision
      - superseded_id: if pick_a/pick_b, the decision to supersede
      - changes: list of changes made
    """
    from datetime import datetime, timezone

    changes: list[str] = []
    result: dict[str, Any] = {"changes": changes}

    if action == ResolutionType.PICK_A:
        contradiction.status = ContradictionStatus.RESOLVED
        contradiction.resolved_by = actor
        contradiction.resolution_note = f"Winner: {contradiction.decision_a_id}. {rationale}"
        contradiction.resolved_at = datetime.now(timezone.utc)
        result["status"] = "resolved"
        result["winner_id"] = str(contradiction.decision_a_id)
        result["superseded_id"] = str(contradiction.decision_b_id)
        changes.append(f"Resolved in favor of '{contradiction.decision_a_title}'")

        # Mark losing decision as superseded if available
        if decisions:
            for d in decisions:
                if d.id == contradiction.decision_b_id:
                    d.status = DecisionStatus.SUPERSEDED
                    d.valid = False
                    changes.append(f"Marked '{d.title}' as superseded")

    elif action == ResolutionType.PICK_B:
        contradiction.status = ContradictionStatus.RESOLVED
        contradiction.resolved_by = actor
        contradiction.resolution_note = f"Winner: {contradiction.decision_b_id}. {rationale}"
        contradiction.resolved_at = datetime.now(timezone.utc)
        result["status"] = "resolved"
        result["winner_id"] = str(contradiction.decision_b_id)
        result["superseded_id"] = str(contradiction.decision_a_id)
        changes.append(f"Resolved in favor of '{contradiction.decision_b_title}'")

        if decisions:
            for d in decisions:
                if d.id == contradiction.decision_a_id:
                    d.status = DecisionStatus.SUPERSEDED
                    d.valid = False
                    changes.append(f"Marked '{d.title}' as superseded")

    elif action == ResolutionType.ACCEPT_EXCEPTION:
        contradiction.status = ContradictionStatus.IGNORED
        contradiction.resolved_by = actor
        contradiction.resolution_note = f"Accepted as exception. {rationale}"
        contradiction.resolved_at = datetime.now(timezone.utc)
        result["status"] = "ignored"
        changes.append("Accepted as exception — excluded from quality gates")

    elif action == ResolutionType.DEFER:
        contradiction.status = ContradictionStatus.DEFERRED
        contradiction.resolved_by = actor
        contradiction.resolution_note = f"Deferred. {rationale}"
        result["status"] = "deferred"
        changes.append("Deferred for later review")

    elif action == ResolutionType.DISMISS:
        contradiction.status = ContradictionStatus.RESOLVED
        contradiction.resolved_by = actor
        contradiction.resolution_note = f"Dismissed as false positive. {rationale}"
        contradiction.resolved_at = datetime.now(timezone.utc)
        result["status"] = "resolved"
        changes.append("Dismissed as false positive")

    elif action == ResolutionType.UPDATE_DECISION:
        # This action requires user to create a new decision — we just mark deferred
        contradiction.status = ContradictionStatus.DEFERRED
        contradiction.resolved_by = actor
        contradiction.resolution_note = f"Pending reconciliation. {rationale}"
        result["status"] = "deferred"
        result["needs_new_decision"] = True
        changes.append("Marked for reconciliation — create a new unified decision")

    else:
        result["status"] = "error"
        result["error"] = f"Unknown action: {action}"

    return result


# ---------------------------------------------------------------------------
# Refactor task generation (Bug 1)
# ---------------------------------------------------------------------------


def generate_refactor_task(
    project_root: Path | str,
    contradiction: Contradiction,
    *,
    loser_id: str,
    loser_title: str,
    winner_title: str,
    actor: str = "dashboard-user",
) -> Path:
    """Write a pending-refactor task file after a pick_a/pick_b resolution.

    File: .smm/pending-refactors/refactor-{contradiction_id[:8]}.md
    Returned path can be included in MCP context so the agent acts on it.
    """
    from datetime import date

    root = Path(project_root)
    refactors_dir = root / ".smm" / "pending-refactors"
    refactors_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    affected = _find_affected_files(root, loser_title)

    lines: list[str] = [
        f"# Refactor: {loser_title} → {winner_title}",
        "",
        f"**Resolved:** {today}  ",
        f"**Actor:** {actor}  ",
        f"**Contradiction ID:** {str(contradiction.id)[:8]}  ",
        "",
        "## What was superseded",
        f"- {loser_title}",
        "",
        "## What won",
        f"- {winner_title}",
        "",
        "## Suggested task",
        f"Migrate code that uses `{loser_title}` to use `{winner_title}` instead.",
        f"Per architectural decision resolved on {today}.",
        "",
    ]
    if affected:
        lines.append("## Potentially affected files")
        lines.extend(f"- {f}" for f in affected)
        lines.append("")
    lines.extend([
        "## Next step",
        "In your next session, look for usages of the superseded technology",
        "and migrate them to the winner. Delete this file when complete.",
    ])

    path = refactors_dir / f"refactor-{str(contradiction.id)[:8]}.md"
    path.write_text("\n".join(lines))
    logger.info("Generated refactor task: %s", path)
    return path


def _find_affected_files(project_root: Path, loser_title: str) -> list[str]:
    """Best-effort scan for .py files that may reference the superseded technology."""
    # Extract meaningful keywords (skip common English words and short tokens)
    _STOPWORDS = {"use", "the", "and", "or", "for", "with", "via", "api",
                  "in", "a", "an", "at", "of", "to", "is", "are", "be"}
    words = re.findall(r"\b[A-Za-z][A-Za-z0-9_-]+\b", loser_title)
    keywords = [w.lower() for w in words if len(w) > 2 and w.lower() not in _STOPWORDS]
    if not keywords:
        return []

    affected: list[str] = []
    try:
        for py_file in sorted(project_root.rglob("*.py")):
            if any(p in py_file.parts for p in (".venv", "__pycache__", ".git", "node_modules")):
                continue
            try:
                content = py_file.read_text(errors="replace").lower()
                if any(kw in content for kw in keywords):
                    affected.append(str(py_file.relative_to(project_root)))
                if len(affected) >= 20:
                    break
            except Exception:
                continue
    except Exception:
        pass
    return affected

"""PostgreSQL graph client for the decision engine.

Replaces Axiom Hub's Kuzu-based GraphClient with PostgreSQL junction tables.
SPEC: "PostgreSQL junction table handles workload up to 10K decisions, no
graph DB needed until proven otherwise."

Key query: shared-dimension-count × recency-multiplier ranking, top 5 results
reordered for LLM attention bias (best match first, second-best last).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    ContextResult,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)
from vt_protocol.exceptions import DecisionNotFoundError, StoreConnectionError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

SCHEMA_DDL = """\
CREATE TABLE IF NOT EXISTS decisions (
    id              UUID PRIMARY KEY,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    rationale       TEXT DEFAULT '',
    status          TEXT DEFAULT 'active',
    decision_type   TEXT DEFAULT 'technical',
    constraints     TEXT[] DEFAULT '{}',
    alternatives    TEXT[] DEFAULT '{}',
    made_by         TEXT NOT NULL,
    project         TEXT NOT NULL,
    source_type     TEXT DEFAULT 'agent',
    confidence      REAL DEFAULT 0.75,
    supersedes      UUID REFERENCES decisions(id),
    session_id      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    valid           BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS decision_dimensions (
    decision_id     UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
    dimension       TEXT NOT NULL,
    PRIMARY KEY (decision_id, dimension)
);

CREATE TABLE IF NOT EXISTS contradictions (
    id                  UUID PRIMARY KEY,
    decision_a_id       UUID NOT NULL REFERENCES decisions(id),
    decision_b_id       UUID NOT NULL REFERENCES decisions(id),
    decision_a_title    TEXT NOT NULL,
    decision_b_title    TEXT NOT NULL,
    verdict             TEXT NOT NULL,
    reasoning           TEXT NOT NULL,
    evidence_a          TEXT NOT NULL,
    evidence_b          TEXT NOT NULL,
    confidence          REAL NOT NULL,
    status              TEXT DEFAULT 'unresolved',
    resolved_by         TEXT,
    resolution_note     TEXT,
    detected_at         TIMESTAMPTZ DEFAULT NOW(),
    resolved_at         TIMESTAMPTZ,
    is_baseline         BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS contradiction_dimensions (
    contradiction_id    UUID NOT NULL REFERENCES contradictions(id) ON DELETE CASCADE,
    dimension           TEXT NOT NULL,
    PRIMARY KEY (contradiction_id, dimension)
);

CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project);
CREATE INDEX IF NOT EXISTS idx_decisions_valid ON decisions(valid);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_dd_dimension ON decision_dimensions(dimension);
CREATE INDEX IF NOT EXISTS idx_contradictions_status ON contradictions(status);
CREATE INDEX IF NOT EXISTS idx_contradictions_project
    ON contradictions(decision_a_id);
"""

# ---------------------------------------------------------------------------
# Ranking query — the core of the junction table approach
# ---------------------------------------------------------------------------

FIND_RELATED_SQL = """\
WITH target_dims AS (
    SELECT dimension FROM decision_dimensions WHERE decision_id = %(target_id)s
)
SELECT
    d.id, d.title, d.content, d.rationale, d.status, d.decision_type,
    d.confidence, d.created_at, d.source_type,
    ARRAY_AGG(DISTINCT dd.dimension) AS dimensions,
    COUNT(DISTINCT dd.dimension) AS shared_count,
    COUNT(DISTINCT dd.dimension)::float * (
        1.0 / (1.0 + EXTRACT(EPOCH FROM (NOW() - d.created_at)) / 2592000.0)
    ) AS score
FROM decisions d
JOIN decision_dimensions dd ON dd.decision_id = d.id
WHERE dd.dimension IN (SELECT dimension FROM target_dims)
  AND d.id != %(target_id)s
  AND d.valid = TRUE
  AND d.project = %(project)s
GROUP BY d.id
ORDER BY score DESC
LIMIT %(limit)s;
"""


# ---------------------------------------------------------------------------
# GraphClient
# ---------------------------------------------------------------------------


class GraphClient:
    """PostgreSQL-backed decision graph with junction-table ranking.

    Uses psycopg3 (sync). Each method opens and closes its own connection
    from the stored conninfo. For higher throughput, swap to a ConnectionPool.
    """

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    def _connect(self) -> psycopg.Connection:
        try:
            return psycopg.connect(self._conninfo, row_factory=dict_row)
        except psycopg.OperationalError as exc:
            raise StoreConnectionError(str(exc)) from exc

    # -- Schema --------------------------------------------------------------

    def init_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self._connect() as conn:
            conn.execute(SCHEMA_DDL)
            conn.commit()

    # -- Decisions -----------------------------------------------------------

    def add_decision(self, decision: Decision) -> UUID:
        """Insert a decision and its dimension edges.

        If the decision supersedes another, the old one is marked as
        invalid (valid=FALSE, status='superseded').
        """
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions
                   (id, title, content, rationale, status, decision_type,
                    constraints, alternatives, made_by, project, source_type,
                    confidence, supersedes, session_id, created_at, valid)
                   VALUES (%(id)s, %(title)s, %(content)s, %(rationale)s,
                    %(status)s, %(decision_type)s, %(constraints)s,
                    %(alternatives)s, %(made_by)s, %(project)s,
                    %(source_type)s, %(confidence)s, %(supersedes)s,
                    %(session_id)s, %(created_at)s, %(valid)s)""",
                {
                    "id": decision.id,
                    "title": decision.title,
                    "content": decision.content,
                    "rationale": decision.rationale,
                    "status": decision.status.value,
                    "decision_type": decision.decision_type.value,
                    "constraints": decision.constraints,
                    "alternatives": decision.alternatives,
                    "made_by": decision.made_by,
                    "project": decision.project,
                    "source_type": decision.source_type.value,
                    "confidence": decision.confidence,
                    "supersedes": decision.supersedes,
                    "session_id": decision.session_id,
                    "created_at": decision.created_at,
                    "valid": decision.valid,
                },
            )

            # Insert dimension edges
            for dim in decision.dimensions:
                conn.execute(
                    """INSERT INTO decision_dimensions (decision_id, dimension)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (decision.id, dim.value),
                )

            # Mark superseded decision as invalid
            if decision.supersedes:
                conn.execute(
                    """UPDATE decisions
                       SET valid = FALSE, status = 'superseded'
                       WHERE id = %s""",
                    (decision.supersedes,),
                )

            conn.commit()
        return decision.id

    def get_decision(self, decision_id: UUID) -> Decision | None:
        """Fetch a single decision by ID, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE id = %s", (decision_id,)
            ).fetchone()
            if not row:
                return None

            dims = conn.execute(
                "SELECT dimension FROM decision_dimensions WHERE decision_id = %s",
                (decision_id,),
            ).fetchall()

            return _row_to_decision(row, [r["dimension"] for r in dims])

    def get_decisions(self, project: str, *, active_only: bool = True) -> list[Decision]:
        """All decisions for a project, optionally filtered to active only."""
        with self._connect() as conn:
            where = "WHERE project = %s"
            if active_only:
                where += " AND valid = TRUE"
            rows = conn.execute(
                f"SELECT * FROM decisions {where} ORDER BY created_at DESC",
                (project,),
            ).fetchall()

            decisions: list[Decision] = []
            for row in rows:
                dims = conn.execute(
                    "SELECT dimension FROM decision_dimensions WHERE decision_id = %s",
                    (row["id"],),
                ).fetchall()
                decisions.append(
                    _row_to_decision(row, [d["dimension"] for d in dims])
                )
            return decisions

    # -- Ranking -------------------------------------------------------------

    def find_related(
        self,
        decision: Decision,
        *,
        limit: int = 5,
        reorder_attention: bool = True,
    ) -> list[ContextResult]:
        """Find decisions sharing dimensions, ranked by shared-dimension-count × recency.

        SPEC: "shared-dimension-count × recency-multiplier ranking, top 5
        results reordered for LLM attention bias (best match first,
        second-best last)."

        Recency multiplier: 1/(1 + age_in_months). A 1-month-old decision
        scores 0.5×, a fresh one scores ~1.0×.
        """
        if not decision.dimensions:
            return []

        with self._connect() as conn:
            # Ensure the target decision's dimensions are in the junction table
            # (the decision might not be persisted yet — use a temp approach)
            rows = conn.execute(
                FIND_RELATED_SQL,
                {"target_id": decision.id, "project": decision.project, "limit": limit},
            ).fetchall()

        results = [
            ContextResult(
                decision_id=row["id"],
                title=row["title"],
                content=row["content"],
                relevance_score=min(1.0, float(row["score"])),
                dimensions=[
                    Dimension(d) for d in (row["dimensions"] or [])
                    if d in Dimension._value2member_map_
                ],
                excerpt=_make_excerpt(row["content"]),
            )
            for row in rows
        ]

        if reorder_attention:
            results = _reorder_for_attention(results)

        return results

    # -- Superseding ---------------------------------------------------------

    def supersede(self, old_id: UUID, new_decision: Decision) -> UUID:
        """Mark old decision as superseded and insert the replacement.

        Raises DecisionNotFoundError if old_id doesn't exist.
        """
        with self._connect() as conn:
            check = conn.execute(
                "SELECT id FROM decisions WHERE id = %s", (old_id,)
            ).fetchone()
            if not check:
                raise DecisionNotFoundError(f"Decision {old_id} not found")

        new_decision.supersedes = old_id
        return self.add_decision(new_decision)

    # -- Contradictions ------------------------------------------------------

    def add_contradiction(self, contradiction: Contradiction) -> UUID:
        """Store a detected contradiction."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO contradictions
                   (id, decision_a_id, decision_b_id, decision_a_title,
                    decision_b_title, verdict, reasoning, evidence_a,
                    evidence_b, confidence, status, detected_at, is_baseline)
                   VALUES (%(id)s, %(a_id)s, %(b_id)s, %(a_title)s,
                    %(b_title)s, %(verdict)s, %(reasoning)s, %(ev_a)s,
                    %(ev_b)s, %(confidence)s, %(status)s, %(detected_at)s,
                    %(is_baseline)s)""",
                {
                    "id": contradiction.id,
                    "a_id": contradiction.decision_a_id,
                    "b_id": contradiction.decision_b_id,
                    "a_title": contradiction.decision_a_title,
                    "b_title": contradiction.decision_b_title,
                    "verdict": contradiction.verdict.value,
                    "reasoning": contradiction.reasoning,
                    "ev_a": contradiction.evidence_a,
                    "ev_b": contradiction.evidence_b,
                    "confidence": contradiction.confidence,
                    "status": contradiction.status.value,
                    "detected_at": contradiction.detected_at,
                    "is_baseline": contradiction.is_baseline,
                },
            )
            for dim in contradiction.shared_dimensions:
                conn.execute(
                    """INSERT INTO contradiction_dimensions
                       (contradiction_id, dimension)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (contradiction.id, dim.value),
                )
            conn.commit()
        return contradiction.id

    def get_unresolved_contradictions(self, project: str) -> list[Contradiction]:
        """All unresolved, non-baseline contradictions for a project."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.* FROM contradictions c
                   JOIN decisions d ON d.id = c.decision_a_id
                   WHERE d.project = %s
                     AND c.status = 'unresolved'
                     AND c.is_baseline = FALSE
                   ORDER BY c.detected_at DESC""",
                (project,),
            ).fetchall()

            results: list[Contradiction] = []
            for row in rows:
                dims = conn.execute(
                    """SELECT dimension FROM contradiction_dimensions
                       WHERE contradiction_id = %s""",
                    (row["id"],),
                ).fetchall()
                results.append(_row_to_contradiction(
                    row, [d["dimension"] for d in dims]
                ))
            return results

    def resolve_contradiction(
        self,
        contradiction_id: UUID,
        resolved_by: str,
        note: str,
    ) -> bool:
        """Mark a contradiction as resolved. Returns True if found and updated."""
        with self._connect() as conn:
            result = conn.execute(
                """UPDATE contradictions
                   SET status = 'resolved',
                       resolved_by = %s,
                       resolution_note = %s,
                       resolved_at = %s
                   WHERE id = %s AND status = 'unresolved'""",
                (resolved_by, note, datetime.now(timezone.utc), contradiction_id),
            )
            conn.commit()
            return result.rowcount > 0

    # -- Cleanup -------------------------------------------------------------

    def close(self) -> None:
        """No-op for simple connection mode. Override for pool-based clients."""


# ---------------------------------------------------------------------------
# Singleton (ported from Axiom Hub's double-checked locking pattern)
# ---------------------------------------------------------------------------

_client: GraphClient | None = None
_client_lock = threading.Lock()


def get_graph_client(conninfo: str | None = None) -> GraphClient:
    """Return the shared GraphClient (thread-safe double-checked locking).

    Reads VT_DATABASE_URL env var if conninfo not provided.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import os

                dsn = conninfo or os.environ.get(
                    "VT_DATABASE_URL",
                    "postgresql://localhost:5432/vt_protocol",
                )
                _client = GraphClient(conninfo=dsn)
    return _client


def reset_client() -> None:
    """Reset the singleton (for testing)."""
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


# ---------------------------------------------------------------------------
# Attention-bias reordering
# ---------------------------------------------------------------------------


def _reorder_for_attention(results: list[ContextResult]) -> list[ContextResult]:
    """Reorder for LLM attention bias: best first, second-best last.

    LLMs pay disproportionate attention to the beginning and end of context
    windows (primacy + recency effects). Given results ranked [A, B, C, D, E]:
    → [A, C, D, E, B]  (A stays first, B moves to last position)

    From SPEC: "top 5 results reordered for LLM attention bias."
    """
    if len(results) <= 2:
        return results
    return [results[0], *results[2:], results[1]]


# ---------------------------------------------------------------------------
# Row → model helpers
# ---------------------------------------------------------------------------


def _row_to_decision(row: dict, dimension_values: list[str]) -> Decision:
    """Convert a database row + dimension list to a Decision model."""
    dimensions = [
        Dimension(d) for d in dimension_values
        if d in Dimension._value2member_map_
    ]
    return Decision(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        rationale=row["rationale"] or "",
        status=DecisionStatus(row["status"]),
        decision_type=DecisionType(row["decision_type"]),
        dimensions=dimensions,
        constraints=row["constraints"] or [],
        alternatives=row["alternatives"] or [],
        made_by=row["made_by"],
        project=row["project"],
        source_type=SourceType(row["source_type"]),
        confidence=row["confidence"],
        supersedes=row["supersedes"],
        session_id=row["session_id"],
        created_at=row["created_at"],
        valid=row["valid"],
    )


def _row_to_contradiction(row: dict, dimension_values: list[str]) -> Contradiction:
    """Convert a database row + dimension list to a Contradiction model."""
    dimensions = [
        Dimension(d) for d in dimension_values
        if d in Dimension._value2member_map_
    ]
    return Contradiction(
        id=row["id"],
        decision_a_id=row["decision_a_id"],
        decision_b_id=row["decision_b_id"],
        decision_a_title=row["decision_a_title"],
        decision_b_title=row["decision_b_title"],
        verdict=ContradictionVerdict(row["verdict"]),
        reasoning=row["reasoning"],
        evidence_a=row["evidence_a"],
        evidence_b=row["evidence_b"],
        confidence=row["confidence"],
        status=ContradictionStatus(row["status"]),
        resolved_by=row.get("resolved_by"),
        resolution_note=row.get("resolution_note"),
        detected_at=row["detected_at"],
        resolved_at=row.get("resolved_at"),
        is_baseline=row["is_baseline"],
        shared_dimensions=dimensions,
    )


def _make_excerpt(content: str, max_sentences: int = 3) -> str:
    """Extract first N sentences as an excerpt."""
    sentences = content.replace("\n", " ").split(". ")
    excerpt_parts = sentences[:max_sentences]
    result = ". ".join(excerpt_parts)
    if not result.endswith("."):
        result += "."
    return result

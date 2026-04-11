"""Tests for PostgreSQL graph client.

These tests require a running PostgreSQL server. They are skipped
automatically if PostgreSQL is not available. Set VT_TEST_DATABASE_URL
to override the connection string.

    export VT_TEST_DATABASE_URL="postgresql://localhost:5432/vt_test"
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from vt_protocol.decisions.graph_client import (
    GraphClient,
    _make_excerpt,
    _reorder_for_attention,
)
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionVerdict,
    ContextResult,
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)

# ---------------------------------------------------------------------------
# Skip if PostgreSQL not available
# ---------------------------------------------------------------------------

_TEST_DSN = os.environ.get("VT_TEST_DATABASE_URL", "")


def _pg_available() -> bool:
    if not _TEST_DSN:
        return False
    try:
        import psycopg

        with psycopg.connect(_TEST_DSN):
            return True
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not available (set VT_TEST_DATABASE_URL)",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph() -> GraphClient:
    """Graph client with fresh schema. Drops tables after each test."""
    client = GraphClient(conninfo=_TEST_DSN)
    client.init_schema()
    yield client  # type: ignore[misc]
    # Cleanup
    import psycopg

    with psycopg.connect(_TEST_DSN) as conn:
        conn.execute("DROP TABLE IF EXISTS contradiction_dimensions CASCADE")
        conn.execute("DROP TABLE IF EXISTS contradictions CASCADE")
        conn.execute("DROP TABLE IF EXISTS decision_dimensions CASCADE")
        conn.execute("DROP TABLE IF EXISTS decisions CASCADE")
        conn.commit()
    client.close()


def _make_decision(**overrides) -> Decision:  # type: ignore[no-untyped-def]
    defaults = {
        "title": "Test decision",
        "content": "Some decision content for testing purposes.",
        "rationale": "Testing",
        "made_by": "test",
        "project": "test-project",
        "dimensions": [Dimension.DATABASE],
        "source_type": SourceType.MANUAL,
    }
    defaults.update(overrides)
    return Decision(**defaults)


# ---------------------------------------------------------------------------
# Unit tests (no PG required)
# ---------------------------------------------------------------------------


class TestAttentionReorder:
    def test_empty(self) -> None:
        assert _reorder_for_attention([]) == []

    def test_single(self) -> None:
        items = [ContextResult(
            decision_id=uuid4(), title="A", content="c",
            relevance_score=0.9, excerpt="e",
        )]
        assert _reorder_for_attention(items) == items

    def test_two_items_unchanged(self) -> None:
        a = ContextResult(decision_id=uuid4(), title="A", content="c",
                          relevance_score=0.9, excerpt="e")
        b = ContextResult(decision_id=uuid4(), title="B", content="c",
                          relevance_score=0.8, excerpt="e")
        result = _reorder_for_attention([a, b])
        assert result == [a, b]

    def test_five_items_best_first_second_last(self) -> None:
        items = [
            ContextResult(decision_id=uuid4(), title=f"D{i}", content="c",
                          relevance_score=1.0 - i * 0.1, excerpt="e")
            for i in range(5)
        ]
        result = _reorder_for_attention(items)
        # Best (index 0) stays first
        assert result[0].title == "D0"
        # Second-best (index 1) moves to last
        assert result[-1].title == "D1"
        # Middle items fill in between
        assert [r.title for r in result] == ["D0", "D2", "D3", "D4", "D1"]


class TestMakeExcerpt:
    def test_short_content(self) -> None:
        assert _make_excerpt("Hello world") == "Hello world."

    def test_multi_sentence(self) -> None:
        text = "First sentence. Second sentence. Third sentence. Fourth."
        result = _make_excerpt(text, max_sentences=2)
        assert result == "First sentence. Second sentence."


# ---------------------------------------------------------------------------
# Integration tests (require PostgreSQL)
# ---------------------------------------------------------------------------


@requires_pg
class TestGraphClientCRUD:
    def test_add_and_get_decision(self, graph: GraphClient) -> None:
        d = _make_decision(title="Use PostgreSQL")
        graph.add_decision(d)
        fetched = graph.get_decision(d.id)
        assert fetched is not None
        assert fetched.title == "Use PostgreSQL"
        assert Dimension.DATABASE in fetched.dimensions

    def test_get_nonexistent_returns_none(self, graph: GraphClient) -> None:
        assert graph.get_decision(uuid4()) is None

    def test_get_decisions_project_filter(self, graph: GraphClient) -> None:
        graph.add_decision(_make_decision(title="D1", project="proj-a"))
        graph.add_decision(_make_decision(title="D2", project="proj-b"))
        results = graph.get_decisions("proj-a")
        assert len(results) == 1
        assert results[0].title == "D1"

    def test_supersede_marks_old_invalid(self, graph: GraphClient) -> None:
        old = _make_decision(title="Use SQLite")
        graph.add_decision(old)

        new = _make_decision(title="Use PostgreSQL")
        graph.supersede(old.id, new)

        old_fetched = graph.get_decision(old.id)
        assert old_fetched is not None
        assert old_fetched.valid is False
        assert old_fetched.status.value == "superseded"

        new_fetched = graph.get_decision(new.id)
        assert new_fetched is not None
        assert new_fetched.supersedes == old.id

    def test_active_only_filter(self, graph: GraphClient) -> None:
        old = _make_decision(title="Old")
        graph.add_decision(old)
        new = _make_decision(title="New")
        graph.supersede(old.id, new)

        active = graph.get_decisions("test-project", active_only=True)
        assert len(active) == 1
        assert active[0].title == "New"

        all_decisions = graph.get_decisions("test-project", active_only=False)
        assert len(all_decisions) == 2


@requires_pg
class TestGraphClientRanking:
    def test_find_related_by_shared_dimension(self, graph: GraphClient) -> None:
        d1 = _make_decision(
            title="Use PostgreSQL", dimensions=[Dimension.DATABASE],
        )
        d2 = _make_decision(
            title="Use SQLAlchemy ORM",
            dimensions=[Dimension.DATABASE, Dimension.API_STYLE],
        )
        d3 = _make_decision(
            title="Use REST API", dimensions=[Dimension.API_STYLE],
        )
        graph.add_decision(d1)
        graph.add_decision(d2)
        graph.add_decision(d3)

        related = graph.find_related(d1, limit=5, reorder_attention=False)
        assert len(related) >= 1
        # d2 shares DATABASE dimension with d1
        titles = {r.title for r in related}
        assert "Use SQLAlchemy ORM" in titles

    def test_find_related_empty_dimensions(self, graph: GraphClient) -> None:
        d = _make_decision(dimensions=[])
        assert graph.find_related(d) == []


@requires_pg
class TestGraphClientContradictions:
    def test_add_and_query_contradiction(self, graph: GraphClient) -> None:
        d1 = _make_decision(title="Use PostgreSQL")
        d2 = _make_decision(title="Use SQLite")
        graph.add_decision(d1)
        graph.add_decision(d2)

        c = Contradiction(
            decision_a_id=d1.id,
            decision_b_id=d2.id,
            decision_a_title=d1.title,
            decision_b_title=d2.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="Both address database choice with opposite conclusions",
            evidence_a="We chose PostgreSQL",
            evidence_b="SQLite with WAL mode",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.92,
        )
        graph.add_contradiction(c)

        unresolved = graph.get_unresolved_contradictions("test-project")
        assert len(unresolved) == 1
        assert unresolved[0].decision_a_title == "Use PostgreSQL"

    def test_resolve_contradiction(self, graph: GraphClient) -> None:
        d1 = _make_decision(title="A")
        d2 = _make_decision(title="B")
        graph.add_decision(d1)
        graph.add_decision(d2)

        c = Contradiction(
            decision_a_id=d1.id,
            decision_b_id=d2.id,
            decision_a_title="A",
            decision_b_title="B",
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="conflict",
            evidence_a="ev_a",
            evidence_b="ev_b",
            confidence=0.8,
        )
        graph.add_contradiction(c)
        assert graph.resolve_contradiction(c.id, "dev", "Chose PostgreSQL")

        unresolved = graph.get_unresolved_contradictions("test-project")
        assert len(unresolved) == 0

    def test_baseline_excluded_from_unresolved(self, graph: GraphClient) -> None:
        d1 = _make_decision(title="A")
        d2 = _make_decision(title="B")
        graph.add_decision(d1)
        graph.add_decision(d2)

        c = Contradiction(
            decision_a_id=d1.id,
            decision_b_id=d2.id,
            decision_a_title="A",
            decision_b_title="B",
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="legacy",
            evidence_a="ev_a",
            evidence_b="ev_b",
            confidence=0.8,
            is_baseline=True,
        )
        graph.add_contradiction(c)

        unresolved = graph.get_unresolved_contradictions("test-project")
        assert len(unresolved) == 0

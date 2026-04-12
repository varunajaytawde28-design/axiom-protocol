"""Tests for temporal decision graph."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from vt_protocol.decisions.temporal import (
    TemporalDecision,
    TemporalEdge,
    TemporalGraph,
    TimelineSnapshot,
)
from vt_protocol.decisions.models import (
    Decision,
    DecisionType,
    Dimension,
    SourceType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_decision(
    title: str = "Test Decision",
    dimensions: list[Dimension] | None = None,
    **kwargs,
) -> Decision:
    defaults = dict(
        title=title,
        content="Content for testing temporal decisions.",
        rationale="Because testing.",
        decision_type=DecisionType.TECHNICAL,
        dimensions=dimensions or [Dimension.DATABASE],
        made_by="test",
        project="test-project",
        source_type=SourceType.MANUAL,
    )
    defaults.update(kwargs)
    return Decision(**defaults)


def _ts(days_ago: int = 0) -> datetime:
    """Create a UTC timestamp N days ago."""
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# ---------------------------------------------------------------------------
# TemporalDecision
# ---------------------------------------------------------------------------


class TestTemporalDecision:
    def test_is_current(self) -> None:
        d = _make_decision()
        td = TemporalDecision(decision=d, valid_from=_ts(1))
        assert td.is_current is True

    def test_not_current_future_start(self) -> None:
        d = _make_decision()
        td = TemporalDecision(
            decision=d,
            valid_from=datetime.now(timezone.utc) + timedelta(days=1),
        )
        assert td.is_current is False

    def test_not_current_expired(self) -> None:
        d = _make_decision()
        td = TemporalDecision(
            decision=d,
            valid_from=_ts(10),
            valid_until=_ts(1),
        )
        assert td.is_current is False

    def test_is_valid_at(self) -> None:
        d = _make_decision()
        td = TemporalDecision(
            decision=d,
            valid_from=_ts(10),
            valid_until=_ts(5),
        )
        assert td.is_valid_at(_ts(7)) is True
        assert td.is_valid_at(_ts(3)) is False
        assert td.is_valid_at(_ts(15)) is False

    def test_valid_at_no_until(self) -> None:
        d = _make_decision()
        td = TemporalDecision(decision=d, valid_from=_ts(10))
        assert td.is_valid_at(_ts(0)) is True

    def test_to_dict(self) -> None:
        d = _make_decision()
        td = TemporalDecision(decision=d, valid_from=_ts(1))
        data = td.to_dict()
        assert "decision_id" in data
        assert "valid_from" in data
        assert "is_current" in data
        assert data["superseded_by"] is None


# ---------------------------------------------------------------------------
# TemporalEdge
# ---------------------------------------------------------------------------


class TestTemporalEdge:
    def test_to_dict(self) -> None:
        edge = TemporalEdge(
            from_decision_id=uuid4(),
            to_decision_id=uuid4(),
        )
        d = edge.to_dict()
        assert "from_decision_id" in d
        assert "to_decision_id" in d
        assert "superseded_at" in d


# ---------------------------------------------------------------------------
# TimelineSnapshot
# ---------------------------------------------------------------------------


class TestTimelineSnapshot:
    def test_to_dict(self) -> None:
        snap = TimelineSnapshot(
            timestamp=_ts(0),
            dimensions_covered=["database", "auth"],
        )
        d = snap.to_dict()
        assert d["active_count"] == 0
        assert d["dimensions_covered"] == ["database", "auth"]


# ---------------------------------------------------------------------------
# TemporalGraph — basic operations
# ---------------------------------------------------------------------------


class TestTemporalGraphBasic:
    def test_empty_graph(self) -> None:
        g = TemporalGraph()
        assert g.decision_count == 0
        assert g.edge_count == 0

    def test_add_decision(self) -> None:
        g = TemporalGraph()
        d = _make_decision()
        td = g.add_decision(d)
        assert g.decision_count == 1
        assert td.decision.id == d.id

    def test_add_with_temporal_bounds(self) -> None:
        g = TemporalGraph()
        start = _ts(10)
        end = _ts(5)
        td = g.add_decision(_make_decision(), valid_from=start, valid_until=end)
        assert td.valid_from == start
        assert td.valid_until == end

    def test_to_dict(self) -> None:
        g = TemporalGraph()
        g.add_decision(_make_decision())
        d = g.to_dict()
        assert d["total_decisions"] == 1
        assert d["total_edges"] == 0


# ---------------------------------------------------------------------------
# TemporalGraph — supersession
# ---------------------------------------------------------------------------


class TestSupersession:
    def test_supersede(self) -> None:
        g = TemporalGraph()
        old = _make_decision(title="Old DB")
        g.add_decision(old, valid_from=_ts(10))

        new = _make_decision(title="New DB")
        new_td = g.supersede(old.id, new)

        assert new_td is not None
        assert g.decision_count == 2
        assert g.edge_count == 1

    def test_supersede_sets_valid_until(self) -> None:
        g = TemporalGraph()
        old = _make_decision(title="Old")
        old_td = g.add_decision(old, valid_from=_ts(10))

        new = _make_decision(title="New")
        g.supersede(old.id, new)

        assert old_td.valid_until is not None
        assert old_td.superseded_by == new.id

    def test_supersede_unknown_returns_none(self) -> None:
        g = TemporalGraph()
        result = g.supersede(uuid4(), _make_decision())
        assert result is None

    def test_supersession_chain(self) -> None:
        g = TemporalGraph()
        d1 = _make_decision(title="v1")
        g.add_decision(d1, valid_from=_ts(30))

        d2 = _make_decision(title="v2")
        g.supersede(d1.id, d2, superseded_at=_ts(20))

        d3 = _make_decision(title="v3")
        g.supersede(d2.id, d3, superseded_at=_ts(10))

        chain = g.get_supersession_chain(d1.id)
        assert len(chain) == 3
        assert chain[0].decision.title == "v1"
        assert chain[2].decision.title == "v3"


# ---------------------------------------------------------------------------
# TemporalGraph — point-in-time queries
# ---------------------------------------------------------------------------


class TestPointInTimeQueries:
    def test_query_at_past(self) -> None:
        g = TemporalGraph()
        old = _make_decision(title="Old DB")
        g.add_decision(old, valid_from=_ts(20), valid_until=_ts(10))

        new = _make_decision(title="New DB")
        g.add_decision(new, valid_from=_ts(10))

        # 15 days ago: only "Old DB" was valid
        snap = g.query_at(_ts(15))
        assert len(snap.active_decisions) == 1
        assert snap.active_decisions[0].decision.title == "Old DB"

        # 5 days ago: only "New DB" is valid
        snap = g.query_at(_ts(5))
        assert len(snap.active_decisions) == 1
        assert snap.active_decisions[0].decision.title == "New DB"

    def test_query_current(self) -> None:
        g = TemporalGraph()
        d = _make_decision()
        g.add_decision(d, valid_from=_ts(1))
        snap = g.query_current()
        assert len(snap.active_decisions) == 1

    def test_query_at_covers_dimensions(self) -> None:
        g = TemporalGraph()
        g.add_decision(
            _make_decision(dimensions=[Dimension.DATABASE, Dimension.CACHING]),
            valid_from=_ts(1),
        )
        snap = g.query_at(_ts(0))
        assert "database" in snap.dimensions_covered
        assert "caching" in snap.dimensions_covered


# ---------------------------------------------------------------------------
# TemporalGraph — dimension history
# ---------------------------------------------------------------------------


class TestDimensionHistory:
    def test_dimension_history(self) -> None:
        g = TemporalGraph()
        d1 = _make_decision(title="Postgres", dimensions=[Dimension.DATABASE])
        g.add_decision(d1, valid_from=_ts(20))

        d2 = _make_decision(title="MySQL", dimensions=[Dimension.DATABASE])
        g.add_decision(d2, valid_from=_ts(10))

        history = g.query_dimension_history(Dimension.DATABASE)
        assert len(history) == 2
        assert history[0].decision.title == "Postgres"  # Older first
        assert history[1].decision.title == "MySQL"

    def test_empty_history(self) -> None:
        g = TemporalGraph()
        history = g.query_dimension_history(Dimension.AUTH)
        assert history == []

    def test_filters_by_dimension(self) -> None:
        g = TemporalGraph()
        g.add_decision(_make_decision(dimensions=[Dimension.DATABASE]), valid_from=_ts(1))
        g.add_decision(_make_decision(dimensions=[Dimension.AUTH]), valid_from=_ts(1))
        history = g.query_dimension_history(Dimension.AUTH)
        assert len(history) == 1

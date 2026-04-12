"""Tests for cross-agent causal coordinator."""

from __future__ import annotations

import pytest

from vt_protocol.coordination.coordinator import (
    CausalCoordinator,
    CausalEdge,
    CausalQuery,
)


class TestCausalEdge:
    def test_to_dict(self) -> None:
        edge = CausalEdge(
            source_agent_id="agent_a",
            target_agent_id="agent_b",
            source_decision_id="d1",
            target_decision_id="d2",
            taint_id="t1",
        )
        d = edge.to_dict()
        assert d["source_agent_id"] == "agent_a"
        assert d["taint_id"] == "t1"

    def test_default_id(self) -> None:
        edge = CausalEdge()
        assert len(edge.id) == 12


class TestCausalQuery:
    def test_empty(self) -> None:
        q = CausalQuery(agent_id="a")
        assert q.influence_count == 0

    def test_to_dict(self) -> None:
        q = CausalQuery(
            agent_id="a",
            influenced_decisions=["d1", "d2"],
        )
        d = q.to_dict()
        assert d["influence_count"] == 2


class TestCausalCoordinator:
    def test_record_edge(self) -> None:
        coord = CausalCoordinator()
        edge = coord.record_causal_edge(
            source_agent_id="agent_a",
            target_agent_id="agent_b",
            source_decision_id="d1",
            target_decision_id="d2",
            taint_id="t1",
        )
        assert coord.edge_count == 1
        assert edge.source_agent_id == "agent_a"

    def test_query_influenced_by(self) -> None:
        coord = CausalCoordinator()
        coord.record_causal_edge(
            source_agent_id="agent_a",
            target_agent_id="agent_b",
            source_decision_id="d1",
            target_decision_id="d2",
        )
        coord.record_causal_edge(
            source_agent_id="agent_a",
            target_agent_id="agent_c",
            source_decision_id="d1",
            target_decision_id="d3",
        )
        result = coord.query_influenced_by("agent_a")
        assert result.influence_count == 2
        assert "d2" in result.influenced_decisions
        assert "d3" in result.influenced_decisions

    def test_query_influences_on(self) -> None:
        coord = CausalCoordinator()
        coord.record_causal_edge(
            source_agent_id="agent_a",
            target_agent_id="agent_b",
            source_decision_id="d1",
            target_decision_id="d2",
        )
        result = coord.query_influences_on("agent_b")
        assert "d1" in result.influenced_decisions

    def test_query_no_results(self) -> None:
        coord = CausalCoordinator()
        result = coord.query_influenced_by("nonexistent")
        assert result.influence_count == 0

    def test_get_causal_chain(self) -> None:
        coord = CausalCoordinator()
        coord.record_causal_edge(
            source_agent_id="a",
            target_agent_id="b",
            source_decision_id="d1",
            target_decision_id="d2",
        )
        coord.record_causal_edge(
            source_agent_id="b",
            target_agent_id="c",
            source_decision_id="d2",
            target_decision_id="d3",
        )
        chain = coord.get_causal_chain("d3")
        assert len(chain) == 2  # d2→d3, d1→d2

    def test_get_causal_chain_single(self) -> None:
        coord = CausalCoordinator()
        coord.record_causal_edge(
            source_agent_id="a",
            target_agent_id="b",
            source_decision_id="d1",
            target_decision_id="d2",
        )
        chain = coord.get_causal_chain("d2")
        assert len(chain) == 1

    def test_get_causal_chain_no_edges(self) -> None:
        coord = CausalCoordinator()
        chain = coord.get_causal_chain("d1")
        assert len(chain) == 0

    def test_get_all_edges(self) -> None:
        coord = CausalCoordinator()
        coord.record_causal_edge(
            source_agent_id="a",
            target_agent_id="b",
            source_decision_id="d1",
            target_decision_id="d2",
        )
        assert len(coord.get_all_edges()) == 1

    def test_clear(self) -> None:
        coord = CausalCoordinator()
        coord.record_causal_edge(
            source_agent_id="a",
            target_agent_id="b",
            source_decision_id="d1",
            target_decision_id="d2",
        )
        coord.clear()
        assert coord.edge_count == 0

    def test_multiple_agents_chain(self) -> None:
        coord = CausalCoordinator()
        # A → B → C → D
        coord.record_causal_edge(source_agent_id="a", target_agent_id="b",
                                  source_decision_id="d1", target_decision_id="d2")
        coord.record_causal_edge(source_agent_id="b", target_agent_id="c",
                                  source_decision_id="d2", target_decision_id="d3")
        coord.record_causal_edge(source_agent_id="c", target_agent_id="d",
                                  source_decision_id="d3", target_decision_id="d4")
        chain = coord.get_causal_chain("d4")
        assert len(chain) == 3

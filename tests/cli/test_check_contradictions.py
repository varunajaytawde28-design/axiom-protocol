"""Tests for Bug 1: vt check contradiction detection.

Verifies that `vt check` detects contradictions between active decisions
sharing a dimension, using either heuristic (provider=none) or LLM pipeline.

Also tests fixes for Bugs A-E:
- Bug A: Heuristic always runs (REST vs GraphQL flagged on same dimension)
- Bug B: report_decision() triggers contradiction detection after save
- Bug C: Debug logging shows dimension grouping / tech extraction
- Bug D: LLM failure falls back to heuristic results
- Bug E: complete_session() triggers vt check
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vt_protocol.cli.commands import (
    _detect_contradictions,
    _extract_existing_sub_ids,
    _heuristic_contradiction_check,
    _save_contradictions,
    main,
)
from vt_protocol.decisions.models import (
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionType,
    Dimension,
    GovernanceConfig,
    ModelConfig,
    SourceType,
)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "myproject"
    root.mkdir()
    (root / ".git").mkdir()
    smm = root / ".smm"
    smm.mkdir()
    (smm / "decisions").mkdir()
    (smm / "contradictions").mkdir()
    (root / "governance.yaml").write_text(
        "extends:\n  - '@vt/recommended'\n"
        "model:\n  provider: none\n  model: ''\n"
        "agents:\n  claude: true\n"
        "rules:\n  freeze-on-adopt: true\n"
    )
    return root


@pytest.fixture()
def decision_sqlite() -> Decision:
    return Decision(
        title="Detected: SQLite Database",
        content="This project uses sqlite database via sqlite3. Do not introduce PostgreSQL, MongoDB, or any external database without explicit approval.",
        rationale="Auto-detected from project scan. Evidence: import:sqlite3",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.DATABASE],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


@pytest.fixture()
def decision_pg() -> Decision:
    return Decision(
        title="Detected: Relational Database",
        content="This project uses relational database via psycopg2. Do not introduce MongoDB, DynamoDB, Cassandra, or other NoSQL databases without explicit approval.",
        rationale="Auto-detected from project scan. Evidence: import:psycopg2",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.DATABASE],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


@pytest.fixture()
def decision_redis() -> Decision:
    return Decision(
        title="Detected: Caching",
        content="This project uses caching via redis.",
        rationale="Auto-detected",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.CACHING],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


@pytest.fixture()
def decision_rest() -> Decision:
    return Decision(
        title="Detected: REST API",
        content="This project uses REST API via fastapi. Do not introduce GraphQL, gRPC, or alternative API patterns without explicit approval.",
        rationale="Auto-detected from project scan. Evidence: import:fastapi",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.API_STYLE],
        constraints=[
            "This project uses REST API via fastapi. Do not introduce GraphQL, gRPC, or alternative API patterns without explicit approval."
        ],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


@pytest.fixture()
def decision_graphql() -> Decision:
    return Decision(
        title="Detected: GraphQL API",
        content="This project uses GraphQL API via graphene. Do not introduce REST, gRPC, or alternative API patterns without explicit approval.",
        rationale="Auto-detected from project scan. Evidence: import:graphene",
        decision_type=DecisionType.CONSTRAINT,
        dimensions=[Dimension.API_STYLE],
        constraints=[
            "This project uses GraphQL API via graphene. Do not introduce REST, gRPC, or alternative API patterns without explicit approval."
        ],
        made_by="vt-init",
        project="test",
        source_type=SourceType.SCAN,
    )


class TestHeuristicContradictionCheck:
    def test_different_techs_same_dimension_is_contradiction(
        self, decision_sqlite, decision_pg
    ) -> None:
        pairs = [(decision_sqlite, decision_pg, [Dimension.DATABASE])]
        results = _heuristic_contradiction_check(pairs)
        assert len(results) == 1
        assert results[0].verdict == ContradictionVerdict.CONTRADICTION
        assert results[0].decision_a_id == decision_sqlite.id
        assert results[0].decision_b_id == decision_pg.id
        assert Dimension.DATABASE in results[0].shared_dimensions

    def test_different_techs_on_separate_dimensions_still_flagged(
        self, decision_sqlite, decision_redis
    ) -> None:
        # Even though these are logically separate concerns, if passed as a pair
        # they get checked. Redis and SQLite are different techs.
        pairs = [(decision_sqlite, decision_redis, [Dimension.DATABASE])]
        results = _heuristic_contradiction_check(pairs)
        assert len(results) == 1

    def test_no_contradiction_same_tech(self) -> None:
        d1 = Decision(
            title="Use PostgreSQL for writes",
            content="PostgreSQL handles all write operations.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="test",
            project="test",
        )
        d2 = Decision(
            title="Use PostgreSQL for reads",
            content="PostgreSQL handles read replicas.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="test",
            project="test",
        )
        pairs = [(d1, d2, [Dimension.DATABASE])]
        results = _heuristic_contradiction_check(pairs)
        assert len(results) == 0

    def test_empty_pairs_returns_empty(self) -> None:
        results = _heuristic_contradiction_check([])
        assert results == []


class TestDetectContradictions:
    def test_provider_none_uses_heuristic(
        self, decision_sqlite, decision_pg
    ) -> None:
        config = GovernanceConfig(model=ModelConfig(provider="none", model=""))
        results = _detect_contradictions([decision_sqlite, decision_pg], config)
        assert len(results) >= 1
        assert any(
            r.verdict == ContradictionVerdict.CONTRADICTION for r in results
        )

    def test_single_decision_no_contradiction(self, decision_sqlite) -> None:
        config = GovernanceConfig(model=ModelConfig(provider="none", model=""))
        results = _detect_contradictions([decision_sqlite], config)
        assert results == []

    def test_no_shared_dimension_no_contradiction(self) -> None:
        d1 = Decision(
            title="Use SQLite",
            content="SQLite for storage.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.DATABASE],
            made_by="test",
            project="test",
        )
        d2 = Decision(
            title="Use Redis for caching",
            content="Redis as cache layer.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.CACHING],
            made_by="test",
            project="test",
        )
        config = GovernanceConfig(model=ModelConfig(provider="none", model=""))
        results = _detect_contradictions([d1, d2], config)
        assert results == []


class TestSaveContradictions:
    def test_saves_to_disk(self, project_root, decision_sqlite, decision_pg) -> None:
        c = Contradiction(
            decision_a_id=decision_sqlite.id,
            decision_b_id=decision_pg.id,
            decision_a_title=decision_sqlite.title,
            decision_b_title=decision_pg.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="Different databases",
            evidence_a="sqlite3",
            evidence_b="psycopg2",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.6,
        )
        _save_contradictions(project_root, [c])

        cdir = project_root / ".smm" / "contradictions"
        files = list(cdir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["verdict"] == "contradiction"

    def test_deduplicates_existing(self, project_root, decision_sqlite, decision_pg) -> None:
        c = Contradiction(
            decision_a_id=decision_sqlite.id,
            decision_b_id=decision_pg.id,
            decision_a_title=decision_sqlite.title,
            decision_b_title=decision_pg.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="Different databases",
            evidence_a="sqlite3",
            evidence_b="psycopg2",
            shared_dimensions=[Dimension.DATABASE],
            confidence=0.6,
        )
        _save_contradictions(project_root, [c])
        _save_contradictions(project_root, [c])  # save again

        cdir = project_root / ".smm" / "contradictions"
        files = list(cdir.glob("*.json"))
        assert len(files) == 1  # not duplicated


class TestVtCheckDetectsContradictions:
    def test_check_shows_contradiction_and_fails(
        self, runner, project_root, decision_sqlite, decision_pg
    ) -> None:
        # Write conflicting decisions to .smm/decisions/
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001-sqlite.json").write_text(decision_sqlite.model_dump_json(indent=2))
        (ddir / "002-pg.json").write_text(decision_pg.model_dump_json(indent=2))

        result = runner.invoke(main, ["check", "--path", str(project_root), "--exit-code"])
        assert result.exit_code == 1
        assert "FAIL" in result.output
        # Should show at least 1 contradiction
        assert "1 actionable" in result.output or "Actionable Contradictions" in result.output

    def test_check_passes_with_no_conflicts(
        self, runner, project_root, decision_sqlite
    ) -> None:
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001-sqlite.json").write_text(decision_sqlite.model_dump_json(indent=2))

        result = runner.invoke(main, ["check", "--path", str(project_root)])
        assert result.exit_code == 0
        assert "PASS" in result.output

    def test_check_json_output_shows_fail(
        self, runner, project_root, decision_sqlite, decision_pg
    ) -> None:
        ddir = project_root / ".smm" / "decisions"
        (ddir / "001-sqlite.json").write_text(decision_sqlite.model_dump_json(indent=2))
        (ddir / "002-pg.json").write_text(decision_pg.model_dump_json(indent=2))

        result = runner.invoke(main, ["check", "--path", str(project_root), "--json-output"])
        assert result.exit_code == 0  # --exit-code not passed
        data = json.loads(result.output)
        assert data["status"] == "fail"
        assert data["actionable_contradictions"] >= 1


# -----------------------------------------------------------------------
# Bug A: Heuristic always runs regardless of provider setting
# -----------------------------------------------------------------------


class TestHeuristicAlwaysRuns:
    """Bug A: Heuristic must run regardless of LLM provider setting."""

    def test_heuristic_flags_rest_vs_graphql(
        self, decision_rest, decision_graphql
    ) -> None:
        """Core bug: REST and GraphQL on same api-style dimension must be flagged."""
        config = GovernanceConfig(model=ModelConfig(provider="none", model=""))
        results = _detect_contradictions(
            [decision_rest, decision_graphql], config
        )
        assert len(results) >= 1
        assert any(c.verdict == ContradictionVerdict.CONTRADICTION for c in results)

    def test_heuristic_runs_with_ollama_provider(
        self, decision_rest, decision_graphql
    ) -> None:
        """Even with ollama provider, heuristic should run as fallback."""
        config = GovernanceConfig(model=ModelConfig(provider="ollama", model="llama3"))

        # Mock the LLM check to return nothing (simulating unreachable Ollama)
        with patch(
            "vt_protocol.cli.commands._llm_contradiction_check", return_value=[]
        ):
            results = _detect_contradictions(
                [decision_rest, decision_graphql], config
            )
        assert len(results) >= 1
        assert any(c.verdict == ContradictionVerdict.CONTRADICTION for c in results)

    def test_llm_overrides_heuristic_when_available(
        self, decision_rest, decision_graphql
    ) -> None:
        """When LLM returns results, they should be used instead of heuristic."""
        config = GovernanceConfig(model=ModelConfig(provider="ollama", model="llama3"))

        fake_llm_result = Contradiction(
            decision_a_id=decision_rest.id,
            decision_b_id=decision_graphql.id,
            decision_a_title=decision_rest.title,
            decision_b_title=decision_graphql.title,
            verdict=ContradictionVerdict.CONTRADICTION,
            reasoning="LLM says these conflict",
            evidence_a="REST API",
            evidence_b="GraphQL API",
            shared_dimensions=[Dimension.API_STYLE],
            confidence=0.9,
        )

        with patch(
            "vt_protocol.cli.commands._llm_contradiction_check",
            return_value=[fake_llm_result],
        ):
            results = _detect_contradictions(
                [decision_rest, decision_graphql], config
            )
        assert len(results) == 1
        assert results[0].confidence == 0.9
        assert "LLM" in results[0].reasoning

    def test_no_contradiction_for_different_dimensions(
        self, decision_rest, decision_sqlite
    ) -> None:
        """Decisions on different dimensions should not be paired."""
        config = GovernanceConfig(model=ModelConfig(provider="none", model=""))
        results = _detect_contradictions(
            [decision_rest, decision_sqlite], config
        )
        assert len(results) == 0


class TestHeuristicTechExtraction:
    """Bug A detail: verify tech extraction from decision content."""

    def test_extracts_different_techs_from_rest_vs_graphql(
        self, decision_rest, decision_graphql
    ) -> None:
        pairs = [(decision_rest, decision_graphql, [Dimension.API_STYLE])]
        results = _heuristic_contradiction_check(pairs)
        assert len(results) == 1
        assert results[0].verdict == ContradictionVerdict.CONTRADICTION
        # Reasoning should mention the tech names
        assert "rest" in results[0].reasoning.lower() or "fastapi" in results[0].reasoning.lower()
        assert "graphql" in results[0].reasoning.lower()

    def test_three_api_decisions_creates_three_contradictions(self) -> None:
        """Three decisions on same dimension should create 3 pairs, all contradictions."""
        d1 = Decision(
            title="REST API",
            content="This project uses REST API via fastapi.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.API_STYLE],
            made_by="test", project="test", source_type=SourceType.SCAN,
        )
        d2 = Decision(
            title="GraphQL API",
            content="This project uses GraphQL API via graphene.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.API_STYLE],
            made_by="test", project="test", source_type=SourceType.SCAN,
        )
        d3 = Decision(
            title="gRPC API",
            content="This project uses gRPC via grpc.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.API_STYLE],
            made_by="test", project="test", source_type=SourceType.SCAN,
        )
        config = GovernanceConfig(model=ModelConfig(provider="none", model=""))
        results = _detect_contradictions([d1, d2, d3], config)
        assert len(results) == 3


# -----------------------------------------------------------------------
# Bug B: report_decision() triggers contradiction detection
# -----------------------------------------------------------------------


class TestReportDecisionContradictions:
    """Bug B: report_decision() should run contradiction detection after save."""

    def test_report_decision_includes_contradictions_key(self) -> None:
        """The report_decision response should include contradiction info."""
        from vt_protocol.mcp.server import report_decision

        with patch("vt_protocol.mcp.server._detect_project", return_value="test"), \
             patch("vt_protocol.mcp.server._get_graph_client") as mock_client, \
             patch("vt_protocol.mcp.server._run_contradiction_detection_after_save", return_value=[]):
            mock_client.return_value.add_decision.return_value = "test-id"
            result = json.loads(report_decision(
                title="Test Decision",
                content="Test content",
                project="test",
            ))

        assert "contradictions_detected" in result
        assert "contradictions" in result

    def test_report_decision_returns_detected_contradictions(self) -> None:
        """When contradictions are found, they should be in the response."""
        from vt_protocol.mcp.server import report_decision

        fake_contradiction = {
            "decision_a": "REST API",
            "decision_b": "GraphQL API",
            "verdict": "contradiction",
            "reasoning": "Different API styles",
            "confidence": 0.8,
        }

        with patch("vt_protocol.mcp.server._detect_project", return_value="test"), \
             patch("vt_protocol.mcp.server._get_graph_client") as mock_client, \
             patch(
                 "vt_protocol.mcp.server._run_contradiction_detection_after_save",
                 return_value=[fake_contradiction],
             ):
            mock_client.return_value.add_decision.return_value = "test-id"
            result = json.loads(report_decision(
                title="Test Decision",
                content="Test content",
                project="test",
            ))

        assert result["contradictions_detected"] == 1
        assert len(result["contradictions"]) == 1
        assert result["contradictions"][0]["verdict"] == "contradiction"


# -----------------------------------------------------------------------
# Bug D: LLM fallback to heuristic
# -----------------------------------------------------------------------


class TestLlmFallback:
    """Bug D: When LLM is unreachable, heuristic results are preserved."""

    def test_ollama_unreachable_falls_back_to_heuristic(
        self, decision_rest, decision_graphql
    ) -> None:
        """If Ollama is down, _detect_contradictions should still return heuristic results."""
        config = GovernanceConfig(model=ModelConfig(provider="ollama", model="llama3"))

        with patch(
            "vt_protocol.cli.commands._llm_contradiction_check", return_value=[]
        ):
            results = _detect_contradictions(
                [decision_rest, decision_graphql], config
            )

        assert len(results) >= 1
        assert any(c.verdict == ContradictionVerdict.CONTRADICTION for c in results)
        # Should be heuristic result (mentions "Heuristic")
        assert any("Heuristic" in c.reasoning for c in results)

    def test_anthropic_unreachable_falls_back_to_heuristic(
        self, decision_sqlite, decision_pg
    ) -> None:
        """If Anthropic API is down, should fall back to heuristic."""
        config = GovernanceConfig(model=ModelConfig(provider="anthropic", model="claude-haiku-4-5-20251001"))

        with patch(
            "vt_protocol.cli.commands._llm_contradiction_check", return_value=[]
        ):
            results = _detect_contradictions(
                [decision_sqlite, decision_pg], config
            )

        assert len(results) >= 1


# -----------------------------------------------------------------------
# Bug E: complete_session triggers vt check
# -----------------------------------------------------------------------


class TestCompleteSession:
    """Bug E: complete_session should trigger contradiction detection."""

    def test_complete_session_returns_pass_status(self, project_root: Path) -> None:
        """complete_session should return pass status when no contradictions."""
        from vt_protocol.mcp.server import complete_session

        class MockModel:
            provider = "none"
            model = ""
        class MockConfig:
            model = MockModel()
            rules = type("R", (), {"max_new_deps_per_task": 3})()

        with patch("vt_protocol.mcp.server.find_project_root", return_value=project_root), \
             patch("vt_protocol.mcp.server._load_config", return_value=MockConfig()), \
             patch("vt_protocol.mcp.server._detect_project", return_value="test"):
            result = json.loads(complete_session(project="test"))

        assert result["status"] == "pass"
        assert "total_decisions" in result
        assert "actionable_contradictions" in result
        assert result["actionable_contradictions"] == 0

    def test_complete_session_detects_contradictions(self, project_root: Path) -> None:
        """complete_session should find and report contradictions."""
        from vt_protocol.mcp.server import complete_session

        # Write two conflicting decisions
        d1 = Decision(
            title="Detected: REST API",
            content="This project uses REST API via fastapi. Do not introduce GraphQL.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.API_STYLE],
            made_by="test", project="test", source_type=SourceType.SCAN,
        )
        d2 = Decision(
            title="Detected: GraphQL API",
            content="This project uses GraphQL API via graphene. Do not introduce REST.",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[Dimension.API_STYLE],
            made_by="test", project="test", source_type=SourceType.SCAN,
        )
        ddir = project_root / ".smm" / "decisions"
        (ddir / f"d1-{str(d1.id)[:8]}.json").write_text(d1.model_dump_json(indent=2))
        (ddir / f"d2-{str(d2.id)[:8]}.json").write_text(d2.model_dump_json(indent=2))

        class MockModel:
            provider = "none"
            model = ""
        class MockConfig:
            model = MockModel()
            rules = type("R", (), {"max_new_deps_per_task": 3})()

        with patch("vt_protocol.mcp.server.find_project_root", return_value=project_root), \
             patch("vt_protocol.mcp.server._load_config", return_value=MockConfig()), \
             patch("vt_protocol.mcp.server._detect_project", return_value="test"):
            result = json.loads(complete_session(project="test"))

        assert result["status"] == "fail"
        assert result["contradictions_detected"] >= 1
        assert result["actionable_contradictions"] >= 1
        assert len(result["actionable"]) >= 1

    def test_complete_session_handles_no_project(self) -> None:
        """complete_session should handle missing project gracefully."""
        from vt_protocol.mcp.server import complete_session

        with patch("vt_protocol.mcp.server.find_project_root", side_effect=FileNotFoundError()), \
             patch("vt_protocol.mcp.server._detect_project", return_value="test"):
            result = json.loads(complete_session(project="test"))

        assert result["status"] == "error"

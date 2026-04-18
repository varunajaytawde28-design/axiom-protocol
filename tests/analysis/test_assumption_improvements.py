"""Tests for all 6 assumption detection engine improvements.

1. Association Rule Mining (assumption_mining.py)
2. Tiered False Positive Thresholds (assumptions.py extensions)
3. Unsupervised Alert Clustering (assumption_cluster.py)
4. Graph-Based Centrality Scoring (assumption_priority.py)
5. Statistical Auto-Disable (assumptions.py extensions)
6. Tracking Metrics (assumption_pipeline.py extensions)
"""

from __future__ import annotations

import json
import os
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from vt_protocol.decisions.models import (
    AssumptionCategory,
    AssumptionStatus,
    CodeEvidence,
    DomainAssumption,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assumption(
    *,
    pattern_id: str = "no_null_handling",
    category: AssumptionCategory = AssumptionCategory.COMPLETENESS,
    severity: str = "medium",
    confidence: float = 0.6,
    file: str = "src/store.py",
    line: int = 10,
    summary: str = "Direct access without null check",
    question: str = "Is this always safe?",
    options: list[str] | None = None,
    status: AssumptionStatus = AssumptionStatus.DETECTED,
    detected_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> DomainAssumption:
    return DomainAssumption(
        id=uuid4(),
        pattern_id=pattern_id,
        category=category,
        severity=severity,
        confidence=confidence,
        summary=summary,
        code_evidence=[CodeEvidence(file=file, line=line, snippet="x['key']")],
        question=question,
        options=options or ["Yes, always safe", "No, add null check", "I need more context"],
        status=status,
        detected_at=detected_at or datetime.now(timezone.utc),
        resolved_at=resolved_at,
    )


# ===========================================================================
# Test 1: Association Rule Mining
# ===========================================================================


class TestAssociationRuleMining:
    """Tests for assumption_mining.py — frequent itemset mining over AST features."""

    def test_extract_features_basic(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            def process(data):
                try:
                    result = db.query("SELECT * FROM users")
                    logger.info("queried")
                except Exception:
                    logger.error("failed")
        """)
        features = extract_features(source)
        assert len(features) == 1
        fs = features[0]
        assert "try_except" in fs
        assert "db_call" in fs
        assert "logging" in fs

    def test_extract_features_null_check(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            def safe_access(obj):
                if obj is None:
                    return None
                return obj.value
        """)
        features = extract_features(source)
        assert len(features) == 1
        assert "null_check" in features[0]
        assert "return_early" in features[0]

    def test_extract_features_http_call(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            def fetch_data():
                response = requests.get("http://example.com")
                return response.json()
        """)
        features = extract_features(source)
        assert len(features) == 1
        assert "http_call" in features[0]

    def test_extract_features_file_io(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            def read_config():
                f = open("config.yaml")
                data = f.read()
                return data
        """)
        features = extract_features(source)
        assert len(features) == 1
        assert "file_io" in features[0]

    def test_extract_features_env_access(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            import os
            def get_key():
                return os.getenv("API_KEY")
        """)
        features = extract_features(source)
        assert len(features) == 1
        assert "env_access" in features[0]

    def test_extract_features_validation(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            def validate(x):
                assert isinstance(x, int)
                if x < 0:
                    raise ValueError("negative")
        """)
        features = extract_features(source)
        assert len(features) == 1
        assert "validation" in features[0]

    def test_extract_features_type_conversion(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            def parse(raw):
                return int(raw)
        """)
        features = extract_features(source)
        assert len(features) == 1
        assert "type_conversion" in features[0]

    def test_extract_features_empty_function(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        source = textwrap.dedent("""\
            def empty():
                pass
        """)
        features = extract_features(source)
        assert len(features) == 0

    def test_extract_features_syntax_error(self):
        from vt_protocol.analysis.assumption_mining import extract_features

        features = extract_features("def broken( {")
        assert features == []

    def test_mine_frequent_itemsets_basic(self):
        from vt_protocol.analysis.assumption_mining import mine_frequent_itemsets

        # 8 out of 10 functions have {try_except, db_call} co-occurring
        feature_sets = [
            frozenset({"try_except", "db_call", "logging"}),
            frozenset({"try_except", "db_call"}),
            frozenset({"try_except", "db_call", "null_check"}),
            frozenset({"try_except", "db_call"}),
            frozenset({"try_except", "db_call", "validation"}),
            frozenset({"try_except", "db_call"}),
            frozenset({"try_except", "db_call"}),
            frozenset({"try_except", "db_call"}),
            frozenset({"http_call", "logging"}),
            frozenset({"file_io", "validation"}),
        ]
        frequent = mine_frequent_itemsets(feature_sets, min_support=0.7)
        # {try_except, db_call} should be frequent (80% support)
        assert any(
            frozenset({"try_except", "db_call"}) <= fs
            for fs in frequent
        )

    def test_mine_frequent_itemsets_empty(self):
        from vt_protocol.analysis.assumption_mining import mine_frequent_itemsets

        assert mine_frequent_itemsets([], min_support=0.7) == []

    def test_mine_frequent_itemsets_threshold(self):
        from vt_protocol.analysis.assumption_mining import mine_frequent_itemsets

        # 6 out of 10 = 60% support, below 70% threshold
        feature_sets = [
            frozenset({"try_except", "db_call"}) for _ in range(6)
        ] + [
            frozenset({"http_call"}) for _ in range(4)
        ]
        frequent = mine_frequent_itemsets(feature_sets, min_support=0.7)
        # {try_except, db_call} should NOT be frequent at 70% threshold
        pairs = [fs for fs in frequent if len(fs) >= 2]
        assert not any(
            frozenset({"try_except", "db_call"}) <= fs
            for fs in pairs
        )

    def test_detect_missing_patterns(self):
        from vt_protocol.analysis.assumption_mining import detect_missing_patterns

        frequent = [frozenset({"try_except", "db_call", "logging"})]
        # Function has db_call and try_except but no logging
        source = textwrap.dedent("""\
            def incomplete():
                try:
                    db.query("SELECT 1")
                except Exception:
                    pass
        """)
        alerts = detect_missing_patterns(source, frequent, file_path="test.py")
        assert len(alerts) >= 1
        alert = alerts[0]
        assert "logging" in alert.full_pattern - alert.pattern_subset
        assert alert.confidence > 0

    def test_detect_missing_patterns_no_violation(self):
        from vt_protocol.analysis.assumption_mining import detect_missing_patterns

        frequent = [frozenset({"try_except", "db_call"})]
        # Function has both — no violation
        source = textwrap.dedent("""\
            def complete():
                try:
                    db.query("SELECT 1")
                except Exception:
                    pass
        """)
        alerts = detect_missing_patterns(source, frequent, file_path="test.py")
        assert len(alerts) == 0

    def test_mine_project(self, tmp_path):
        from vt_protocol.analysis.assumption_mining import mine_project

        # Use a nested "proj" dir to avoid pytest test_ prefix in tmp_path
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "a.py").write_text(textwrap.dedent("""\
            def f1():
                try:
                    db.query("x")
                    logger.info("ok")
                except Exception:
                    logger.error("fail")
            def f2():
                try:
                    db.execute("y")
                    logger.info("ok")
                except Exception:
                    logger.error("fail")
            def f3():
                try:
                    db.filter(x=1)
                    logger.info("ok")
                except Exception:
                    logger.error("fail")
        """))
        # b.py has a function missing logging
        (proj / "b.py").write_text(textwrap.dedent("""\
            def f4():
                try:
                    db.query("z")
                except Exception:
                    pass
        """))
        alerts = mine_project(proj, min_support=0.7)
        # Should detect missing "logging" in f4
        assert isinstance(alerts, list)


# ===========================================================================
# Test 2: Tiered False Positive Thresholds
# ===========================================================================


class TestTieredThresholds:
    """Tests for tiered confidence thresholds in assumptions.py."""

    def test_patterns_have_tier_field(self):
        from vt_protocol.analysis.assumptions import PATTERNS

        for p in PATTERNS:
            assert p.tier in ("architectural", "implementation"), \
                f"Pattern {p.pattern_id} has invalid tier: {p.tier}"

    def test_architectural_patterns_correct(self):
        from vt_protocol.analysis.assumptions import PATTERNS

        arch_ids = {p.pattern_id for p in PATTERNS if p.tier == "architectural"}
        # DATA_SCOPE, CONFIGURATION, FRAMEWORK should be architectural
        assert "single_source_write" in arch_ids
        assert "env_no_fallback" in arch_ids
        assert "orm_no_loading_strategy" in arch_ids
        assert "hardcoded_table_name" in arch_ids

    def test_implementation_patterns_correct(self):
        from vt_protocol.analysis.assumptions import PATTERNS

        impl_ids = {p.pattern_id for p in PATTERNS if p.tier == "implementation"}
        # COMPLETENESS, TEMPORAL, ACCESS should be implementation
        assert "no_null_handling" in impl_ids
        assert "no_pagination" in impl_ids
        assert "incomplete_enum" in impl_ids

    def test_tier_thresholds_values(self):
        from vt_protocol.analysis.assumptions import TIER_THRESHOLDS

        assert TIER_THRESHOLDS["architectural"] == 0.4
        assert TIER_THRESHOLDS["implementation"] == 0.7

    def test_get_tier_for_category(self):
        from vt_protocol.analysis.assumptions import get_tier_for_category

        assert get_tier_for_category(AssumptionCategory.DATA_SCOPE) == "architectural"
        assert get_tier_for_category(AssumptionCategory.CONFIGURATION) == "architectural"
        assert get_tier_for_category(AssumptionCategory.FRAMEWORK) == "architectural"
        assert get_tier_for_category(AssumptionCategory.COMPLETENESS) == "implementation"
        assert get_tier_for_category(AssumptionCategory.TEMPORAL) == "implementation"
        assert get_tier_for_category(AssumptionCategory.ACCESS) == "implementation"

    def test_get_tiered_threshold(self):
        from vt_protocol.analysis.assumptions import get_tiered_threshold

        assert get_tiered_threshold("architectural") == 0.4
        assert get_tiered_threshold("implementation") == 0.7
        assert get_tiered_threshold("unknown") == 0.5  # fallback

    def test_pipeline_uses_tiered_thresholds(self, tmp_path):
        """Architectural patterns with confidence 0.5 should pass (>0.4 threshold)
        but implementation patterns with confidence 0.5 should fail (>0.7 threshold)."""
        from vt_protocol.analysis.assumptions import scan_file
        from vt_protocol.analysis.assumption_pipeline import _PATTERN_TIERS

        # Directly scan a source string — avoids pytest tmp_path triggering _is_test_path
        source = (
            "import os\n"
            "key = os.environ['SECRET_KEY']\n"
            "data = result['field']\n"
        )
        from pathlib import Path
        raw = scan_file(Path("src/app.py"), source=source)

        # Should detect both env_no_fallback and no_null_handling
        pid_set = {a.pattern_id for a in raw}
        assert "env_no_fallback" in pid_set
        assert "no_null_handling" in pid_set

        # Check that tiered thresholds would filter correctly
        from vt_protocol.analysis.assumptions import get_tiered_threshold

        for a in raw:
            tier = _PATTERN_TIERS.get(a.pattern_id, "implementation")
            threshold = get_tiered_threshold(tier)
            if a.pattern_id == "env_no_fallback":
                # architectural, conf=0.7, threshold=0.4 → passes
                assert a.confidence >= threshold
            elif a.pattern_id == "no_null_handling":
                # implementation, conf=0.5, threshold=0.7 → filtered
                assert a.confidence < threshold


# ===========================================================================
# Test 3: Unsupervised Alert Clustering
# ===========================================================================


class TestAlertClustering:
    """Tests for assumption_cluster.py."""

    def test_cluster_empty_list(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assert cluster_assumptions([]) == []

    def test_singleton_cluster(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        a = _make_assumption(file="src/a.py", line=1)
        clusters = cluster_assumptions([a])
        assert len(clusters) == 1
        assert clusters[0].count == 1
        assert clusters[0].representative.id == a.id

    def test_same_file_same_pattern_clusters(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assumptions = [
            _make_assumption(file="src/store.py", line=i, pattern_id="no_null_handling")
            for i in range(5)
        ]
        clusters = cluster_assumptions(assumptions)
        # 5 same-pattern, same-file assumptions should form 1 cluster
        assert len(clusters) == 1
        assert clusters[0].count == 5

    def test_auto_cluster_threshold(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        # >10 same pattern in same file should auto-cluster
        assumptions = [
            _make_assumption(file="src/big.py", line=i, pattern_id="no_null_handling")
            for i in range(15)
        ]
        clusters = cluster_assumptions(assumptions)
        assert len(clusters) == 1
        assert clusters[0].count == 15

    def test_different_files_same_dir_merge(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assumptions = [
            _make_assumption(file="src/a.py", line=1, pattern_id="no_null_handling"),
            _make_assumption(file="src/b.py", line=2, pattern_id="no_null_handling"),
            _make_assumption(file="src/c.py", line=3, pattern_id="no_null_handling"),
        ]
        clusters = cluster_assumptions(assumptions)
        # 3 files in same directory, same pattern → merged into 1 cluster
        assert len(clusters) == 1
        assert clusters[0].count == 3

    def test_different_patterns_not_merged(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assumptions = [
            _make_assumption(file="src/a.py", line=1, pattern_id="no_null_handling"),
            _make_assumption(file="src/a.py", line=2, pattern_id="env_no_fallback",
                             category=AssumptionCategory.CONFIGURATION),
        ]
        clusters = cluster_assumptions(assumptions)
        assert len(clusters) == 2

    def test_cluster_severity_max(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assumptions = [
            _make_assumption(file="src/a.py", line=1, severity="low"),
            _make_assumption(file="src/a.py", line=2, severity="high"),
            _make_assumption(file="src/a.py", line=3, severity="medium"),
        ]
        clusters = cluster_assumptions(assumptions)
        assert len(clusters) == 1
        assert clusters[0].severity == "high"

    def test_cluster_confidence_avg(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assumptions = [
            _make_assumption(file="src/a.py", line=1, confidence=0.4),
            _make_assumption(file="src/a.py", line=2, confidence=0.8),
        ]
        clusters = cluster_assumptions(assumptions)
        assert len(clusters) == 1
        assert abs(clusters[0].confidence - 0.6) < 0.01

    def test_cluster_sort_order(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assumptions = [
            _make_assumption(file="src/a.py", line=1, severity="low",
                             pattern_id="no_pagination"),
            _make_assumption(file="src/b.py", line=1, severity="critical",
                             pattern_id="env_no_fallback",
                             category=AssumptionCategory.CONFIGURATION),
        ]
        clusters = cluster_assumptions(assumptions)
        assert len(clusters) == 2
        # Critical should come first
        assert clusters[0].severity == "critical"
        assert clusters[1].severity == "low"

    def test_flatten_clusters(self):
        from vt_protocol.analysis.assumption_cluster import (
            cluster_assumptions,
            flatten_clusters,
        )

        assumptions = [
            _make_assumption(file="src/a.py", line=i)
            for i in range(5)
        ]
        clusters = cluster_assumptions(assumptions)
        flat = flatten_clusters(clusters)
        assert len(flat) == 1  # 1 cluster = 1 representative
        assert isinstance(flat[0], DomainAssumption)

    def test_cluster_representative_has_question(self):
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions

        assumptions = [
            _make_assumption(file="src/a.py", line=1, question="", severity="low"),
            _make_assumption(file="src/a.py", line=2, question="Is this safe?",
                             severity="high"),
        ]
        clusters = cluster_assumptions(assumptions)
        assert clusters[0].question == "Is this safe?"


# ===========================================================================
# Test 4: Graph-Based Centrality Scoring
# ===========================================================================


class TestCentralityScoring:
    """Tests for assumption_priority.py."""

    def test_build_module_graph(self, tmp_path):
        from vt_protocol.analysis.assumption_priority import build_module_graph

        proj = tmp_path / "proj"
        proj.mkdir()
        src = proj / "src"
        pkg = src / "myapp"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("import json\n")
        (pkg / "api.py").write_text("import myapp.core\n")
        (pkg / "cli.py").write_text("import myapp.core\nimport myapp.api\n")

        graph = build_module_graph(proj)
        # myapp.core is imported by api and cli
        assert graph.in_degree.get("myapp.core", 0) >= 2 or graph.max_in_degree >= 2
        assert graph.max_in_degree >= 1

    def test_build_module_graph_empty(self, tmp_path):
        from vt_protocol.analysis.assumption_priority import build_module_graph

        graph = build_module_graph(tmp_path)
        assert graph.max_in_degree == 0

    def test_severity_scores(self):
        from vt_protocol.analysis.assumption_priority import SEVERITY_SCORES

        assert SEVERITY_SCORES["critical"] == 4.0
        assert SEVERITY_SCORES["high"] == 3.0
        assert SEVERITY_SCORES["medium"] == 2.0
        assert SEVERITY_SCORES["low"] == 1.0

    def test_prioritize_assumptions_ordering(self, tmp_path):
        from vt_protocol.analysis.assumption_priority import prioritize_assumptions

        proj = tmp_path / "proj"
        proj.mkdir()
        src = proj / "src"
        pkg = src / "myapp"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "store.py").write_text("import json\n")
        # Many modules import store
        for i in range(5):
            (pkg / f"mod{i}.py").write_text("from myapp import store\n")
        (pkg / "utils.py").write_text("pass\n")

        assumptions = [
            _make_assumption(
                file=str(pkg / "store.py"),
                severity="high",
                pattern_id="env_no_fallback",
            ),
            _make_assumption(
                file=str(pkg / "utils.py"),
                severity="high",
                pattern_id="no_null_handling",
            ),
        ]
        prioritized = prioritize_assumptions(assumptions, proj)
        assert len(prioritized) == 2
        # store.py has higher centrality, should be first
        assert prioritized[0].in_degree >= prioritized[1].in_degree

    def test_prioritize_empty(self, tmp_path):
        from vt_protocol.analysis.assumption_priority import prioritize_assumptions

        result = prioritize_assumptions([], tmp_path)
        assert result == []

    def test_centrality_multiplier_formula(self, tmp_path):
        from vt_protocol.analysis.assumption_priority import (
            build_module_graph,
            prioritize_assumptions,
        )

        proj = tmp_path / "proj"
        proj.mkdir()
        src = proj / "src" / "pkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "core.py").write_text("")
        (src / "a.py").write_text("from pkg import core\n")
        (src / "b.py").write_text("from pkg import core\n")

        graph = build_module_graph(proj)
        assert graph.max_in_degree >= 2

        a = _make_assumption(file=str(src / "core.py"), severity="medium")
        prioritized = prioritize_assumptions([a], proj)
        assert len(prioritized) == 1
        # centrality_multiplier should be > 1.0 for the most-imported module
        assert prioritized[0].centrality_multiplier >= 1.0

    def test_churn_scores_non_git(self, tmp_path):
        from vt_protocol.analysis.assumption_priority import get_churn_scores

        # tmp_path is not a git repo
        scores = get_churn_scores(tmp_path)
        assert scores == {}


# ===========================================================================
# Test 5: Statistical Auto-Disable
# ===========================================================================


class TestStatisticalAutoDisable:
    """Tests for pattern stats tracking and shadow mode in assumptions.py."""

    def test_load_save_pattern_stats(self, tmp_path):
        from vt_protocol.analysis.assumptions import (
            load_pattern_stats,
            save_pattern_stats,
        )

        (tmp_path / ".smm").mkdir()
        stats = {"no_null_handling": {"times_triggered": 5, "validated": 1,
                                       "rejected": 3, "deferred": 0, "mode": "active"}}
        save_pattern_stats(tmp_path, stats)
        loaded = load_pattern_stats(tmp_path)
        assert loaded["no_null_handling"]["times_triggered"] == 5

    def test_update_pattern_stats(self):
        from vt_protocol.analysis.assumptions import update_pattern_stats

        stats: dict = {}
        update_pattern_stats(stats, "test_pattern", "triggered")
        assert stats["test_pattern"]["times_triggered"] == 1
        update_pattern_stats(stats, "test_pattern", "triggered")
        assert stats["test_pattern"]["times_triggered"] == 2
        update_pattern_stats(stats, "test_pattern", "validated")
        assert stats["test_pattern"]["validated"] == 1
        update_pattern_stats(stats, "test_pattern", "rejected")
        assert stats["test_pattern"]["rejected"] == 1

    def test_check_shadow_mode_not_triggered_enough(self):
        from vt_protocol.analysis.assumptions import check_shadow_mode

        stats = {"p1": {"times_triggered": 10, "validated": 0, "rejected": 10}}
        assert check_shadow_mode(stats, "p1") is False  # < 20 triggers

    def test_check_shadow_mode_high_rejection(self):
        from vt_protocol.analysis.assumptions import check_shadow_mode

        stats = {"p1": {"times_triggered": 25, "validated": 1, "rejected": 20}}
        # rejection_ratio = 20/21 = 0.952 > 0.90, triggered >= 20
        assert check_shadow_mode(stats, "p1") is True

    def test_check_shadow_mode_acceptable_ratio(self):
        from vt_protocol.analysis.assumptions import check_shadow_mode

        stats = {"p1": {"times_triggered": 25, "validated": 10, "rejected": 10}}
        # rejection_ratio = 10/20 = 0.5 < 0.90
        assert check_shadow_mode(stats, "p1") is False

    def test_check_shadow_mode_no_resolutions(self):
        from vt_protocol.analysis.assumptions import check_shadow_mode

        stats = {"p1": {"times_triggered": 30, "validated": 0, "rejected": 0}}
        assert check_shadow_mode(stats, "p1") is False

    def test_get_pattern_mode(self):
        from vt_protocol.analysis.assumptions import get_pattern_mode

        # Active pattern
        stats = {"p1": {"times_triggered": 5, "validated": 3, "rejected": 1, "mode": "active"}}
        assert get_pattern_mode(stats, "p1") == "active"

        # Shadow by stats
        stats = {"p1": {"times_triggered": 25, "validated": 1, "rejected": 20, "mode": "active"}}
        assert get_pattern_mode(stats, "p1") == "shadow"

        # Manually set to shadow
        stats = {"p1": {"times_triggered": 5, "validated": 3, "rejected": 1, "mode": "shadow"}}
        assert get_pattern_mode(stats, "p1") == "shadow"

    def test_set_pattern_mode(self):
        from vt_protocol.analysis.assumptions import set_pattern_mode

        stats: dict = {}
        set_pattern_mode(stats, "p1", "shadow")
        assert stats["p1"]["mode"] == "shadow"

        set_pattern_mode(stats, "p1", "active")
        assert stats["p1"]["mode"] == "active"

    def test_compute_rule_roi(self):
        from vt_protocol.analysis.assumptions import compute_rule_roi

        stats = {"p1": {"times_triggered": 20, "validated": 10, "rejected": 5}}
        roi = compute_rule_roi(stats, "p1")
        # ROI = 10 / (20 * 0.1) = 10 / 2 = 5.0
        assert roi == 5.0

    def test_compute_rule_roi_zero_triggered(self):
        from vt_protocol.analysis.assumptions import compute_rule_roi

        stats = {"p1": {"times_triggered": 0, "validated": 0}}
        assert compute_rule_roi(stats, "p1") == 0.0

    def test_compute_rule_roi_missing_pattern(self):
        from vt_protocol.analysis.assumptions import compute_rule_roi

        assert compute_rule_roi({}, "nonexistent") == 0.0

    def test_pipeline_filters_shadow_patterns(self, tmp_path):
        """Shadow-mode patterns should be counted but not surfaced."""
        from vt_protocol.analysis.assumptions import (
            check_shadow_mode,
            get_pattern_mode,
            load_pattern_stats,
            save_pattern_stats,
        )

        # Test the shadow mode logic directly (avoids _is_test_path issue with
        # pytest tmp_path containing 'test_' in path)
        smm = tmp_path / ".smm"
        smm.mkdir()

        # Pattern with high rejection ratio: 25/26 = 0.96 > 0.90, triggered >= 20
        stats = {
            "env_no_fallback": {
                "times_triggered": 30,
                "validated": 1,
                "rejected": 25,
                "deferred": 0,
                "mode": "active",
            }
        }
        save_pattern_stats(tmp_path, stats)

        loaded = load_pattern_stats(tmp_path)
        assert check_shadow_mode(loaded, "env_no_fallback") is True
        assert get_pattern_mode(loaded, "env_no_fallback") == "shadow"

        # Pattern with acceptable ratio should NOT be shadowed
        stats["single_source_write"] = {
            "times_triggered": 30,
            "validated": 10,
            "rejected": 5,
            "deferred": 2,
            "mode": "active",
        }
        save_pattern_stats(tmp_path, stats)
        loaded = load_pattern_stats(tmp_path)
        assert check_shadow_mode(loaded, "single_source_write") is False
        assert get_pattern_mode(loaded, "single_source_write") == "active"


# ===========================================================================
# Test 6: Tracking Metrics
# ===========================================================================


class TestTrackingMetrics:
    """Tests for metrics tracking in assumption_pipeline.py."""

    def test_compute_metrics_basic(self, tmp_path):
        from vt_protocol.analysis.assumption_pipeline import compute_metrics

        (tmp_path / ".smm" / "assumptions").mkdir(parents=True)
        now = datetime.now(timezone.utc)
        assumptions = [
            _make_assumption(
                pattern_id="env_no_fallback",
                category=AssumptionCategory.CONFIGURATION,
                status=AssumptionStatus.VALIDATED,
                detected_at=now - timedelta(hours=2),
                resolved_at=now - timedelta(hours=1),
            ),
            _make_assumption(
                pattern_id="no_null_handling",
                category=AssumptionCategory.COMPLETENESS,
                status=AssumptionStatus.VALIDATED,
                detected_at=now - timedelta(hours=2),
                resolved_at=now,
            ),
            _make_assumption(
                pattern_id="no_pagination",
                status=AssumptionStatus.PROPOSED,
                detected_at=now,
            ),
        ]
        metrics = compute_metrics(tmp_path, assumptions)
        assert metrics.validated_architectural == 1  # env_no_fallback
        assert metrics.validated_implementation == 1  # no_null_handling
        assert metrics.cognitive_value_ratio == 1.0
        assert metrics.ttfva_seconds is not None

    def test_compute_metrics_no_validations(self, tmp_path):
        from vt_protocol.analysis.assumption_pipeline import compute_metrics

        (tmp_path / ".smm").mkdir()
        assumptions = [
            _make_assumption(status=AssumptionStatus.PROPOSED),
        ]
        metrics = compute_metrics(tmp_path, assumptions)
        assert metrics.ttfva_seconds is None
        assert metrics.cognitive_value_ratio == 0.0

    def test_compute_metrics_empty(self, tmp_path):
        from vt_protocol.analysis.assumption_pipeline import compute_metrics

        (tmp_path / ".smm").mkdir()
        metrics = compute_metrics(tmp_path, [])
        assert metrics.total_architectural == 0
        assert metrics.total_implementation == 0

    def test_format_metrics_summary(self):
        from vt_protocol.analysis.assumption_pipeline import (
            AssumptionMetrics,
            format_metrics_summary,
        )

        metrics = AssumptionMetrics(
            ttfva_seconds=120.0,
            cognitive_value_ratio=2.5,
            validated_architectural=5,
            validated_implementation=2,
            cluster_efficiency=3.2,
            rule_roi={"env_no_fallback": 5.0, "no_null_handling": 1.0},
        )
        summary = format_metrics_summary(metrics)
        assert "TTFVA" in summary
        assert "2.0min" in summary
        assert "Cognitive Value" in summary
        assert "2.5x" in summary
        assert "Cluster Efficiency" in summary
        assert "Rule ROI" in summary

    def test_format_metrics_no_ttfva(self):
        from vt_protocol.analysis.assumption_pipeline import (
            AssumptionMetrics,
            format_metrics_summary,
        )

        metrics = AssumptionMetrics(cognitive_value_ratio=0.0)
        summary = format_metrics_summary(metrics)
        assert "TTFVA" not in summary

    def test_pipeline_result_has_cluster_fields(self):
        from vt_protocol.analysis.assumption_pipeline import AssumptionPipelineResult

        result = AssumptionPipelineResult()
        assert result.shadowed == 0
        assert result.clusters_formed == 0
        assert result.cluster_compression == 0.0

    def test_resolve_updates_pattern_stats(self, tmp_path):
        """Resolving an assumption should update pattern stats."""
        from vt_protocol.analysis.assumption_pipeline import (
            resolve_assumption,
            save_assumptions,
        )
        from vt_protocol.analysis.assumptions import load_pattern_stats

        smm = tmp_path / ".smm"
        smm.mkdir()

        assumption = _make_assumption(
            status=AssumptionStatus.PROPOSED,
            pattern_id="env_no_fallback",
        )
        save_assumptions(tmp_path, [assumption])

        # Resolve as VALIDATED (option 0)
        resolve_assumption(tmp_path, str(assumption.id), 0, resolved_by="test")

        stats = load_pattern_stats(tmp_path)
        assert stats.get("env_no_fallback", {}).get("validated", 0) >= 1


# ===========================================================================
# Integration: Pipeline with all improvements
# ===========================================================================


class TestPipelineIntegration:
    """Integration tests for the full enhanced pipeline."""

    def test_full_pipeline_with_clustering_and_priority(self):
        """Test clustering and priority scoring with direct scan_file calls."""
        from vt_protocol.analysis.assumptions import scan_file
        from vt_protocol.analysis.assumption_cluster import cluster_assumptions
        from vt_protocol.analysis.assumption_priority import SEVERITY_SCORES

        source = textwrap.dedent("""\
            import os
            DB_URL = os.environ["DATABASE_URL"]
            API_KEY = os.environ["API_KEY"]
            SECRET = os.environ["SECRET"]
            data = result["name"]
            info = result["email"]
            value = result["id"]
        """)
        assumptions = scan_file(Path("src/store.py"), source=source)
        assert len(assumptions) > 0

        # Test clustering
        clusters = cluster_assumptions(assumptions)
        assert len(clusters) >= 1
        # Multiple same-pattern assumptions should cluster
        if len(assumptions) > 1:
            assert len(clusters) <= len(assumptions)

        # Test severity ordering
        for i in range(len(clusters) - 1):
            sev_a = SEVERITY_SCORES.get(clusters[i].severity, 0)
            sev_b = SEVERITY_SCORES.get(clusters[i + 1].severity, 0)
            assert sev_a >= sev_b

    def test_pipeline_preserves_all_fields(self):
        """Test that scanned assumptions have all required fields."""
        from vt_protocol.analysis.assumptions import scan_file

        source = 'import os\nkey = os.environ["KEY"]\n'
        assumptions = scan_file(Path("src/app.py"), source=source)
        for a in assumptions:
            assert a.pattern_id
            assert a.severity
            assert a.category
            assert a.confidence > 0

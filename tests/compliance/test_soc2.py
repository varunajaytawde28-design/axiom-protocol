"""Tests for SOC 2 compliance module."""

from __future__ import annotations

import pytest

from vt_protocol.compliance.soc2 import (
    SOC2_CRITERIA,
    TSC_CATEGORIES,
    VT_FEATURE_MAP,
    EvidenceItem,
    SOC2ComplianceReport,
    TrustServiceCriterion,
    check_soc2_compliance,
    generate_evidence_matrix,
    get_tsc_categories,
    map_features_to_criteria,
)


# ---------------------------------------------------------------------------
# Trust Service Criteria
# ---------------------------------------------------------------------------


class TestTrustServiceCriteria:
    def test_criteria_defined(self):
        assert len(SOC2_CRITERIA) >= 15

    def test_criteria_structure(self):
        for c in SOC2_CRITERIA:
            assert c.criterion_id != ""
            assert c.category in TSC_CATEGORIES
            assert c.title != ""

    def test_criterion_to_dict(self):
        c = SOC2_CRITERIA[0]
        d = c.to_dict()
        assert "criterion_id" in d
        assert "vt_features" in d


# ---------------------------------------------------------------------------
# Feature mapping
# ---------------------------------------------------------------------------


class TestFeatureMapping:
    def test_feature_map_not_empty(self):
        assert len(VT_FEATURE_MAP) > 0

    def test_map_features(self):
        result = map_features_to_criteria(["merkle_audit_log", "decision_tracking"])
        assert len(result) > 0
        assert "CC7.2" in result  # merkle_audit_log covers CC7.2

    def test_map_empty_features(self):
        result = map_features_to_criteria([])
        assert len(result) == 0

    def test_map_unknown_feature(self):
        result = map_features_to_criteria(["nonexistent_feature"])
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Evidence matrix
# ---------------------------------------------------------------------------


class TestEvidenceMatrix:
    def test_generates_items(self):
        items = generate_evidence_matrix(["merkle_audit_log"])
        assert len(items) > 0

    def test_available_evidence(self):
        items = generate_evidence_matrix(["merkle_audit_log"])
        available = [i for i in items if i.status == "available"]
        assert len(available) > 0

    def test_missing_evidence(self):
        items = generate_evidence_matrix([])
        missing = [i for i in items if i.status == "missing"]
        assert len(missing) == len(SOC2_CRITERIA)

    def test_evidence_item_to_dict(self):
        item = EvidenceItem(criterion_id="CC7.2", source="merkle_audit_log")
        d = item.to_dict()
        assert d["criterion_id"] == "CC7.2"


# ---------------------------------------------------------------------------
# SOC 2 Compliance Report
# ---------------------------------------------------------------------------


class TestSOC2ComplianceReport:
    def test_audit_ready_high_coverage(self):
        r = SOC2ComplianceReport(
            criteria_met=["CC1.1"] * 20,
            coverage_score=0.85,
        )
        assert r.audit_ready

    def test_not_audit_ready_low_coverage(self):
        r = SOC2ComplianceReport(coverage_score=0.5)
        assert not r.audit_ready

    def test_not_audit_ready_unmet(self):
        r = SOC2ComplianceReport(
            coverage_score=0.9,
            criteria_unmet=["CC1.1"],
        )
        assert not r.audit_ready

    def test_to_dict(self):
        r = SOC2ComplianceReport()
        d = r.to_dict()
        assert "audit_ready" in d
        assert "coverage_score" in d


# ---------------------------------------------------------------------------
# check_soc2_compliance
# ---------------------------------------------------------------------------


class TestCheckSOC2Compliance:
    def test_no_features(self):
        report = check_soc2_compliance(active_features=[])
        assert report.coverage_score == 0.0
        assert len(report.criteria_unmet) > 0

    def test_with_features(self):
        report = check_soc2_compliance(
            active_features=["merkle_audit_log", "decision_tracking", "governance_config"],
        )
        assert report.coverage_score > 0.0
        assert len(report.criteria_met) > 0

    def test_full_features(self):
        all_features = list(VT_FEATURE_MAP.keys())
        report = check_soc2_compliance(active_features=all_features)
        assert report.coverage_score > 0.5
        assert len(report.criteria_met) > len(report.criteria_unmet)

    def test_categories_covered(self):
        report = check_soc2_compliance(
            active_features=["merkle_audit_log", "infra_governance"],
        )
        assert "security" in report.categories_covered

    def test_recommendations(self):
        report = check_soc2_compliance(active_features=[])
        assert len(report.recommendations) > 0

    def test_with_governance_config(self):
        report = check_soc2_compliance(
            active_features=[],
            governance_config={"security": True, "access": True, "controls": True},
        )
        # Some criteria might be partially met due to config
        assert report.coverage_score >= 0.0

    def test_evidence_matrix_generated(self):
        report = check_soc2_compliance(active_features=["merkle_audit_log"])
        assert len(report.evidence_matrix) > 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_tsc_categories(self):
        cats = get_tsc_categories()
        assert "security" in cats
        assert "availability" in cats
        assert len(cats) == 5

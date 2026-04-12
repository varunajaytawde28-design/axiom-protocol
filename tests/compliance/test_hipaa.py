"""Tests for HIPAA compliance module."""

from __future__ import annotations

import pytest

from vt_protocol.compliance.hipaa import (
    HIPAA_DIMENSIONS,
    HIPAA_REQUIREMENTS,
    HIPAAComplianceReport,
    HIPAARequirement,
    PHIDetection,
    check_hipaa_compliance,
    get_hipaa_dimensions,
    scan_for_phi,
)


# ---------------------------------------------------------------------------
# PHI Detection
# ---------------------------------------------------------------------------


class TestPHIDetection:
    def test_to_dict(self):
        d = PHIDetection(pattern_name="ssn", matched_text="123-45-6789")
        assert d.to_dict()["pattern_name"] == "ssn"


class TestScanForPHI:
    def test_detect_ssn(self):
        content = "Patient SSN: 123-45-6789"
        detections = scan_for_phi(content)
        assert len(detections) >= 1
        assert any(d.pattern_name == "ssn" for d in detections)

    def test_detect_mrn(self):
        content = "MRN: 1234567890"
        detections = scan_for_phi(content)
        assert any(d.pattern_name == "mrn" for d in detections)

    def test_detect_dob(self):
        content = "DOB: 01/15/1990"
        detections = scan_for_phi(content)
        assert any(d.pattern_name == "dob" for d in detections)

    def test_detect_npi(self):
        content = "NPI: 1234567890"
        detections = scan_for_phi(content)
        assert any(d.pattern_name == "npi" for d in detections)

    def test_no_phi(self):
        content = "This is clean code with no PHI."
        detections = scan_for_phi(content)
        assert len(detections) == 0

    def test_file_path_and_line(self):
        content = "line1\nSSN: 123-45-6789\nline3"
        detections = scan_for_phi(content, file_path="test.py")
        assert detections[0].file_path == "test.py"
        assert detections[0].line_number == 2

    def test_ssn_severity_high(self):
        content = "SSN: 123-45-6789"
        detections = scan_for_phi(content)
        ssn = [d for d in detections if d.pattern_name == "ssn"]
        assert ssn[0].severity == "high"


# ---------------------------------------------------------------------------
# HIPAA Requirements
# ---------------------------------------------------------------------------


class TestHIPAARequirements:
    def test_requirements_defined(self):
        assert len(HIPAA_REQUIREMENTS) >= 6

    def test_requirement_structure(self):
        for req in HIPAA_REQUIREMENTS:
            assert req.rule_id != ""
            assert req.title != ""
            assert req.section != ""
            assert len(req.dimensions) > 0

    def test_requirement_to_dict(self):
        req = HIPAA_REQUIREMENTS[0]
        d = req.to_dict()
        assert "rule_id" in d
        assert "dimensions" in d


# ---------------------------------------------------------------------------
# HIPAA Compliance Report
# ---------------------------------------------------------------------------


class TestHIPAAComplianceReport:
    def test_compliant_when_clean(self):
        r = HIPAAComplianceReport(
            requirements_met=["HIPAA-ACCESS-1"],
        )
        assert r.compliant

    def test_not_compliant_with_phi(self):
        r = HIPAAComplianceReport(
            phi_detections=[PHIDetection(pattern_name="ssn")],
        )
        assert not r.compliant

    def test_not_compliant_with_unmet(self):
        r = HIPAAComplianceReport(
            requirements_unmet=["HIPAA-ACCESS-1"],
        )
        assert not r.compliant


# ---------------------------------------------------------------------------
# check_hipaa_compliance
# ---------------------------------------------------------------------------


class TestCheckHIPAACompliance:
    def test_empty_config_all_unmet(self):
        report = check_hipaa_compliance(governance_config={})
        assert len(report.requirements_unmet) > 0
        assert report.risk_score > 0

    def test_config_with_dimensions(self):
        config = {
            "dimensions": ["access-controls", "audit-trail", "phi-handling"],
        }
        report = check_hipaa_compliance(governance_config=config)
        assert len(report.requirements_met) > 0

    def test_source_with_phi(self):
        config = {"dimensions": ["phi-handling"]}
        report = check_hipaa_compliance(
            governance_config=config,
            source_content="Patient SSN: 123-45-6789",
        )
        assert len(report.phi_detections) > 0
        assert report.risk_score > 0

    def test_clean_source_no_phi(self):
        config = {"dimensions": HIPAA_DIMENSIONS}
        report = check_hipaa_compliance(
            governance_config=config,
            source_content="clean code here",
        )
        assert len(report.phi_detections) == 0

    def test_recommendations_generated(self):
        report = check_hipaa_compliance(governance_config={})
        assert len(report.recommendations) > 0

    def test_risk_score_bounded(self):
        report = check_hipaa_compliance(
            governance_config={},
            source_content="SSN: 123-45-6789\n" * 20,
        )
        assert 0.0 <= report.risk_score <= 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_hipaa_dimensions(self):
        dims = get_hipaa_dimensions()
        assert "phi-handling" in dims
        assert "access-controls" in dims
        assert len(dims) == 8

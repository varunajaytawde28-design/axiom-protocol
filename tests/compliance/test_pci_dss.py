"""Tests for PCI-DSS compliance module."""

from __future__ import annotations

import pytest

from vt_protocol.compliance.pci_dss import (
    CARD_PATTERNS,
    PCI_DIMENSIONS,
    PCI_REQUIREMENTS,
    CardDataDetection,
    PCIComplianceReport,
    check_pci_compliance,
    get_pci_dimensions,
    luhn_check,
    scan_for_card_data,
)


# ---------------------------------------------------------------------------
# Luhn check
# ---------------------------------------------------------------------------


class TestLuhnCheck:
    def test_valid_visa(self):
        # Standard Luhn-valid test number
        assert luhn_check("4111111111111111") is True

    def test_valid_mastercard(self):
        assert luhn_check("5500000000000004") is True

    def test_invalid_number(self):
        assert luhn_check("1234567890123456") is False

    def test_too_short(self):
        assert luhn_check("123456") is False

    def test_too_long(self):
        assert luhn_check("12345678901234567890") is False


# ---------------------------------------------------------------------------
# Card data scanning
# ---------------------------------------------------------------------------


class TestScanForCardData:
    def test_detect_visa(self):
        # Use a Luhn-valid Visa test number
        content = "Card: 4111 1111 1111 1111"
        detections = scan_for_card_data(content)
        assert any(d.pattern_name == "visa" for d in detections)

    def test_detect_cvv(self):
        content = "CVV: 123"
        detections = scan_for_card_data(content)
        assert any(d.pattern_name == "cvv" for d in detections)

    def test_detect_expiry(self):
        content = "expiry: 12/25"
        detections = scan_for_card_data(content)
        assert any(d.pattern_name == "expiry" for d in detections)

    def test_no_card_data(self):
        content = "This is clean code with no card data."
        detections = scan_for_card_data(content)
        assert len(detections) == 0

    def test_invalid_luhn_rejected(self):
        content = "Card: 4111 1111 1111 1112"  # invalid Luhn
        detections = scan_for_card_data(content)
        visa_matches = [d for d in detections if d.pattern_name == "visa"]
        assert len(visa_matches) == 0

    def test_file_path_and_line(self):
        content = "line1\nCVV: 123\nline3"
        detections = scan_for_card_data(content, file_path="payment.py")
        assert detections[0].file_path == "payment.py"
        assert detections[0].line_number == 2

    def test_card_severity_critical(self):
        content = "Card: 4111 1111 1111 1111"
        detections = scan_for_card_data(content)
        card_matches = [d for d in detections if d.pattern_name == "visa"]
        if card_matches:
            assert card_matches[0].severity == "critical"


# ---------------------------------------------------------------------------
# PCI Requirements
# ---------------------------------------------------------------------------


class TestPCIRequirements:
    def test_twelve_requirements(self):
        assert len(PCI_REQUIREMENTS) == 12

    def test_requirement_structure(self):
        for req in PCI_REQUIREMENTS:
            assert req.req_id.startswith("PCI-")
            assert req.title != ""
            assert len(req.dimensions) > 0

    def test_requirement_to_dict(self):
        d = PCI_REQUIREMENTS[0].to_dict()
        assert "req_id" in d
        assert "controls" in d


# ---------------------------------------------------------------------------
# PCI Compliance Report
# ---------------------------------------------------------------------------


class TestPCIComplianceReport:
    def test_compliant_when_clean(self):
        r = PCIComplianceReport(requirements_met=["PCI-1"])
        assert r.compliant

    def test_not_compliant_with_detections(self):
        r = PCIComplianceReport(
            card_detections=[CardDataDetection(pattern_name="visa")],
        )
        assert not r.compliant

    def test_to_dict(self):
        r = PCIComplianceReport(saq_type="SAQ-A")
        d = r.to_dict()
        assert d["saq_type"] == "SAQ-A"


# ---------------------------------------------------------------------------
# check_pci_compliance
# ---------------------------------------------------------------------------


class TestCheckPCICompliance:
    def test_empty_config(self):
        report = check_pci_compliance(governance_config={})
        assert len(report.requirements_unmet) > 0
        assert report.risk_score > 0

    def test_config_with_dimensions(self):
        config = {
            "dimensions": ["cardholder-data", "encryption", "access-control"],
        }
        report = check_pci_compliance(governance_config=config)
        assert len(report.requirements_met) > 0

    def test_source_with_card_data(self):
        config = {"dimensions": ["cardholder-data"]}
        report = check_pci_compliance(
            governance_config=config,
            source_content="CVV: 123",
        )
        assert len(report.card_detections) > 0

    def test_saq_type_default(self):
        report = check_pci_compliance(governance_config={})
        assert report.saq_type == "SAQ-A-EP"

    def test_saq_type_payment_processor(self):
        report = check_pci_compliance(
            governance_config={"stack": {"payment_processor": True}},
        )
        assert report.saq_type == "SAQ-D"

    def test_recommendations(self):
        report = check_pci_compliance(governance_config={})
        assert len(report.recommendations) > 0

    def test_risk_score_bounded(self):
        report = check_pci_compliance(governance_config={})
        assert 0.0 <= report.risk_score <= 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_pci_dimensions(self):
        dims = get_pci_dimensions()
        assert "cardholder-data" in dims
        assert len(dims) == 8

"""PCI-DSS compliance module.

Detect payment card data patterns in code and decisions,
map governance dimensions to PCI-DSS requirements.

From SPEC Sprint 23: "Industry compliance modules — PCI-DSS."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# PCI-DSS specific governance dimensions
PCI_DIMENSIONS: list[str] = [
    "cardholder-data",
    "network-segmentation",
    "access-control",
    "encryption",
    "vulnerability-management",
    "monitoring-logging",
    "security-testing",
    "security-policy",
]

# Card number patterns (Luhn-checkable prefixes)
CARD_PATTERNS: dict[str, str] = {
    "visa": r"\b4\d{3}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
    "mastercard": r"\b5[1-5]\d{2}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
    "amex": r"\b3[47]\d{2}[\s\-]?\d{6}[\s\-]?\d{5}\b",
    "cvv": r"\b(?:CVV|CVC|CVV2)[:\s]*\d{3,4}\b",
    "expiry": r"\b(?:exp(?:iry|iration)?)[:\s]*\d{2}[/\-]\d{2,4}\b",
    "track_data": r"\b%B\d{13,19}\^\w+\b",  # magnetic stripe track data
}


@dataclass
class CardDataDetection:
    """A detected instance of potential card data in source code."""

    pattern_name: str = ""
    matched_text: str = ""
    file_path: str = ""
    line_number: int = 0
    severity: str = "critical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_name": self.pattern_name,
            "matched_text": self.matched_text,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "severity": self.severity,
        }


@dataclass
class PCIRequirement:
    """A PCI-DSS requirement mapped to governance dimensions."""

    req_id: str = ""
    title: str = ""
    description: str = ""
    dimensions: list[str] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "req_id": self.req_id,
            "title": self.title,
            "description": self.description,
            "dimensions": self.dimensions,
            "controls": self.controls,
        }


@dataclass
class PCIComplianceReport:
    """Report of PCI-DSS compliance analysis."""

    card_detections: list[CardDataDetection] = field(default_factory=list)
    requirements_met: list[str] = field(default_factory=list)
    requirements_unmet: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    recommendations: list[str] = field(default_factory=list)
    saq_type: str = ""  # Self-Assessment Questionnaire type

    @property
    def compliant(self) -> bool:
        return len(self.card_detections) == 0 and len(self.requirements_unmet) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "compliant": self.compliant,
            "card_detections": [d.to_dict() for d in self.card_detections],
            "requirements_met": self.requirements_met,
            "requirements_unmet": self.requirements_unmet,
            "risk_score": round(self.risk_score, 4),
            "recommendations": self.recommendations,
            "saq_type": self.saq_type,
        }


# ---------------------------------------------------------------------------
# PCI-DSS 4.0 requirement definitions (12 requirements)
# ---------------------------------------------------------------------------

PCI_REQUIREMENTS: list[PCIRequirement] = [
    PCIRequirement(
        req_id="PCI-1",
        title="Install and maintain network security controls",
        description="Firewall and router configurations to protect cardholder data.",
        dimensions=["network-segmentation"],
        controls=["firewall_rules", "network_diagram", "dmz"],
    ),
    PCIRequirement(
        req_id="PCI-2",
        title="Apply secure configurations to all system components",
        description="Do not use vendor-supplied defaults for system passwords.",
        dimensions=["security-policy"],
        controls=["hardening_standards", "no_default_passwords", "unnecessary_services_disabled"],
    ),
    PCIRequirement(
        req_id="PCI-3",
        title="Protect stored account data",
        description="Protect stored cardholder data with encryption.",
        dimensions=["cardholder-data", "encryption"],
        controls=["encryption_at_rest", "key_management", "data_retention_policy"],
    ),
    PCIRequirement(
        req_id="PCI-4",
        title="Protect cardholder data with strong cryptography during transmission",
        description="Encrypt transmission of cardholder data across open networks.",
        dimensions=["encryption"],
        controls=["tls_required", "certificate_management"],
    ),
    PCIRequirement(
        req_id="PCI-5",
        title="Protect all systems and networks from malicious software",
        description="Anti-malware mechanisms on all applicable systems.",
        dimensions=["vulnerability-management"],
        controls=["antivirus", "malware_detection"],
    ),
    PCIRequirement(
        req_id="PCI-6",
        title="Develop and maintain secure systems and software",
        description="Address vulnerabilities through patching and secure SDLC.",
        dimensions=["vulnerability-management", "security-testing"],
        controls=["patch_management", "secure_sdlc", "code_review"],
    ),
    PCIRequirement(
        req_id="PCI-7",
        title="Restrict access to system components and cardholder data",
        description="Limit access by business need to know.",
        dimensions=["access-control"],
        controls=["role_based_access", "least_privilege"],
    ),
    PCIRequirement(
        req_id="PCI-8",
        title="Identify users and authenticate access",
        description="Assign unique IDs and enforce strong authentication.",
        dimensions=["access-control"],
        controls=["unique_user_ids", "mfa", "password_policy"],
    ),
    PCIRequirement(
        req_id="PCI-9",
        title="Restrict physical access to cardholder data",
        description="Limit physical access to cardholder data environments.",
        dimensions=["cardholder-data"],
        controls=["physical_access_controls", "visitor_logs"],
    ),
    PCIRequirement(
        req_id="PCI-10",
        title="Log and monitor all access to system components",
        description="Track and monitor all access to network resources and cardholder data.",
        dimensions=["monitoring-logging"],
        controls=["audit_logging", "log_review", "time_synchronization"],
    ),
    PCIRequirement(
        req_id="PCI-11",
        title="Test security of systems and networks regularly",
        description="Regular testing of security systems and processes.",
        dimensions=["security-testing"],
        controls=["vulnerability_scanning", "penetration_testing", "ids_ips"],
    ),
    PCIRequirement(
        req_id="PCI-12",
        title="Support information security with organizational policies",
        description="Maintain a security policy for all personnel.",
        dimensions=["security-policy"],
        controls=["security_policy", "risk_assessment", "security_awareness"],
    ),
]


def luhn_check(number: str) -> bool:
    """Validate a card number using the Luhn algorithm."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def scan_for_card_data(content: str, *, file_path: str = "") -> list[CardDataDetection]:
    """Scan content for potential payment card data."""
    detections: list[CardDataDetection] = []
    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        for pattern_name, regex in CARD_PATTERNS.items():
            for match in re.finditer(regex, line, re.IGNORECASE):
                text = match.group()
                # For card numbers, validate with Luhn
                if pattern_name in ("visa", "mastercard", "amex"):
                    digits = re.sub(r"[\s\-]", "", text)
                    if not luhn_check(digits):
                        continue

                detections.append(CardDataDetection(
                    pattern_name=pattern_name,
                    matched_text=text,
                    file_path=file_path,
                    line_number=line_num,
                    severity="critical" if pattern_name in ("visa", "mastercard", "amex", "track_data") else "high",
                ))

    return detections


def check_pci_compliance(
    *,
    governance_config: dict[str, Any],
    source_content: str = "",
    file_path: str = "",
) -> PCIComplianceReport:
    """Check governance config and source code for PCI-DSS compliance."""
    report = PCIComplianceReport()

    # Scan source for card data
    if source_content:
        report.card_detections = scan_for_card_data(source_content, file_path=file_path)

    # Determine SAQ type based on config
    config_stack = governance_config.get("stack", {})
    if "payment_processor" in str(config_stack):
        report.saq_type = "SAQ-D"
    elif "hosted_payment" in str(config_stack) or "iframe" in str(config_stack):
        report.saq_type = "SAQ-A"
    else:
        report.saq_type = "SAQ-A-EP"

    # Check requirements
    config_dims = set(governance_config.get("dimensions", []))
    config_rules = governance_config.get("rules", {})
    config_conventions = governance_config.get("conventions", {})

    for req in PCI_REQUIREMENTS:
        met = False

        req_dims_covered = any(d in config_dims for d in req.dimensions)
        controls_in_config = any(
            c in str(config_rules) or c in str(config_conventions)
            for c in req.controls
        )

        if req_dims_covered or controls_in_config:
            met = True

        if met:
            report.requirements_met.append(req.req_id)
        else:
            report.requirements_unmet.append(req.req_id)
            report.recommendations.append(
                f"{req.req_id}: {req.title} — {req.description}"
            )

    # Risk score
    total_reqs = len(PCI_REQUIREMENTS)
    unmet = len(report.requirements_unmet)
    card_penalty = min(len(report.card_detections) * 0.15, 0.5)
    report.risk_score = min(1.0, (unmet / max(total_reqs, 1)) + card_penalty)

    return report


def get_pci_dimensions() -> list[str]:
    """Return the list of PCI-DSS specific governance dimensions."""
    return list(PCI_DIMENSIONS)

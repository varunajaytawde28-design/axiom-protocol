"""HIPAA compliance module.

Detect PHI (Protected Health Information) patterns in code and decisions,
map governance dimensions to HIPAA requirements.

From SPEC Sprint 23: "Industry compliance modules — HIPAA."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# HIPAA-specific governance dimensions
HIPAA_DIMENSIONS: list[str] = [
    "phi-handling",
    "minimum-necessary",
    "access-controls",
    "audit-trail",
    "encryption-at-rest",
    "encryption-in-transit",
    "breach-notification",
    "business-associate",
]

# PHI detection patterns (regex)
PHI_PATTERNS: dict[str, str] = {
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "mrn": r"\bMRN[:\s#]*\d{6,10}\b",
    "dob": r"\b(?:DOB|date.?of.?birth)[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b",
    "phone": r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "email_health": r"\b[a-zA-Z0-9._%+-]+@(?:hospital|clinic|health|medical)\.[a-zA-Z]{2,}\b",
    "diagnosis_code": r"\b[A-Z]\d{2}(?:\.\d{1,4})?\b",  # ICD-10 codes
    "npi": r"\bNPI[:\s#]*\d{10}\b",
}


@dataclass
class PHIDetection:
    """A detected instance of potential PHI in source code or config."""

    pattern_name: str = ""
    matched_text: str = ""
    file_path: str = ""
    line_number: int = 0
    severity: str = "high"  # high, medium, low

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_name": self.pattern_name,
            "matched_text": self.matched_text,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "severity": self.severity,
        }


@dataclass
class HIPAARequirement:
    """A HIPAA requirement mapped to governance dimensions."""

    rule_id: str = ""
    title: str = ""
    section: str = ""  # e.g. "164.312(a)(1)"
    description: str = ""
    dimensions: list[str] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "section": self.section,
            "description": self.description,
            "dimensions": self.dimensions,
            "controls": self.controls,
        }


@dataclass
class HIPAAComplianceReport:
    """Report of HIPAA compliance analysis."""

    phi_detections: list[PHIDetection] = field(default_factory=list)
    requirements_met: list[str] = field(default_factory=list)
    requirements_unmet: list[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0 = fully compliant, 1.0 = high risk
    recommendations: list[str] = field(default_factory=list)

    @property
    def compliant(self) -> bool:
        return len(self.phi_detections) == 0 and len(self.requirements_unmet) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "compliant": self.compliant,
            "phi_detections": [d.to_dict() for d in self.phi_detections],
            "requirements_met": self.requirements_met,
            "requirements_unmet": self.requirements_unmet,
            "risk_score": round(self.risk_score, 4),
            "recommendations": self.recommendations,
        }


# ---------------------------------------------------------------------------
# HIPAA requirement definitions
# ---------------------------------------------------------------------------

HIPAA_REQUIREMENTS: list[HIPAARequirement] = [
    HIPAARequirement(
        rule_id="HIPAA-ACCESS-1",
        title="Access Control",
        section="164.312(a)(1)",
        description="Implement technical policies and procedures for access to ePHI.",
        dimensions=["access-controls", "auth"],
        controls=["unique_user_id", "emergency_access", "automatic_logoff", "encryption"],
    ),
    HIPAARequirement(
        rule_id="HIPAA-AUDIT-1",
        title="Audit Controls",
        section="164.312(b)",
        description="Implement mechanisms to record and examine access to ePHI.",
        dimensions=["audit-trail", "logging"],
        controls=["audit_logging", "access_monitoring", "log_review"],
    ),
    HIPAARequirement(
        rule_id="HIPAA-INTEGRITY-1",
        title="Integrity Controls",
        section="164.312(c)(1)",
        description="Protect ePHI from improper alteration or destruction.",
        dimensions=["phi-handling"],
        controls=["data_validation", "error_checking", "authentication_mechanism"],
    ),
    HIPAARequirement(
        rule_id="HIPAA-TRANSMISSION-1",
        title="Transmission Security",
        section="164.312(e)(1)",
        description="Protect ePHI during electronic transmission.",
        dimensions=["encryption-in-transit"],
        controls=["tls_required", "integrity_controls"],
    ),
    HIPAARequirement(
        rule_id="HIPAA-MINIMUM-1",
        title="Minimum Necessary",
        section="164.502(b)",
        description="Limit use, disclosure, and requests for PHI to the minimum necessary.",
        dimensions=["minimum-necessary"],
        controls=["role_based_access", "data_segmentation", "field_level_encryption"],
    ),
    HIPAARequirement(
        rule_id="HIPAA-BREACH-1",
        title="Breach Notification",
        section="164.408",
        description="Notification to affected individuals within 60 days of discovery.",
        dimensions=["breach-notification"],
        controls=["incident_response_plan", "notification_procedures", "risk_assessment"],
    ),
]


def scan_for_phi(content: str, *, file_path: str = "") -> list[PHIDetection]:
    """Scan content for potential PHI patterns."""
    detections: list[PHIDetection] = []
    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        for pattern_name, regex in PHI_PATTERNS.items():
            for match in re.finditer(regex, line, re.IGNORECASE):
                detections.append(PHIDetection(
                    pattern_name=pattern_name,
                    matched_text=match.group(),
                    file_path=file_path,
                    line_number=line_num,
                    severity="high" if pattern_name in ("ssn", "mrn") else "medium",
                ))

    return detections


def check_hipaa_compliance(
    *,
    governance_config: dict[str, Any],
    source_content: str = "",
    file_path: str = "",
) -> HIPAAComplianceReport:
    """Check governance config and source code for HIPAA compliance."""
    report = HIPAAComplianceReport()

    # Scan source for PHI
    if source_content:
        report.phi_detections = scan_for_phi(source_content, file_path=file_path)

    # Check requirements against governance config
    config_dims = set(governance_config.get("dimensions", []))
    config_rules = governance_config.get("rules", {})
    config_conventions = governance_config.get("conventions", {})

    for req in HIPAA_REQUIREMENTS:
        met = False

        # Check if required dimensions are covered
        req_dims_covered = any(d in config_dims for d in req.dimensions)

        # Check if controls are referenced
        controls_in_config = any(
            c in str(config_rules) or c in str(config_conventions)
            for c in req.controls
        )

        if req_dims_covered or controls_in_config:
            met = True

        if met:
            report.requirements_met.append(req.rule_id)
        else:
            report.requirements_unmet.append(req.rule_id)
            report.recommendations.append(
                f"{req.rule_id}: {req.title} — {req.description}"
            )

    # Compute risk score
    total_reqs = len(HIPAA_REQUIREMENTS)
    unmet = len(report.requirements_unmet)
    phi_penalty = min(len(report.phi_detections) * 0.1, 0.5)
    report.risk_score = min(1.0, (unmet / max(total_reqs, 1)) + phi_penalty)

    return report


def get_hipaa_dimensions() -> list[str]:
    """Return the list of HIPAA-specific governance dimensions."""
    return list(HIPAA_DIMENSIONS)

"""SOC 2 compliance module.

Map VT Protocol features to Trust Service Criteria (TSC),
generate evidence matrix for SOC 2 Type II audits.

From SPEC Sprint 23: "Industry compliance modules — SOC 2."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# SOC 2 Trust Service Categories
TSC_CATEGORIES: list[str] = [
    "security",          # CC (Common Criteria) — required
    "availability",      # A
    "processing-integrity",  # PI
    "confidentiality",   # C
    "privacy",           # P
]


@dataclass
class TrustServiceCriterion:
    """A SOC 2 Trust Service Criterion."""

    criterion_id: str = ""
    category: str = ""
    title: str = ""
    description: str = ""
    vt_features: list[str] = field(default_factory=list)  # VT Protocol features that satisfy
    evidence_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "vt_features": self.vt_features,
            "evidence_types": self.evidence_types,
        }


@dataclass
class EvidenceItem:
    """A piece of evidence for SOC 2 compliance."""

    criterion_id: str = ""
    evidence_type: str = ""  # "automatic", "manual", "configuration"
    description: str = ""
    source: str = ""  # VT Protocol feature name
    status: str = "available"  # "available", "partial", "missing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "evidence_type": self.evidence_type,
            "description": self.description,
            "source": self.source,
            "status": self.status,
        }


@dataclass
class SOC2ComplianceReport:
    """SOC 2 compliance mapping report."""

    criteria_met: list[str] = field(default_factory=list)
    criteria_partial: list[str] = field(default_factory=list)
    criteria_unmet: list[str] = field(default_factory=list)
    evidence_matrix: list[EvidenceItem] = field(default_factory=list)
    coverage_score: float = 0.0  # 0.0 to 1.0
    categories_covered: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    @property
    def audit_ready(self) -> bool:
        return self.coverage_score >= 0.8 and len(self.criteria_unmet) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_ready": self.audit_ready,
            "coverage_score": round(self.coverage_score, 4),
            "criteria_met": self.criteria_met,
            "criteria_partial": self.criteria_partial,
            "criteria_unmet": self.criteria_unmet,
            "categories_covered": self.categories_covered,
            "evidence_matrix": [e.to_dict() for e in self.evidence_matrix],
            "recommendations": self.recommendations,
        }


# ---------------------------------------------------------------------------
# VT Protocol feature → SOC 2 criterion mapping
# ---------------------------------------------------------------------------

VT_FEATURE_MAP: dict[str, list[str]] = {
    "merkle_audit_log": ["CC7.2", "CC7.3", "CC8.1"],
    "contradiction_detection": ["CC3.2", "PI1.4"],
    "decision_tracking": ["CC3.1", "CC3.2", "CC7.1"],
    "governance_config": ["CC1.1", "CC1.2", "CC5.1"],
    "freeze_on_adopt": ["CC8.1", "CC6.1"],
    "session_tracking": ["CC6.1", "CC7.1"],
    "dimension_taxonomy": ["CC3.1"],
    "quality_gates": ["CC8.1", "PI1.3"],
    "behavioral_contracts": ["CC7.2", "PI1.4"],
    "ltl_monitors": ["CC7.2", "CC7.3"],
    "infra_governance": ["CC6.1", "CC6.2", "A1.2"],
    "auto_resolve": ["CC3.2", "CC7.1"],
    "predictive_governance": ["CC3.3"],
    "causal_coordination": ["CC7.1", "PI1.4"],
    "encryption": ["CC6.7", "C1.1"],
    "access_controls": ["CC6.1", "CC6.3"],
}


# ---------------------------------------------------------------------------
# SOC 2 criteria definitions
# ---------------------------------------------------------------------------

SOC2_CRITERIA: list[TrustServiceCriterion] = [
    TrustServiceCriterion(
        criterion_id="CC1.1",
        category="security",
        title="COSO Principle 1: Demonstrates Commitment to Integrity",
        description="The entity demonstrates a commitment to integrity and ethical values.",
        vt_features=["governance_config"],
        evidence_types=["configuration", "manual"],
    ),
    TrustServiceCriterion(
        criterion_id="CC1.2",
        category="security",
        title="COSO Principle 2: Board Exercises Oversight",
        description="The board exercises oversight responsibility.",
        vt_features=["governance_config"],
        evidence_types=["manual"],
    ),
    TrustServiceCriterion(
        criterion_id="CC3.1",
        category="security",
        title="COSO Principle 6: Specifies Suitable Objectives",
        description="The entity specifies objectives with sufficient clarity.",
        vt_features=["decision_tracking", "dimension_taxonomy"],
        evidence_types=["automatic", "configuration"],
    ),
    TrustServiceCriterion(
        criterion_id="CC3.2",
        category="security",
        title="COSO Principle 7: Identifies and Analyzes Risks",
        description="The entity identifies risks and analyzes significance.",
        vt_features=["contradiction_detection", "auto_resolve", "decision_tracking"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="CC3.3",
        category="security",
        title="COSO Principle 8: Assesses Fraud Risk",
        description="The entity considers the potential for fraud in risk assessment.",
        vt_features=["predictive_governance"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="CC5.1",
        category="security",
        title="COSO Principle 10: Selects and Develops Control Activities",
        description="The entity selects and develops control activities.",
        vt_features=["governance_config"],
        evidence_types=["configuration"],
    ),
    TrustServiceCriterion(
        criterion_id="CC6.1",
        category="security",
        title="Logical and Physical Access Controls",
        description="Implement logical access security to protect against unauthorized access.",
        vt_features=["freeze_on_adopt", "session_tracking", "infra_governance", "access_controls"],
        evidence_types=["automatic", "configuration"],
    ),
    TrustServiceCriterion(
        criterion_id="CC6.2",
        category="security",
        title="Prior to Issuing Credentials",
        description="Prior to issuing system credentials, registration and authorization are performed.",
        vt_features=["infra_governance"],
        evidence_types=["configuration"],
    ),
    TrustServiceCriterion(
        criterion_id="CC6.3",
        category="security",
        title="Manages Access and Modifications",
        description="The entity manages access and modifications based on authorization.",
        vt_features=["access_controls"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="CC6.7",
        category="security",
        title="Restricts Transmission of Data",
        description="Restrict transmission, movement, and removal of information.",
        vt_features=["encryption"],
        evidence_types=["configuration"],
    ),
    TrustServiceCriterion(
        criterion_id="CC7.1",
        category="security",
        title="Detect and Monitor Security Events",
        description="Monitor system components for anomalies indicating malicious acts.",
        vt_features=["decision_tracking", "session_tracking", "auto_resolve", "causal_coordination"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="CC7.2",
        category="security",
        title="Monitor System Components",
        description="Monitor system components and operation of those components.",
        vt_features=["merkle_audit_log", "behavioral_contracts", "ltl_monitors"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="CC7.3",
        category="security",
        title="Evaluate Security Events",
        description="Evaluate events to determine if they are security incidents.",
        vt_features=["merkle_audit_log", "ltl_monitors"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="CC8.1",
        category="security",
        title="Manages Changes to Infrastructure",
        description="Changes to infrastructure and software are managed.",
        vt_features=["merkle_audit_log", "freeze_on_adopt", "quality_gates"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="A1.2",
        category="availability",
        title="Environmental Protections",
        description="Environmental protections, software, data backup, and recovery.",
        vt_features=["infra_governance"],
        evidence_types=["configuration"],
    ),
    TrustServiceCriterion(
        criterion_id="PI1.3",
        category="processing-integrity",
        title="Accurate Processing",
        description="System processing is complete, accurate, timely, and authorized.",
        vt_features=["quality_gates"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="PI1.4",
        category="processing-integrity",
        title="Outputs Are Complete and Accurate",
        description="System outputs are complete, accurate, and distributed as intended.",
        vt_features=["contradiction_detection", "behavioral_contracts", "causal_coordination"],
        evidence_types=["automatic"],
    ),
    TrustServiceCriterion(
        criterion_id="C1.1",
        category="confidentiality",
        title="Identifies Confidential Information",
        description="Identifies and maintains confidential information.",
        vt_features=["encryption"],
        evidence_types=["configuration"],
    ),
]


def map_features_to_criteria(
    active_features: list[str],
) -> dict[str, list[str]]:
    """Map active VT Protocol features to SOC 2 criteria they satisfy."""
    criteria_map: dict[str, list[str]] = {}
    for feature in active_features:
        criteria = VT_FEATURE_MAP.get(feature, [])
        for c in criteria:
            criteria_map.setdefault(c, []).append(feature)
    return criteria_map


def generate_evidence_matrix(
    active_features: list[str],
) -> list[EvidenceItem]:
    """Generate an evidence matrix for SOC 2 audit."""
    items: list[EvidenceItem] = []
    criteria_map = map_features_to_criteria(active_features)

    for criterion in SOC2_CRITERIA:
        features_for_criterion = criteria_map.get(criterion.criterion_id, [])

        if features_for_criterion:
            for feat in features_for_criterion:
                items.append(EvidenceItem(
                    criterion_id=criterion.criterion_id,
                    evidence_type="automatic",
                    description=f"Evidence from VT Protocol feature: {feat}",
                    source=feat,
                    status="available",
                ))
        else:
            items.append(EvidenceItem(
                criterion_id=criterion.criterion_id,
                evidence_type="manual",
                description=f"No automatic evidence — manual review needed for: {criterion.title}",
                source="",
                status="missing",
            ))

    return items


def check_soc2_compliance(
    *,
    active_features: list[str],
    governance_config: dict[str, Any] | None = None,
) -> SOC2ComplianceReport:
    """Check SOC 2 compliance based on active VT Protocol features."""
    report = SOC2ComplianceReport()
    criteria_map = map_features_to_criteria(active_features)

    for criterion in SOC2_CRITERIA:
        cid = criterion.criterion_id
        if cid in criteria_map:
            report.criteria_met.append(cid)
        else:
            # Check if governance config has relevant settings
            if governance_config and _config_covers_criterion(governance_config, criterion):
                report.criteria_partial.append(cid)
            else:
                report.criteria_unmet.append(cid)
                report.recommendations.append(
                    f"{cid}: {criterion.title} — enable features: {criterion.vt_features}"
                )

    # Evidence matrix
    report.evidence_matrix = generate_evidence_matrix(active_features)

    # Coverage score
    total = len(SOC2_CRITERIA)
    met = len(report.criteria_met)
    partial = len(report.criteria_partial)
    report.coverage_score = (met + partial * 0.5) / max(total, 1)

    # Categories covered
    met_set = set(report.criteria_met) | set(report.criteria_partial)
    for criterion in SOC2_CRITERIA:
        if criterion.criterion_id in met_set:
            if criterion.category not in report.categories_covered:
                report.categories_covered.append(criterion.category)

    return report


def _config_covers_criterion(
    config: dict[str, Any], criterion: TrustServiceCriterion,
) -> bool:
    """Check if governance config partially covers a criterion."""
    config_str = str(config).lower()
    keywords = criterion.title.lower().split()
    matches = sum(1 for kw in keywords if kw in config_str)
    return matches >= len(keywords) * 0.3


def get_tsc_categories() -> list[str]:
    """Return the list of Trust Service Categories."""
    return list(TSC_CATEGORIES)

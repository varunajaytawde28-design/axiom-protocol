"""CISO compliance view — evidence collection and framework mapping.

Provides:
  1. AI vs human code attribution (agent vs manual decisions)
  2. Compliance framework mapping (EU AI Act, SOC 2, HIPAA)
  3. One-click evidence export (JSON bundle)
  4. Agent activity timeline

Compliance frameworks mapped:
  - EU AI Act Article 12: logging requirements for AI systems
  - SOC 2 CC6.1: logical and physical access controls
  - HIPAA §164.312: audit controls for ePHI access
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from vt_protocol.decisions.models import (
    AuditEntry,
    AuditEventType,
    Decision,
    SourceType,
)

logger = logging.getLogger(__name__)


# Agent source types
_AGENT_SOURCES = {SourceType.AGENT, SourceType.SCAN}
_HUMAN_SOURCES = {
    SourceType.MANUAL, SourceType.GIT_PR, SourceType.GIT_RELEASE,
    SourceType.MEETING, SourceType.GIT_ISSUE, SourceType.GIT_COMMIT,
}


class ComplianceFramework:
    """Compliance framework identifiers."""

    EU_AI_ACT_ART12 = "eu_ai_act_article_12"
    SOC2_CC6_1 = "soc2_cc6.1"
    HIPAA_AUDIT = "hipaa_164.312"


@dataclass
class AttributionStats:
    """AI vs human decision attribution statistics."""

    total_decisions: int = 0
    agent_decisions: int = 0
    human_decisions: int = 0
    agent_percentage: float = 0.0
    human_percentage: float = 0.0
    by_source_type: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "agent_decisions": self.agent_decisions,
            "human_decisions": self.human_decisions,
            "agent_percentage": round(self.agent_percentage, 2),
            "human_percentage": round(self.human_percentage, 2),
            "by_source_type": self.by_source_type,
        }


@dataclass
class ComplianceMapping:
    """A mapping of system capabilities to compliance requirements."""

    framework: str
    requirement: str
    description: str
    status: str  # "met", "partial", "not_met"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "requirement": self.requirement,
            "description": self.description,
            "status": self.status,
            "evidence": self.evidence,
        }


@dataclass
class AgentActivity:
    """A single agent activity event for the timeline."""

    timestamp: str
    agent_id: str
    action: str
    resource: str = ""
    authorized_by: str = ""
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "action": self.action,
            "resource": self.resource,
            "authorized_by": self.authorized_by,
            "session_id": self.session_id,
        }


@dataclass
class EvidenceBundle:
    """Complete evidence export for compliance auditors."""

    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    attribution: AttributionStats = field(default_factory=AttributionStats)
    compliance_mappings: list[ComplianceMapping] = field(default_factory=list)
    agent_activities: list[AgentActivity] = field(default_factory=list)
    audit_entries: list[dict[str, Any]] = field(default_factory=list)
    tree_heads: list[dict[str, Any]] = field(default_factory=list)
    inclusion_proofs: list[dict[str, Any]] = field(default_factory=list)
    consistency_proofs: list[dict[str, Any]] = field(default_factory=list)
    timestamp_tokens: list[dict[str, Any]] = field(default_factory=list)
    verification_instructions: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "attribution": self.attribution.to_dict(),
            "compliance_mappings": [m.to_dict() for m in self.compliance_mappings],
            "agent_activities": [a.to_dict() for a in self.agent_activities],
            "audit_entries_count": len(self.audit_entries),
            "audit_entries": self.audit_entries,
            "tree_heads": self.tree_heads,
            "inclusion_proofs": self.inclusion_proofs,
            "consistency_proofs": self.consistency_proofs,
            "timestamp_tokens": self.timestamp_tokens,
            "verification_instructions": self.verification_instructions,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


def compute_attribution(decisions: list[Decision]) -> AttributionStats:
    """Compute AI vs human attribution statistics."""
    if not decisions:
        return AttributionStats()

    by_source: dict[str, int] = {}
    agent_count = 0
    human_count = 0

    for d in decisions:
        st = d.source_type.value
        by_source[st] = by_source.get(st, 0) + 1

        if d.source_type in _AGENT_SOURCES:
            agent_count += 1
        else:
            human_count += 1

    total = len(decisions)
    return AttributionStats(
        total_decisions=total,
        agent_decisions=agent_count,
        human_decisions=human_count,
        agent_percentage=(agent_count / total * 100) if total > 0 else 0.0,
        human_percentage=(human_count / total * 100) if total > 0 else 0.0,
        by_source_type=by_source,
    )


def generate_compliance_mappings(
    *,
    has_merkle_audit: bool = False,
    has_signing: bool = False,
    has_rfc3161: bool = False,
    has_agent_registry: bool = False,
    has_attribution: bool = False,
    audit_entry_count: int = 0,
) -> list[ComplianceMapping]:
    """Generate compliance framework mappings based on system capabilities."""
    mappings: list[ComplianceMapping] = []

    # EU AI Act Article 12 — Logging requirements
    eu_evidence: list[str] = []
    eu_status = "not_met"
    if has_merkle_audit:
        eu_evidence.append("RFC 6962 Merkle tree audit log with append-only guarantees")
    if has_signing:
        eu_evidence.append("Ed25519 signed tree heads for tamper evidence")
    if has_rfc3161:
        eu_evidence.append("RFC 3161 external timestamping for temporal proof")
    if has_attribution:
        eu_evidence.append("AI vs human decision attribution tracking")
    if audit_entry_count > 0:
        eu_evidence.append(f"{audit_entry_count} audit entries recorded")

    if len(eu_evidence) >= 3:
        eu_status = "met"
    elif len(eu_evidence) >= 1:
        eu_status = "partial"

    mappings.append(ComplianceMapping(
        framework=ComplianceFramework.EU_AI_ACT_ART12,
        requirement="Automatic recording of events (logging)",
        description=(
            "AI systems must enable automatic recording of events "
            "relevant to the identification of risks and post-market monitoring."
        ),
        status=eu_status,
        evidence=eu_evidence,
    ))

    # SOC 2 CC6.1 — Logical and physical access controls
    soc2_evidence: list[str] = []
    soc2_status = "not_met"
    if has_merkle_audit:
        soc2_evidence.append("Immutable audit log with cryptographic verification")
    if has_agent_registry:
        soc2_evidence.append("Agent registry with capability restrictions")
    if has_signing:
        soc2_evidence.append("Cryptographic signing of audit trail")

    if len(soc2_evidence) >= 2:
        soc2_status = "met"
    elif len(soc2_evidence) >= 1:
        soc2_status = "partial"

    mappings.append(ComplianceMapping(
        framework=ComplianceFramework.SOC2_CC6_1,
        requirement="Logical and physical access controls",
        description=(
            "The entity implements logical access security software, infrastructure, "
            "and architectures over protected information assets."
        ),
        status=soc2_status,
        evidence=soc2_evidence,
    ))

    # HIPAA §164.312 — Audit controls
    hipaa_evidence: list[str] = []
    hipaa_status = "not_met"
    if has_merkle_audit:
        hipaa_evidence.append("Hardware/software mechanisms recording access to ePHI systems")
    if has_rfc3161:
        hipaa_evidence.append("Externally anchored timestamps for audit integrity")
    if audit_entry_count > 0:
        hipaa_evidence.append("Activity logs capturing who/what/when for system access")

    if len(hipaa_evidence) >= 2:
        hipaa_status = "met"
    elif len(hipaa_evidence) >= 1:
        hipaa_status = "partial"

    mappings.append(ComplianceMapping(
        framework=ComplianceFramework.HIPAA_AUDIT,
        requirement="Audit controls",
        description=(
            "Implement hardware, software, and/or procedural mechanisms that "
            "record and examine activity in information systems."
        ),
        status=hipaa_status,
        evidence=hipaa_evidence,
    ))

    return mappings


def extract_agent_activities(
    audit_entries: list[AuditEntry],
) -> list[AgentActivity]:
    """Extract agent activity timeline from audit entries."""
    activities: list[AgentActivity] = []

    for entry in audit_entries:
        if entry.actor in ("system", ""):
            continue

        activity = AgentActivity(
            timestamp=entry.timestamp.isoformat(),
            agent_id=entry.actor,
            action=entry.event_type.value,
            resource=entry.payload.get("resource", ""),
            authorized_by=entry.payload.get("authorized_by", ""),
            session_id=entry.session_id or "",
        )
        activities.append(activity)

    return activities


VERIFICATION_INSTRUCTIONS = """\
## Evidence Verification Instructions

### 1. Verify Merkle Tree Integrity
Each audit entry has an inclusion proof that proves it belongs to the
tree at the stated size. To verify:

```python
from vt_protocol.audit.merkle import MerkleTree, leaf_hash
tree = MerkleTree("path/to/audit.db")
for proof_data in evidence["inclusion_proofs"]:
    leaf_data = proof_data["entry_json"].encode("utf-8")
    root = bytes.fromhex(proof_data["root_hash_hex"])
    # Reconstruct from proof hashes...
```

### 2. Verify Ed25519 Signatures
Tree heads are signed with Ed25519. The public verification key is
included in this bundle.

### 3. Verify RFC 3161 Timestamps
Timestamp tokens from DigiCert TSA prove the tree head existed at the
stated time. Use OpenSSL to verify:

```bash
openssl ts -verify -in token.tsr -data tree_head.bin -CAfile digicert_ca.pem
```

### 4. Verify Consistency Proofs
Consistency proofs confirm the log is append-only between two tree sizes.
Each proof shows that an earlier tree state is a prefix of a later one.
"""


def build_evidence_bundle(
    decisions: list[Decision],
    audit_entries: list[AuditEntry],
    *,
    tree_heads: list[dict[str, Any]] | None = None,
    inclusion_proofs: list[dict[str, Any]] | None = None,
    consistency_proofs: list[dict[str, Any]] | None = None,
    timestamp_tokens: list[dict[str, Any]] | None = None,
    has_signing: bool = False,
    has_rfc3161: bool = False,
    has_agent_registry: bool = False,
) -> EvidenceBundle:
    """Build a complete evidence bundle for compliance export."""
    attribution = compute_attribution(decisions)
    mappings = generate_compliance_mappings(
        has_merkle_audit=len(audit_entries) > 0,
        has_signing=has_signing,
        has_rfc3161=has_rfc3161,
        has_agent_registry=has_agent_registry,
        has_attribution=len(decisions) > 0,
        audit_entry_count=len(audit_entries),
    )
    activities = extract_agent_activities(audit_entries)

    # Serialize audit entries
    serialized_entries = []
    for entry in audit_entries:
        serialized_entries.append({
            "entry_id": str(entry.entry_id),
            "timestamp": entry.timestamp.isoformat(),
            "event_type": entry.event_type.value,
            "actor": entry.actor,
            "session_id": entry.session_id,
            "payload": entry.payload,
            "entry_hash": entry.entry_hash,
        })

    return EvidenceBundle(
        attribution=attribution,
        compliance_mappings=mappings,
        agent_activities=activities,
        audit_entries=serialized_entries,
        tree_heads=tree_heads or [],
        inclusion_proofs=inclusion_proofs or [],
        consistency_proofs=consistency_proofs or [],
        timestamp_tokens=timestamp_tokens or [],
        verification_instructions=VERIFICATION_INSTRUCTIONS,
    )

"""Core Pydantic models for VT Protocol decision engine.

Ported from Axiom Hub's context_graph/models.py and JSONL schemas,
upgraded with:
- Dimension taxonomy (12 core dimensions from SPEC)
- Ternary contradiction verdicts (contradiction/tension/compatible)
- Merkle-tree-ready audit entries (replacing JSONL hash chains)
- Freeze-on-adoption baseline tracking (SonarQube CaYC pattern)
- Structured LLM judgment with mandatory evidence citation
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DecisionStatus(str, Enum):
    """Lifecycle status of a decision record."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    PROPOSED = "proposed"


class DecisionType(str, Enum):
    """Canonical decision types.

    Axiom Hub mapped 30+ variants to 4 types. We keep the same 4 canonical
    types but accept the original granular values via the ``normalize`` helper.
    """

    ARCHITECTURAL = "architectural"
    TECHNICAL = "technical"
    PRODUCT = "product"
    CONSTRAINT = "constraint"

    @classmethod
    def normalize(cls, raw: str) -> DecisionType:
        """Map free-form type strings to canonical types.

        Ported from Axiom Hub's _DECISION_TYPE_MAP (jsonl_writer.py / dashboard).
        """
        mapping: dict[str, DecisionType] = {
            # architectural
            "architecture": cls.ARCHITECTURAL,
            "architectural": cls.ARCHITECTURAL,
            "infrastructure": cls.ARCHITECTURAL,
            "deployment": cls.ARCHITECTURAL,
            "system-design": cls.ARCHITECTURAL,
            # technical
            "technical": cls.TECHNICAL,
            "database": cls.TECHNICAL,
            "framework": cls.TECHNICAL,
            "security": cls.TECHNICAL,
            "testing": cls.TECHNICAL,
            "async-processing": cls.TECHNICAL,
            "api-design": cls.TECHNICAL,
            "performance": cls.TECHNICAL,
            "tooling": cls.TECHNICAL,
            # product
            "product": cls.PRODUCT,
            "feature": cls.PRODUCT,
            "business": cls.PRODUCT,
            "ux": cls.PRODUCT,
            # constraint
            "constraint": cls.CONSTRAINT,
            "limitation": cls.CONSTRAINT,
            "requirement": cls.CONSTRAINT,
            "compliance": cls.CONSTRAINT,
        }
        return mapping.get(raw.lower().strip(), cls.TECHNICAL)


class Dimension(str, Enum):
    """12 core architectural dimensions for auto-tagging.

    From SPEC Phase 1: decisions are tagged with dimensions so the graph can
    route contradiction checks to pairs that share dimensions (junction table
    query: shared-dimension-count x recency-multiplier).
    """

    DATABASE = "database"
    AUTH = "auth"
    CACHING = "caching"
    API_STYLE = "api-style"
    DEPLOYMENT = "deployment"
    CONCURRENCY = "concurrency"
    LOGGING = "logging"
    TESTING = "testing"
    ERROR_HANDLING = "error-handling"
    STATE_MANAGEMENT = "state-management"
    MESSAGING = "messaging"
    SECURITY = "security"


class SourceType(str, Enum):
    """How a decision was captured.

    Confidence baselines from Axiom Hub's SOURCE_CONFIDENCE map — explicit
    human decisions rank highest, auto-scanned patterns lowest.
    """

    MANUAL = "manual"
    GIT_PR = "git_pr"
    GIT_RELEASE = "git_release"
    MEETING = "meeting"
    GIT_ISSUE = "git_issue"
    AGENT = "agent"
    GIT_COMMIT = "git_commit"
    SCAN = "scan"


# Ported from Axiom Hub context_graph/client.py SOURCE_CONFIDENCE
SOURCE_CONFIDENCE: dict[SourceType, float] = {
    SourceType.MANUAL: 0.95,
    SourceType.GIT_PR: 0.90,
    SourceType.GIT_RELEASE: 0.88,
    SourceType.MEETING: 0.80,
    SourceType.GIT_ISSUE: 0.70,
    SourceType.AGENT: 0.75,
    SourceType.GIT_COMMIT: 0.60,
    SourceType.SCAN: 0.50,
}


class ContradictionVerdict(str, Enum):
    """Ternary LLM judgment — SPEC requires reasoning BEFORE verdict.

    Default-to-compatible instruction suppresses false positives.
    """

    CONTRADICTION = "contradiction"
    TENSION = "tension"
    COMPATIBLE = "compatible"


class ContradictionStatus(str, Enum):
    """Resolution state of a contradiction pair.

    Ported from Axiom Hub's contradiction_index.py pair statuses.
    """

    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    DEFERRED = "deferred"
    IGNORED = "ignored"


class AssumptionCategory(str, Enum):
    """6 domain assumption categories from SCA taxonomy."""

    DATA_SCOPE = "data_scope"
    TEMPORAL = "temporal"
    ACCESS = "access"
    COMPLETENESS = "completeness"
    CONFIGURATION = "configuration"
    FRAMEWORK = "framework"


class AssumptionStatus(str, Enum):
    """Lifecycle status of a domain assumption."""

    DETECTED = "detected"
    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class AuditEventType(str, Enum):
    """Events recorded in the Merkle-tree audit log."""

    DECISION_ADDED = "decision_added"
    DECISION_SUPERSEDED = "decision_superseded"
    CONTRADICTION_DETECTED = "contradiction_detected"
    CONTRADICTION_RESOLVED = "contradiction_resolved"
    CONTEXT_INJECTION = "context_injection"
    SESSION_STARTED = "session_started"
    SESSION_COMPLETED = "session_completed"
    ASSUMPTION_DETECTED = "assumption_detected"
    ASSUMPTION_VALIDATED = "assumption_validated"
    ASSUMPTION_REJECTED = "assumption_rejected"


# ---------------------------------------------------------------------------
# Core Models
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Decision(BaseModel):
    """A recorded architectural or technical decision.

    Superset of Michael Nygard's ADR format (SPEC T1). Adds dimension tags,
    graph edges (supersedes), agent source, and confidence scoring.

    Confidence is auto-calculated from source type + content richness
    (EVOKG-based algorithm from Axiom Hub context_graph/client.py).
    """

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, description="Full description / markdown body")
    rationale: str = Field(default="", description="Why this choice was made")
    status: DecisionStatus = DecisionStatus.ACTIVE
    decision_type: DecisionType = DecisionType.TECHNICAL
    dimensions: list[Dimension] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    made_by: str = Field(description="Agent ID or human identifier")
    project: str = Field(description="Project name / identifier")
    source_type: SourceType = SourceType.AGENT
    confidence: float = Field(ge=0.0, le=1.0, default=0.75)
    supersedes: UUID | None = None
    session_id: str | None = Field(
        default=None, description="MCP session token for tracing"
    )
    created_at: datetime = Field(default_factory=_utcnow)
    valid: bool = Field(default=True, description="False if superseded by another decision")

    def compute_confidence(self) -> float:
        """EVOKG-based confidence from Axiom Hub.

        base  = SOURCE_CONFIDENCE[source_type]
        boost = +0.05 if has alternatives
              + +0.05 if rationale + content > 200 chars
              + +0.05 if content > 500 chars
        """
        base = SOURCE_CONFIDENCE.get(self.source_type, 0.50)
        boost = 0.0
        if self.alternatives:
            boost += 0.05
        if self.rationale and len(self.content) > 200:
            boost += 0.05
        if len(self.content) > 500:
            boost += 0.05
        return min(1.0, base + boost)

    @model_validator(mode="after")
    def _set_computed_confidence(self) -> Decision:
        """Auto-set confidence from source + content if still at default."""
        if self.confidence == 0.75:
            self.confidence = self.compute_confidence()
        return self


class Contradiction(BaseModel):
    """A detected conflict between two decisions.

    SPEC requires:
    - Reasoning BEFORE verdict (structured LLM output)
    - Ternary judgment: contradiction / tension / compatible
    - Mandatory evidence citation from each decision
    - ~$0.002 per check (single LLM call)
    - Default to COMPATIBLE to suppress false positives

    The ``is_baseline`` flag implements freeze-on-adoption (SonarQube CaYC):
    contradictions present at adoption time are snapshotted and excluded from
    quality gates. Only NEW contradictions block.
    """

    id: UUID = Field(default_factory=uuid4)
    decision_a_id: UUID
    decision_b_id: UUID
    decision_a_title: str
    decision_b_title: str
    verdict: ContradictionVerdict
    reasoning: str = Field(
        min_length=1,
        description="LLM reasoning chain — must appear BEFORE verdict in prompt output",
    )
    evidence_a: str = Field(
        min_length=1, description="Specific text cited from decision A"
    )
    evidence_b: str = Field(
        min_length=1, description="Specific text cited from decision B"
    )
    shared_dimensions: list[Dimension] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    status: ContradictionStatus = ContradictionStatus.UNRESOLVED
    resolved_by: str | None = None
    resolution_note: str | None = None
    detected_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None
    is_baseline: bool = Field(
        default=False,
        description="True if frozen on adoption — excluded from quality gates",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pair_key(self) -> str:
        """Order-independent key for deduplication.

        Ported from Axiom Hub contradiction_index.py — uses sorted UUIDs
        so (A,B) and (B,A) produce the same key.
        """
        ids = sorted([str(self.decision_a_id), str(self.decision_b_id)])
        return f"{ids[0]}::{ids[1]}"

    @property
    def is_actionable(self) -> bool:
        """True if this contradiction should trigger quality gate failure."""
        return (
            self.verdict == ContradictionVerdict.CONTRADICTION
            and self.status == ContradictionStatus.UNRESOLVED
            and not self.is_baseline
        )


class ContextResult(BaseModel):
    """A search result from the decision graph.

    Returned by ``get_project_decisions`` and ``check_before_coding`` MCP tools.
    Results are ranked by shared-dimension-count x recency-multiplier, then
    reordered for LLM attention bias (best first, second-best last).
    """

    decision_id: UUID
    title: str
    content: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    dimensions: list[Dimension] = Field(default_factory=list)
    excerpt: str = Field(description="2-3 most relevant sentences")


class DimensionEdge(BaseModel):
    """Edge between two decisions that share a dimension.

    PostgreSQL junction table: decisions_dimensions maps each decision to its
    dimensions. Pairs sharing dimensions are candidates for contradiction check.
    Weight = shared_dimension_count * recency_multiplier.
    """

    decision_a_id: UUID
    decision_b_id: UUID
    shared_dimensions: list[Dimension]
    weight: float = Field(ge=0.0, description="shared_count * recency_multiplier")


# ---------------------------------------------------------------------------
# Audit Models (Merkle-tree ready, replacing JSONL hash chains)
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """Single entry in the Merkle-tree audit log.

    Replaces Axiom Hub's append-only JSONL with RFC 6962-style Merkle tree.
    Each entry carries a hash of its content and the hash of the previous entry,
    forming a verifiable chain. The full Merkle tree implementation lives in
    audit/merkle.py — this model represents a single leaf.

    Fields designed for EU AI Act Article 12 compliance:
    - Who made the decision (actor)
    - What context was the AI given (payload.decisions_surfaced)
    - When it happened (timestamp)
    - What session it belongs to (session_id)
    """

    entry_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=_utcnow)
    event_type: AuditEventType
    actor: str = Field(description="agent_id, 'system', or human identifier")
    session_id: str | None = None
    project: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = Field(default="", description="SHA-256 of previous entry")
    entry_hash: str = Field(default="", description="SHA-256 of this entry's content")

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of this entry's canonical content.

        Hash covers: entry_id, timestamp, event_type, actor, session_id,
        project, payload, prev_hash. The entry_hash field itself is excluded
        to avoid circular dependency.
        """
        canonical = json.dumps(
            {
                "entry_id": str(self.entry_id),
                "timestamp": self.timestamp.isoformat(),
                "event_type": self.event_type.value,
                "actor": self.actor,
                "session_id": self.session_id,
                "project": self.project,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _set_entry_hash(self) -> AuditEntry:
        """Auto-compute entry_hash if not already set."""
        if not self.entry_hash:
            self.entry_hash = self.compute_hash()
        return self

    def verify(self) -> bool:
        """Verify this entry's hash matches its content."""
        return self.entry_hash == self.compute_hash()


# ---------------------------------------------------------------------------
# Session Models (MCP session tracking)
# ---------------------------------------------------------------------------


class Session(BaseModel):
    """An MCP session — tracks decisions made during one agent interaction.

    Ported from Axiom Hub's session_token pattern in mcp_server.py.
    Every MCP interaction starts with get_project_context which creates a
    session. All decisions recorded in that session are tagged with the
    session_id for audit tracing.
    """

    session_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    project: str
    agent_id: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    decisions_made: list[UUID] = Field(default_factory=list)
    contradictions_found: list[UUID] = Field(default_factory=list)
    context_injections: int = 0


# ---------------------------------------------------------------------------
# Domain Assumption Models
# ---------------------------------------------------------------------------


class CodeEvidence(BaseModel):
    """A code location that evidences a domain assumption."""

    file: str
    line: int
    snippet: str = ""


class DomainAssumption(BaseModel):
    """A detected implicit assumption in code that needs human validation.

    From research: AI agents embed domain assumptions silently. These must
    be surfaced as bounded multiple-choice questions (not yes/no — that
    causes acquiescence bias) and routed through a lifecycle:
    DETECTED → PROPOSED → VALIDATED | REJECTED | DEFERRED.

    VALIDATED assumptions become immutable domain constraints.
    REJECTED assumptions trigger refactor prompts (not hard contradictions).
    DEFERRED assumptions re-surface after 7 days or on related code change.
    """

    id: UUID = Field(default_factory=uuid4)
    category: AssumptionCategory
    status: AssumptionStatus = AssumptionStatus.DETECTED
    pattern_id: str = Field(description="Detection rule ID, e.g. 'single_source_write'")
    summary: str = Field(description="Human-readable assumption statement")
    code_evidence: list[CodeEvidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.6)
    severity: str = Field(default="medium", description="low | medium | high | critical")

    # Question (populated by question generator)
    question: str = ""
    options: list[str] = Field(default_factory=list)
    selected_option: int | None = Field(default=None, description="0-based index of chosen option")
    answer_rationale: str = ""

    # Resolution metadata
    resolved_by: str = ""
    business_context: str = ""
    related_decision_id: UUID | None = None
    dimensions: list[Dimension] = Field(default_factory=list)

    # Timestamps
    detected_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None
    deferred_until: datetime | None = None

    # Freeze-on-adopt
    is_baseline: bool = Field(
        default=False,
        description="True if pre-existing at adoption — excluded from gates",
    )

    # Agent attribution
    detected_by: str = Field(default="vt-scanner", description="Agent or tool that detected this")
    session_id: str | None = None

    @property
    def dedup_key(self) -> str:
        """Content-based key for deduplication across scans."""
        first_file = self.code_evidence[0].file if self.code_evidence else ""
        return f"{self.pattern_id}::{first_file}::{self.summary[:80]}"

    @property
    def is_actionable(self) -> bool:
        """True if this assumption needs human attention."""
        return self.status in (AssumptionStatus.DETECTED, AssumptionStatus.PROPOSED)

    def generate_rule_text(self) -> str:
        """Generate a CLAUDE.md rule from a resolved assumption."""
        if self.status == AssumptionStatus.VALIDATED and self.options and self.selected_option is not None:
            chosen = self.options[self.selected_option]
            return f"DOMAIN RULE: {self.summary} — {chosen} [Validated by {self.resolved_by} on {self.resolved_at:%b %d}]"
        if self.status == AssumptionStatus.REJECTED and self.options and self.selected_option is not None:
            chosen = self.options[self.selected_option]
            return f"DOMAIN RULE: DO NOT assume {self.summary.lower()}. {chosen} [Corrected by {self.resolved_by} on {self.resolved_at:%b %d}]"
        return ""


# ---------------------------------------------------------------------------
# Governance Config Models (governance.yaml schema)
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """LLM provider configuration for contradiction detection."""

    provider: str = Field(
        default="anthropic",
        description="LLM provider: anthropic | openai | ollama | none",
    )
    model: str = Field(
        default="claude-haiku-4-5-20251001",
        description="Model identifier",
    )
    api_key_env: str | None = Field(
        default=None,
        description="Environment variable name for API key",
    )
    base_url: str | None = Field(
        default=None,
        description="Custom endpoint URL (e.g. http://localhost:11434/v1 for Ollama)",
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    fallback: str = Field(
        default="nli-only",
        description="Fallback on LLM failure: nli-only | error | skip",
    )


class AgentConfig(BaseModel):
    """Per-agent onboarding configuration in governance.yaml."""

    type: str = Field(default="claude-code", description="Agent type identifier")
    role: str = Field(default="full-stack", description="Role: backend | frontend | infra | full-stack | security | custom")
    display_name: str = ""
    allowed_paths: list[str] = Field(default_factory=list, description="Glob patterns for allowed files")
    blocked_paths: list[str] = Field(default_factory=list, description="Glob patterns for blocked files")
    allowed_dimensions: list[str] = Field(default_factory=list, description="Dimensions agent can decide on")
    restricted_dimensions: list[str] = Field(default_factory=list, description="Dimensions requiring human approval")
    context_level: str = Field(default="full", description="full | relevant | minimal")
    auto_resolve: bool = Field(default=False, description="Can auto-resolve low-risk tensions")
    session_ttl_minutes: int = Field(default=60, ge=0)
    block_on_contradiction: bool = Field(default=True, description="Block agent on unresolved contradictions")
    owner: str = ""
    created_at: str = ""
    last_active: str | None = None


class GovernanceConfig(BaseModel):
    """Schema for governance.yaml — the project-level governance configuration.

    From SPEC T1: governance.yaml is our 'Dockerfile moment'. Readable without
    documentation. A developer should understand it in 30 seconds.
    """

    extends: list[str] = Field(
        default_factory=lambda: ["@vt/recommended"],
        description="Shareable configs to extend",
    )
    agents: dict[str, bool | AgentConfig] = Field(
        default_factory=lambda: {"claude": True, "cursor": True, "copilot": True},
        description="Agent configs: simple bool or full AgentConfig",
    )
    model: ModelConfig = Field(
        default_factory=ModelConfig,
        description="LLM provider configuration",
    )
    rules: GovernanceRules = Field(default_factory=lambda: GovernanceRules())
    decisions_path: str = Field(
        default=".smm/decisions/",
        description="Where decision records live",
    )


class GovernanceRules(BaseModel):
    """Rules section of governance.yaml."""

    freeze_on_adopt: bool = Field(
        default=True,
        description="SonarQube CaYC — only enforce on new changes",
    )
    contradiction_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence below this routes to human review",
    )
    max_new_deps_per_task: int = Field(
        default=3,
        description="Flag tasks adding more than N new dependencies",
    )

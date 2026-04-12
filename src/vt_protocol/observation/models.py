"""Observation engine data models.

Ported from Lattice's store.py schema and graph.py span structures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Span(BaseModel):
    """A single observed LLM call.

    Ported from Lattice patch.py _build_span(). Captures model, provider,
    I/O, tokens, latency, and optional vector clock for causal ordering.
    """

    span_id: str = Field(description="16 hex chars")
    trace_id: str = Field(description="16 hex chars")
    agent_id: str | None = None
    agent_role: str | None = None
    model: str
    provider: str = Field(description="'anthropic' or 'openai'")
    input_messages: str = Field(description="JSON-serialized messages")
    output: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    latency_ms: float = 0.0
    status: str = "success"
    framework: str | None = None
    timestamp: float = Field(description="Unix timestamp")
    vclock: dict[str, int] | None = Field(
        default=None, description="Vector clock for causal ordering"
    )
    tainted_source: str | None = Field(
        default=None, description="span_id of upstream LLM output detected in input"
    )


class CausalEdge(BaseModel):
    """A causal link between two spans.

    Ported from Lattice graph.py detect_causal_links().
    Edge types reflect the detection layer that found the link:
    - taint: TaintedStr metadata (Layer 0, 1.0 confidence)
    - taint_tracked: exact substring match (Layer 1, 1.0 confidence)
    - minhash: near-copy Jaccard > 0.3 (Layer 2, variable)
    - semantic: cosine similarity > 0.80 (Layer 3, variable)
    """

    source_span_id: str
    target_span_id: str
    edge_type: str = Field(description="taint | taint_tracked | minhash | semantic")
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    detected_at: datetime = Field(default_factory=_utcnow)


class AgentInfo(BaseModel):
    """Metadata about an observed AI agent.

    Ported from Lattice store.py agents table (unpopulated in Lattice,
    populated here via system prompt parsing).
    """

    agent_id: str
    role: str | None = None
    framework: str | None = None
    model_provider: str | None = None
    first_seen: datetime = Field(default_factory=_utcnow)
    last_seen: datetime = Field(default_factory=_utcnow)
    total_calls: int = 0
    total_cost_usd: float = 0.0


class GraphSummary(BaseModel):
    """Aggregated summary of observation data.

    Ported from Lattice server.py _build_graph_payload() summary field.
    """

    total_agents: int = 0
    total_spans: int = 0
    total_cost_usd: float = 0.0
    total_time_ms: float = 0.0
    total_edges: int = 0


class GraphPayload(BaseModel):
    """Full graph data for dashboard rendering."""

    spans: list[Span] = Field(default_factory=list)
    edges: list[CausalEdge] = Field(default_factory=list)
    summary: GraphSummary = Field(default_factory=GraphSummary)


class ActivityEntry(BaseModel):
    """A single entry in the unified activity timeline.

    Common data model produced by all observers — MCP tool calls,
    file reads/writes, shell commands, git operations, LLM calls.
    """

    entry_id: str = Field(description="16 hex chars")
    timestamp: float = Field(description="Unix timestamp")
    agent_id: str = ""
    session_id: str = ""
    action_type: str = Field(
        description="llm_call | mcp_tool | file_read | file_write | shell_command | git_operation"
    )
    tool_name: str = ""
    summary: str = ""
    severity: str = Field(default="info", description="info | warning | critical")
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0

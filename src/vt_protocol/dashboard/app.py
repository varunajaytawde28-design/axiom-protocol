"""Dashboard backend — FastAPI REST API + WebSocket for live updates.

Endpoints:
  GET  /api/decisions          — list decisions (filter by dimension, status)
  GET  /api/decisions/{id}     — single decision with full context
  GET  /api/contradictions     — list unresolved contradictions
  POST /api/contradictions/{id}/resolve — resolve with winner + rationale
  GET  /api/graph              — decisions + edges (Cytoscape.js format)
  GET  /api/audit              — audit trail with Merkle proof verification
  GET  /api/health             — coherence score, counts, status
  GET  /api/sessions           — recent agent sessions
  WS   /ws                     — live updates for decisions/contradictions

Serves static frontend from ./static/ with no build step.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vt_protocol.audit.merkle import MerkleTree
from vt_protocol.config import load_governance_config
from vt_protocol.decisions.models import (
    AgentConfig,
    AuditEntry,
    AuditEventType,
    Contradiction,
    ContradictionStatus,
    ContradictionVerdict,
    Decision,
    DecisionStatus,
    DecisionType,
    Dimension,
    SourceType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="VT Protocol Dashboard",
    version="0.1.0",
    description="Architecture governance dashboard",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# In-memory state (loaded from .smm/ on startup, no PostgreSQL required)
# ---------------------------------------------------------------------------

_state: DashboardState | None = None


class DashboardState:
    """Holds loaded decisions, contradictions, and audit tree."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or Path.cwd()
        self.decisions: list[Decision] = []
        self.contradictions: list[Contradiction] = []
        self.sessions: list[dict[str, Any]] = []
        self._merkle: MerkleTree | None = None
        self._ws_clients: list[WebSocket] = []
        # Observation state
        self._signals: list[Any] = []
        self._spans: list[Any] = []
        self._edges: list[Any] = []
        self._trajectory_alerts: list[dict[str, Any]] = []
        self._snapshot_diff: Any = None
        # Unified activity timeline (merged from all observers)
        self._activity_timeline: list[dict[str, Any]] = []
        self._mcp_calls: list[dict[str, Any]] = []
        self._shell_executions: list[dict[str, Any]] = []
        self._git_operations: list[dict[str, Any]] = []
        self._file_reads: list[dict[str, Any]] = []
        # Domain assumptions
        self._assumptions: list[Any] = []
        # Trace events from hooks and CLI
        self._trace_events: list[dict[str, Any]] = []

    @property
    def merkle(self) -> MerkleTree | None:
        if self._merkle is None:
            audit_db = self.project_root / ".smm" / "audit" / "audit.db"
            if audit_db.exists():
                self._merkle = MerkleTree(audit_db, check_same_thread=False)
        return self._merkle

    def load(self) -> None:
        """Load decisions and contradictions from .smm/ directory."""
        self.decisions = _load_decisions(self.project_root)
        self.contradictions = _load_contradictions(self.project_root)
        self.sessions = _load_sessions(self.project_root)
        self._load_observation()
        self._load_assumptions()
        self._load_trace_events()

    def _load_assumptions(self) -> None:
        """Load domain assumptions from .smm/assumptions/."""
        from vt_protocol.analysis.assumption_pipeline import load_assumptions

        try:
            self._assumptions = load_assumptions(self.project_root)
        except Exception:
            logger.debug("Failed to load assumptions", exc_info=True)
            self._assumptions = []

    def _load_trace_events(self) -> None:
        """Load trace events from .smm/traces/events.jsonl.

        Also triggers a one-shot sync from Claude Code session logs
        to pick up LLM call events.
        """
        # Sync from Claude Code session logs
        try:
            from vt_protocol.observation.session_logs import sync_session_to_traces
            sync_session_to_traces(self.project_root)
        except Exception:
            logger.debug("Session log sync failed", exc_info=True)

        events_path = self.project_root / ".smm" / "traces" / "events.jsonl"
        if not events_path.exists():
            self._trace_events = []
            return
        events: list[dict[str, Any]] = []
        try:
            for line in events_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception:
            logger.debug("Failed to load trace events", exc_info=True)
        # Sort newest first
        events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        self._trace_events = events

    def _load_observation(self) -> None:
        """Load observation data from .smm/cache and .smm/observation."""
        from vt_protocol.observation.cache import diff_snapshots, load_snapshot
        from vt_protocol.observation.signals import (
            detect_dependency_changes,
            detect_file_changes,
        )

        # Load snapshots and compute signals
        cache_dir = self.project_root / ".smm" / "cache"
        before_path = cache_dir / "snapshot_before.json"
        after_path = cache_dir / "snapshot_after.json"

        if before_path.exists() and after_path.exists():
            try:
                before = load_snapshot(before_path)
                after = load_snapshot(after_path)
                self._snapshot_diff = diff_snapshots(before, after)
                self._signals = detect_file_changes(before, after)
                self._signals += detect_dependency_changes(self._snapshot_diff)
            except Exception:
                logger.debug("Failed to load observation snapshots", exc_info=True)

        # Load persisted spans
        obs_dir = self.project_root / ".smm" / "observation"
        spans_path = obs_dir / "spans.json"
        if spans_path.exists():
            try:
                from vt_protocol.observation.models import Span

                data = json.loads(spans_path.read_text())
                self._spans = [Span(**s) for s in data]
            except Exception:
                logger.debug("Failed to load observation spans", exc_info=True)

        # Load persisted causal edges
        edges_path = obs_dir / "edges.json"
        if edges_path.exists():
            try:
                from vt_protocol.observation.models import CausalEdge

                data = json.loads(edges_path.read_text())
                self._edges = [CausalEdge(**e) for e in data]
            except Exception:
                logger.debug("Failed to load observation edges", exc_info=True)

        # Load trajectory alerts
        trajectory_path = obs_dir / "trajectory.json"
        if trajectory_path.exists():
            try:
                self._trajectory_alerts = json.loads(trajectory_path.read_text())
            except Exception:
                logger.debug("Failed to load trajectory data", exc_info=True)

        # Load MCP tool calls
        mcp_path = obs_dir / "mcp_calls.json"
        if mcp_path.exists():
            try:
                self._mcp_calls = json.loads(mcp_path.read_text())
            except Exception:
                logger.debug("Failed to load MCP calls", exc_info=True)

        # Load shell executions
        shell_path = obs_dir / "shell_executions.json"
        if shell_path.exists():
            try:
                self._shell_executions = json.loads(shell_path.read_text())
            except Exception:
                logger.debug("Failed to load shell executions", exc_info=True)

        # Load git operations
        git_path = obs_dir / "git_operations.json"
        if git_path.exists():
            try:
                self._git_operations = json.loads(git_path.read_text())
            except Exception:
                logger.debug("Failed to load git operations", exc_info=True)

        # Load file reads
        reads_path = obs_dir / "file_reads.json"
        if reads_path.exists():
            try:
                self._file_reads = json.loads(reads_path.read_text())
            except Exception:
                logger.debug("Failed to load file reads", exc_info=True)

        # Build unified activity timeline
        self._build_activity_timeline()

    def _build_activity_timeline(self) -> None:
        """Merge all observation sources into a single sorted timeline."""
        timeline: list[dict[str, Any]] = []

        # LLM call spans → activity entries
        for s in self._spans:
            timeline.append({
                "entry_id": s.span_id,
                "timestamp": s.timestamp,
                "agent_id": s.agent_id or "",
                "session_id": "",
                "action_type": "llm_call",
                "tool_name": s.model,
                "summary": f"LLM call: {s.model} ({s.provider}) — {s.tokens_in}+{s.tokens_out} tokens",
                "severity": "info",
                "details": {
                    "model": s.model,
                    "provider": s.provider,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "cost_usd": s.cost_usd,
                },
                "duration_ms": s.latency_ms,
            })

        # MCP calls
        timeline.extend(self._mcp_calls)

        # Shell executions
        timeline.extend(self._shell_executions)

        # Git operations
        timeline.extend(self._git_operations)

        # File reads
        timeline.extend(self._file_reads)

        # Sort by timestamp descending
        timeline.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        self._activity_timeline = timeline

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Push event to all connected WebSocket clients."""
        message = json.dumps({"type": event_type, "data": data})
        disconnected: list[WebSocket] = []
        for ws in self._ws_clients:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self._ws_clients.remove(ws)


def get_state() -> DashboardState:
    global _state
    if _state is None:
        _state = DashboardState()
        _state.load()
    return _state


def set_state(state: DashboardState) -> None:
    """Set dashboard state (for testing)."""
    global _state
    _state = state


def reset_state() -> None:
    """Reset dashboard state (for testing)."""
    global _state
    _state = None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ResolveRequest(BaseModel):
    winner_id: str
    rationale: str


class HealthResponse(BaseModel):
    status: str
    total_decisions: int
    active_decisions: int
    total_contradictions: int
    actionable_contradictions: int
    coherence_score: float
    audit_entries: int


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/")
async def index() -> HTMLResponse:
    """Serve the main dashboard page."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text())
    return HTMLResponse("<h1>VT Protocol Dashboard</h1><p>Static files not found.</p>")


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    """Architecture health summary — coherence score, decision count, contradiction count."""
    state = get_state()
    active = [d for d in state.decisions if d.valid]
    actionable = [c for c in state.contradictions if c.is_actionable]

    # Coherence score: 1.0 minus penalty for actionable contradictions
    total = max(len(active), 1)
    coherence = max(0.0, 1.0 - (len(actionable) / total))

    audit_count = 0
    if state.merkle:
        audit_count = state.merkle.size

    return {
        "status": "healthy" if not actionable else "degraded",
        "total_decisions": len(state.decisions),
        "active_decisions": len(active),
        "total_contradictions": len(state.contradictions),
        "actionable_contradictions": len(actionable),
        "coherence_score": round(coherence, 3),
        "audit_entries": audit_count,
    }


@app.get("/api/decisions")
async def api_decisions(
    dimension: str | None = Query(None, description="Filter by dimension"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List all decisions with optional filtering."""
    state = get_state()
    # Re-read from disk on every request so new decisions appear immediately
    # without requiring a dashboard restart (Bug 2 fix).
    state.decisions = _load_decisions(state.project_root)
    filtered = state.decisions

    if dimension:
        try:
            dim = Dimension(dimension)
            filtered = [d for d in filtered if dim in d.dimensions]
        except ValueError:
            raise HTTPException(400, f"Invalid dimension: {dimension}")

    if status:
        try:
            st = DecisionStatus(status)
            filtered = [d for d in filtered if d.status == st]
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")

    total = len(filtered)
    page = filtered[offset:offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "decisions": [_serialize_decision(d) for d in page],
    }


@app.get("/api/decisions/{decision_id}")
async def api_decision_detail(decision_id: str) -> dict[str, Any]:
    """Single decision with full context."""
    state = get_state()
    try:
        uid = UUID(decision_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")

    for d in state.decisions:
        if d.id == uid:
            detail = _serialize_decision(d)
            # Add related contradictions
            related_contradictions = [
                _serialize_contradiction(c) for c in state.contradictions
                if c.decision_a_id == uid or c.decision_b_id == uid
            ]
            detail["related_contradictions"] = related_contradictions
            return detail

    raise HTTPException(404, "Decision not found")


@app.get("/api/contradictions")
async def api_contradictions(
    status: str | None = Query(None, description="Filter by status"),
) -> dict[str, Any]:
    """List contradictions (defaults to unresolved)."""
    state = get_state()
    # Re-read from disk on every request so new contradictions appear immediately
    # without requiring a dashboard restart (Bug 2 fix).
    state.contradictions = _load_contradictions(state.project_root)
    filtered = state.contradictions

    if status:
        try:
            st = ContradictionStatus(status)
            filtered = [c for c in filtered if c.status == st]
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    else:
        # Default: show unresolved
        filtered = [c for c in filtered if c.status == ContradictionStatus.UNRESOLVED]

    return {
        "total": len(filtered),
        "contradictions": [_serialize_contradiction(c) for c in filtered],
    }


@app.post("/api/contradictions/{contradiction_id}/resolve")
async def api_resolve_contradiction(
    contradiction_id: str,
    body: ResolveRequest,
) -> dict[str, Any]:
    """Resolve a contradiction — pick a winner with rationale."""
    state = get_state()
    try:
        uid = UUID(contradiction_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")

    for c in state.contradictions:
        if c.id == uid:
            logger.info(
                "Resolving contradiction %s: winner=%s, "
                "decision_a=%s (%s), decision_b=%s (%s)",
                uid, body.winner_id,
                c.decision_a_id, c.decision_a_title,
                c.decision_b_id, c.decision_b_title,
            )

            c.status = ContradictionStatus.RESOLVED
            c.resolved_by = "dashboard-user"
            c.resolution_note = f"Winner: {body.winner_id}. {body.rationale}"
            c.resolved_at = datetime.now(timezone.utc)

            # Determine loser and mark as superseded
            winner_uuid = UUID(body.winner_id)
            loser_id = (
                c.decision_b_id if winner_uuid == c.decision_a_id
                else c.decision_a_id
            )
            logger.info("Loser decision: %s", loser_id)

            loser_decision = None
            for d in state.decisions:
                if d.id == loser_id:
                    logger.info(
                        "Found loser in state: '%s' (status=%s, valid=%s)",
                        d.title, d.status, d.valid,
                    )
                    d.status = DecisionStatus.SUPERSEDED
                    d.valid = False
                    loser_decision = d
                    logger.info(
                        "After update: status=%s, valid=%s", d.status, d.valid,
                    )
                    break

            if loser_decision is None:
                logger.warning(
                    "Could not find loser %s in state.decisions (%d decisions loaded)",
                    loser_id, len(state.decisions),
                )

            # Persist to disk
            _save_contradiction(state.project_root, c)
            if loser_decision is not None:
                _save_decision(state.project_root, loser_decision)

            # Generate refactor task file (Bug 1 fix)
            winner_title = (
                c.decision_a_title if winner_uuid == c.decision_a_id
                else c.decision_b_title
            )
            if loser_decision is not None:
                try:
                    from vt_protocol.decisions.resolution import generate_refactor_task
                    generate_refactor_task(
                        state.project_root,
                        c,
                        loser_id=str(loser_id),
                        loser_title=loser_decision.title,
                        winner_title=winner_title,
                    )
                except Exception:
                    logger.debug("Failed to generate refactor task", exc_info=True)

            # Log resolution to trace events
            _log_resolution_trace_event(state.project_root, c, body.winner_id, winner_title)

            # Auto-regenerate CLAUDE.md / .cursor/rules after resolution (Bug 4 fix)
            _auto_apply_rules(state)

            # Delete contradiction.lock so the agent can resume writing
            _delete_contradiction_lock(state.project_root)

            # Broadcast to WebSocket clients
            await state.broadcast("contradiction_resolved", {
                "id": str(uid),
                "winner_id": body.winner_id,
                "loser_id": str(loser_id),
                "winner_title": winner_title,
                "loser_title": loser_decision.title if loser_decision else "",
            })

            return {
                "status": "resolved",
                "id": str(uid),
                "loser_id": str(loser_id),
                "loser_superseded": loser_decision is not None,
            }

    raise HTTPException(404, "Contradiction not found")


@app.post("/api/contradictions/{contradiction_id}/defer")
async def api_defer_contradiction(contradiction_id: str) -> dict[str, Any]:
    """Defer a contradiction — unlock the agent and decide later."""
    state = get_state()
    try:
        uid = UUID(contradiction_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")

    for c in state.contradictions:
        if c.id == uid:
            c.status = ContradictionStatus.DEFERRED
            c.resolved_by = "dashboard-user"
            c.resolution_note = "Deferred via dashboard"
            # No resolved_at — contradiction remains open

            _save_contradiction(state.project_root, c)
            _delete_contradiction_lock(state.project_root)

            await state.broadcast("contradiction_deferred", {
                "id": str(uid),
                "status": "deferred",
            })

            return {"status": "deferred", "id": str(uid)}

    raise HTTPException(404, "Contradiction not found")


@app.get("/api/graph")
async def api_graph() -> dict[str, Any]:
    """Decision graph in Cytoscape.js format — nodes + edges."""
    state = get_state()
    # Re-read from disk so new decisions appear without restart (Bug 2 fix).
    state.decisions = _load_decisions(state.project_root)
    active = [d for d in state.decisions if d.valid]

    nodes = []
    edges = []

    for d in active:
        nodes.append({
            "data": {
                "id": str(d.id),
                "label": d.title[:50],
                "type": d.decision_type.value,
                "dimensions": [dim.value for dim in d.dimensions],
                "confidence": d.confidence,
                "status": d.status.value,
            },
        })

        # Supersedes edges
        if d.supersedes:
            edges.append({
                "data": {
                    "id": f"supersedes-{d.id}",
                    "source": str(d.id),
                    "target": str(d.supersedes),
                    "type": "SUPERSEDES",
                },
            })

    # Contradiction edges
    for c in state.contradictions:
        if c.status == ContradictionStatus.UNRESOLVED:
            edges.append({
                "data": {
                    "id": f"contradiction-{c.id}",
                    "source": str(c.decision_a_id),
                    "target": str(c.decision_b_id),
                    "type": c.verdict.value.upper(),
                    "confidence": c.confidence,
                },
            })

    # Shared-dimension edges between active decisions
    seen_pairs: set[str] = set()
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            shared = set(a.dimensions) & set(b.dimensions)
            if shared:
                pair_key = f"{min(str(a.id), str(b.id))}::{max(str(a.id), str(b.id))}"
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    edges.append({
                        "data": {
                            "id": f"shared-{pair_key}",
                            "source": str(a.id),
                            "target": str(b.id),
                            "type": "SHARED_DIMENSION",
                            "dimensions": [d.value for d in shared],
                            "weight": len(shared),
                        },
                    })

    return {"nodes": nodes, "edges": edges}


def _audit_event_label(event_type: str) -> str:
    """Human-readable label for an audit event_type value."""
    return {
        "decision_added": "Decision Created",
        "decision_superseded": "Decision Superseded",
        "contradiction_detected": "Contradiction Detected",
        "contradiction_resolved": "Contradiction Resolved",
        "context_injection": "vt check",
        "session_started": "vt init",
        "session_completed": "vt apply",
        "assumption_detected": "Assumption Found",
        "assumption_validated": "Assumption Validated",
        "assumption_rejected": "Assumption Rejected",
        "assumption_scan": "Assumptions Scanned",
    }.get(event_type, event_type)


def _audit_event_result(event_type: str) -> str:
    """Result classification for an audit event."""
    if event_type in ("contradiction_detected",):
        return "warning"
    if event_type in ("contradiction_resolved", "assumption_validated"):
        return "pass"
    if event_type in ("assumption_rejected",):
        return "fail"
    return "info"


def _audit_entry_description(event_type: str, payload: dict[str, Any]) -> str:
    """Build a short human description from event_type + payload."""
    if event_type == "decision_added":
        return payload.get("title", payload.get("decision_title", ""))
    if event_type == "decision_superseded":
        title = payload.get("title", "")
        status = payload.get("status", "")
        return f"{title} [{status}]" if title else status
    if event_type == "contradiction_detected":
        a = payload.get("decision_a_title", "")
        b = payload.get("decision_b_title", "")
        verdict = payload.get("verdict", "")
        base = f"{a} ↔ {b}" if a and b else a or b
        return f"{base} ({verdict})" if verdict else base
    if event_type == "contradiction_resolved":
        winner = payload.get("winner_title", payload.get("resolution_note", ""))
        return f"Winner: {winner}" if winner else ""
    if event_type == "context_injection":
        count = payload.get("decision_count", len(payload.get("decisions_surfaced", [])))
        query = payload.get("query", "")
        return f"{count} decision(s) surfaced — {query[:80]}" if query else f"{count} decision(s) surfaced"
    if event_type in ("session_started", "session_completed"):
        commit = payload.get("commit_hash", "")
        msg = payload.get("message", "")
        return f"{msg[:80]} ({commit[:8]})" if commit else msg[:80]
    if event_type in ("assumption_detected", "assumption_validated", "assumption_rejected"):
        sev = payload.get("severity", "")
        summary = payload.get("summary", payload.get("pattern_id", ""))
        return f"[{sev.upper()}] {summary}" if sev and sev != "medium" else summary
    if event_type == "assumption_scan":
        count = payload.get("count", 0)
        return f"{count} assumption(s) detected"
    return json.dumps(payload)[:120] if payload else ""


def _build_decision_entries(state: "DashboardState") -> list[dict[str, Any]]:
    """Synthesize audit entries from loaded Decision objects.

    Generates decision_added events from created_at and decision_superseded
    events for decisions that have been superseded or marked invalid.
    """
    entries: list[dict[str, Any]] = []
    for d in state.decisions:
        actor = d.source_type.value if d.source_type else "cli"
        ts = d.created_at.isoformat() if d.created_at else ""
        entries.append({
            "timestamp": ts,
            "event_type": "decision_added",
            "label": "Decision Created",
            "description": d.title,
            "actor": actor,
            "result": "info",
            "source": "decisions",
            "payload": {
                "title": d.title,
                "source_type": actor,
                "status": d.status.value,
                "dimensions": [x.value if hasattr(x, "value") else str(x) for x in (d.dimensions or [])],
            },
            "verified": None,
        })
        if not d.valid or d.status.value in ("superseded", "deprecated", "archived"):
            entries.append({
                "timestamp": ts,
                "event_type": "decision_superseded",
                "label": "Decision Superseded",
                "description": f"{d.title} [{d.status.value}]",
                "actor": "system",
                "result": "info",
                "source": "decisions",
                "payload": {"title": d.title, "status": d.status.value},
                "verified": None,
            })
    return entries


def _build_contradiction_entries(state: "DashboardState") -> list[dict[str, Any]]:
    """Synthesize audit entries from loaded Contradiction objects."""
    entries: list[dict[str, Any]] = []
    for c in state.contradictions:
        ts_detected = c.detected_at.isoformat() if c.detected_at else ""
        entries.append({
            "timestamp": ts_detected,
            "event_type": "contradiction_detected",
            "label": "Contradiction Detected",
            "description": _audit_entry_description("contradiction_detected", {
                "decision_a_title": c.decision_a_title,
                "decision_b_title": c.decision_b_title,
                "verdict": c.verdict.value if c.verdict else "",
            }),
            "actor": "system",
            "result": "warning",
            "source": "contradictions",
            "payload": {
                "decision_a_title": c.decision_a_title,
                "decision_b_title": c.decision_b_title,
                "verdict": c.verdict.value if c.verdict else "",
                "status": c.status.value if c.status else "",
            },
            "verified": None,
        })
        if c.status and c.status.value == "resolved":
            entries.append({
                "timestamp": ts_detected,  # No separate resolved_at on model
                "event_type": "contradiction_resolved",
                "label": "Contradiction Resolved",
                "description": f"Winner: {c.resolution_note or 'see rationale'}" if c.resolution_note else f"{c.decision_a_title} ↔ {c.decision_b_title}",
                "actor": c.resolved_by or "dashboard",
                "result": "pass",
                "source": "contradictions",
                "payload": {
                    "decision_a_title": c.decision_a_title,
                    "decision_b_title": c.decision_b_title,
                    "resolved_by": c.resolved_by,
                    "resolution_note": c.resolution_note,
                },
                "verified": None,
            })
    return entries


def _build_assumption_entries(state: "DashboardState") -> list[dict[str, Any]]:
    """Synthesize audit entries from loaded DomainAssumption objects.

    Emits assumption_detected per assumption, plus assumption_validated /
    assumption_rejected for resolved ones, and a single assumption_scan
    summary entry dated to the earliest detected_at.
    """
    assumptions = getattr(state, "_assumptions", [])
    if not assumptions:
        return []

    entries: list[dict[str, Any]] = []
    earliest_ts = None

    for a in assumptions:
        ts = a.detected_at.isoformat() if a.detected_at else ""
        if ts and (earliest_ts is None or ts < earliest_ts):
            earliest_ts = ts

        sev = getattr(a, "severity", "medium") or "medium"
        payload: dict[str, Any] = {
            "pattern_id": a.pattern_id,
            "summary": a.summary,
            "severity": sev,
            "category": a.category.value if a.category else "",
            "status": a.status.value if a.status else "",
        }

        entries.append({
            "timestamp": ts,
            "event_type": "assumption_detected",
            "label": "Assumption Found",
            "description": f"[{sev.upper()}] {a.summary}",
            "actor": getattr(a, "detected_by", "vt-scanner") or "vt-scanner",
            "result": "warning" if sev in ("high", "critical") else "info",
            "source": "assumptions",
            "payload": payload,
            "verified": None,
        })

        if a.status and a.status.value == "validated" and a.resolved_at:
            entries.append({
                "timestamp": a.resolved_at.isoformat(),
                "event_type": "assumption_validated",
                "label": "Assumption Validated",
                "description": a.summary,
                "actor": getattr(a, "resolved_by", "dashboard") or "dashboard",
                "result": "pass",
                "source": "assumptions",
                "payload": payload,
                "verified": None,
            })
        elif a.status and a.status.value == "rejected" and a.resolved_at:
            entries.append({
                "timestamp": a.resolved_at.isoformat(),
                "event_type": "assumption_rejected",
                "label": "Assumption Rejected",
                "description": a.summary,
                "actor": getattr(a, "resolved_by", "dashboard") or "dashboard",
                "result": "fail",
                "source": "assumptions",
                "payload": payload,
                "verified": None,
            })

    # One scan summary entry
    if earliest_ts:
        entries.append({
            "timestamp": earliest_ts,
            "event_type": "assumption_scan",
            "label": "Assumptions Scanned",
            "description": f"{len(assumptions)} assumption(s) detected",
            "actor": "vt-scanner",
            "result": "info",
            "source": "assumptions",
            "payload": {"count": len(assumptions)},
            "verified": None,
        })

    return entries


def _read_trace_events(project_root: Path) -> list[dict[str, Any]]:
    """Read .smm/traces/events.jsonl and normalize to the unified audit schema.

    Maps hook/llm_call events to the same shape as other audit entries so the
    UI shows a single merged chronological table.
    """
    jsonl_path = project_root / ".smm" / "traces" / "events.jsonl"
    entries: list[dict[str, Any]] = []
    if not jsonl_path.exists():
        return entries

    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue

                ev_type = raw.get("type", "unknown")
                action = raw.get("action", "")
                result = raw.get("result", "info")
                agent = raw.get("agent", "cli")
                ts = raw.get("timestamp", "")

                if ev_type == "hook":
                    event_type = f"hook_{action.lower()}" if action else "hook"
                    label = f"Hook: {action}"
                    file_ = raw.get("file", "")
                    reason = raw.get("reason", "")
                    description = file_.split("/")[-1] if file_ else ""
                    if reason:
                        description = f"{description} — {reason}"
                    result_cls = "pass" if result == "pass" else "fail" if result in ("block", "blocked") else "info"
                elif ev_type == "llm_call":
                    event_type = "llm_call"
                    label = "LLM Call"
                    model = raw.get("model", "")
                    tokens_in = raw.get("input_tokens", 0)
                    tokens_out = raw.get("output_tokens", 0)
                    preview = raw.get("prompt_preview", "")
                    if preview:
                        description = f"{model} — {preview[:80]}"
                    elif model:
                        description = f"{model} ({tokens_in}→{tokens_out} tok)"
                    else:
                        description = f"{tokens_in}→{tokens_out} tokens"
                    result_cls = "info"
                else:
                    event_type = ev_type
                    label = ev_type
                    description = ""
                    result_cls = "info"

                entries.append({
                    "timestamp": ts,
                    "event_type": event_type,
                    "label": label,
                    "description": description,
                    "actor": agent,
                    "result": result_cls,
                    "source": "traces",
                    "payload": {k: v for k, v in raw.items() if k not in ("type", "agent", "timestamp")},
                    "verified": None,
                })
    except Exception:
        pass

    return entries


@app.get("/api/audit")
async def api_audit(
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    verify: bool = Query(False, description="Verify Merkle inclusion proofs"),
) -> dict[str, Any]:
    """Unified audit trail — merges all governance event sources.

    Sources (in order of merge):
    - state.decisions          → decision_added / decision_superseded
    - state.contradictions     → contradiction_detected / contradiction_resolved
    - state._assumptions       → assumption_detected / assumption_validated / assumption_rejected
    - .smm/traces/events.jsonl → hook_edit, hook_write, llm_call
    - .smm/audit/audit.db      → session_completed (Merkle-verified)

    Schema per entry: timestamp, event_type, label, description, actor, result, source, payload, verified.
    """
    state = get_state()

    # --- 1. Decision lifecycle events ---
    decision_entries = _build_decision_entries(state)

    # --- 2. Contradiction events ---
    contradiction_entries = _build_contradiction_entries(state)

    # --- 3. Assumption events ---
    assumption_entries = _build_assumption_entries(state)

    # --- 4. Trace events (hook + llm_call from events.jsonl) ---
    trace_entries = _read_trace_events(state.project_root)

    # --- 5. Merkle audit.db entries ---
    merkle_entries: list[dict[str, Any]] = []
    tree_size = 0
    if state.merkle:
        tree = state.merkle
        tree_size = tree.size
        db_entries = tree.get_entries(limit=1000, offset=0)

        raw_jsons: list[str] = []
        if verify:
            raw_jsons = _get_raw_entry_jsons(tree, limit=1000, offset=0)

        for i, entry in enumerate(db_entries):
            ev = entry.event_type.value
            payload = entry.payload or {}
            entry_dict: dict[str, Any] = {
                "timestamp": entry.timestamp.isoformat(),
                "event_type": ev,
                "label": _audit_event_label(ev),
                "description": _audit_entry_description(ev, payload),
                "actor": entry.actor,
                "result": _audit_event_result(ev),
                "source": "audit_db",
                "payload": payload,
                "verified": None,
            }
            if verify and i < tree_size:
                try:
                    proof = tree.inclusion_proof(i, tree_size)
                    root = tree.root_hash(tree_size)
                    raw_data = raw_jsons[i].encode("utf-8") if i < len(raw_jsons) else b""
                    entry_dict["verified"] = tree.verify_inclusion(proof, raw_data, root)
                except Exception:
                    entry_dict["verified"] = False
            merkle_entries.append(entry_dict)

    # --- Merge all sources and sort chronologically (newest first) ---
    all_entries = (
        decision_entries
        + contradiction_entries
        + assumption_entries
        + trace_entries
        + merkle_entries
    )
    all_entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    total = len(all_entries)
    page = all_entries[offset: offset + limit]

    return {
        "total": total,
        "tree_size": tree_size,
        "sources": {
            "decisions": len(decision_entries),
            "contradictions": len(contradiction_entries),
            "assumptions": len(assumption_entries),
            "traces": len(trace_entries),
            "audit_db": len(merkle_entries),
        },
        "entries": page,
    }


@app.get("/api/blast-radius/{decision_id}")
async def api_blast_radius(decision_id: str) -> dict[str, Any]:
    """Blast radius for a decision — what's affected if it changes.

    Returns:
      - Directly related decisions (shared dimensions)
      - Contradictions involving this decision
      - Dependent decisions (those that supersede or are superseded by)
      - Estimated impact score (0.0-1.0)
    """
    state = get_state()
    try:
        uid = UUID(decision_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")

    target = None
    for d in state.decisions:
        if d.id == uid:
            target = d
            break

    if target is None:
        raise HTTPException(404, "Decision not found")

    # Find directly related decisions (shared dimensions)
    related: list[dict[str, Any]] = []
    for d in state.decisions:
        if d.id == uid or not d.valid:
            continue
        shared = set(target.dimensions) & set(d.dimensions)
        if shared:
            related.append({
                "id": str(d.id),
                "title": d.title,
                "shared_dimensions": [dim.value for dim in shared],
                "relationship": "shared_dimension",
            })

    # Find contradictions involving this decision
    affected_contradictions: list[dict[str, Any]] = []
    for c in state.contradictions:
        if c.decision_a_id == uid or c.decision_b_id == uid:
            other_id = c.decision_b_id if c.decision_a_id == uid else c.decision_a_id
            other_title = c.decision_b_title if c.decision_a_id == uid else c.decision_a_title
            affected_contradictions.append({
                "id": str(c.id),
                "other_decision_id": str(other_id),
                "other_decision_title": other_title,
                "verdict": c.verdict.value,
                "status": c.status.value,
            })

    # Find supersession chain
    chain: list[dict[str, Any]] = []
    for d in state.decisions:
        if d.supersedes == uid:
            chain.append({
                "id": str(d.id),
                "title": d.title,
                "relationship": "superseded_by",
            })
    if target.supersedes:
        for d in state.decisions:
            if d.id == target.supersedes:
                chain.append({
                    "id": str(d.id),
                    "title": d.title,
                    "relationship": "supersedes",
                })

    # Impact score: proportion of project affected
    total_active = max(len([d for d in state.decisions if d.valid]), 1)
    impacted_count = len(related) + len(chain)
    impact_score = min(1.0, impacted_count / total_active)

    # Build Cytoscape graph for blast-radius visualization
    nodes = [{
        "data": {
            "id": str(target.id),
            "label": target.title[:50],
            "type": "center",
            "dimensions": [dim.value for dim in target.dimensions],
        }
    }]
    edges = []

    for r in related:
        nodes.append({
            "data": {
                "id": r["id"],
                "label": r["title"][:50],
                "type": "related",
                "dimensions": r["shared_dimensions"],
            }
        })
        edges.append({
            "data": {
                "id": f"shared-{r['id']}",
                "source": str(target.id),
                "target": r["id"],
                "type": "SHARED_DIMENSION",
            }
        })

    for c in affected_contradictions:
        if not any(n["data"]["id"] == c["other_decision_id"] for n in nodes):
            nodes.append({
                "data": {
                    "id": c["other_decision_id"],
                    "label": c["other_decision_title"][:50],
                    "type": "contradicting",
                }
            })
        edges.append({
            "data": {
                "id": f"contra-{c['id']}",
                "source": str(target.id),
                "target": c["other_decision_id"],
                "type": c["verdict"].upper(),
            }
        })

    for ch in chain:
        if not any(n["data"]["id"] == ch["id"] for n in nodes):
            nodes.append({
                "data": {
                    "id": ch["id"],
                    "label": ch["title"][:50],
                    "type": ch["relationship"],
                }
            })
        edges.append({
            "data": {
                "id": f"chain-{ch['id']}",
                "source": str(target.id),
                "target": ch["id"],
                "type": "SUPERSEDES",
            }
        })

    return {
        "decision": _serialize_decision(target),
        "related_decisions": related,
        "contradictions": affected_contradictions,
        "supersession_chain": chain,
        "impact_score": round(impact_score, 3),
        "total_affected": impacted_count + len(affected_contradictions),
        "graph": {"nodes": nodes, "edges": edges},
    }


@app.get("/api/contradictions/{contradiction_id}/resolution-paths")
async def api_resolution_paths(contradiction_id: str) -> dict[str, Any]:
    """Suggest resolution paths for a contradiction.

    Returns 2-3 actionable resolution options (CodeRabbit-style).
    """
    from vt_protocol.decisions.resolution import suggest_resolution_paths

    state = get_state()
    try:
        uid = UUID(contradiction_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")

    target_c = None
    for c in state.contradictions:
        if c.id == uid:
            target_c = c
            break

    if target_c is None:
        raise HTTPException(404, "Contradiction not found")

    # Find the actual decisions for richer path descriptions
    da = next((d for d in state.decisions if d.id == target_c.decision_a_id), None)
    db = next((d for d in state.decisions if d.id == target_c.decision_b_id), None)

    paths = suggest_resolution_paths(target_c, da, db)
    return {
        "contradiction_id": str(uid),
        "paths": [p.to_dict() for p in paths],
    }


@app.post("/api/contradictions/{contradiction_id}/apply-resolution")
async def api_apply_resolution(
    contradiction_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Apply a resolution action from suggested paths.

    Body: {action: str, rationale: str}
    """
    from vt_protocol.decisions.resolution import apply_resolution

    state = get_state()
    try:
        uid = UUID(contradiction_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")

    target_c = None
    for c in state.contradictions:
        if c.id == uid:
            target_c = c
            break

    if target_c is None:
        raise HTTPException(404, "Contradiction not found")

    action = body.get("action", "")
    rationale = body.get("rationale", "")

    result = apply_resolution(
        target_c,
        action,
        rationale=rationale,
        decisions=state.decisions,
    )

    # Persist changes
    _save_contradiction(state.project_root, target_c)

    # Persist superseded decision to disk
    superseded_id = result.get("superseded_id")
    loser_d = None
    winner_d = None
    if superseded_id:
        winner_id = result.get("winner_id", "")
        for d in state.decisions:
            if str(d.id) == superseded_id:
                _save_decision(state.project_root, d)
                loser_d = d
            if str(d.id) == winner_id:
                winner_d = d

    # Generate refactor task file when a decision is superseded (Bug 1 fix)
    if loser_d is not None and winner_d is not None and result.get("status") == "resolved":
        try:
            from vt_protocol.decisions.resolution import generate_refactor_task
            generate_refactor_task(
                state.project_root,
                target_c,
                loser_id=str(loser_d.id),
                loser_title=loser_d.title,
                winner_title=winner_d.title,
            )
        except Exception:
            logger.debug("Failed to generate refactor task", exc_info=True)

    # Log resolution to trace events
    winner_title = winner_d.title if winner_d else ""
    _log_resolution_trace_event(state.project_root, target_c, result.get("winner_id", ""), winner_title)

    # Auto-regenerate CLAUDE.md / .cursor/rules after resolution (Bug 4 fix)
    _auto_apply_rules(state)

    # Delete contradiction.lock so the agent can resume writing
    _delete_contradiction_lock(state.project_root)

    # Broadcast to WebSocket clients
    await state.broadcast("contradiction_resolved", {
        "id": str(uid),
        "action": action,
        "result": result,
    })

    return {"id": str(uid), **result}


@app.get("/api/calibration")
async def api_calibration() -> dict[str, Any]:
    """LLM judge calibration metrics (IRT-based).

    Returns ECE, Brier score, Wasserstein distance, accuracy, and drift.
    """
    state = get_state()
    calibration_db = state.project_root / ".smm" / "calibration.db"

    if not calibration_db.exists():
        return {
            "metrics": None,
            "message": "No calibration data yet. Resolve contradictions to build calibration history.",
        }

    from vt_protocol.decisions.calibration import CalibrationStore

    store = CalibrationStore(calibration_db, check_same_thread=False)
    try:
        metrics = store.compute_metrics()
        return {"metrics": metrics.to_dict()}
    finally:
        store.close()


@app.get("/api/compliance")
async def api_compliance() -> dict[str, Any]:
    """CISO compliance view — attribution, framework mapping, agent timeline.

    Returns AI vs human attribution stats, compliance framework mappings,
    and recent agent activity.
    """
    from vt_protocol.audit.compliance import (
        compute_attribution,
        extract_agent_activities,
        generate_compliance_mappings,
    )

    state = get_state()

    # Attribution stats
    attribution = compute_attribution(state.decisions)

    # Determine capabilities
    has_merkle = state.merkle is not None and state.merkle.size > 0
    audit_count = state.merkle.size if state.merkle else 0

    # Check for signing key
    signing_key_path = state.project_root / ".smm" / "signing_key"
    has_signing = signing_key_path.exists()

    # Check for RFC 3161 timestamps
    timestamps_dir = state.project_root / ".smm" / "audit" / "timestamps"
    has_rfc3161 = timestamps_dir.is_dir() and any(timestamps_dir.iterdir()) if timestamps_dir.is_dir() else False

    mappings = generate_compliance_mappings(
        has_merkle_audit=has_merkle,
        has_signing=has_signing,
        has_rfc3161=has_rfc3161,
        has_agent_registry=False,  # Will be enabled in Sprint 13
        has_attribution=len(state.decisions) > 0,
        audit_entry_count=audit_count,
    )

    # Agent activity timeline
    activities = []
    if state.merkle:
        entries = state.merkle.get_entries(limit=100)
        agent_activities = extract_agent_activities(entries)
        activities = [a.to_dict() for a in agent_activities]

    return {
        "attribution": attribution.to_dict(),
        "compliance_mappings": [m.to_dict() for m in mappings],
        "agent_activities": activities,
    }


@app.get("/api/compliance/export")
async def api_compliance_export() -> dict[str, Any]:
    """One-click evidence export — complete JSON bundle for auditors.

    Includes audit entries, Merkle proofs, signatures, timestamps,
    and verification instructions.
    """
    from vt_protocol.audit.compliance import build_evidence_bundle

    state = get_state()

    # Collect audit entries
    audit_entries: list[AuditEntry] = []
    tree_heads_data: list[dict[str, Any]] = []
    inclusion_proofs_data: list[dict[str, Any]] = []
    consistency_proofs_data: list[dict[str, Any]] = []

    if state.merkle:
        tree = state.merkle
        audit_entries = tree.get_entries(limit=10000)

        # Collect tree head and proofs
        if tree.size > 0:
            head = tree.get_tree_head()
            tree_heads_data.append({
                "tree_size": head.tree_size,
                "root_hash_hex": head.root_hash.hex(),
                "timestamp": head.timestamp.isoformat(),
                "signature_hex": head.signature.hex() if head.signature else "",
            })

            # Inclusion proofs for last 10 entries
            start = max(0, tree.size - 10)
            raw_jsons = _get_raw_entry_jsons(tree, limit=10, offset=start)
            for i, raw in enumerate(raw_jsons):
                idx = start + i
                try:
                    proof = tree.inclusion_proof(idx)
                    inclusion_proofs_data.append({
                        "leaf_index": idx,
                        "tree_size": proof.tree_size,
                        "entry_json": raw,
                        "root_hash_hex": tree.root_hash(proof.tree_size).hex(),
                        "proof_hashes_hex": [h.hex() for h in proof.hashes],
                    })
                except Exception:
                    pass

    # Check capabilities
    signing_key_path = state.project_root / ".smm" / "signing_key"
    has_signing = signing_key_path.exists()
    timestamps_dir = state.project_root / ".smm" / "audit" / "timestamps"
    has_rfc3161 = timestamps_dir.is_dir() and any(timestamps_dir.iterdir()) if timestamps_dir.is_dir() else False

    bundle = build_evidence_bundle(
        state.decisions,
        audit_entries,
        tree_heads=tree_heads_data,
        inclusion_proofs=inclusion_proofs_data,
        consistency_proofs=consistency_proofs_data,
        has_signing=has_signing,
        has_rfc3161=has_rfc3161,
    )

    return bundle.to_dict()


@app.get("/api/compliance/anchoring")
async def api_compliance_anchoring() -> dict[str, Any]:
    """RFC 3161 anchoring history — shows when tree heads were timestamped."""
    from vt_protocol.audit.rfc3161 import AnchoringHistory, TimestampToken

    state = get_state()
    timestamps_dir = state.project_root / ".smm" / "audit" / "timestamps"

    history = AnchoringHistory()
    if timestamps_dir.is_dir():
        import json as _json
        for fp in sorted(timestamps_dir.glob("*.json")):
            try:
                data = _json.loads(fp.read_text())
                token = TimestampToken(
                    tree_size=data.get("tree_size", 0),
                    root_hash_hex=data.get("root_hash_hex", ""),
                    tsa_url=data.get("tsa_url", ""),
                    response_status=data.get("response_status", ""),
                    verified=data.get("verified", False),
                )
                if data.get("token_hex"):
                    token.token_bytes = bytes.fromhex(data["token_hex"])
                history.anchors.append(token)
            except Exception:
                logger.debug("Failed to load timestamp from %s", fp, exc_info=True)

    return history.to_dict()


@app.get("/api/sessions")
async def api_sessions() -> dict[str, Any]:
    """Recent agent sessions with decisions captured."""
    state = get_state()
    return {
        "total": len(state.sessions),
        "sessions": state.sessions,
    }


# ---------------------------------------------------------------------------
# Agent management endpoints
# ---------------------------------------------------------------------------


@app.get("/api/agents")
async def api_agents() -> dict[str, Any]:
    """List all onboarded agents with activity stats."""
    state = get_state()
    try:
        config = load_governance_config(state.project_root)
    except Exception:
        return {"total": 0, "agents": []}

    agents_list = []
    for name, val in config.agents.items():
        if isinstance(val, bool):
            agents_list.append({
                "name": name,
                "enabled": val,
                "type": "simple",
                "role": None,
                "config": None,
            })
        elif isinstance(val, AgentConfig):
            # Compute activity stats from decisions
            decisions_by_agent = [d for d in state.decisions if d.made_by == name]
            agents_list.append({
                "name": name,
                "enabled": True,
                "type": val.type,
                "role": val.role,
                "display_name": val.display_name,
                "allowed_paths": val.allowed_paths,
                "blocked_paths": val.blocked_paths,
                "allowed_dimensions": val.allowed_dimensions,
                "restricted_dimensions": val.restricted_dimensions,
                "context_level": val.context_level,
                "session_ttl_minutes": val.session_ttl_minutes,
                "block_on_contradiction": val.block_on_contradiction,
                "auto_resolve": val.auto_resolve,
                "activity": {
                    "decisions_made": len(decisions_by_agent),
                    "contradictions_triggered": sum(
                        1 for c in state.contradictions
                        if any(d.id in (c.decision_a_id, c.decision_b_id) for d in decisions_by_agent)
                    ),
                },
                "config": {
                    "owner": val.owner,
                    "created_at": val.created_at,
                    "last_active": val.last_active,
                },
            })

    return {"total": len(agents_list), "agents": agents_list}


@app.get("/api/agents/{agent_name}")
async def api_agent_detail(agent_name: str) -> dict[str, Any]:
    """Single agent detail with recent decisions."""
    state = get_state()
    try:
        config = load_governance_config(state.project_root)
    except Exception:
        raise HTTPException(404, "Cannot load governance config")

    val = config.agents.get(agent_name)
    if val is None:
        raise HTTPException(404, f"Agent '{agent_name}' not found")

    if isinstance(val, bool):
        return {"name": agent_name, "enabled": val, "type": "simple"}

    decisions_by_agent = [d for d in state.decisions if d.made_by == agent_name]

    return {
        "name": agent_name,
        "type": val.type,
        "role": val.role,
        "display_name": val.display_name,
        "allowed_paths": val.allowed_paths,
        "blocked_paths": val.blocked_paths,
        "allowed_dimensions": val.allowed_dimensions,
        "restricted_dimensions": val.restricted_dimensions,
        "context_level": val.context_level,
        "session_ttl_minutes": val.session_ttl_minutes,
        "block_on_contradiction": val.block_on_contradiction,
        "recent_decisions": [_serialize_decision(d) for d in decisions_by_agent[:10]],
    }


# ---------------------------------------------------------------------------
# Observation endpoints (Lattice)
# ---------------------------------------------------------------------------


@app.get("/api/signals")
async def api_signals() -> dict[str, Any]:
    """Traffic-light view of the 7 golden signals.

    Returns signals grouped by severity (critical/warning/info)
    with an overall status (green/yellow/red).
    """
    state = get_state()
    signals = list(state._signals)

    # Add dangerous shell command signals
    for e in state._shell_executions:
        if e.get("details", {}).get("dangerous"):
            from vt_protocol.observation.signals import Signal
            signals.append(Signal(
                name="dangerous_command",
                severity=e.get("severity", "warning"),
                message=f"Dangerous command: {e.get('details', {}).get('command', '')[:80]}",
                details={
                    "command": e.get("details", {}).get("command", ""),
                    "reasons": e.get("details", {}).get("danger_reasons", []),
                    "agent_id": e.get("agent_id", ""),
                },
            ))

    by_severity: dict[str, list[dict[str, Any]]] = {
        "critical": [],
        "warning": [],
        "info": [],
    }
    for sig in signals:
        entry = {
            "name": sig.name,
            "severity": sig.severity,
            "message": sig.message,
            "details": sig.details,
            "timestamp": sig.timestamp.isoformat(),
        }
        by_severity.get(sig.severity, by_severity["info"]).append(entry)

    if by_severity["critical"]:
        status = "red"
    elif by_severity["warning"]:
        status = "yellow"
    else:
        status = "green"

    return {
        "status": status,
        "total": len(signals),
        "critical": by_severity["critical"],
        "warning": by_severity["warning"],
        "info": by_severity["info"],
        "snapshot_available": state._snapshot_diff is not None,
        "file_changes": {
            "added": len(state._snapshot_diff.added) if state._snapshot_diff else 0,
            "removed": len(state._snapshot_diff.removed) if state._snapshot_diff else 0,
            "modified": len(state._snapshot_diff.modified) if state._snapshot_diff else 0,
        },
    }


@app.get("/api/traces")
async def api_traces(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action_type: str | None = Query(None, description="Filter: llm_call, mcp_tool, file_read, shell_command, git_operation, hook, cli, llm_call"),
    agent_id: str | None = Query(None, description="Filter by agent ID"),
    severity: str | None = Query(None, description="Filter: info, warning, critical"),
) -> dict[str, Any]:
    """Unified activity timeline — merges LLM calls, MCP tools, file reads, shell commands, git ops, hook events, CLI events.

    Filterable by action_type, agent_id, and severity.
    Also reads .smm/traces/events.jsonl for hook and CLI governance events.
    """
    state = get_state()

    # Re-read trace events from disk (they may have been appended since startup)
    state._load_trace_events()

    # Merge observation timeline with trace events
    timeline = list(state._activity_timeline)
    for te in state._trace_events:
        severity_val = "warning" if te.get("result") == "block" else "info"
        summary = f"{te.get('type', 'event')}: {te.get('action', '')} {te.get('file', '')}".strip()
        if te.get("reason"):
            summary += f" — {te['reason']}"
        timeline.append({
            "entry_id": f"trace-{te.get('timestamp', '')}-{te.get('action', '')}",
            "timestamp": te.get("timestamp", ""),
            "agent_id": te.get("agent", ""),
            "session_id": "",
            "action_type": te.get("type", "hook"),
            "tool_name": te.get("action", ""),
            "summary": summary,
            "severity": severity_val,
            "details": te,
            "duration_ms": 0,
        })
    # Sort by timestamp descending
    timeline.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    # Apply filters
    if action_type:
        timeline = [e for e in timeline if e.get("action_type") == action_type]
    if agent_id:
        timeline = [e for e in timeline if e.get("agent_id") == agent_id]
    if severity:
        timeline = [e for e in timeline if e.get("severity") == severity]

    total = len(timeline)
    page = timeline[offset : offset + limit]

    # Compute summary from full (unfiltered, merged) timeline
    full = timeline
    action_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    agents_seen: set[str] = set()
    for e in full:
        at = e.get("action_type", "unknown")
        action_counts[at] = action_counts.get(at, 0) + 1
        sv = e.get("severity", "info")
        severity_counts[sv] = severity_counts.get(sv, 0) + 1
        aid = e.get("agent_id", "")
        if aid:
            agents_seen.add(aid)

    # LLM-specific summary — aggregate from observation spans
    total_cost = sum(s.cost_usd or 0 for s in state._spans)
    total_tokens_in = sum(s.tokens_in for s in state._spans)
    total_tokens_out = sum(s.tokens_out for s in state._spans)
    # Also aggregate from trace events (llm_call type) written by session_logs sync
    # These carry the real token data from Claude Code JSONL files (Bug 5 fix).
    for te in state._trace_events:
        if te.get("type") == "llm_call":
            ti = int(te.get("input_tokens") or 0)
            to_ = int(te.get("output_tokens") or 0)
            total_tokens_in += ti
            total_tokens_out += to_
            # Use pre-computed cost_usd if present, else estimate
            if te.get("cost_usd"):
                total_cost += float(te["cost_usd"])
            else:
                total_cost += _estimate_llm_cost(te.get("model", ""), ti, to_)

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "summary": {
            "total_activities": len(full),
            "action_counts": action_counts,
            "severity_counts": severity_counts,
            "agents": sorted(agents_seen),
            "total_cost_usd": round(total_cost, 6),
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "providers": sorted({s.provider for s in state._spans}),
            "models": sorted({s.model for s in state._spans}),
        },
        "entries": page,
    }


@app.get("/api/provenance")
async def api_provenance() -> dict[str, Any]:
    """Causal provenance graph — TaintedStr flow + CausalEdge links.

    Returns a Cytoscape.js-format graph with spans as nodes and
    causal edges between them.
    """
    state = get_state()
    spans = state._spans
    edges = state._edges

    nodes = []
    for s in spans:
        nodes.append({
            "data": {
                "id": s.span_id,
                "label": f"{s.model} ({s.provider})",
                "agent_id": s.agent_id or "unknown",
                "model": s.model,
                "provider": s.provider,
                "cost_usd": s.cost_usd,
                "latency_ms": s.latency_ms,
                "timestamp": s.timestamp,
            },
        })

    graph_edges = []
    for e in edges:
        graph_edges.append({
            "data": {
                "id": f"{e.source_span_id}-{e.target_span_id}",
                "source": e.source_span_id,
                "target": e.target_span_id,
                "type": e.edge_type,
                "confidence": e.confidence,
            },
        })

    edge_types: dict[str, int] = dict(Counter(e.edge_type for e in edges)) if edges else {}

    return {
        "summary": {
            "total_spans": len(spans),
            "total_edges": len(edges),
            "edge_types": edge_types,
        },
        "graph": {
            "nodes": nodes,
            "edges": graph_edges,
        },
    }


@app.get("/api/secrets")
async def api_secrets() -> dict[str, Any]:
    """Secret detection scan across decision content."""
    from vt_protocol.observation.secrets import scan as scan_secrets

    state = get_state()
    all_matches: list[dict[str, Any]] = []
    files_scanned = 0

    for d in state.decisions:
        result = scan_secrets(d.content)
        if result.has_secrets:
            for m in result.matches:
                all_matches.append({
                    "source": f"decision:{d.title}",
                    "secret_type": m.secret_type,
                    "redacted": m.redacted,
                    "preview": m.original_preview,
                })
        files_scanned += 1

    by_type: dict[str, int] = {}
    for m in all_matches:
        by_type[m["secret_type"]] = by_type.get(m["secret_type"], 0) + 1

    return {
        "total_matches": len(all_matches),
        "files_scanned": files_scanned,
        "by_type": by_type,
        "matches": all_matches[:100],
        "status": "clean" if not all_matches else "alert",
    }


@app.get("/api/scope-creep")
async def api_scope_creep() -> dict[str, Any]:
    """Scope creep and trajectory analysis — alerts for loops, thrashing, drift."""
    state = get_state()
    alerts = list(state._trajectory_alerts)

    scope_signal = None
    if state._snapshot_diff:
        from vt_protocol.observation.signals import detect_scope_creep

        changed_files = [e.path for e in state._snapshot_diff.added] + [
            after.path for _, after in state._snapshot_diff.modified
        ]
        if changed_files:
            sig = detect_scope_creep(
                task_description="project maintenance",
                changed_files=changed_files,
            )
            if sig:
                scope_signal = {
                    "name": sig.name,
                    "severity": sig.severity,
                    "message": sig.message,
                    "details": sig.details,
                }

    return {
        "total_alerts": len(alerts),
        "alerts": alerts,
        "scope_signal": scope_signal,
        "file_changes_summary": {
            "total": state._snapshot_diff.total_changes if state._snapshot_diff else 0,
            "by_category": {
                k.value: v
                for k, v in state._snapshot_diff.changes_by_category().items()
            }
            if state._snapshot_diff
            else {},
        },
    }


# ---------------------------------------------------------------------------
# GET /api/assumptions — Domain Assumption Governance
# ---------------------------------------------------------------------------


@app.get("/api/assumptions")
async def api_assumptions(
    status: str | None = Query(None, description="Filter: detected, proposed, validated, rejected, deferred"),
    category: str | None = Query(None, description="Filter: data_scope, temporal, access, completeness, configuration, framework"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List domain assumptions with filtering and pagination."""
    state = get_state()
    assumptions = list(state._assumptions)

    if status:
        assumptions = [a for a in assumptions if a.status.value == status]
    if category:
        assumptions = [a for a in assumptions if a.category.value == category]

    total = len(assumptions)
    page = assumptions[offset : offset + limit]

    # Compute stats
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for a in state._assumptions:
        by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
        by_category[a.category.value] = by_category.get(a.category.value, 0) + 1

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "stats": {
            "by_status": by_status,
            "by_category": by_category,
            "actionable": sum(1 for a in state._assumptions if a.is_actionable),
        },
        "assumptions": [_serialize_assumption(a) for a in page],
    }


@app.get("/api/assumptions/{assumption_id}")
async def api_assumption_detail(assumption_id: str) -> dict[str, Any]:
    """Get a single assumption by ID."""
    state = get_state()
    for a in state._assumptions:
        if str(a.id) == assumption_id or a.id.hex[:8] == assumption_id:
            return {"assumption": _serialize_assumption(a)}
    raise HTTPException(status_code=404, detail="Assumption not found")


class _ResolveBody(BaseModel):
    selected_option: int
    resolved_by: str = "dashboard-user"
    rationale: str = ""


@app.post("/api/assumptions/{assumption_id}/resolve")
async def api_resolve_assumption(
    assumption_id: str, body: _ResolveBody
) -> dict[str, Any]:
    """Resolve a domain assumption via dashboard.

    Mapping (mirrors resolve_assumption in assumption_pipeline.py):
      option 0  → VALIDATED  (A — current code is correct)
      option with "need more context" text → DEFERRED  (E)
      any other option → REJECTED  (B / C / D)

    After resolving, reloads state._assumptions so counts update immediately.
    """
    from vt_protocol.analysis.assumption_pipeline import resolve_assumption

    state = get_state()

    # Confirm the assumption exists in-memory first
    target = None
    for a in state._assumptions:
        if str(a.id) == assumption_id or a.id.hex == assumption_id or a.id.hex[:8] == assumption_id:
            target = a
            break
    if target is None:
        raise HTTPException(status_code=404, detail="Assumption not found")

    updated = resolve_assumption(
        state.project_root,
        str(target.id),
        body.selected_option,
        resolved_by=body.resolved_by,
        rationale=body.rationale,
    )
    if updated is None:
        raise HTTPException(status_code=422, detail="Could not resolve assumption (invalid option or already resolved)")

    # If validated, create a decision from the assumption so it becomes a CLAUDE.md rule
    decision_created = False
    if updated.status.value == "validated":
        try:
            decision_created = _create_decision_from_assumption(state, updated)
        except Exception:
            logger.debug("Failed to create decision from validated assumption", exc_info=True)

    # Log trace event for assumption resolution
    _log_assumption_trace_event(state.project_root, updated)

    # Auto-regenerate CLAUDE.md to include new assumption rules
    _auto_apply_rules(state)

    # Broadcast to WebSocket clients
    await state.broadcast("assumption_resolved", {
        "id": str(updated.id),
        "status": updated.status.value,
        "decision_created": decision_created,
    })

    # Reload so the next /api/assumptions call reflects the new status
    state._load_assumptions()

    return {"assumption": _serialize_assumption(updated), "status": updated.status.value, "decision_created": decision_created}


@app.post("/api/assumptions/scan")
async def api_scan_assumptions() -> dict[str, Any]:
    """Trigger a fresh assumption scan."""
    from vt_protocol.analysis.assumption_pipeline import run_assumption_pipeline

    state = get_state()
    result = run_assumption_pipeline(state.project_root)
    state._load_assumptions()  # Reload after scan

    return {
        "detected": result.detected,
        "new": result.new,
        "pre_validated": result.pre_validated,
        "deduped": result.deduped,
        "below_threshold": result.below_threshold,
    }


@app.get("/api/assumptions/stats")
async def api_assumption_stats() -> dict[str, Any]:
    """Acceptance/rejection rates for adaptive learning."""
    from vt_protocol.analysis.assumption_pipeline import compute_stats

    state = get_state()
    stats = compute_stats(state._assumptions)
    return {
        "total_detected": stats.total_detected,
        "total_validated": stats.total_validated,
        "total_rejected": stats.total_rejected,
        "total_deferred": stats.total_deferred,
        "by_category": stats.by_category,
        "by_pattern": stats.by_pattern,
    }


def _serialize_assumption(a: Any) -> dict[str, Any]:
    """Serialize a DomainAssumption for JSON response."""
    return {
        "id": str(a.id),
        "category": a.category.value,
        "status": a.status.value,
        "pattern_id": a.pattern_id,
        "summary": a.summary,
        "confidence": a.confidence,
        "severity": a.severity,
        "question": a.question,
        "options": a.options,
        "selected_option": a.selected_option,
        "answer_rationale": a.answer_rationale,
        "resolved_by": a.resolved_by,
        "code_evidence": [
            {"file": e.file, "line": e.line, "snippet": e.snippet}
            for e in a.code_evidence
        ],
        "detected_at": a.detected_at.isoformat(),
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "detected_by": a.detected_by,
        "is_baseline": a.is_baseline,
        "is_actionable": a.is_actionable,
    }


# ---------------------------------------------------------------------------
# Dashboard redesign endpoints (home, specs, contracts, persona)
# ---------------------------------------------------------------------------


@app.get("/api/home")
async def get_home() -> dict[str, Any]:
    """Aggregated home dashboard data for the triage view."""
    state = get_state()
    # Re-read contradictions from disk so first load is never stale (Bug 2 fix).
    state.contradictions = _load_contradictions(state.project_root)
    # Health data (reuse health logic)
    active = [d for d in state.decisions if d.status.value == "active"]
    actionable = [c for c in state.contradictions if c.is_actionable]
    total = len(state.decisions)
    coherence = 1.0 - (len(actionable) / max(total, 1)) if total else 1.0

    # Top contradictions (critical first)
    sorted_contras = sorted(
        [c for c in state.contradictions if c.status.value == "unresolved"],
        key=lambda c: (0 if c.verdict.value == "contradiction" else 1, -c.confidence),
    )

    # Agent drift - find highest drift from trajectory alerts
    highest_drift: dict[str, Any] = {"agent": "none", "score": 0.0}
    for alert in state._trajectory_alerts:
        score = alert.get("drift_score", alert.get("score", 0.0))
        if score > highest_drift["score"]:
            highest_drift = {"agent": alert.get("agent_id", "unknown"), "score": score}

    # LLM anomaly count
    anomalous_llm = sum(
        1 for e in state._activity_timeline
        if e.get("action_type") == "llm_call" and e.get("severity") in ("warning", "critical")
    )

    # Golden signals summary
    signals_summary = []
    signal_types = ["file_changes", "dependency_mutations", "config_sensitivity",
                    "scope_creep", "intent_drift", "pattern_violations", "llm_anomalies"]
    for st in signal_types:
        count = sum(1 for s in state._signals if getattr(s, 'signal_type', s.get('type', '')) == st) if state._signals else 0
        signals_summary.append({"signal": st, "status": "green" if count == 0 else ("red" if count > 3 else "yellow"), "count": count})

    # Assumption stats
    proposed_assumptions = sum(1 for a in state._assumptions if getattr(a, 'status', None) and a.status.value == "proposed")

    return {
        "health": {
            "coherence_score": round(coherence, 3),
            "status": "healthy" if coherence >= 0.8 else ("degraded" if coherence >= 0.5 else "critical"),
            "total_decisions": total,
            "active_decisions": len(active),
            "open_contradictions": len(actionable),
            "highest_drift": highest_drift,
            "anomalous_llm_calls": anomalous_llm,
            "pending_assumptions": proposed_assumptions,
        },
        "triage": {
            "total": len(sorted_contras),
            "contradictions": [_serialize_contradiction(c) for c in sorted_contras[:5]],
        },
        "signals": signals_summary,
    }


@app.get("/api/specs")
async def get_specs() -> dict[str, Any]:
    """Return living specifications and coverage data."""
    state = get_state()
    # Load specs from .smm/specs/ if they exist
    specs_dir = state.project_root / ".smm" / "specs"
    specs = []
    coverage_reports = []

    if specs_dir.is_dir():
        from vt_protocol.dashboard.specs import Specification, compute_coverage, extract_requirements

        for f in sorted(specs_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                spec = Specification(
                    id=data.get("id", f.stem),
                    title=data.get("title", f.stem),
                    raw_text=data.get("raw_text", ""),
                )
                # Rebuild requirements from raw text if needed
                if data.get("requirements"):
                    from vt_protocol.dashboard.specs import Requirement
                    spec.requirements = [Requirement(**r) for r in data["requirements"]]
                elif spec.raw_text:
                    extracted = extract_requirements(spec.raw_text, title=spec.title)
                    spec.requirements = extracted.requirements

                specs.append(spec.to_dict())

                # Compute coverage against decisions
                report = compute_coverage(spec, state.decisions)
                coverage_reports.append(report.to_dict())
            except Exception:
                logger.debug("Failed to load spec %s", f, exc_info=True)

    # Aggregate coverage
    total_reqs = sum(len(s.get("requirements", [])) for s in specs)
    implemented = sum(r.get("implemented", 0) for r in coverage_reports)
    coverage_pct = round((implemented / total_reqs * 100) if total_reqs else 0, 1)

    return {
        "total_specs": len(specs),
        "total_requirements": total_reqs,
        "coverage_percent": coverage_pct,
        "implemented": implemented,
        "specs": specs,
        "coverage_reports": coverage_reports,
    }


@app.get("/api/contracts")
async def get_contracts() -> dict[str, Any]:
    """Return API contract analysis."""
    state = get_state()
    # Scan Python files in the project for API endpoints
    from vt_protocol.dashboard.contracts import analyze_contracts

    sources: list[tuple[str, str, str]] = []
    src_dir = state.project_root / "src"
    if not src_dir.is_dir():
        src_dir = state.project_root

    # Scan for Python files with route decorators (limit to avoid perf issues)
    count = 0
    for py_file in src_dir.rglob("*.py"):
        if count >= 50:
            break
        if any(part.startswith('.') or part in ('__pycache__', 'node_modules', '.venv', 'test')
               for part in py_file.parts):
            continue
        try:
            content = py_file.read_text(errors="replace")
            if "@app." in content or "@router." in content or "@blueprint" in content:
                rel = str(py_file.relative_to(state.project_root))
                service_name = py_file.stem
                sources.append((content, service_name, rel))
                count += 1
        except Exception:
            continue

    report = analyze_contracts(sources) if sources else None

    if report:
        return report.to_dict()
    return {
        "total_endpoints": 0,
        "violation_count": 0,
        "consistency_score": 1.0,
        "services": [],
        "violations": [],
    }


@app.get("/api/persona")
async def get_persona() -> dict[str, Any]:
    """Return persona-based routing configuration."""
    state = get_state()
    try:
        config = load_governance_config(state.project_root)
        # Check for persona setting in governance config
        persona = "tech-lead"  # default
        if hasattr(config, 'persona'):
            persona = config.persona
    except Exception:
        persona = "tech-lead"

    routing = {
        "tech-lead": {"landing": "home", "sidebar_order": ["home", "lattice", "axiom", "alignment", "governance"]},
        "ciso": {"landing": "governance", "sidebar_order": ["governance", "home", "lattice", "axiom", "alignment"]},
        "pm": {"landing": "alignment", "sidebar_order": ["alignment", "home", "axiom", "lattice", "governance"]},
        "qa": {"landing": "alignment", "sidebar_order": ["alignment", "lattice", "home", "axiom", "governance"]},
    }

    return {
        "persona": persona,
        "routing": routing.get(persona, routing["tech-lead"]),
        "available_personas": list(routing.keys()),
    }


# ---------------------------------------------------------------------------
# WebSocket for live updates
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket for live dashboard updates."""
    state = get_state()
    await websocket.accept()
    state._ws_clients.append(websocket)
    try:
        while True:
            # Keep connection alive, handle incoming messages
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif data == "refresh":
                state.load()
                await websocket.send_text(json.dumps({"type": "refreshed"}))
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in state._ws_clients:
            state._ws_clients.remove(websocket)


# Background task: watch events.jsonl for new entries and push via WebSocket
_events_watch_task: asyncio.Task | None = None
_last_events_size: int = 0


async def _watch_events_jsonl() -> None:
    """Poll events.jsonl for new entries and broadcast via WebSocket."""
    global _last_events_size
    state = get_state()
    events_path = state.project_root / ".smm" / "traces" / "events.jsonl"

    while True:
        await asyncio.sleep(2)
        try:
            if not events_path.exists():
                continue
            current_size = events_path.stat().st_size
            if current_size <= _last_events_size:
                continue

            # Read new lines
            content = events_path.read_text()
            lines = content.splitlines()
            # Count lines we've already seen (approximate by size)
            new_lines = []
            seen_size = 0
            for line in lines:
                seen_size += len(line.encode("utf-8")) + 1  # +1 for newline
                if seen_size > _last_events_size:
                    line = line.strip()
                    if line:
                        try:
                            new_lines.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

            _last_events_size = current_size

            for event in new_lines:
                event_type = event.get("type", "event")
                await state.broadcast(f"trace_{event_type}", event)
        except Exception:
            pass


@app.on_event("startup")
async def _start_events_watcher() -> None:
    global _events_watch_task, _last_events_size
    state = get_state()
    events_path = state.project_root / ".smm" / "traces" / "events.jsonl"
    if events_path.exists():
        _last_events_size = events_path.stat().st_size
    _events_watch_task = asyncio.create_task(_watch_events_jsonl())


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_decision(d: Decision) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "title": d.title,
        "content": d.content,
        "rationale": d.rationale,
        "status": d.status.value,
        "decision_type": d.decision_type.value,
        "dimensions": [dim.value for dim in d.dimensions],
        "confidence": d.confidence,
        "made_by": d.made_by,
        "source_type": d.source_type.value,
        "created_at": d.created_at.isoformat(),
        "valid": d.valid,
        "alternatives": d.alternatives,
        "constraints": d.constraints,
    }


def _serialize_contradiction(c: Contradiction) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "decision_a_id": str(c.decision_a_id),
        "decision_b_id": str(c.decision_b_id),
        "decision_a_title": c.decision_a_title,
        "decision_b_title": c.decision_b_title,
        "verdict": c.verdict.value,
        "reasoning": c.reasoning,
        "evidence_a": c.evidence_a,
        "evidence_b": c.evidence_b,
        "shared_dimensions": [d.value for d in c.shared_dimensions],
        "confidence": c.confidence,
        "status": c.status.value,
        "resolved_by": c.resolved_by,
        "resolution_note": c.resolution_note,
        "detected_at": c.detected_at.isoformat(),
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "is_actionable": c.is_actionable,
    }


# ---------------------------------------------------------------------------
# File I/O helpers (reads .smm/ directory structure)
# ---------------------------------------------------------------------------


def _load_decisions(root: Path) -> list[Decision]:
    """Load decisions from .smm/decisions/*.json."""
    decisions_dir = root / ".smm" / "decisions"
    if not decisions_dir.is_dir():
        return []
    decisions = []
    for fp in sorted(decisions_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text())
            decisions.append(Decision(**data))
        except Exception:
            logger.debug("Failed to load decision from %s", fp, exc_info=True)
    return decisions


def _load_contradictions(root: Path) -> list[Contradiction]:
    """Load contradictions from .smm/contradictions/*.json.

    Deduplicates by ``id`` field so that multiple files for the same
    contradiction (e.g. ``contradiction-abc12345.json`` and ``abc12345.json``)
    don't cause phantom unresolved entries in the dashboard.
    """
    contradictions_dir = root / ".smm" / "contradictions"
    if not contradictions_dir.is_dir():
        return []
    seen_ids: dict[str, Contradiction] = {}
    for fp in sorted(contradictions_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text())
            c = Contradiction(**data)
            cid = str(c.id)
            if cid not in seen_ids:
                seen_ids[cid] = c
            else:
                # Keep the one that's resolved, or the latest
                existing = seen_ids[cid]
                if c.status == ContradictionStatus.RESOLVED and existing.status != ContradictionStatus.RESOLVED:
                    seen_ids[cid] = c
        except Exception:
            logger.debug("Failed to load contradiction from %s", fp, exc_info=True)
    return list(seen_ids.values())


def _load_sessions(root: Path) -> list[dict[str, Any]]:
    """Load session records from .smm/sessions/*.json."""
    sessions_dir = root / ".smm" / "sessions"
    if not sessions_dir.is_dir():
        return []
    sessions = []
    for fp in sorted(sessions_dir.glob("*.json"), reverse=True)[:50]:
        try:
            sessions.append(json.loads(fp.read_text()))
        except Exception:
            logger.debug("Failed to load session from %s", fp, exc_info=True)
    return sessions


def _get_raw_entry_jsons(tree: MerkleTree, *, limit: int, offset: int) -> list[str]:
    """Fetch raw stored JSON strings from the Merkle tree's SQLite backend."""
    try:
        rows = tree._conn.execute(
            "SELECT entry_json FROM leaves ORDER BY idx LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [row[0] for row in rows]
    except Exception:
        return []


def _save_contradiction(root: Path, c: Contradiction) -> None:
    """Persist a contradiction back to .smm/contradictions/.

    Finds ALL existing files with matching ``id`` field, writes the
    canonical file (contradiction-{id[:8]}.json), and deletes any duplicates.
    """
    contradictions_dir = root / ".smm" / "contradictions"
    contradictions_dir.mkdir(parents=True, exist_ok=True)

    target_id = str(c.id)
    canonical_name = f"contradiction-{target_id[:8]}.json"
    content = c.model_dump_json(indent=2)
    matched_files: list[Path] = []

    for fp in contradictions_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text())
            if data.get("id") == target_id:
                matched_files.append(fp)
        except Exception:
            continue

    # Write canonical file, delete all duplicates
    canonical_path = contradictions_dir / canonical_name
    canonical_path.write_text(content)
    for fp in matched_files:
        if fp != canonical_path:
            logger.debug("_save_contradiction: removing duplicate %s (id=%s)", fp.name, target_id)
            fp.unlink()


def _delete_contradiction_lock(project_root: Path) -> None:
    """Delete .smm/contradiction.lock so the agent can resume writing.

    Called after a human resolves a contradiction via dashboard.
    This transitions the state machine from CONTRADICTION_DETECTED → RESOLVED → CLEAN.
    """
    lock_file = project_root / ".smm" / "contradiction.lock"
    try:
        lock_file.unlink(missing_ok=True)
    except Exception:
        logger.debug("Failed to delete contradiction.lock", exc_info=True)


def _auto_apply_rules(state: "DashboardState") -> None:
    """Regenerate CLAUDE.md and .cursor/rules/ after a resolution (Bug 4 fix)."""
    try:
        from vt_protocol.config import load_governance_config
        from vt_protocol.prevention.rulesync import sync_rules

        config = load_governance_config(state.project_root)
        active = [d for d in state.decisions if d.valid]
        sync_rules(active, state.project_root, config)
    except Exception:
        logger.debug("Auto apply rules failed", exc_info=True)


def _estimate_llm_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate LLM cost in USD from model name and token counts (Bug 5 fix)."""
    m = model.lower()
    if "opus" in m:
        rate_in, rate_out = 15.0, 75.0
    elif "haiku" in m:
        rate_in, rate_out = 0.80, 4.0
    else:  # sonnet / default
        rate_in, rate_out = 3.0, 15.0
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


def _log_resolution_trace_event(root: Path, c: Contradiction, winner_id: str, winner_title: str) -> None:
    """Log contradiction resolution to .smm/traces/events.jsonl."""
    traces_dir = root / ".smm" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": "contradiction_resolved",
        "action": "resolve_contradiction",
        "file": "",
        "result": "pass",
        "reason": f"Winner: {winner_title}. {c.decision_a_title} vs {c.decision_b_title}",
        "agent": c.resolved_by or "dashboard",
        "contradiction_id": str(c.id),
        "winner_id": winner_id,
    }
    try:
        with open(traces_dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        logger.debug("Failed to write resolution trace event", exc_info=True)


def _generate_imperative_rule(assumption: Any) -> str:
    """Generate a clean, imperative constraint rule from a validated assumption.

    Instead of dumping raw code evidence, produces a human-readable rule
    that Claude can follow when writing code.
    """
    pattern_id = getattr(assumption, "pattern_id", "")
    evidence = assumption.code_evidence[0] if assumption.code_evidence else None
    snippet = evidence.snippet if evidence else ""
    file_path = evidence.file if evidence else ""
    rationale = assumption.answer_rationale or "Confirmed by domain expert."

    # Extract contextual names from evidence where possible
    import re

    if pattern_id == "single_source_write":
        # Try to extract function and table names from summary/evidence
        func_match = re.search(r'(\w+)\(\)', assumption.summary)
        table_match = re.search(r"['\"](\w+)['\"]", snippet) or re.search(r'to\s+(\w+)', assumption.summary)
        func_name = func_match.group(1) if func_match else "the designated function"
        table_name = table_match.group(1) if table_match else "this table"
        return (
            f"Only {func_name}() may write to {table_name}. "
            f"Do not introduce direct writes from other modules without explicit approval.\n\n"
            f"Rationale: {rationale}"
        )

    elif pattern_id == "env_no_fallback":
        var_match = re.search(r"environ\[?['\"](\w+)", snippet) or re.search(r'(\w+)', assumption.summary)
        var_name = var_match.group(1) if var_match else "this environment variable"
        return (
            f"Environment variable {var_name} must always be set in deployment. "
            f"Do not access it without a fallback or validation at startup.\n\n"
            f"Rationale: {rationale}"
        )

    elif pattern_id == "single_table_query":
        table_match = re.search(r"(\w+)", assumption.summary)
        table_name = table_match.group(1) if table_match else "this table"
        return (
            f"Queries to {table_name} do not require JOINs in the current architecture. "
            f"Do not add JOINs without verifying the data model has changed.\n\n"
            f"Rationale: {rationale}"
        )

    elif pattern_id == "no_auth_check":
        return (
            f"Authentication is not required for this endpoint in the current design. "
            f"Do not add auth middleware without reviewing access requirements.\n\n"
            f"Rationale: {rationale}"
        )

    elif pattern_id == "hardcoded_timeout":
        return (
            f"The timeout value in this code is intentionally hardcoded. "
            f"Do not extract it to configuration without understanding the failure mode.\n\n"
            f"Rationale: {rationale}"
        )

    # Generic fallback — still imperative, not raw evidence
    category_label = assumption.category.value.replace("_", " ")
    return (
        f"{assumption.summary}\n\n"
        f"This is a validated {category_label} constraint. "
        f"Do not change this behavior without explicit approval.\n\n"
        f"Rationale: {rationale}"
    )


def _create_decision_from_assumption(state: "DashboardState", assumption: Any) -> bool:
    """Create a new decision from a validated assumption so it becomes a CLAUDE.md rule.

    Returns True if the decision was created successfully.
    """
    title = f"Validated: {assumption.summary[:80]}"
    content = _generate_imperative_rule(assumption)

    # Map assumption category to a dimension
    _CATEGORY_TO_DIMENSION = {
        "data_scope": Dimension.DATABASE,
        "temporal": Dimension.STATE_MANAGEMENT,
        "access": Dimension.AUTH,
        "completeness": Dimension.TESTING,
        "configuration": Dimension.DEPLOYMENT,
        "framework": Dimension.API_STYLE,
    }
    dimension = _CATEGORY_TO_DIMENSION.get(assumption.category.value, Dimension.API_STYLE)

    decision = Decision(
        title=title,
        content=content,
        rationale=f"Validated assumption: {assumption.summary}",
        dimensions=[dimension],
        decision_type=DecisionType.ARCHITECTURAL,
        made_by=assumption.resolved_by or "dashboard-user",
        project="",
        source_type=SourceType.AGENT,
    )

    # Save to disk
    decisions_dir = state.project_root / ".smm" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{str(decision.id)[:8]}.json"
    (decisions_dir / filename).write_text(decision.model_dump_json(indent=2))

    # Add to in-memory state
    state.decisions.append(decision)
    logger.info("Created decision from validated assumption: %s", title)
    return True


def _log_assumption_trace_event(root: Path, assumption: Any) -> None:
    """Log assumption resolution to .smm/traces/events.jsonl."""
    traces_dir = root / ".smm" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": f"assumption_{assumption.status.value}",
        "action": "resolve_assumption",
        "file": "",
        "result": "pass" if assumption.status.value == "validated" else "info",
        "reason": assumption.summary,
        "agent": assumption.resolved_by or "dashboard",
    }
    try:
        with open(traces_dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        logger.debug("Failed to write assumption trace event", exc_info=True)


def _save_decision(root: Path, d: Decision) -> None:
    """Persist a decision back to .smm/decisions/.

    Finds the existing file by reading each JSON and matching the ``id``
    field, then overwrites in place.  This works regardless of filename
    convention (``001-database-relational.json``, ``<uuid>.json``, etc.).
    """
    decisions_dir = root / ".smm" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)

    target_id = str(d.id)
    for fp in decisions_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text())
            if data.get("id") == target_id:
                logger.debug("_save_decision: overwriting %s (id=%s)", fp.name, target_id)
                fp.write_text(d.model_dump_json(indent=2))
                return
        except Exception:
            continue

    # No existing file found — write new
    logger.debug("_save_decision: creating new file for id=%s", target_id)
    filename = f"{str(d.id)[:8]}.json"
    (decisions_dir / filename).write_text(d.model_dump_json(indent=2))

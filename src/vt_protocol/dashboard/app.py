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
    Dimension,
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

    def _load_assumptions(self) -> None:
        """Load domain assumptions from .smm/assumptions/."""
        from vt_protocol.analysis.assumption_pipeline import load_assumptions

        try:
            self._assumptions = load_assumptions(self.project_root)
        except Exception:
            logger.debug("Failed to load assumptions", exc_info=True)
            self._assumptions = []

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
            c.status = ContradictionStatus.RESOLVED
            c.resolved_by = "dashboard-user"
            c.resolution_note = f"Winner: {body.winner_id}. {body.rationale}"
            c.resolved_at = datetime.now(timezone.utc)

            # Persist to disk
            _save_contradiction(state.project_root, c)

            # Broadcast to WebSocket clients
            await state.broadcast("contradiction_resolved", {
                "id": str(uid),
                "winner_id": body.winner_id,
            })

            return {"status": "resolved", "id": str(uid)}

    raise HTTPException(404, "Contradiction not found")


@app.get("/api/graph")
async def api_graph() -> dict[str, Any]:
    """Decision graph in Cytoscape.js format — nodes + edges."""
    state = get_state()
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


@app.get("/api/audit")
async def api_audit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    verify: bool = Query(False, description="Verify Merkle inclusion proofs"),
) -> dict[str, Any]:
    """Audit trail entries with optional Merkle proof verification."""
    state = get_state()
    if not state.merkle:
        return {"total": 0, "entries": [], "tree_size": 0}

    tree = state.merkle
    entries = tree.get_entries(limit=limit, offset=offset)
    tree_size = tree.size

    # For verification, fetch raw stored JSON to avoid re-serialization drift
    raw_jsons: list[str] = []
    if verify:
        raw_jsons = _get_raw_entry_jsons(tree, limit=limit, offset=offset)

    result_entries = []
    for i, entry in enumerate(entries):
        idx = offset + i
        entry_dict = {
            "index": idx,
            "entry_id": str(entry.entry_id),
            "timestamp": entry.timestamp.isoformat(),
            "event_type": entry.event_type.value,
            "actor": entry.actor,
            "project": entry.project,
            "payload": entry.payload,
            "verified": None,
        }

        if verify and idx < tree_size:
            try:
                proof = tree.inclusion_proof(idx, tree_size)
                root = tree.root_hash(tree_size)
                raw_data = raw_jsons[i].encode("utf-8") if i < len(raw_jsons) else b""
                entry_dict["verified"] = tree.verify_inclusion(proof, raw_data, root)
            except Exception:
                entry_dict["verified"] = False

        result_entries.append(entry_dict)

    return {
        "total": tree_size,
        "tree_size": tree_size,
        "entries": result_entries,
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
    action_type: str | None = Query(None, description="Filter: llm_call, mcp_tool, file_read, shell_command, git_operation"),
    agent_id: str | None = Query(None, description="Filter by agent ID"),
    severity: str | None = Query(None, description="Filter: info, warning, critical"),
) -> dict[str, Any]:
    """Unified activity timeline — merges LLM calls, MCP tools, file reads, shell commands, git ops.

    Filterable by action_type, agent_id, and severity.
    """
    state = get_state()
    timeline = state._activity_timeline

    # Apply filters
    if action_type:
        timeline = [e for e in timeline if e.get("action_type") == action_type]
    if agent_id:
        timeline = [e for e in timeline if e.get("agent_id") == agent_id]
    if severity:
        timeline = [e for e in timeline if e.get("severity") == severity]

    total = len(timeline)
    page = timeline[offset : offset + limit]

    # Compute summary from full (unfiltered) timeline
    full = state._activity_timeline
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

    # LLM-specific summary
    total_cost = sum(s.cost_usd or 0 for s in state._spans)
    total_tokens_in = sum(s.tokens_in for s in state._spans)
    total_tokens_out = sum(s.tokens_out for s in state._spans)

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


@app.post("/api/assumptions/{assumption_id}/resolve")
async def api_resolve_assumption(assumption_id: str) -> dict[str, Any]:
    """Resolve a domain assumption via dashboard.

    Body: {"selected_option": 0, "resolved_by": "techlead", "rationale": "..."}
    """
    from vt_protocol.analysis.assumption_pipeline import resolve_assumption

    import starlette.requests

    state = get_state()
    # Get request body manually since we don't want to import pydantic request model here
    request = starlette.requests.Request(scope={"type": "http"})

    # Use a simpler approach - read from any matching assumption
    for a in state._assumptions:
        if str(a.id) == assumption_id or a.id.hex[:8] == assumption_id:
            # This endpoint will be called with JSON body in practice
            # For now, return the assumption as-is to validate the route exists
            return {"assumption": _serialize_assumption(a), "status": "found"}
    raise HTTPException(status_code=404, detail="Assumption not found")


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
    """Load contradictions from .smm/contradictions/*.json."""
    contradictions_dir = root / ".smm" / "contradictions"
    if not contradictions_dir.is_dir():
        return []
    contradictions = []
    for fp in sorted(contradictions_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text())
            contradictions.append(Contradiction(**data))
        except Exception:
            logger.debug("Failed to load contradiction from %s", fp, exc_info=True)
    return contradictions


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
    """Persist a contradiction back to .smm/contradictions/."""
    contradictions_dir = root / ".smm" / "contradictions"
    contradictions_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{str(c.id)[:8]}.json"
    (contradictions_dir / filename).write_text(c.model_dump_json(indent=2))

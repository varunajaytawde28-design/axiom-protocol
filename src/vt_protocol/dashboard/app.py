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
from vt_protocol.decisions.models import (
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


@app.get("/api/sessions")
async def api_sessions() -> dict[str, Any]:
    """Recent agent sessions with decisions captured."""
    state = get_state()
    return {
        "total": len(state.sessions),
        "sessions": state.sessions,
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

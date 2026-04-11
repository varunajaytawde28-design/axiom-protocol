"""CLI commands — vt init / check / apply / dashboard / serve.

From SPEC T2: "smm init / check / apply becomes muscle memory — our
git add / commit / push."
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click

logger = logging.getLogger(__name__)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging")
def main(verbose: bool) -> None:
    """VT Protocol — AI agent governance CLI."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


# ---------------------------------------------------------------------------
# vt init
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
@click.option("--no-hooks", is_flag=True, help="Skip git hook installation")
@click.option("--no-mcp", is_flag=True, help="Skip .mcp.json creation")
def init(path: str, no_hooks: bool, no_mcp: bool) -> None:
    """Initialize VT Protocol governance for a project.

    Creates .smm/ directory, governance.yaml with defaults, scans for
    existing architecture, and installs git hooks.
    """
    from vt_protocol.config import ensure_smm_structure, save_governance_config
    from vt_protocol.decisions.taxonomy import scan_project, scan_to_core_dimensions

    root = Path(path).resolve()
    click.echo(f"Initializing VT Protocol in {root}")

    # 1. Create .smm/ structure
    ensure_smm_structure(root)
    click.echo("  Created .smm/ directory structure")

    # 2. Write governance.yaml (if not present)
    gov_path = root / "governance.yaml"
    if gov_path.exists():
        click.echo("  governance.yaml already exists — skipping")
    else:
        save_governance_config(root)
        click.echo("  Created governance.yaml with defaults")

    # 3. Auto-detect existing architecture
    click.echo("  Scanning project for architectural patterns...")
    matches = scan_project(root)
    if matches:
        dims = scan_to_core_dimensions(root)
        click.echo(f"  Detected {len(matches)} patterns across {len(dims)} dimensions:")
        for m in matches[:10]:
            evidence_str = ", ".join(m.evidence[:3])
            click.echo(f"    - {m.sub_dimension.label}: {evidence_str}")
        if len(matches) > 10:
            click.echo(f"    ... and {len(matches) - 10} more")

        # Write initial decision records
        _write_initial_decisions(root, matches)
    else:
        click.echo("  No architectural patterns detected (empty project?)")

    # 4. Install git hooks
    if not no_hooks and (root / ".git").is_dir():
        from vt_protocol.integrations.git_hooks import install_hooks
        installed = install_hooks(root)
        if installed:
            click.echo(f"  Installed git hooks: {', '.join(installed)}")
        else:
            click.echo("  Git hooks already installed")
    elif no_hooks:
        click.echo("  Skipped git hooks (--no-hooks)")
    else:
        click.echo("  No .git/ directory — skipped git hooks")

    # 5. Create .mcp.json
    if not no_mcp:
        from vt_protocol.integrations.git_hooks import create_mcp_json
        mcp_path = create_mcp_json(root)
        click.echo(f"  Created {mcp_path.name} for MCP auto-discovery")
    else:
        click.echo("  Skipped .mcp.json (--no-mcp)")

    click.echo("")
    click.echo("Done! Next steps:")
    click.echo("  vt check   — review governance status")
    click.echo("  vt apply   — generate agent instruction files")


# ---------------------------------------------------------------------------
# vt check
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
@click.option("--exit-code", is_flag=True, help="Exit 1 on violations (for CI)")
@click.option("--json-output", "json_out", is_flag=True, help="Output as JSON")
def check(path: str, exit_code: bool, json_out: bool) -> None:
    """Check governance status — the terraform plan equivalent.

    Shows decisions, contradictions, and violations without changing anything.
    Exit code 0 = pass, 1 = violations found.
    """
    from vt_protocol.config import find_project_root, load_governance_config

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project (no .smm/ or .git/ found)")
        click.echo("Run 'vt init' first.")
        sys.exit(1)

    config = load_governance_config(root)
    project_name = root.name

    # Load decisions from .smm/decisions/
    decisions = _load_local_decisions(root)
    active = [d for d in decisions if d.valid]

    # Count contradictions (from local contradiction records)
    contradictions = _load_local_contradictions(root)
    actionable = [c for c in contradictions if c.is_actionable]

    if json_out:
        result = {
            "project": project_name,
            "total_decisions": len(decisions),
            "active_decisions": len(active),
            "total_contradictions": len(contradictions),
            "actionable_contradictions": len(actionable),
            "status": "fail" if actionable else "pass",
        }
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"# VT Protocol — Governance Check")
        click.echo(f"")
        click.echo(f"**Project:** {project_name}")
        click.echo(f"**Decisions:** {len(active)} active ({len(decisions)} total)")
        click.echo(f"**Contradictions:** {len(actionable)} actionable ({len(contradictions)} total)")
        click.echo(f"")

        if active:
            click.echo("## Active Decisions")
            click.echo("")
            for d in active[:20]:
                dims = ", ".join(dim.value for dim in d.dimensions)
                click.echo(f"  - {d.title} [{dims}] (confidence: {d.confidence:.0%})")
            if len(active) > 20:
                click.echo(f"  ... and {len(active) - 20} more")
            click.echo("")

        if actionable:
            click.echo("## Actionable Contradictions")
            click.echo("")
            for c in actionable:
                click.echo(f"  - **{c.verdict.value.upper()}:** {c.decision_a_title} vs {c.decision_b_title}")
                click.echo(f"    Reasoning: {c.reasoning[:100]}...")
                click.echo(f"    Confidence: {c.confidence:.0%}")
                click.echo("")

        status = "FAIL" if actionable else "PASS"
        click.echo(f"**Result: {status}**")

    if exit_code and actionable:
        sys.exit(1)


# ---------------------------------------------------------------------------
# vt apply
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
def apply(path: str) -> None:
    """Generate agent instruction files from the decision graph.

    Writes CLAUDE.md, .cursor/rules/, AGENTS.md based on governance config.
    Idempotent — safe to run multiple times.
    """
    from vt_protocol.config import find_project_root, load_governance_config
    from vt_protocol.prevention.rulesync import sync_rules

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    config = load_governance_config(root)
    decisions = _load_local_decisions(root)
    active = [d for d in decisions if d.valid]

    if not active:
        click.echo("No active decisions found. Run 'vt init' to scan your project.")
        return

    result = sync_rules(active, root, config)

    click.echo(f"Generated {len(result.files_written)} files:")
    for f in result.files_written:
        try:
            rel = f.relative_to(root)
        except ValueError:
            rel = f
        click.echo(f"  - {rel}")
    click.echo("")
    click.echo(
        f"Tiers: {result.always_count} always / "
        f"{result.auto_count} auto / "
        f"{result.on_demand_count} on-demand"
    )


# ---------------------------------------------------------------------------
# vt dashboard
# ---------------------------------------------------------------------------


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind")
@click.option("--port", default=7842, help="Port to bind")
def dashboard(host: str, port: int) -> None:
    """Launch the FastAPI web dashboard."""
    import uvicorn

    click.echo(f"Starting VT Protocol dashboard on http://{host}:{port}")
    click.echo("Press Ctrl+C to stop.")

    try:
        from vt_protocol.dashboard.app import app
        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError:
        # Minimal fallback if dashboard app isn't built yet
        from fastapi import FastAPI
        app = FastAPI(title="VT Protocol Dashboard")

        @app.get("/")
        async def index():  # type: ignore[no-untyped-def]
            return {"status": "ok", "message": "VT Protocol dashboard — coming soon"}

        @app.get("/health")
        async def health():  # type: ignore[no-untyped-def]
            return {"status": "healthy"}

        uvicorn.run(app, host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# vt serve
# ---------------------------------------------------------------------------


@main.command()
@click.option("--stdio", is_flag=True, help="Use stdio transport (for MCP clients)")
@click.option("--host", default="127.0.0.1", help="Host for HTTP transport")
@click.option("--port", default=7843, help="Port for HTTP transport")
def serve(stdio: bool, host: str, port: int) -> None:
    """Start the MCP server for AI agent governance."""
    from vt_protocol.mcp.server import mcp

    if stdio:
        click.echo("Starting VT Protocol MCP server (stdio mode)")
        mcp.run(transport="stdio")
    else:
        click.echo(f"Starting VT Protocol MCP server on http://{host}:{port}")
        mcp.run(transport="streamable-http", host=host, port=port)


# ---------------------------------------------------------------------------
# vt audit-commit (internal — called by post-commit hook)
# ---------------------------------------------------------------------------


@main.command(name="audit-commit", hidden=True)
@click.option("--hash", "commit_hash", default="", help="Commit hash")
@click.option("--message", default="", help="Commit message")
@click.option("--author", default="", help="Commit author")
def audit_commit(commit_hash: str, message: str, author: str) -> None:
    """Append a commit event to the Merkle tree audit log (internal)."""
    from vt_protocol.audit.merkle import MerkleTree
    from vt_protocol.config import find_project_root
    from vt_protocol.decisions.models import AuditEntry, AuditEventType

    try:
        root = find_project_root()
    except FileNotFoundError:
        return

    audit_db = root / ".smm" / "audit" / "audit.db"
    audit_db.parent.mkdir(parents=True, exist_ok=True)

    tree = MerkleTree(audit_db)
    entry = AuditEntry(
        event_type=AuditEventType.SESSION_COMPLETED,
        actor=author or "git",
        project=root.name,
        payload={
            "commit_hash": commit_hash,
            "message": message[:200],
            "author": author,
        },
    )
    tree.append(entry)
    tree.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_initial_decisions(root: Path, matches: list) -> None:
    """Write auto-detected dimensions as initial decision YAML files."""
    decisions_dir = root / ".smm" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)

    from vt_protocol.decisions.models import Decision, DecisionType, Dimension, SourceType

    written = 0
    # Group by core dimension to avoid duplicate records
    seen_dims: set[str] = set()
    for m in matches:
        dim_key = m.core_dimension.value
        if dim_key in seen_dims:
            continue
        seen_dims.add(dim_key)

        evidence_str = ", ".join(m.evidence[:5])
        decision = Decision(
            title=f"Detected: {m.sub_dimension.label}",
            content=f"Auto-detected from project scan. Evidence: {evidence_str}",
            rationale="Scanned from existing project structure",
            decision_type=DecisionType.TECHNICAL,
            dimensions=[m.core_dimension],
            made_by="vt-init",
            project=root.name,
            source_type=SourceType.SCAN,
        )

        # Write as JSON (simpler than YAML for structured data)
        filename = f"{written + 1:03d}-{dim_key}.json"
        filepath = decisions_dir / filename
        if not filepath.exists():
            filepath.write_text(decision.model_dump_json(indent=2))
            written += 1

    if written:
        click.echo(f"  Wrote {written} initial decision records to .smm/decisions/")


def _load_local_decisions(root: Path) -> list:
    """Load decision records from .smm/decisions/*.json."""
    from vt_protocol.decisions.models import Decision

    decisions_dir = root / ".smm" / "decisions"
    if not decisions_dir.is_dir():
        return []

    decisions = []
    for filepath in sorted(decisions_dir.glob("*.json")):
        try:
            data = json.loads(filepath.read_text())
            decisions.append(Decision(**data))
        except Exception:
            logger.debug("Failed to load decision from %s", filepath, exc_info=True)

    return decisions


def _load_local_contradictions(root: Path) -> list:
    """Load contradiction records from .smm/contradictions/*.json."""
    from vt_protocol.decisions.models import Contradiction

    contradictions_dir = root / ".smm" / "contradictions"
    if not contradictions_dir.is_dir():
        return []

    contradictions = []
    for filepath in sorted(contradictions_dir.glob("*.json")):
        try:
            data = json.loads(filepath.read_text())
            contradictions.append(Contradiction(**data))
        except Exception:
            logger.debug("Failed to load contradiction from %s", filepath, exc_info=True)

    return contradictions

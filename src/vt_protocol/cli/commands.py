"""CLI commands — vt init / check / apply / dashboard / serve.

From SPEC T2: "smm init / check / apply becomes muscle memory — our
git add / commit / push."
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

logger = logging.getLogger(__name__)


def _log_trace_event(root: Path, event_type: str, action: str, result: str, reason: str = "") -> None:
    """Append a governance event to .smm/traces/events.jsonl."""
    traces_dir = root / ".smm" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "type": event_type,
        "action": action,
        "file": "",
        "result": result,
        "reason": reason,
        "agent": "cli",
    }
    try:
        with open(traces_dir / "events.jsonl", "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        logger.debug("Failed to write trace event", exc_info=True)


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
@click.option("--no-llm-prompt", is_flag=True, help="Skip LLM provider selection (use defaults)")
@click.option("--no-agent-prompt", is_flag=True, help="Skip agent onboarding wizard")
def init(path: str, no_hooks: bool, no_mcp: bool, no_llm_prompt: bool, no_agent_prompt: bool) -> None:
    """Initialize VT Protocol governance for a project.

    Creates .smm/ directory, governance.yaml with defaults, scans for
    existing architecture, and installs git hooks.
    """
    from vt_protocol.config import (
        ensure_smm_structure,
        load_governance_config,
        save_governance_config,
    )
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

    # 2b. LLM provider selection
    if not no_llm_prompt:
        config = load_governance_config(root)
        model_config = _run_llm_provider_wizard()
        if model_config is not None:
            config.model = model_config
            save_governance_config(root, config)

    # 2c. Agent onboarding
    if not no_agent_prompt:
        config = load_governance_config(root)
        new_agents = _run_agent_onboarding_wizard()
        if new_agents:
            config.agents.update(new_agents)
            save_governance_config(root, config)

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

    # 6. Install Claude Code PreToolUse hook
    if not no_hooks:
        from vt_protocol.integrations.git_hooks import install_claude_code_hook
        if install_claude_code_hook(root):
            click.echo("  Installed Claude Code PreToolUse hook (.claude/hooks/)")
        else:
            click.echo("  Claude Code hook already installed")

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
@click.option("--resolve", "do_resolve", is_flag=True, help="Interactively resolve contradictions after detection")
def check(path: str, exit_code: bool, json_out: bool, do_resolve: bool) -> None:
    """Check governance status — the terraform plan equivalent.

    Shows decisions, contradictions, and violations without changing anything.
    Exit code 0 = pass, 1 = violations found.

    With --resolve, enters interactive mode to resolve detected contradictions.
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

    # Detect contradictions across decisions sharing a dimension
    detected = _detect_contradictions(active, config)
    if detected:
        _save_contradictions(root, detected)

    # Load all contradiction records (previously saved + newly detected)
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

        # Assumptions section
        from vt_protocol.analysis.assumption_pipeline import (
            compute_metrics,
            format_metrics_summary,
            load_assumptions,
        )

        assumptions = load_assumptions(root)
        pending = [a for a in assumptions if a.is_actionable]
        if assumptions:
            click.echo("## Domain Assumptions")
            click.echo("")
            click.echo(f"  {len(assumptions)} detected, {len(pending)} require resolution")
            if pending:
                click.echo("")
                for a in pending[:5]:
                    click.echo(f"  - [{a.severity.upper()}] {a.summary}")
                if len(pending) > 5:
                    click.echo(f"  ... and {len(pending) - 5} more")

            # Show metrics if there are resolved assumptions
            try:
                metrics = compute_metrics(root, assumptions)
                summary = format_metrics_summary(metrics)
                if summary:
                    click.echo(f"\n  Metrics: {summary}")
            except Exception:
                pass
            click.echo("")

        status = "FAIL" if actionable else "PASS"
        click.echo(f"**Result: {status}**")

    _log_trace_event(
        root, "cli", "check",
        "fail" if actionable else "pass",
        f"{len(actionable)} actionable contradictions" if actionable else "No violations",
    )

    if do_resolve and actionable:
        _resolve_contradictions_interactive(root, actionable, decisions)
        return

    if exit_code and actionable:
        sys.exit(1)


# ---------------------------------------------------------------------------
# vt resolve
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
def resolve(path: str) -> None:
    """Interactively resolve unresolved contradictions.

    Loads unresolved contradictions from .smm/contradictions/, presents
    an interactive menu for each, and auto-runs vt apply after all
    resolutions to regenerate CLAUDE.md and agent files.
    """
    from vt_protocol.config import find_project_root

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    contradictions = _load_local_contradictions(root)
    decisions = _load_local_decisions(root)

    from vt_protocol.decisions.models import ContradictionStatus

    unresolved = [
        c for c in contradictions
        if c.status == ContradictionStatus.UNRESOLVED
    ]

    if not unresolved:
        click.echo("No unresolved contradictions. All clear!")
        return

    _resolve_contradictions_interactive(root, unresolved, decisions)


def _resolve_contradictions_interactive(
    root: Path,
    contradictions: list,
    decisions: list,
) -> None:
    """Interactive contradiction resolution loop.

    For each contradiction, displays decision titles and lets the user
    choose: keep A, keep B, or accept exception.  After all resolutions,
    auto-runs vt apply to regenerate agent files.
    """
    from vt_protocol.decisions.models import (
        ContradictionStatus,
        DecisionStatus,
    )
    from vt_protocol.decisions.resolution import (
        ResolutionType,
        apply_resolution,
    )

    # Build id→decision lookup
    decision_map = {str(d.id): d for d in decisions}

    resolved_count = 0
    deferred_count = 0

    click.echo(f"\n{len(contradictions)} contradiction(s) to resolve:\n")

    for i, c in enumerate(contradictions):
        title_a = c.decision_a_title
        title_b = c.decision_b_title
        dims = ", ".join(d.value for d in c.shared_dimensions) if c.shared_dimensions else "unknown"

        click.echo(f"--- Contradiction {i + 1}/{len(contradictions)} ---")
        click.echo(f"  {c.verdict.value.upper()} on [{dims}]")
        click.echo(f"  A: {title_a}")
        click.echo(f"  B: {title_b}")
        click.echo(f"  Reasoning: {c.reasoning[:120]}")
        click.echo("")
        click.echo(f"  A) Keep '{title_a[:50]}' — supersede B")
        click.echo(f"  B) Keep '{title_b[:50]}' — supersede A")
        click.echo(f"  C) Accept exception — keep both")
        click.echo(f"  D) Defer — unlock agent, decide later")
        click.echo(f"  S) Skip")
        click.echo("")

        choice = click.prompt("Choose", type=click.Choice(["A", "B", "C", "D", "S", "a", "b", "c", "d", "s"]), default="S")
        choice = choice.upper()

        if choice == "S":
            click.echo("  → Skipped\n")
            continue

        if choice == "D":
            apply_resolution(
                c, ResolutionType.DEFER,
                rationale="Deferred via CLI",
                actor="cli-user",
                decisions=decisions,
            )
            _save_contradiction_file(root, c)
            click.echo("  → Deferred. Agent unblocked. Contradiction remains open.\n")
            deferred_count += 1
            continue

        action_map = {
            "A": ResolutionType.PICK_A,
            "B": ResolutionType.PICK_B,
            "C": ResolutionType.ACCEPT_EXCEPTION,
        }
        action = action_map[choice]

        result = apply_resolution(
            c, action,
            rationale="Resolved via CLI",
            actor="cli-user",
            decisions=decisions,
        )

        # Persist contradiction status
        _save_contradiction_file(root, c)

        # Persist superseded decision to disk
        superseded_id = result.get("superseded_id")
        if superseded_id and superseded_id in decision_map:
            loser = decision_map[superseded_id]
            _save_decision(root, loser)

        changes = result.get("changes", [])
        for change in changes:
            click.echo(f"  → {change}")
        click.echo("")
        resolved_count += 1

    if resolved_count > 0:
        click.echo(f"Resolved {resolved_count} contradiction(s). Running vt apply...\n")
        _run_apply(root)
        _delete_contradiction_lock(root)
    elif deferred_count > 0:
        click.echo(f"Deferred {deferred_count} contradiction(s). Agent unblocked.")
        _delete_contradiction_lock(root)
    else:
        click.echo("No contradictions resolved.")


def _run_apply(root: Path) -> None:
    """Run the apply pipeline to regenerate agent files."""
    from vt_protocol.config import load_governance_config
    from vt_protocol.prevention.rulesync import sync_rules

    config = load_governance_config(root)
    decisions = _load_local_decisions(root)
    active = [d for d in decisions if d.valid]

    if not active:
        click.echo("No active decisions — skipping apply.")
        return

    result = sync_rules(active, root, config)
    click.echo(f"Generated {len(result.files_written)} files:")
    for f in result.files_written:
        try:
            rel = f.relative_to(root)
        except ValueError:
            rel = f
        click.echo(f"  - {rel}")
    _log_trace_event(root, "cli", "apply", "pass", f"{len(result.files_written)} files (post-resolve)")


def _delete_contradiction_lock(root: Path) -> None:
    """Delete .smm/contradiction.lock so the agent can resume writing.

    Called after a human resolves contradictions via CLI.
    Transitions the state machine from CONTRADICTION_DETECTED → CLEAN.
    """
    lock_file = root / ".smm" / "contradiction.lock"
    try:
        lock_file.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# vt infer
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
def infer(path: str) -> None:
    """Re-scan project for architectural patterns and create new decisions.

    Compares newly detected patterns against existing decisions in
    .smm/decisions/ and creates decision records for any new patterns.
    """
    from vt_protocol.config import find_project_root
    from vt_protocol.decisions.taxonomy import scan_project

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    click.echo("Scanning project for architectural patterns...")
    matches = scan_project(root)

    if not matches:
        click.echo("No architectural patterns detected.")
        return

    # Load existing decisions to find what's already tracked
    existing_decisions = _load_local_decisions(root)
    existing_sub_ids = _extract_existing_sub_ids(existing_decisions)

    # Filter to only new patterns
    new_matches = [m for m in matches if m.sub_dimension.id not in existing_sub_ids]

    if not new_matches:
        click.echo(f"Found {len(matches)} patterns — all already tracked in decisions.")
        return

    click.echo(f"Found {len(new_matches)} new pattern(s) ({len(matches)} total):")
    for m in new_matches:
        evidence_str = ", ".join(m.evidence[:3])
        click.echo(f"  - {m.sub_dimension.label}: {evidence_str}")

    _write_initial_decisions(root, new_matches)
    _log_trace_event(root, "cli", "infer", "pass", f"{len(new_matches)} new patterns detected")
    click.echo("")
    click.echo("Run 'vt check' to detect any contradictions with existing decisions.")


# ---------------------------------------------------------------------------
# vt assumptions
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
@click.option("--scan", is_flag=True, help="Run fresh assumption scan")
@click.option("--resolve", is_flag=True, help="Interactive resolution mode")
@click.option("--status", type=click.Choice(["detected", "proposed", "validated", "rejected", "deferred"]), default=None)
@click.option("--json-output", "json_out", is_flag=True, help="Output as JSON")
def assumptions(path: str, scan: bool, resolve: bool, status: str | None, json_out: bool) -> None:
    """Manage domain assumptions detected in code.

    Shows implicit assumptions that AI agents embedded in code,
    generates clarifying questions, and routes to human review.
    """
    from vt_protocol.analysis.assumption_pipeline import (
        load_assumptions,
        resolve_assumption,
        run_assumption_pipeline,
        save_assumptions,
    )
    from vt_protocol.config import find_project_root

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    def _get_assumption_tier(a):
        """Get tier for an assumption from the pattern registry."""
        from vt_protocol.analysis.assumptions import PATTERNS
        for p in PATTERNS:
            if p.pattern_id == a.pattern_id:
                return p.tier
        return "implementation"

    if scan:
        click.echo("Scanning for domain assumptions...")
        result = run_assumption_pipeline(root)
        click.echo(f"  Detected:   {result.detected}")
        click.echo(f"  New:        {result.new}")
        click.echo(f"  Deduped:    {result.deduped}")
        click.echo(f"  Below threshold: {result.below_threshold}")
        if result.shadowed:
            click.echo(f"  Shadowed:   {result.shadowed} (auto-disabled patterns)")
        if result.clusters_formed:
            click.echo(
                f"\n  Assumptions: {result.detected} detected"
                f" → {result.clusters_formed} clusters"
                f" ({result.cluster_compression:.1f}x compression)"
            )
            # Show tier breakdown
            arch = sum(1 for a in result.assumptions if _get_assumption_tier(a) == "architectural")
            impl = len(result.assumptions) - arch
            high = sum(1 for a in result.assumptions if a.severity in ("high", "critical"))
            click.echo(f"  → {high} HIGH priority | {arch} architectural, {impl} implementation")
        if result.assumptions:
            save_assumptions(root, result.assumptions)
            click.echo(f"\nSaved {len(result.assumptions)} assumptions to .smm/assumptions/")
        _log_trace_event(root, "cli", "assumptions_scan", "pass", f"{result.detected} assumptions detected")
        return

    all_assumptions = load_assumptions(root)
    if status:
        all_assumptions = [a for a in all_assumptions if a.status.value == status]

    if resolve:
        proposed = [a for a in all_assumptions if a.is_actionable and a.question]
        if not proposed:
            click.echo("No assumptions pending resolution.")
            return

        click.echo(f"\n{len(proposed)} assumptions need your input:\n")
        for i, a in enumerate(proposed):
            click.echo(f"--- Assumption {i + 1}/{len(proposed)} ---")
            click.echo(f"[{a.severity.upper()}] {a.summary}")
            if a.code_evidence:
                ev = a.code_evidence[0]
                click.echo(f"  File: {ev.file}:{ev.line}")
                if ev.snippet:
                    for line in ev.snippet.splitlines()[:5]:
                        click.echo(f"    {line}")
            click.echo(f"\n  {a.question}\n")
            for j, opt in enumerate(a.options):
                click.echo(f"    {j + 1}) {opt}")
            click.echo(f"    0) Skip for now (defer)")
            click.echo("")

            choice = click.prompt("Your choice", type=int, default=0)
            if choice == 0:
                resolved = resolve_assumption(root, str(a.id), -1, resolved_by="cli-user")
                click.echo("  → Deferred\n")
            elif 1 <= choice <= len(a.options):
                resolved = resolve_assumption(root, str(a.id), choice - 1, resolved_by="cli-user")
                if resolved:
                    click.echo(f"  → {resolved.status.value.upper()}\n")
            else:
                click.echo("  → Invalid choice, skipping\n")
        return

    if json_out:
        items = [a.model_dump(mode="json") for a in all_assumptions]
        click.echo(json.dumps({"total": len(items), "assumptions": items}, indent=2, default=str))
        return

    if not all_assumptions:
        click.echo("No assumptions found. Run 'vt assumptions --scan' to detect assumptions in your codebase.")
        return

    click.echo(f"# Domain Assumptions ({len(all_assumptions)} total)\n")
    by_status: dict[str, list] = {}
    for a in all_assumptions:
        by_status.setdefault(a.status.value, []).append(a)

    for st in ["proposed", "detected", "validated", "rejected", "deferred"]:
        group = by_status.get(st, [])
        if not group:
            continue
        click.echo(f"## {st.upper()} ({len(group)})\n")
        for a in group:
            sev = a.severity.upper()
            click.echo(f"  [{sev}] {a.summary}")
            if a.code_evidence:
                click.echo(f"         {a.code_evidence[0].file}:{a.code_evidence[0].line}")
            if a.question and st == "proposed":
                click.echo(f"         Q: {a.question}")
        click.echo("")


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
    _log_trace_event(root, "cli", "apply", "pass", f"{len(result.files_written)} files generated")


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
# vt proxy start / stop — session log sync
# ---------------------------------------------------------------------------


@main.group()
def proxy() -> None:
    """Manage LLM call observation (session log sync)."""


@proxy.command(name="start")
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
@click.option("--interval", default=5, help="Sync interval in seconds")
def proxy_start(path: str, interval: int) -> None:
    """Start background session log sync.

    Parses Claude Code session JSONL logs from ~/.claude/projects/
    and appends LLM call events to .smm/traces/events.jsonl.
    Runs as a background daemon, polling every --interval seconds.
    """
    import os
    import signal
    import time

    from vt_protocol.config import find_project_root
    from vt_protocol.observation.session_logs import sync_session_to_traces

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    pid_path = root / ".smm" / "proxy.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if already running
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            os.kill(old_pid, 0)  # Check if process exists
            click.echo(f"Session sync already running (PID {old_pid})")
            return
        except (OSError, ValueError):
            pid_path.unlink(missing_ok=True)

    # Fork to background
    pid = os.fork()
    if pid > 0:
        # Parent: write PID and exit
        pid_path.write_text(str(pid))
        click.echo(f"Session sync started (PID {pid})")
        click.echo(f"LLM call events will appear in .smm/traces/events.jsonl")
        click.echo(f"Stop with: vt proxy stop")
        return

    # Child: daemonize
    os.setsid()

    def _shutdown(signum, frame):  # type: ignore[no-untyped-def]
        pid_path.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        try:
            count = sync_session_to_traces(root)
            if count:
                logger.debug("Synced %d new LLM call events", count)
        except Exception:
            logger.debug("Session sync error", exc_info=True)
        time.sleep(interval)


@proxy.command(name="stop")
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
def proxy_stop(path: str) -> None:
    """Stop background session log sync."""
    import os
    import signal

    from vt_protocol.config import find_project_root

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project.")
        sys.exit(1)

    pid_path = root / ".smm" / "proxy.pid"
    if not pid_path.exists():
        click.echo("No session sync running.")
        return

    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        click.echo(f"Session sync stopped (PID {pid})")
    except (OSError, ValueError) as e:
        click.echo(f"Failed to stop: {e}")
    finally:
        pid_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# vt gate
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
@click.option("--json-output", "json_out", is_flag=True, help="Output as JSON")
def gate(path: str, json_out: bool) -> None:
    """Run architecture quality gates — binary pass/fail for CI.

    Two conditions:
      1. No new unresolved contradictions (above baseline)
      2. All new decisions have required metadata (title, dimensions, rationale)

    Exit code 0 = pass, 1 = fail.
    """
    from vt_protocol.config import find_project_root, load_governance_config
    from vt_protocol.decisions.quality_gate import run_quality_gate

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    config = load_governance_config(root)
    decisions = _load_local_decisions(root)
    contradictions = _load_local_contradictions(root)

    result = run_quality_gate(
        decisions,
        contradictions,
        require_rationale=True,
        require_dimensions=True,
    )

    if json_out:
        output = {
            "passed": result.passed,
            "checks_run": result.checks_run,
            "checks_passed": result.checks_passed,
            "errors": [
                {"rule": v.rule, "message": v.message, "details": v.details}
                for v in result.errors
            ],
            "warnings": [
                {"rule": v.rule, "message": v.message, "details": v.details}
                for v in result.warnings
            ],
        }
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo("# VT Protocol — Quality Gate")
        click.echo("")
        click.echo(f"Checks: {result.checks_passed}/{result.checks_run} passed")
        click.echo("")

        if result.errors:
            click.echo("## Errors (blocking)")
            for v in result.errors:
                click.echo(f"  ✗ [{v.rule}] {v.message}")
            click.echo("")

        if result.warnings:
            click.echo("## Warnings")
            for v in result.warnings:
                click.echo(f"  ⚠ [{v.rule}] {v.message}")
            click.echo("")

        status = "PASS" if result.passed else "FAIL"
        click.echo(f"**Result: {status}**")

    if not result.passed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# vt validate-change (internal — called by Claude Code PreToolUse hook)
# ---------------------------------------------------------------------------


@main.command(name="validate-change", hidden=True)
@click.option("--file-path", required=True, help="File being written/edited")
@click.option("--content", default="", help="New file content or edit snippet")
@click.option("--path", type=click.Path(exists=True), default=None, help="Project root path")
def validate_change(file_path: str, content: str, path: str | None) -> None:
    """Validate a proposed file change against decisions. Exit 0=pass, 1=violation.

    Reads content from stdin if --content is not provided. Outputs JSON with
    status, violations list, and a one-line reason for Claude Code hooks.
    """
    from vt_protocol.config import find_project_root

    if not content:
        if not sys.stdin.isatty():
            content = sys.stdin.read()

    start = Path(path).resolve() if path else Path.cwd()
    try:
        root = find_project_root(start)
    except FileNotFoundError:
        # No project — pass by default
        click.echo(json.dumps({"status": "pass", "reason": "No VT project found"}))
        return

    decisions = _load_local_decisions(root)
    active = [d for d in decisions if d.valid]

    if not active:
        click.echo(json.dumps({"status": "pass", "reason": "No active decisions"}))
        return

    violations = _check_content_against_decisions(file_path, content, active)

    if violations:
        output = {
            "status": "fail",
            "violations": violations,
            "reason": violations[0]["reason"],
        }
        click.echo(json.dumps(output))
        sys.exit(1)
    else:
        click.echo(json.dumps({"status": "pass", "reason": "No violations"}))


def _check_content_against_decisions(
    file_path: str, content: str, decisions: list
) -> list[dict]:
    """Check file content against active decisions for violations.

    Detects when new code introduces a technology that an existing decision
    explicitly forbids (the "Do not introduce X" constraint pattern).

    Only flags a violation when:
    1. The import's core dimension matches the decision's core dimension (same dimension)
    2. The import is NOT one of the packages explicitly mentioned as approved in the
       decision's content (via "uses X via Y" pattern)
    3. The import's label or a known name for it appears in the "Do not introduce" text

    Imports from a different dimension (e.g., fastapi vs sqlite3) are never violations.
    Imports that match an EXISTING decision's approved tech always pass.
    """
    import re

    from vt_protocol.decisions.taxonomy import TAXONOMY

    violations: list[dict] = []

    # Extract imports from the content (Python)
    _IMPORT_RE = re.compile(
        r"^(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+))", re.MULTILINE
    )
    content_imports: set[str] = set()
    for m in _IMPORT_RE.finditer(content):
        if m.group(1):
            mod = m.group(1)
            content_imports.add(mod)
            content_imports.add(mod.split(".")[0])
        elif m.group(2):
            for mod in m.group(2).split(","):
                mod = mod.strip().split(" as ")[0].strip()
                if mod:
                    content_imports.add(mod)
                    content_imports.add(mod.split(".")[0])

    if not content_imports:
        return []

    # Build maps: package → sub-dimension label, package → core dimension
    pkg_to_subdim: dict[str, str] = {}
    pkg_to_core: dict[str, str] = {}
    for sd in TAXONOMY:
        for pkg in sd.python_packages:
            normalized = pkg.replace("-", "_").lower()
            pkg_to_subdim[normalized] = sd.label
            pkg_to_core[normalized] = sd.core_dimension.value

    def _decision_core_dims(d) -> set[str]:
        """Get core dimension values from a decision's dimensions field."""
        return {dim.value for dim in d.dimensions}

    def _extract_approved_packages(d) -> set[str]:
        """Extract packages that the decision explicitly approves.

        Looks at the "via X" pattern in the decision content and the
        evidence in the rationale (e.g., "Evidence: import:sqlite3").
        """
        approved: set[str] = set()
        text = f"{d.content} {d.rationale}"
        # Match "via X" or "import:X" patterns
        via_pattern = re.compile(r"via\s+([\w, ]+?)(?:\.|,|\s|$)", re.IGNORECASE)
        for m in via_pattern.finditer(text):
            for pkg in m.group(1).split(","):
                pkg = pkg.strip().replace("-", "_").lower()
                if pkg:
                    approved.add(pkg)
        # Match "import:X" in evidence
        imp_pattern = re.compile(r"import:([\w.]+)")
        for m in imp_pattern.finditer(text):
            pkg = m.group(1).replace("-", "_").lower()
            approved.add(pkg)
            approved.add(pkg.split(".")[0])
        return approved

    # Check each decision's constraints
    for d in decisions:
        decision_dims = _decision_core_dims(d)
        approved = _extract_approved_packages(d)

        for constraint in d.constraints:
            lower = constraint.lower()
            if "do not introduce" not in lower:
                continue

            # The text after "Do not introduce" names forbidden alternatives
            forbidden_text = lower.split("do not introduce", 1)[1]

            for imp in content_imports:
                normalized_imp = imp.replace("-", "_").lower()
                if normalized_imp not in pkg_to_subdim:
                    continue

                # If this import is explicitly approved by the decision, pass
                if normalized_imp in approved:
                    continue

                imp_core_dim = pkg_to_core[normalized_imp]

                # Only flag if the import is on the SAME core dimension
                if imp_core_dim not in decision_dims:
                    continue

                # Check if the import's label or package name appears in the
                # "Do not introduce" text
                imp_label = pkg_to_subdim[normalized_imp].lower()
                # Check for partial name matches in forbidden text
                # e.g., "PostgreSQL" in "Do not introduce PostgreSQL, MongoDB..."
                # Also check common aliases
                _LABEL_ALIASES: dict[str, list[str]] = {
                    "relational database": ["postgresql", "postgres", "mysql", "mariadb", "mongodb", "external database"],
                    "nosql database": ["postgresql", "mysql", "sqlite"],
                    "rest api": ["graphql", "grpc", "alternative api"],
                    "graphql": ["rest", "grpc"],
                }

                # Check if package name or known tech name is in the forbidden text
                should_block = False
                # Direct package name check
                if normalized_imp in forbidden_text:
                    should_block = True
                # Check common names for the technology the package belongs to
                for sd in TAXONOMY:
                    if normalized_imp in [p.replace("-", "_").lower() for p in sd.python_packages]:
                        # Check if this sub-dimension's label or common names appear in forbidden text
                        if sd.label.lower() in forbidden_text:
                            should_block = True
                        # Check known tech names (postgresql, mongodb, etc.)
                        tech_names = _get_tech_names_for_package(normalized_imp)
                        for name in tech_names:
                            if name in forbidden_text:
                                should_block = True
                                break
                        break

                if should_block:
                    violations.append({
                        "decision": d.title,
                        "constraint": constraint[:200],
                        "file": file_path,
                        "import": imp,
                        "reason": (
                            f"Import '{imp}' ({pkg_to_subdim[normalized_imp]}) "
                            f"violates decision: {d.title}"
                        ),
                    })
                    break  # One violation per decision is enough

    return violations


def _get_tech_names_for_package(pkg: str) -> list[str]:
    """Map a package name to well-known technology names for constraint matching."""
    _PKG_TECH_NAMES: dict[str, list[str]] = {
        "psycopg2": ["postgresql", "postgres"],
        "psycopg": ["postgresql", "postgres"],
        "asyncpg": ["postgresql", "postgres"],
        "pymysql": ["mysql"],
        "mysqlclient": ["mysql"],
        "pymongo": ["mongodb", "mongo"],
        "motor": ["mongodb", "mongo"],
        "redis": ["redis"],
        "cassandra_driver": ["cassandra"],
        "graphene": ["graphql"],
        "strawberry_graphql": ["graphql"],
        "ariadne": ["graphql"],
        "grpcio": ["grpc"],
        "grpcio_tools": ["grpc"],
        "betterproto": ["grpc"],
        "sqlite3": ["sqlite"],
        "aiosqlite": ["sqlite"],
    }
    return _PKG_TECH_NAMES.get(pkg, [])


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
# vt config (group)
# ---------------------------------------------------------------------------


@main.group()
def config() -> None:
    """View and modify VT Protocol configuration."""


@config.command(name="llm")
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
@click.option("--provider", type=click.Choice(["anthropic", "openai", "ollama", "none"]), default=None)
@click.option("--model", "model_name", default=None, help="Model identifier")
@click.option("--base-url", default=None, help="Custom endpoint URL")
@click.option("--test", "test_conn", is_flag=True, help="Test the LLM connection")
def config_llm(path: str, provider: str | None, model_name: str | None, base_url: str | None, test_conn: bool) -> None:
    """View or update LLM provider configuration.

    With no options, displays the current LLM config.
    With --provider, updates the provider in governance.yaml.
    With --test, tests the connection.
    """
    from vt_protocol.config import find_project_root, load_governance_config, save_governance_config

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    cfg = load_governance_config(root)

    if provider is None and model_name is None and base_url is None and not test_conn:
        # Display current config
        m = cfg.model
        click.echo(f"LLM Provider Configuration:")
        click.echo(f"  Provider:  {m.provider}")
        click.echo(f"  Model:     {m.model}")
        click.echo(f"  API Key:   {m.api_key_env or '(none)'}")
        click.echo(f"  Base URL:  {m.base_url or '(default)'}")
        click.echo(f"  Timeout:   {m.timeout_seconds}s")
        click.echo(f"  Fallback:  {m.fallback}")
        return

    if provider is not None:
        cfg.model.provider = provider
        # Set sensible defaults for each provider
        if provider == "anthropic" and model_name is None:
            cfg.model.model = "claude-haiku-4-5-20251001"
            cfg.model.api_key_env = "ANTHROPIC_API_KEY"
            cfg.model.base_url = None
        elif provider == "openai" and model_name is None:
            cfg.model.model = "gpt-4o-mini"
            cfg.model.api_key_env = "OPENAI_API_KEY"
            cfg.model.base_url = None
        elif provider == "ollama":
            if model_name is None:
                cfg.model.model = "llama3:8b"
            cfg.model.api_key_env = None
            cfg.model.base_url = base_url or "http://localhost:11434"
        elif provider == "none":
            cfg.model.api_key_env = None
            cfg.model.base_url = None

    if model_name is not None:
        cfg.model.model = model_name
    if base_url is not None:
        cfg.model.base_url = base_url

    save_governance_config(root, cfg)
    click.echo(f"Updated LLM config: provider={cfg.model.provider}, model={cfg.model.model}")

    if test_conn:
        _test_llm_connection(cfg.model)


# ---------------------------------------------------------------------------
# vt onboard
# ---------------------------------------------------------------------------


@main.command()
@click.option("--path", type=click.Path(exists=True), default=".", help="Project root path")
@click.option("--edit", "edit_name", default=None, help="Edit an existing agent by name")
@click.option("--remove", "remove_name", default=None, help="Remove an agent by name")
@click.option("--list", "list_agents", is_flag=True, help="List all onboarded agents")
def onboard(path: str, edit_name: str | None, remove_name: str | None, list_agents: bool) -> None:
    """Manage agent onboarding — add, edit, remove, or list agents.

    With no flags, runs the interactive onboarding wizard.
    """
    from vt_protocol.config import find_project_root, load_governance_config, save_governance_config
    from vt_protocol.decisions.models import AgentConfig

    root = Path(path).resolve()
    try:
        root = find_project_root(root)
    except FileNotFoundError:
        click.echo("Error: not a VT Protocol project. Run 'vt init' first.")
        sys.exit(1)

    cfg = load_governance_config(root)

    if list_agents:
        _list_agents(cfg)
        return

    if remove_name:
        if remove_name in cfg.agents:
            del cfg.agents[remove_name]
            save_governance_config(root, cfg)
            click.echo(f"Removed agent: {remove_name}")
        else:
            click.echo(f"Agent not found: {remove_name}")
            sys.exit(1)
        return

    if edit_name:
        existing = cfg.agents.get(edit_name)
        if existing is None:
            click.echo(f"Agent not found: {edit_name}")
            sys.exit(1)
        if isinstance(existing, bool):
            existing = AgentConfig()
        agent_config = _run_single_agent_wizard(edit_name, existing)
        cfg.agents[edit_name] = agent_config
        save_governance_config(root, cfg)
        click.echo(f"Updated agent: {edit_name}")
        return

    # Interactive wizard — add new agent
    new_agents = _run_agent_onboarding_wizard()
    if new_agents:
        cfg.agents.update(new_agents)
        save_governance_config(root, cfg)
        click.echo(f"Saved {len(new_agents)} agent(s) to governance.yaml")
    else:
        click.echo("No agents configured.")


# ---------------------------------------------------------------------------
# LLM provider wizard
# ---------------------------------------------------------------------------


_LLM_PROVIDER_CHOICES = {
    "1": ("anthropic", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY"),
    "2": ("openai", "gpt-4o-mini", "OPENAI_API_KEY"),
    "3": ("ollama", "llama3:8b", None),
    "4": ("none", "", None),
}


def _run_llm_provider_wizard():
    """Interactive LLM provider selection. Returns ModelConfig or None."""
    from vt_protocol.decisions.models import ModelConfig

    click.echo("")
    click.echo("Contradiction Detection Setup")
    click.echo("─" * 40)
    click.echo("VT Protocol uses an LLM to judge architectural contradictions.")
    click.echo("The NLI pre-filter (local, no API needed) always runs first.")
    click.echo("")
    click.echo("Which LLM provider for deep contradiction analysis?")
    click.echo("")
    click.echo("  1. Anthropic (Claude Haiku 4.5) — fast, ~$0.002/check")
    click.echo("  2. OpenAI (GPT-4o-mini) — comparable speed and cost")
    click.echo("  3. Ollama (local) — free, private, no data leaves your machine")
    click.echo("  4. None — NLI pre-filter only, no LLM judgment")
    click.echo("")

    choice = click.prompt("Choose", type=click.Choice(["1", "2", "3", "4"]), default="1")
    provider, default_model, key_env = _LLM_PROVIDER_CHOICES[choice]

    if provider == "anthropic":
        import os
        if not os.environ.get("ANTHROPIC_API_KEY"):
            click.echo("  ⚠ ANTHROPIC_API_KEY not set. Set it before running contradiction checks.")
        return ModelConfig(provider="anthropic", model=default_model, api_key_env="ANTHROPIC_API_KEY")

    if provider == "openai":
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            click.echo("  ⚠ OPENAI_API_KEY not set. Set it before running contradiction checks.")
        return ModelConfig(provider="openai", model=default_model, api_key_env="OPENAI_API_KEY")

    if provider == "ollama":
        click.echo("")
        click.echo("Ollama selected. Testing connection...")
        from vt_protocol.decisions.llm_providers import test_ollama_connection
        result = test_ollama_connection()
        if result["connected"]:
            click.echo(f"  ✓ Ollama running at localhost:11434")
            if result["models"]:
                click.echo(f"  Available models: {', '.join(result['models'][:10])}")
                model_name = click.prompt("Which model?", default=default_model)
            else:
                click.echo("  No models found. Pull one with: ollama pull llama3:8b")
                model_name = default_model
        else:
            click.echo(f"  ✗ Cannot connect to Ollama: {result['error']}")
            click.echo("  Install: https://ollama.ai — then run: ollama serve")
            model_name = default_model
        return ModelConfig(provider="ollama", model=model_name, base_url="http://localhost:11434")

    if provider == "none":
        click.echo("  NLI-only mode selected. Confidence capped at 0.6.")
        return ModelConfig(provider="none", model="")

    return None


def _test_llm_connection(model_config):
    """Test an LLM provider connection."""
    if model_config.provider == "ollama":
        from vt_protocol.decisions.llm_providers import test_ollama_connection
        base = model_config.base_url or "http://localhost:11434"
        result = test_ollama_connection(base)
        if result["connected"]:
            click.echo(f"  ✓ Ollama connected. Models: {', '.join(result['models'][:5])}")
        else:
            click.echo(f"  ✗ Ollama unreachable: {result['error']}")
    elif model_config.provider == "anthropic":
        import os
        key = os.environ.get(model_config.api_key_env or "ANTHROPIC_API_KEY")
        click.echo(f"  {'✓' if key else '✗'} ANTHROPIC_API_KEY {'set' if key else 'not set'}")
    elif model_config.provider == "openai":
        import os
        key = os.environ.get(model_config.api_key_env or "OPENAI_API_KEY")
        click.echo(f"  {'✓' if key else '✗'} OPENAI_API_KEY {'set' if key else 'not set'}")
    elif model_config.provider == "none":
        click.echo("  ✓ NLI-only mode — no external connection needed")


# ---------------------------------------------------------------------------
# Agent onboarding wizard
# ---------------------------------------------------------------------------


_AGENT_TYPES = ["claude-code", "cursor", "copilot", "devin", "windsurf", "other"]

_ROLE_DEFAULTS = {
    "backend": {
        "allowed_paths": ["src/**", "api/**", "services/**", "tests/**", "migrations/**"],
        "blocked_paths": [".env", ".env.*", "secrets/**", "terraform/**", ".github/workflows/**", "infrastructure/**"],
        "allowed_dimensions": ["database", "api-style", "caching", "concurrency", "error-handling", "logging", "testing", "state-management"],
        "restricted_dimensions": ["security", "auth", "deployment"],
    },
    "frontend": {
        "allowed_paths": ["ui/**", "components/**", "pages/**", "styles/**", "public/**", "src/**"],
        "blocked_paths": [".env", "secrets/**", "api/**", "services/**", "migrations/**"],
        "allowed_dimensions": ["state-management", "testing", "error-handling", "logging"],
        "restricted_dimensions": ["security", "auth", "api-style", "database"],
    },
    "infra": {
        "allowed_paths": ["terraform/**", "infrastructure/**", ".github/workflows/**", "docker/**", "k8s/**"],
        "blocked_paths": [".env", "secrets/**"],
        "allowed_dimensions": ["deployment", "security", "logging"],
        "restricted_dimensions": ["database", "api-style", "state-management"],
    },
    "full-stack": {
        "allowed_paths": [],
        "blocked_paths": [".env", ".env.*", "secrets/**"],
        "allowed_dimensions": [],
        "restricted_dimensions": ["security"],
    },
    "security": {
        "allowed_paths": ["**"],
        "blocked_paths": [],
        "allowed_dimensions": ["security", "auth", "error-handling"],
        "restricted_dimensions": [],
    },
    "custom": {
        "allowed_paths": [],
        "blocked_paths": [".env", "secrets/**"],
        "allowed_dimensions": [],
        "restricted_dimensions": [],
    },
}


def _run_agent_onboarding_wizard() -> dict:
    """Interactive agent onboarding wizard. Returns dict of name → AgentConfig."""
    from vt_protocol.decisions.models import AgentConfig

    click.echo("")
    click.echo("Agent Onboarding")
    click.echo("─" * 40)
    click.echo("Configure AI agents that will work on this project.")
    click.echo("")

    configure = click.confirm("Configure AI agents?", default=True)
    if not configure:
        return {}

    agents = {}
    while True:
        click.echo("")
        name = click.prompt("Agent name (blank to finish)", default="", show_default=False)
        if not name:
            break
        agent_config = _run_single_agent_wizard(name)
        agents[name] = agent_config
        click.echo(f"  ✓ Agent '{name}' configured as {agent_config.role}")

    if agents:
        click.echo("")
        click.echo("Summary")
        click.echo("═" * 40)
        for name, ac in agents.items():
            click.echo(f"  {name}: type={ac.type}, role={ac.role}, "
                        f"context={ac.context_level}, ttl={ac.session_ttl_minutes}min")

    return agents


def _run_single_agent_wizard(name: str, existing=None) -> "AgentConfig":
    """Run wizard for a single agent. Returns AgentConfig."""
    from vt_protocol.decisions.models import AgentConfig

    if existing is None:
        existing = AgentConfig()

    click.echo(f"\n  Setting up: {name}")

    # Type
    type_choices = ["claude-code", "cursor", "copilot", "devin", "windsurf", "other"]
    agent_type = click.prompt(
        "  Agent type",
        type=click.Choice(type_choices),
        default=existing.type if existing.type in type_choices else "claude-code",
    )

    # Role
    role_choices = ["full-stack", "backend", "frontend", "infra", "security", "custom"]
    role = click.prompt(
        "  Role",
        type=click.Choice(role_choices),
        default=existing.role if existing.role in role_choices else "full-stack",
    )

    defaults = _ROLE_DEFAULTS.get(role, _ROLE_DEFAULTS["custom"])

    # Paths
    allowed_paths = existing.allowed_paths or defaults["allowed_paths"]
    blocked_paths = existing.blocked_paths or defaults["blocked_paths"]

    if allowed_paths:
        click.echo(f"  Allowed paths: {', '.join(allowed_paths[:5])}")
    if blocked_paths:
        click.echo(f"  Blocked paths: {', '.join(blocked_paths[:5])}")

    # Dimensions
    allowed_dims = existing.allowed_dimensions or defaults["allowed_dimensions"]
    restricted_dims = existing.restricted_dimensions or defaults["restricted_dimensions"]

    # Context level
    context = click.prompt(
        "  Context level",
        type=click.Choice(["full", "relevant", "minimal"]),
        default=existing.context_level,
    )

    # Session TTL
    ttl = click.prompt("  Session TTL (minutes, 0=unlimited)", type=int, default=existing.session_ttl_minutes)

    # Block on contradiction
    block = click.confirm("  Block on unresolved contradictions?", default=existing.block_on_contradiction)

    return AgentConfig(
        type=agent_type,
        role=role,
        display_name=existing.display_name or name.replace("-", " ").title(),
        allowed_paths=allowed_paths,
        blocked_paths=blocked_paths,
        allowed_dimensions=allowed_dims,
        restricted_dimensions=restricted_dims,
        context_level=context,
        auto_resolve=existing.auto_resolve,
        session_ttl_minutes=ttl,
        block_on_contradiction=block,
        owner=existing.owner,
    )


def _list_agents(cfg) -> None:
    """Display all configured agents."""
    from vt_protocol.decisions.models import AgentConfig

    click.echo("Onboarded Agents:")
    click.echo("─" * 60)
    found = False
    for name, val in cfg.agents.items():
        found = True
        if isinstance(val, bool):
            status = "enabled" if val else "disabled"
            click.echo(f"  {name}: {status} (simple mode)")
        elif isinstance(val, AgentConfig):
            click.echo(f"  {name}:")
            click.echo(f"    Type:        {val.type}")
            click.echo(f"    Role:        {val.role}")
            click.echo(f"    Context:     {val.context_level}")
            click.echo(f"    Allowed:     {', '.join(val.allowed_paths[:3]) or '(all)'}")
            click.echo(f"    Blocked:     {', '.join(val.blocked_paths[:3]) or '(none)'}")
            click.echo(f"    Dimensions:  {len(val.allowed_dimensions)} allowed, {len(val.restricted_dimensions)} restricted")
            click.echo(f"    TTL:         {val.session_ttl_minutes} min")
            click.echo(f"    Block:       {'yes' if val.block_on_contradiction else 'no'}")
    if not found:
        click.echo("  No agents configured.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_initial_decisions(root: Path, matches: list) -> None:
    """Write auto-detected dimensions as initial decision files.

    Writes ONE decision per detected sub-dimension (not per core dimension).
    Each decision includes imperative constraints — "use X, do not introduce Y".
    """
    decisions_dir = root / ".smm" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)

    from vt_protocol.decisions.models import Decision, DecisionType, Dimension, SourceType
    from vt_protocol.decisions.taxonomy import generate_constraint

    written = 0
    seen_sub: set[str] = set()
    for m in matches:
        sub_id = m.sub_dimension.id
        if sub_id in seen_sub:
            continue
        seen_sub.add(sub_id)

        evidence_str = ", ".join(m.evidence[:5])
        constraint_text = generate_constraint(m.sub_dimension, m.evidence)

        decision = Decision(
            title=f"Detected: {m.sub_dimension.label}",
            content=constraint_text,
            rationale=f"Auto-detected from project scan. Evidence: {evidence_str}",
            decision_type=DecisionType.CONSTRAINT,
            dimensions=[m.core_dimension],
            constraints=[constraint_text],
            made_by="vt-init",
            project=root.name,
            source_type=SourceType.SCAN,
        )

        filename = f"{written + 1:03d}-{sub_id.replace('.', '-')}.json"
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
    """Load contradiction records from .smm/contradictions/*.json.

    Deduplicates by ``id`` field so that multiple files for the same
    contradiction don't cause phantom unresolved entries in ``vt check``.
    Prefers the resolved copy when duplicates differ in status.
    """
    from vt_protocol.decisions.models import Contradiction, ContradictionStatus

    contradictions_dir = root / ".smm" / "contradictions"
    if not contradictions_dir.is_dir():
        return []

    seen_ids: dict[str, Contradiction] = {}
    for filepath in sorted(contradictions_dir.glob("*.json")):
        try:
            data = json.loads(filepath.read_text())
            c = Contradiction(**data)
            cid = str(c.id)
            if cid not in seen_ids:
                seen_ids[cid] = c
            else:
                existing = seen_ids[cid]
                if c.status == ContradictionStatus.RESOLVED and existing.status != ContradictionStatus.RESOLVED:
                    seen_ids[cid] = c
        except Exception:
            logger.debug("Failed to load contradiction from %s", filepath, exc_info=True)

    return list(seen_ids.values())


def _detect_contradictions(active_decisions: list, config) -> list:
    """Detect contradictions among active decisions sharing a dimension.

    Groups decisions by dimension, then for each dimension with 2+ decisions,
    checks pairs for contradictions using:
    1. Heuristic check (always runs first — fast, no external deps)
    2. LLM pipeline (if provider is configured and reachable)

    LLM results override heuristic results for the same pair when available.
    If LLM fails (unreachable Ollama, missing API key), heuristic results
    are preserved as fallback.

    Returns list of new Contradiction objects.
    """
    from itertools import combinations

    from vt_protocol.decisions.models import (
        Contradiction,
        ContradictionVerdict,
        ContradictionStatus,
        Decision,
        Dimension,
    )

    # Group decisions by dimension
    by_dimension: dict[Dimension, list[Decision]] = {}
    for d in active_decisions:
        for dim in d.dimensions:
            by_dimension.setdefault(dim, []).append(d)

    logger.debug("Contradiction detection: %d dimensions with decisions", len(by_dimension))
    for dim, decisions in by_dimension.items():
        logger.debug("  %s: %d decisions (%s)", dim.value, len(decisions),
                      ", ".join(d.title for d in decisions))

    # Collect pairs that share a dimension
    pairs_seen: set[str] = set()
    candidate_pairs: list[tuple[Decision, Decision, list[Dimension]]] = []

    for dim, decisions in by_dimension.items():
        if len(decisions) < 2:
            continue
        for a, b in combinations(decisions, 2):
            pair_key = "::".join(sorted([str(a.id), str(b.id)]))
            if pair_key in pairs_seen:
                continue
            pairs_seen.add(pair_key)
            shared = list(set(a.dimensions) & set(b.dimensions))
            candidate_pairs.append((a, b, shared))

    logger.debug("Contradiction detection: %d candidate pairs", len(candidate_pairs))

    if not candidate_pairs:
        return []

    # Stage 1: Always run heuristic (fast, no external deps)
    heuristic_results = _heuristic_contradiction_check(candidate_pairs)
    logger.debug("Heuristic found %d contradictions", len(heuristic_results))

    provider_name = config.model.provider.lower() if config.model else "none"
    logger.debug("LLM provider: %s", provider_name)

    if provider_name == "none":
        return heuristic_results

    # Stage 2: Try LLM for all pairs — LLM results override heuristic
    llm_results = _llm_contradiction_check(candidate_pairs, config)
    logger.debug("LLM found %d contradictions", len(llm_results))

    if llm_results:
        # LLM succeeded — use LLM results (higher quality)
        return llm_results

    # LLM returned nothing (provider unreachable) — fall back to heuristic
    if heuristic_results:
        logger.debug("LLM unavailable, using %d heuristic results as fallback", len(heuristic_results))
    return heuristic_results


def _heuristic_contradiction_check(
    pairs: list[tuple],
) -> list:
    """Simple heuristic contradiction detection (no LLM needed).

    For each pair on the same dimension, extracts the primary technology
    each decision advocates for (from "via X" or title), ignoring the
    "Do not introduce" prohibition clauses. If two decisions advocate
    different technologies on the same dimension, flag as CONTRADICTION.
    """
    import re
    from vt_protocol.decisions.models import (
        Contradiction,
        ContradictionVerdict,
        ContradictionStatus,
    )

    _TECH_PATTERN = re.compile(
        r"\b(postgres(?:ql)?|psycopg2?|asyncpg|sqlite\d?|aiosqlite"
        r"|mysql|mariadb|pymysql|mongodb|pymongo|redis|cassandra|dynamodb"
        r"|rest|graphql|grpc|websocket"
        r"|docker|kubernetes|k8s|serverless|lambda"
        r"|celery|rabbitmq|kafka|nats|sqs"
        r"|jwt|oauth|saml"
        r"|react|vue|angular|svelte"
        r"|fastapi|flask|django|express|next\.?js"
        r"|pytest|jest|vitest|mocha"
        r"|asyncio|threading|multiprocessing"
        r"|sqlalchemy|prisma|typeorm|sequelize)\b",
        re.IGNORECASE,
    )

    # Map specific libs/drivers to canonical tech names
    _CANONICAL: dict[str, str] = {
        "psycopg2": "postgresql", "psycopg": "postgresql", "asyncpg": "postgresql",
        "postgresql": "postgresql", "postgres": "postgresql",
        "sqlite": "sqlite", "sqlite3": "sqlite", "aiosqlite": "sqlite",
        "pymysql": "mysql", "mysql": "mysql", "mariadb": "mysql",
        "pymongo": "mongodb", "mongodb": "mongodb",
    }

    def _extract_primary_techs(decision) -> set[str]:
        """Extract the techs a decision advocates FOR (not against)."""
        # Use the title + content before any "Do not" prohibition
        text = f"{decision.title} "
        content = decision.content
        # Only look at the primary assertion (before "Do not introduce")
        do_not_idx = content.lower().find("do not")
        if do_not_idx > 0:
            text += content[:do_not_idx]
        else:
            text += content

        raw = set(_TECH_PATTERN.findall(text.lower()))
        canonical = set()
        for t in raw:
            c = _CANONICAL.get(t.lower(), t.lower())
            canonical.add(c)
        return canonical

    results = []
    for a, b, shared_dims in pairs:
        techs_a = _extract_primary_techs(a)
        techs_b = _extract_primary_techs(b)

        logger.debug("Heuristic pair: '%s' vs '%s'", a.title, b.title)
        logger.debug("  Techs A: %s", techs_a)
        logger.debug("  Techs B: %s", techs_b)
        logger.debug("  Overlap: %s", techs_a & techs_b)
        logger.debug("  Would flag: techs_a=%s techs_b=%s no_overlap=%s",
                      bool(techs_a), bool(techs_b), not bool(techs_a & techs_b))

        # If both advocate for tech but with no overlap, it's a contradiction
        if techs_a and techs_b and not (techs_a & techs_b):
            results.append(Contradiction(
                decision_a_id=a.id,
                decision_b_id=b.id,
                decision_a_title=a.title,
                decision_b_title=b.title,
                verdict=ContradictionVerdict.CONTRADICTION,
                reasoning=(
                    f"Heuristic: decisions advocate different technologies "
                    f"({', '.join(sorted(techs_a))} vs {', '.join(sorted(techs_b))}) "
                    f"on shared dimension(s) {', '.join(d.value for d in shared_dims)}."
                ),
                evidence_a=f"Technologies: {', '.join(sorted(techs_a))}",
                evidence_b=f"Technologies: {', '.join(sorted(techs_b))}",
                shared_dimensions=shared_dims,
                confidence=0.6,
                status=ContradictionStatus.UNRESOLVED,
            ))

    return results


def _llm_contradiction_check(
    pairs: list[tuple],
    config,
) -> list:
    """Run contradiction detection using NLI pre-filter + LLM judge."""
    from vt_protocol.decisions.contradiction import check_contradiction
    from vt_protocol.decisions.llm_providers import get_llm_provider

    provider = get_llm_provider(config.model)

    results = []
    for a, b, _shared_dims in pairs:
        logger.debug("LLM checking pair: '%s' vs '%s'", a.title, b.title)
        try:
            result = check_contradiction(
                a, b,
                provider=provider,
                model=config.model.model,
            )
            if result is not None:
                logger.debug("LLM verdict: %s (confidence: %.2f)", result.verdict.value, result.confidence)
                results.append(result)
            else:
                logger.debug("LLM returned None for pair (provider may be unreachable)")
        except Exception:
            logger.debug("Contradiction check failed for (%s, %s)", a.title, b.title, exc_info=True)

    return results


def _save_contradictions(root: Path, contradictions: list) -> None:
    """Save contradiction records to .smm/contradictions/*.json.

    Deduplicates by pair_key to avoid saving the same contradiction twice.
    """
    contradictions_dir = root / ".smm" / "contradictions"
    contradictions_dir.mkdir(parents=True, exist_ok=True)

    # Load existing pair keys to avoid duplicates
    existing_keys: set[str] = set()
    for filepath in contradictions_dir.glob("*.json"):
        try:
            data = json.loads(filepath.read_text())
            ids = sorted([data.get("decision_a_id", ""), data.get("decision_b_id", "")])
            existing_keys.add(f"{ids[0]}::{ids[1]}")
        except Exception:
            pass

    written = 0
    for c in contradictions:
        if c.pair_key in existing_keys:
            continue
        existing_keys.add(c.pair_key)
        filename = f"contradiction-{str(c.id)[:8]}.json"
        filepath = contradictions_dir / filename
        filepath.write_text(c.model_dump_json(indent=2))
        written += 1


def _save_decision(root: Path, d) -> None:
    """Persist a decision back to .smm/decisions/.

    Finds the existing file by reading each JSON and matching the ``id``
    field, then overwrites in place.  Works regardless of filename convention.
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


def _save_contradiction_file(root: Path, c) -> None:
    """Persist a single contradiction back to .smm/contradictions/.

    Finds ALL existing files with matching ``id`` field, updates the first
    canonical one (contradiction-{id[:8]}.json) and deletes any duplicates.
    """
    contradictions_dir = root / ".smm" / "contradictions"
    contradictions_dir.mkdir(parents=True, exist_ok=True)

    target_id = str(c.id)
    canonical_name = f"contradiction-{str(c.id)[:8]}.json"
    content = c.model_dump_json(indent=2)
    matched_files: list[Path] = []

    for fp in contradictions_dir.glob("*.json"):
        try:
            data = json.loads(fp.read_text())
            if data.get("id") == target_id:
                matched_files.append(fp)
        except Exception:
            continue

    if matched_files:
        # Write canonical file, delete all others
        canonical_path = contradictions_dir / canonical_name
        canonical_path.write_text(content)
        for fp in matched_files:
            if fp != canonical_path:
                logger.debug("_save_contradiction_file: removing duplicate %s", fp.name)
                fp.unlink()
    else:
        # No existing file — write new with canonical name
        (contradictions_dir / canonical_name).write_text(content)


def _extract_existing_sub_ids(decisions: list) -> set[str]:
    """Extract sub-dimension IDs from existing decision titles.

    Decision titles from vt init follow the pattern 'Detected: <label>'.
    We match against the taxonomy to recover sub-dimension IDs.
    """
    from vt_protocol.decisions.taxonomy import TAXONOMY

    label_to_id = {sd.label: sd.id for sd in TAXONOMY}
    existing: set[str] = set()

    for d in decisions:
        # Match "Detected: <label>" pattern from _write_initial_decisions
        if d.title.startswith("Detected: "):
            label = d.title[len("Detected: "):]
            if label in label_to_id:
                existing.add(label_to_id[label])
        # Also check content for constraint patterns
        for sd in TAXONOMY:
            if sd.label.lower() in d.title.lower():
                existing.add(sd.id)

    return existing

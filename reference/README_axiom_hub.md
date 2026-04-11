# Axiom Hub

**Context-as-a-Service for AI coding agents.**

<!-- Badges -->
[![PyPI version](https://img.shields.io/pypi/v/smm-sync.svg)](https://pypi.org/project/smm-sync/)
[![License](https://img.shields.io/badge/license-TBD-blue.svg)](#license)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

---

## The Problem

AI coding agents make dozens of architectural decisions per session — which database to use, how to structure an API, whether to split a module. When the session ends, those decisions vanish. The next session starts from zero, re-discovers the same trade-offs, and often reaches a different conclusion. After a few weeks, the codebase is a layer cake of contradictory choices that no one can explain.

This is the **Week Seven Wall**. The first few weeks of AI-assisted development feel magical. Then contradictions accumulate silently — a module uses SQLite because Session 12 decided it was simpler, while another module uses Postgres because Session 19 decided it was necessary. Neither session knew about the other's reasoning. The human developer becomes a full-time archaeologist, reverse-engineering why things are the way they are.

Axiom Hub fixes this by giving every AI session a shared memory of what was decided, why, and what was rejected. Contradictions are detected automatically and surfaced before they become bugs. Every decision gets an audit trail. The agent can't even start working until it loads context from prior sessions.

---

## How It Works

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                        AI AGENT SESSION                         │
  │                                                                 │
  │  1. Agent starts ──► get_project_context()                      │
  │     ┌──────────────────────────────────────────────┐            │
  │     │ Returns: active decisions, unresolved         │            │
  │     │ contradictions, constraints, session token    │            │
  │     └──────────────────────────────────────────────┘            │
  │                          │                                      │
  │  2. Agent works ──► add_decision() (before writing code)        │
  │     Records: title, rationale, alternatives, constraints        │
  │                          │                                      │
  │  3. Agent finishes ──► complete_session(token)                  │
  │     ┌──────────────────────────────────────────────┐            │
  │     │ Counts decisions captured, lists modified      │            │
  │     │ files, fires background smm check             │            │
  │     └──────────────────────────────────────────────┘            │
  │                          │                                      │
  └──────────────────────────┼──────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │   smm check     │  (background / post-commit)
                    │                 │
                    │  • Sync JSONL   │
                    │    into Kuzu    │
                    │  • Detect       │
                    │    contradictions│
                    │  • Build edges  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  NEXT SESSION   │
                    │                 │
                    │  get_project_   │
                    │  context()      │
                    │  shows new      │
                    │  contradictions │
                    │  + all prior    │
                    │  decisions      │
                    └─────────────────┘
```

---

## Quickstart

```bash
# Install
pip install smm-sync

# Initialize in your project root
cd your-project
smm init

# Add the MCP server to your agent config (.mcp.json):
cat <<'EOF' > .mcp.json
{
  "mcpServers": {
    "smm-sync": {
      "command": "smm",
      "args": ["serve"]
    }
  }
}
EOF

# Start coding — the agent will call get_project_context automatically
```

For a guided setup that configures API keys, GitHub capture, and the knowledge graph in one command:

```bash
smm install
```

---

## CLI Reference

All commands are invoked as `smm <command>`.

### `smm init`

Scaffold AGENTS.md, `.smm/` directory, `.claude/settings.json` hooks, and agent-specific config files.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--name` | `str` | directory name | Project name |
| `--mode` | `dev\|dashboard` | `dev` | `dev` = MCP only; `dashboard` = also launches web UI at `http://localhost:7842` |

Prompts for agent type: `claude-code`, `cursor`, `both`, or `skip`.

### `smm serve`

Start the MCP server (stdio transport).

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--host` | `str` | `127.0.0.1` | Host to bind to |
| `--port` | `int` | `0` | Port (0 = auto-assign) |

### `smm check`

Sync new decisions from JSONL into Kuzu, detect contradictions, build edges. This is the only command that loads heavy dependencies (Kuzu, sentence-transformers, optionally Claude).

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--all` | flag | `False` | Re-process all decisions, not just new ones |
| `--project` | `str` | `smm-sync` | Project name |
| `--quiet` | flag | `False` | Suppress output |

### `smm add-decision`

Record a decision from JSON (stdin or file) or named flags. Hot path: tries the compiled Rust binary (`smm-fast-write`) first (~10 ms), falls back to pure-Python JSONL append (< 500 ms). Neither path loads Kuzu, embeddings, or an LLM.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `[JSON_FILE\|-]` | positional | stdin | JSON source |
| `--project` | `str` | `smm-sync` | Project name |
| `--title` | `str` | — | Decision title (alternative to JSON input) |
| `--description` | `str` | — | Decision description/rationale |
| `--type` | `str` | — | `architectural` / `technical` / `product` / `constraint` |
| `--confidence` | `float` | — | Confidence score (0.0–1.0) |
| `--made-by` | `str` | — | Who made this decision |
| `--context` | `str` | — | Context note: PRD name, ticket ID, source description |
| `--local` | flag | `False` | Kept for backward compatibility |

### `smm add-decisions-batch`

Ingest multiple decisions from a JSONL file in a single process. Loads sentence-transformers once for the whole batch.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `JSONL_FILE` | positional | required | Path to JSONL file |
| `--project` | `str` | `smm-sync` | Project name |

### `smm check-contradictions`

Check if a decision contradicts existing ones. Used by the Axiom Lore-Hook before commit.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--title` | `str` | required | Decision title to check |
| `--content` | `str` | `""` | Decision content/rationale |
| `--project` | `str` | `smm-sync` | Project name |
| `--json-output` | flag | `False` | Output as JSON for scripting |

### `smm handle-contradictions`

Interactive Resolve/Defer/Ignore handler for contradictions detected at commit time.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--title` | `str` | required | Title of the new decision being committed |
| `--contra-file` | path | required | Path to JSON output by `check-contradictions --json-output` |
| `--non-interactive` | flag | `False` | Auto-defer all (CI/CD mode) |
| `--project` | `str` | `smm-sync` | Project name |

### `smm record-contradiction-action`

Record an action on a contradiction pair so it is never re-flagged.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--title-a` | `str` | required | First decision title |
| `--title-b` | `str` | required | Second decision title |
| `--status` | `resolved\|deferred\|ignored` | required | Action taken |
| `--note` | `str` | `""` | Resolution note |
| `--actor` | `str` | `dev` | Who performed the action |

### `smm get-context`

Output a clean summary of project decisions, contradictions, and PM resolutions. Reads from JSONL — no model loading, no graph sync.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | `str` | `smm-sync` | Project name |

### `smm dashboard`

Start the CaaS Dashboard web UI.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--host` | `str` | `127.0.0.1` | Host to bind to |
| `--port` | `int` | `7842` | Port to listen on |

### `smm dedupe`

Remove duplicate contradiction pairs from `contradictions.jsonl`.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | `str` | `smm-sync` | Project name (unused, for consistency) |

### `smm digest`

Print a digest of CaaS activity for a period. Zero LLM calls.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--period` | `day\|week\|month` | `week` | Time period |
| `--slack-webhook` | `str` | env `CAAS_SLACK_WEBHOOK` | Slack webhook URL |
| `--json` | flag | `False` | Output as JSON |

### `smm onboard`

Generate an AI-powered `ONBOARDING.md` from the context graph using Claude Haiku (~$0.003 per run).

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--output` | `str` | `ONBOARDING.md` | Output file path |
| `--project` | `str` | inferred | Project name |

### `smm discover-edges`

Discover and create edges between decisions in the graph.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | `str` | `smm-sync` | Project name |
| `--local` | flag | `False` | Embedding-only (no Claude CLI). Fully offline. |

### `smm seed-graph`

Seed the context graph with 18 interconnected architectural decisions. Makes ~54–126 Anthropic API calls, takes 5–10 minutes. Run once.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | `str` | `smm-sync` | Project name |

### `smm query`

Query the context graph with a natural language question.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `QUESTION` | positional | required | Natural language question |
| `--project` | `str` | `smm-sync` | Project name |
| `--limit` | `int` | `5` | Max results |

### `smm decisions`

List all recorded decisions for a project.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | `str` | `smm-sync` | Project name |

### `smm sync-from-git`

Parse `Axiom-*` git trailers from commit history and ingest decisions. The "new team member" path after cloning a repo.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | `str` | `smm-sync` | Project name |
| `--dry-run` | flag | `False` | Show decisions without ingesting |

### `smm status`

Show current coordination state: claimed files and active sessions. No flags.

### `smm claim`

Atomically claim a file using Tuple Space + event log.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `FILEPATH` | positional | required | Path to claim |
| `--session` | `str` | hostname:pid | Session identifier |
| `--task` | `str` | `""` | Description of the task |

### `smm release`

Release a claimed file.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `FILEPATH` | positional | required | Path to release |
| `--session` | `str` | `""` | Session identifier |

### `smm refresh`

Read `AGENTS.md` and update `.smm/parsed_context.json`.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--quiet` | flag | `False` | Suppress output |

### `smm reset`

Wipe all project data (graph, contradictions, compliance log, board). Preserves config.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--confirm` | flag | required | Confirm data wipe |

### `smm install`

Interactive setup wizard. Prompts for API keys, detects git remote, creates `.smm/`, runs first GitHub capture, seeds the graph, writes `.mcp.json`.

### `smm setup`

Interactive wizard to onboard a new repository to CaaS. All steps are idempotent.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--project` | `str` | inferred | Project name |
| `--skip-capture` | flag | `False` | Skip initial GitHub capture |
| `--skip-onboarding` | flag | `False` | Skip ONBOARDING.md generation |

### `smm capture init`

Create `.smm/github.yml` with repos pre-configured from git remote.

### `smm capture run`

Run the GitHub capture pipeline.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--once` | flag | `False` | Run once and exit (default: run forever) |
| `--since` | `str` | — | Backfill from date (YYYY-MM-DD) |

### `smm capture status`

Show current capture state.

### `smm compliance show`

Show the compliance audit trail.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--session` | `str` | `""` | Filter by session ID |
| `--decision` | `str` | `""` | Filter by decision title |

### `smm compliance stats`

Show compliance lineage summary statistics.

---

## MCP Tools

When an agent connects to Axiom Hub via MCP (`smm serve`), the following tools are available. All tools except `get_project_context` require a session to be initialized first.

### `get_project_context`

**Must be called first in every session.** Returns active decisions, unresolved contradictions, constraints, and a session token.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project` | `str` | `"smm-sync"` | Project name |
| `session_id` | `str` | `""` | Optional session identifier |

### `add_decision`

Record an architectural, technical, product, or constraint decision. Always writes to `decisions.jsonl` (critical path). Graph sync is optional and non-blocking.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str` | required | Short decision title |
| `content` | `str` | `""` | Full description (alias: `description`) |
| `rationale` | `str` | `""` | Why this decision was made |
| `made_by` | `str` | `""` | Who made it (defaults to `"agent"`) |
| `project` | `str` | `"smm-sync"` | Project name |
| `constraints` | `list[str]` | `[]` | Known constraints imposed |
| `alternatives` | `list[str]` | `[]` | Alternatives considered |
| `decision_type` | `str` | `"technical"` | `architectural` / `technical` / `product` / `constraint` (alias: `type`) |
| `confidence` | `float\|None` | `None` | Confidence score 0.0–1.0 |
| `session_token` | `str` | `""` | Session token from `get_project_context` |

### `complete_session`

Close the current work session and summarize decisions captured. Must be called after implementation work is done, before committing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_token` | `str` | required | UUID returned by `get_project_context` |

### `resolve_contradiction`

Resolve a detected contradiction. **Requires explicit developer confirmation** — the agent must ask the developer and receive `"YES"` before calling.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `id` | `str` | required | Contradiction UUID |
| `winner` | `str` | required | `"a"` (keep decision A), `"b"` (keep decision B), or `"dismiss"` |
| `note` | `str` | `""` | Developer comment |
| `confirmation` | `str` | required | Must be exactly `"YES"` |

### `check_contradictions`

Run `smm check` inline to sync new decisions and detect contradictions.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `project` | `str` | `"smm-sync"` | Project name |

### `query_decisions`

Search team decisions and architectural knowledge. Includes "Deja Vu" detection — warns if a query resembles previously-rejected alternatives.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | `str` | required | Natural language question |
| `project` | `str` | `"smm-sync"` | Project name |
| `limit` | `int` | `5` | Max results |
| `session_id` | `str` | `""` | Session identifier |

### `check_constraints`

Check if a proposed action violates any known project constraints.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `proposed_action` | `str` | required | What you are about to do |
| `project` | `str` | `"smm-sync"` | Project name |
| `session_id` | `str` | `""` | Session identifier |

### `add_constraint`

Register a non-negotiable project constraint (different from a decision — constraints are rules that must never be violated).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `constraint` | `str` | required | Constraint rule in one sentence |
| `scope_keywords` | `list[str]` | required | Keywords that trigger this constraint |
| `rationale` | `str` | required | Why this constraint exists |
| `project` | `str` | `"smm-sync"` | Project name |

### `get_decision_timeline`

Chronological history of decisions related to a topic, showing how team thinking evolved.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `topic` | `str` | required | Topic to trace (e.g. "database choice") |
| `project` | `str` | `"smm-sync"` | Project name |

### `get_compliance_lineage`

Audit trail for EU AI Act compliance and SOC 2 AI governance audits.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_id` | `str\|None` | `None` | Filter by session |
| `decision_title` | `str\|None` | `None` | Filter by decision title |

### `get_path_context`

Just-in-time context for the file being edited. Returns constraints and high-confidence decisions relevant to that specific file path. Zero LLM calls.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` | required | Path to the file being edited |
| `project` | `str` | `"smm-sync"` | Project name |

### `get_board_items`

Read the kanban-style decision board.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | `str` | `""` | Filter: `"backlog"`, `"in_progress"`, `"done"`, or all |

### `update_board_item`

Create or update a board item.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `title` | `str` | `""` | Item title |
| `status` | `str` | `"backlog"` | `backlog` / `in_progress` / `done` |
| `description` | `str` | `""` | Longer description |
| `item_id` | `str` | `""` | If provided, update existing; if empty, create new |

### `read_context`

Return AGENTS.md content plus active coordination state (claimed files, active sessions).

### `claim_file`

Atomically claim a file for exclusive editing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | `str` | required | Relative path to claim |
| `session_id` | `str` | required | Unique session identifier |
| `task` | `str` | `""` | What you're doing with this file |

### `release_file`

Release a claimed file after completing edits.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | `str` | required | Relative path to release |
| `session_id` | `str` | required | Session identifier |

### `refresh_context`

Re-parse AGENTS.md after a git commit lands.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_id` | `str` | required | Session identifier |

---

## Two-State Session Machine

Axiom Hub enforces a session discipline through a two-state machine backed by a lock file.

### State 1: Uninitialized

No lock file exists (or it's older than 30 minutes). The `PreToolUse` hook blocks **every** tool call with:

```
AXIOM HUB: Call get_project_context first.
```

The agent cannot read files, write code, or run commands until it loads context.

### State 2: Context Loaded

`get_project_context` creates a lock file at `/tmp/smm-session-<hash>.lock`. The hash is derived from the project directory path. Once the lock file exists and is fresh (< 30 minutes old), all tool calls proceed normally.

### Transitions

| From | To | Trigger |
|------|----|---------|
| State 1 | State 2 | Agent calls `get_project_context` (creates lock file) |
| State 2 | State 1 | Agent runs `git commit` (PostToolUse hook deletes lock file) |
| State 2 | State 1 | Lock file ages past 30 minutes (TTL expiry) |

### How It's Enforced

`smm init` installs three hooks in `.claude/settings.json`:

1. **PreToolUse** (`.*` matcher) — Checks for a fresh session lock file. If missing, blocks the tool call. Always allows `get_project_context` through.

2. **PostToolUse** (`Write|Edit|MultiEdit` matcher) — After every file write, reminds the agent: *"Did you make an architectural choice? If so, call add_decision before continuing."*

3. **PostToolUse** (`Bash` matcher) — Detects `git commit` in the command and deletes the session lock file, forcing the next task to start fresh with `get_project_context`.

### Commit Gate

At commit time, the Axiom Lore-Hook pipeline:
1. Classifies the diff for architectural decisions (Claude Haiku)
2. Runs `smm check-contradictions` against the graph
3. Presents interactive Resolve / Defer / Ignore prompts for each conflict
4. Injects `Axiom-Decision`, `Axiom-Rationale`, `Axiom-Type` git trailers
5. Fires background `smm check` for the next session

---

## Dashboard

Start with `smm dashboard` (default: `http://localhost:7842`).

### Overview (`/`)

Health cards (Decisions, Pending, Contradictions, Human Oversight), Today's Captures feed with approve/reject actions, Contradiction resolution with A/B diff view, compliance log, agent status. Command palette via Cmd+K.

<!-- Screenshot: dashboard-overview.png -->

### All Decisions (`/decisions`)

Searchable, filterable table of every recorded decision. Type tags, confidence pills, rationale previews. Export as CSV, PDF, or individual ADR markdown files.

<!-- Screenshot: dashboard-decisions.png -->

### Decision Graph (`/graph`)

Interactive Cytoscape.js visualization of the decision DAG. Nodes grouped by category (Database & Storage, Auth & Security, API & Transport, etc.). Edges show SUPERSEDES, CONTRADICTS, RELATES_TO relationships. Search, zoom, and click-to-detail.

<!-- Screenshot: dashboard-graph.png -->

### Decision Board (`/board`)

3-column Kanban board (Backlog / In Progress / Done). Contradiction items show resolve and dismiss actions. Drag-and-drop between columns.

<!-- Screenshot: dashboard-board.png -->

### AI Governance Audit Trail (`/compliance`)

Full compliance ledger for EU AI Act Article 12 and SOC 2. SHA-256 hash-chained entries. Date range filtering, expandable detail rows with forensic IDs, CSV/PDF export, hash chain integrity verification.

<!-- Screenshot: dashboard-compliance.png -->

### Contradictions (`/contradictions`)

Dedicated list of architecture contradictions with resolve modal (pick winner A or B, add resolution note).

### Constraints (`/constraints`)

Non-negotiable project rules. Add constraints with scope keywords and rationale.

### Weekly Digest (`/digest`)

Period selector (day/week/month). Stat cards for decisions captured, time saved, context injections, and alerts. Top 10 decisions list.

### Timeline (`/timeline`)

Coming soon — time-travel replay of decision history.

---

## Agent Compatibility

`smm init` generates the appropriate config files for each agent:

| Agent | Config Generated | How Context Is Loaded |
|-------|------------------|-----------------------|
| **Claude Code** | `.claude/settings.json` (PreToolUse + PostToolUse hooks) | MCP tools via `.mcp.json` |
| **Cursor** | `.cursor/rules/axiom-hub.mdc` (alwaysApply rule) | MCP tools via `.mcp.json` |
| **Windsurf** | `.agents/skills/axiom-caas/SKILL.md` | agentskills.io standard |
| **Cline** | `.agents/skills/axiom-caas/SKILL.md` | agentskills.io standard |
| **Copilot** | `AGENTS.md` (auto-read) | AGENTS.md conventions |
| **Devin** | `AGENTS.md` (auto-read) | AGENTS.md conventions |
| **Codex** | `AGENTS.md` (auto-read) | AGENTS.md conventions |

---

## Architecture

```
src/smm_sync/
├── __init__.py                    # Package init, Rust binary stub (smm-fast-write fallback)
├── cli.py                         # Click CLI: all smm commands, hook templates, init scaffolding
├── config.py                      # smm.toml parser, project root detection, SmmConfig dataclass
├── state.py                       # Propose-validate-commit engine, events.jsonl, state.json
├── coordinator.py                 # Tuple Space: os.link() atomic file claiming in .smm/locks/
├── ingester.py                    # AGENTS.md parser → .smm/parsed_context.json
├── mcp_server.py                  # FastMCP server: 17 tools, session machine, lock file management
├── jsonl_writer.py                # Pure-Python JSONL writer for decisions (hot path < 500ms)
├── contradiction_index.py         # Actioned contradiction pair tracking (never re-flag)
├── digest.py                      # Weekly/daily/monthly digest generator (zero LLM calls)
├── git_utils.py                   # Pre-commit hook install, git diff/remote parsing
├── security.py                    # Prompt injection sanitization (15 regex patterns)
├── lore_hook.py                   # Git hook templates: diff classification, trailer injection
├── context_graph/
│   ├── __init__.py                # Exports GraphClient, seed_test_data
│   ├── client.py                  # Kuzu + Graphiti wrapper, contradiction detection, edge discovery
│   ├── models.py                  # Pydantic models: Decision, ContextResult, RejectionResult
│   └── seed.py                    # 18 interconnected seed decisions for new graphs
├── capture/
│   ├── __init__.py                # Exports GitHubCapture, load_config, load_capture_state
│   ├── github_capture.py          # GitHub passive capture: PRs, commits, issues, releases
│   └── models.py                  # Pydantic models: RepoConfig, CaptureSettings, CapturedEvent
├── compliance/
│   ├── __init__.py                # Singleton LineageLogger accessor
│   └── lineage.py                 # Append-only compliance logger (EU AI Act, SOC 2)
└── dashboard/
    ├── __init__.py                # Exports FastAPI app, run_dashboard
    ├── app.py                     # FastAPI backend: REST API + static file serving
    └── static/
        ├── index.html             # Overview dashboard
        ├── decisions.html         # All decisions list with search/filter/export
        ├── graph.html             # Cytoscape.js decision graph visualization
        ├── board.html             # Kanban decision board
        ├── contradictions.html    # Contradiction list with resolve modal
        ├── compliance.html        # AI governance audit trail
        ├── constraints.html       # Non-negotiable constraint rules
        ├── digest_page.html       # Periodic digest viewer
        └── timeline.html          # Time-travel (coming soon)
```

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | For graph sync, capture, onboarding | Anthropic API key for Claude calls |
| `GITHUB_TOKEN` | For GitHub capture | GitHub personal access token |
| `CAAS_SLACK_WEBHOOK` | Optional | Slack webhook for `smm digest` |
| `SMM_DASHBOARD_PORT` | Optional | Override dashboard port (default: 7842) |
| `SMM_FAST_WRITE_BIN` | Optional | Path to compiled Rust binary override |
| `CAAS_DEBUG` | Optional | Enable debug logging in security module |
| `AXIOM_NON_INTERACTIVE` | Optional | Set to `1` for non-interactive contradiction handling |

### `.smm/` Directory Contents

```
.smm/
├── config.json                 # Agent type, project settings
├── decisions.jsonl             # All recorded decisions (primary source of truth)
├── contradictions.jsonl        # Detected contradictions with resolution status
├── contradiction_index.json    # Actioned pairs (never re-flagged)
├── compliance_lineage.jsonl    # SHA-256 hash-chained audit trail
├── board.json                  # Kanban board items
├── parsed_context.json         # Cached AGENTS.md parse output
├── events.jsonl                # Propose-validate-commit event log
├── state.json                  # Materialized coordination state
├── killed_sessions.json        # Sessions disconnected via dashboard
├── .check_dirty                # Flag for pre-commit hook
├── graph/                      # Kuzu embedded graph database
├── locks/                      # Atomic file claim locks
├── capture_state.json          # GitHub capture watermarks
└── github.yml                  # GitHub capture repo config
```

---

## Known Limitations

<!-- To be filled in manually -->

---

## Roadmap

### v1.1

- **`isError` gate** — Contradiction resolution flow returns `isError: true` to the MCP client so the agent must address conflicts before proceeding
- **Rust binary for `add-decision`** — Compiled via maturin, target < 10 ms writes (currently behind `smm-fast-write` stub)
- **Kuzu lock retry** — Retry with backoff when the graph database is locked by another process
- **NLI pre-filter** — Natural Language Inference model to pre-filter contradiction candidates before embedding similarity
- **`--model` flag** — Override the default Claude model for contradiction detection and capture
- **Smart incremental checking** — Only re-check decisions affected by new additions, not the full graph
- **OAuth/RBAC on MCP tools** — Authentication and role-based access control for multi-user MCP server deployments

---

## Contributing

```bash
# Clone and install in dev mode
git clone https://github.com/yourusername/smm-sync.git
cd smm-sync
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run tests excluding slow (API-calling) tests
pytest tests/ -m "not slow"
```

---

## License

TBD

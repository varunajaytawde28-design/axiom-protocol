# Axiom Protocol

**Status:** Working CLI · ~2,900 tests passing · MCP integration tested with Claude Code · RFC 6962 Merkle audit log verified end-to-end · Pre-1.0, APIs may change.

**Governance for AI coding agents. Decisions tracked, contradictions caught, assumptions surfaced — before they ship.**

## See it in action

- 📺 **7-minute demo video:** https://youtu.be/IBbo51YOhdc
- 📄 **Pitch deck:** https://drive.google.com/file/d/1tdKbBB4BWF_qGv7tCMM3B6-z5IylvmeF/view?usp=sharing

## Why this exists

Axiom started from a pattern I kept seeing in production. AI coding agents were compressing two-year build cycles into two months — but bug rates multiplied 10x and architectural decisions stopped being legible. Developers would say "Claude did this, we don't know why." In a separate system, an AI collections bot operated outside its policy scope — used data it shouldn't have, called customers it shouldn't have. Both failures had the same root cause: agents acting without a system of record. Axiom is that system.

---

## The Problem

AI coding agents make dozens of architectural decisions per session — database choices, auth patterns, API styles — but none of it is recorded or checked for consistency. Two agents working on the same codebase will silently contradict each other. A decision made in Monday's session gets overwritten by Friday's. We call this **session drift**: the slow, invisible divergence of a codebase from its own architectural intent.

Axiom Protocol makes every architectural decision explicit, detects contradictions between them, and surfaces the implicit assumptions buried in your code — so you catch drift before it compounds.

## 30-Second Quick Start

```bash
# Install from source (PyPI release coming soon)
git clone https://github.com/varunajaytawde28-design/axiom-protocol.git
cd axiom-protocol
pip install -e .

# Initialize governance in your project
vt init

# Auto-detect architectural patterns and generate agent rules
vt apply

# Check for contradictions
vt check

# Start the dashboard
vt dashboard
```

`vt init` scans your codebase, detects architectural patterns (which database, which API framework, which auth library), records them as decisions, installs git hooks, and sets up MCP integration. `vt apply` generates governance rules into your `CLAUDE.md` and `.cursor/rules` so agents see the rules before they write code. `vt check` runs contradiction detection across all recorded decisions.

Everything lives in a `.smm/` directory alongside your code. No external database. No hosted service. Git-tracked.

---

## How It Works

### The Lattice — Architectural Decision Graph

Every architectural choice is a node in a decision graph, organized across 12 core dimensions (database, auth, caching, API style, deployment, concurrency, logging, testing, error handling, state management, messaging, security). When you run `vt init` or `vt infer`, the system scans your project for package imports, config files, and directory patterns to auto-detect what you're already using. Decisions have confidence scores, statuses, and can supersede each other.

**CLI**: `vt init`, `vt infer`, `vt check`

### The Axiom Hub — Contradiction Detection & Resolution

When decisions conflict — say one agent picks PostgreSQL and another introduces MongoDB — the system catches it. Detection runs a two-stage pipeline: an NLI cross-encoder pre-filter (local, no API call) followed by an LLM judgment call (Claude Haiku 4.5 by default, configurable via `governance.yaml`) for cases that pass the threshold. Contradictions get a ternary verdict: **CONTRADICTION**, **TENSION**, or **COMPATIBLE**. Each comes with cited evidence from both decisions and suggested resolution paths.

Resolution options: pick one decision over the other, accept the tension as an exception (with four tiers from auto-waiver to break-glass), or defer for later review.

**CLI**: `vt check`, `vt check --resolve`, `vt resolve`

### Assumption Governance — Implicit Risk Detection

Your code is full of unstated assumptions: "this field is never null," "requests complete within 5 seconds," "only admins access this endpoint." Axiom scans Python source files with 19 regex patterns across six categories (data scope, temporal, access, completeness, configuration, framework) and surfaces them as bounded multiple-choice questions — not yes/no prompts — to prevent acquiescence bias.

Assumptions follow a lifecycle: **detected → proposed → validated/rejected/deferred**. Validated assumptions become domain constraints in your governance rules. Rejected ones trigger refactor prompts. A shadow-mode system auto-mutes patterns with high false-positive rates.

**CLI**: `vt assumptions --scan`, `vt assumptions --resolve`, `vt assumptions --status proposed`

---

## Dashboard

Run `vt dashboard` to launch the local web UI on `127.0.0.1:7842`. It shows the decision graph, unresolved contradictions, the assumption validation queue, and the full audit trail.

The audit trail includes a "Verify Merkle" action that re-runs RFC 6962 inclusion proofs against the current Merkle root and reports the result inline — `Verified N of N audit entries against Merkle root — RFC 6962 inclusion proofs passed. Audit log is tamper-evident.`

See the [demo video](https://youtu.be/IBbo51YOhdc) for a walkthrough.

---

## Who this is for

Axiom is built for engineering teams running AI coding agents (Claude Code, Cursor, Copilot) on production codebases — especially teams of 5–50 developers where multiple agents touch the same code and architectural drift is starting to show. If you've ever asked "why did the agent pick this database?" or shipped a contradiction between two agent sessions, this is for you.

---

## CLI Reference

| Command | What it does |
|---------|-------------|
| `vt init` | Scan project, record architectural patterns as decisions, install git hooks, set up MCP config |
| `vt apply` | Generate governance rules into `CLAUDE.md`, `.cursor/rules`, `AGENTS.md` from the decision graph |
| `vt check` | Run contradiction detection across all decisions. `--exit-code` for CI, `--json-output` for machines |
| `vt check --resolve` | Run check then enter interactive resolution for any contradictions found |
| `vt resolve` | Enter interactive contradiction resolution directly |
| `vt infer` | Re-scan codebase for new architectural patterns and run contradiction detection |
| `vt assumptions` | List domain assumptions. `--scan` to re-scan, `--resolve` for interactive validation, `--status <status>` to filter |
| `vt dashboard` | Start the web dashboard on `127.0.0.1:7842` |
| `vt serve` | Start the MCP server. `--stdio` for MCP clients, `--host`/`--port` for HTTP |
| `vt gate` | Quality gate for CI/CD pipelines. Returns exit code 0 (pass) or 1 (fail). `--json-output` for structured results |

### Git Hooks (installed by `vt init`)

- **Pre-commit**: Runs `vt check --exit-code`. Blocks commits with critical contradictions.
- **Post-commit**: Re-scans for new patterns, checks for new contradictions, appends to audit trail. Runs in background — never blocks.

---

## MCP Tools

When you run `vt serve --stdio`, six tools are exposed to AI agents via the Model Context Protocol:

| Tool | Purpose |
|------|---------|
| `check_before_coding` | Agent calls this before modifying a file. Returns relevant architectural decisions, access control status, and deferred contradictions as warnings |
| `validate_change` | Agent passes a diff. Checks for new dependency additions and governance violations |
| `get_project_decisions` | Query the decision graph. Filter by dimension, active-only, or get all. Returns top 10 ranked by relevance |
| `report_decision` | Agent records a new architectural decision. Auto-computes confidence, runs contradiction detection, returns any conflicts found |
| `get_resolution` | Fetch full details on a specific contradiction — both decisions, verdict, evidence, resolution status |
| `complete_session` | Signals end of agent session. Triggers a full contradiction detection pass and returns actionable items |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  AI Agent (Claude Code / Cursor / Copilot)      │
│  ↕ MCP (stdio)                                  │
├─────────────────────────────────────────────────┤
│  MCP Server (vt serve)                          │
│  6 tools: check, validate, query, report, ...   │
├────────────┬────────────┬───────────────────────┤
│  Decision  │ Assumption │  Contradiction        │
│  Graph     │ Scanner    │  Detector             │
│  (YAML)    │ (regex)    │  (NLI + LLM)          │
├────────────┴────────────┴───────────────────────┤
│  .smm/ directory (git-tracked)                  │
│  decisions/ contradictions/ assumptions/         │
│  traces/ audit/ generated/                      │
├─────────────────────────────────────────────────┤
│  Git Hooks          │  Rule Sync (vt apply)     │
│  pre-commit: check  │  → CLAUDE.md              │
│  post-commit: infer │  → .cursor/rules/         │
│                     │  → AGENTS.md              │
└─────────────────────┴───────────────────────────┘
```

**What's local**: Decision storage, assumption scanning, taxonomy detection, rule generation, dashboard, audit trail. All in `.smm/`.

**What calls an LLM**: Contradiction detection (Claude Haiku 4.5 by default, configurable via `governance.yaml`). Falls back to NLI-only or "assume compatible" if no API key is set.

**What's optional**: NLI cross-encoder (`sentence-transformers`), tree-sitter for deeper code analysis, the LLM itself. The system degrades gracefully at each level.

---

## Test Suite

~2,900 tests across 159 test files.

```
pytest                              # run all tests
pytest -m "not slow"                # skip slow tests
pytest -m integration               # integration tests only
pytest tests/decisions/             # decision subsystem
pytest tests/dashboard/             # dashboard API tests
pytest tests/analysis/              # assumption scanning
```

Test markers: `integration`, `adversarial`, `performance`, `chaos`, `compliance`, `slow`.

---

## Known Limitations

- **Assumption scanning is Python-only.** The 19 regex patterns match Python syntax (imports, decorators, type hints). No JavaScript/TypeScript scanning yet.
- **Taxonomy is rigid.** The 12 core dimensions and 46 sub-dimensions are hardcoded. If your architectural concern doesn't map to one of them, it won't be auto-detected.
- **Tested with Claude Code.** The MCP integration and git hooks are tested against Claude Code. Config is generated for Cursor (`.cursor/rules/`) and listed in `AGENTS.md`, but these agent integrations are not independently tested.
- **LLM-dependent contradiction detection.** Without an `ANTHROPIC_API_KEY`, contradiction detection falls back to the NLI model (if installed) or defaults to "compatible." The full pipeline requires an API key.
- **No multi-repo support.** Governance is per-project. Cross-repo architectural decisions aren't tracked.
- **Regex-based assumption detection.** Assumptions are found via pattern matching, not static analysis. This means false positives on some patterns and missed assumptions that don't match any regex.
- **Brand transition in progress.** The CLI command is `vt` and the package is `vt-protocol` for historical reasons; the product is Axiom Protocol. These will be unified in a future release.

---

## License

MIT

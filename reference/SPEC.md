# Axiom Hub + Lattice: Product Roadmap

**Core narrative: "Your AI agents are silently changing your product. We stop that."**

Not token savings. Not observability. Product integrity.

---

## Phase 1 — The Demo (Months 1-4, solo founder)

**Goal:** Working product compelling enough to raise pre-seed/seed and onboard 5 design partners.

### Axiom Hub (Decision Engine)

- [ ] Migrate from archived Kuzu to FalkorDB or PostgreSQL with Cypher abstraction layer
- [ ] 12 core dimensions auto-tagging (database, auth, caching, API-style, deployment, concurrency, logging, testing, error-handling, state-management, messaging, security)
- [ ] 30+ dimensions auto-detectable from code WITHOUT LLM call (scan package.json, requirements.txt, docker-compose, config files, directory structure)
- [ ] Single-LLM contradiction detection with structured output (reasoning BEFORE verdict, ternary judgment: contradiction/tension/compatible, ~$0.002 per check)
- [ ] "Default to COMPATIBLE" instruction in prompt to suppress false positives
- [ ] Mandatory evidence citation in schema — LLM must cite specific text from each decision
- [ ] PostgreSQL junction table query: shared-dimension-count × recency-multiplier ranking, top 5 results reordered for LLM attention bias (best match first, second-best last)
- [ ] `isError` gate — contradiction resolution returns `isError: true` so agent must address conflicts before proceeding
- [ ] **Freeze existing violations on adoption** (SonarQube's "Clean as You Code") — snapshot all current contradictions as baseline, quality gate only checks NEW contradictions
- [ ] Web dashboard with decision graph visualization (Cytoscape.js)
- [ ] Basic Merkle-tree audit log (RFC 6962 style, replacing JSONL)
- [ ] **ContraGen synthetic contradiction generation** (arXiv 2510.03418) — generate synthetic contradictions across all 42 dimensions for labeled training data from day one, don't wait for 5,000 real customer resolutions to fine-tune

### Lattice (Observation Engine)

- [ ] SDK monkey-patching for Anthropic + OpenAI clients (existing)
- [ ] TaintedStr provenance tracking (existing)
- [ ] Vector clocks for causal ordering (existing)
- [ ] **NEW: File change tracking** — git snapshot diffing before/after agent task, auto-categorize by path pattern (source/config/test/dependency/CI)
- [ ] **NEW: Dependency mutation tracking** — parse package.json/requirements.txt diffs, flag >3 new deps for single task
- [ ] Tree-sitter extraction for Python + TypeScript

### Prevention Layer (NEW — highest-leverage feature)

- [ ] **Auto-generate CLAUDE.md from decision graph** — select only decisions relevant to current task, compact imperative instructions, respect ~100-150 instruction budget
- [ ] **Auto-generate .cursor/rules/*.mdc** — YAML frontmatter with glob-matched activation
- [ ] **Auto-generate AGENTS.md** — universal cross-tool baseline
- [ ] Three-tier context injection: Always (<15 universal rules) / Auto-attached (triggered by file patterns) / On-demand (available via MCP query)
- [ ] Regenerate all files via git hooks whenever decisions change

### MCP Server (5 tools max)

- [ ] `check_before_coding(file_path)` → architectural constraints for this file
- [ ] `validate_change(diff)` → checks proposed change against decision graph, pass/fail
- [ ] `get_project_decisions(dimension)` → active decisions for a dimension
- [ ] `report_decision(title, rationale, dimensions)` → records new architectural decision
- [ ] `get_resolution(contradiction_id)` → fetches previous resolution
- [ ] Ship as remote Streamable HTTP with OAuth 2.1 + stdio fallback for evaluation
- [ ] Commit `.cursor/mcp.json` and `.vscode/mcp.json` to repo — whole team gets governance automatically

### MCP Gateway/Proxy Mode (from Gemini — complementary to tools above)

- [ ] **Sit BETWEEN agent and its existing MCP servers** — intercept ALL MCP traffic transparently (InitializeRequest, tool list, execution commands)
- [ ] Agent calls GitHub MCP → platform intercepts → inspects for architectural implications → passes through
- [ ] Zero agent configuration — observe everything without agent needing to know you exist
- [ ] Both modes coexist: Gateway (passive observation) + Server (active tools agents call)

### PR Integration (Codecov model)

- [ ] GitHub App — post architecture review comment on every PR
- [ ] Show: what decisions this PR introduces, which existing decisions it touches, any contradictions, architecture health score
- [ ] Status check blocks merge for critical contradictions
- [ ] Start advisory-only, let teams enable blocking when ready
- [ ] Edit existing comments rather than posting new ones

**Key metric:** Time-to-first-contradiction under 10 minutes from installation.

---

## Phase 2 — Team Product (Months 5-8)

**Goal:** 3 paying teams. $5K MRR. Launch Team tier at $49/seat/month.

### Axiom Hub

- [ ] Role-based routing engine — contradictions auto-route based on dimension (database → tech lead, auth → tech lead + CISO, API contract → QA, user-facing → PM)
- [ ] Tech lead view with blast radius visualization
- [ ] QA view with behavioral contract violations
- [ ] Architecture quality gates — binary pass/fail, start with 2 conditions only (no new contradictions, all new decisions have required metadata)
- [ ] **Snyk-style backlog trickle** — one auto-fix PR per sprint targeting highest-priority resolvable contradiction (not big-bang remediation)
- [ ] Each contradiction offers 2-3 resolution paths: "Update the decision" / "Modify the code" / "Create a time-bound exception"
- [ ] **CodeRabbit-style one-click resolution buttons** in PR comments (Accept Exception / Update Decision / Fix Code)
- [ ] **Learn from dismissals** — track every false positive dismissal, auto-tune detection thresholds per dimension, suppress recurring false positives
- [ ] Confidence scoring — categorical (high/medium/low), route "low" confidence to human review
- [ ] Team management (invites, roles, permissions)

### Lattice

- [ ] **NEW: Configuration change sensitivity scoring** — 🔴 critical (.env, security configs), 🟡 warning (Docker/CI/Terraform), 🟢 info (linter/editor configs)
- [ ] **NEW: Scope creep detection** — embedding similarity between original task prompt and each file change, cosine similarity <0.5 = agent built something nobody asked for
- [ ] **NEW: Pattern violation detection** — lint before/after, AST-based matching for project conventions
- [ ] **PagerDuty's Events → Alerts → Incidents abstraction** — raw file changes are events, "3 new npm deps added" is an alert, "⚠️ Scope creep: Agent added refresh token logic not in task" is an incident, traffic-light dashboard
- [ ] **NEW: Agent Trajectory Monitors** (from Gemini) — monitor the SEQUENCE of agent steps, not just final output. Detect infinite reasoning loops, deviation from approved problem-solving pathways, repeated failed approaches. LTL-lite over action sequences
- [ ] Add Go and Java language support

### Integrations

- [ ] Slack — notifications routed by role, inline triage actions (Resolve/Defer/Escalate buttons)
- [ ] Jira/Linear — bidirectional sync, contradictions auto-create tickets
- [ ] CODEOWNERS integration — auto-route contradictions to code owners without manual config

### Exception Handling (NEW — four-tier model)

- [ ] **Auto-waivers** — known low-risk patterns (test naming, generated code excluded from lint). No approval needed, permanent, logged
- [ ] **Standard exceptions** — documented deviations, single architect approval, 30-day duration, enhanced monitoring
- [ ] **Elevated exceptions** — significant architectural deviations, lead + team lead approval, 90-day duration with mandatory renewal
- [ ] **Break-glass overrides** — emergencies, any authorized person, 24-72 hour duration, mandatory post-incident review within 48 hours
- [ ] Recurring exceptions surface as "rule needs updating" report
- [ ] **Dual-authorization sign-off** (from Gemini) — when PM and tech lead disagree on resolution, require BOTH to sign off, store disagreement + resolution immutably in Merkle tree audit log

---

## Phase 3 — Enterprise Moat (Months 9-18)

**Goal:** 2 enterprise contracts. $500K ARR. Launch Enterprise tier at $30-150K/year.

### Axiom Hub

- [ ] PM view — **Living Specifications** (from Gemini): CONTINUOUSLY map extracted architectural facts back to PRD requirements as code is written (real-time stream, not snapshot at PR time). PRD ingestion (Confluence/Notion/pasted), semantic matching against decision graph, green/yellow/red coverage
- [ ] CISO view — Merkle-tree audit trail with external anchoring (RFC 3161 timestamp servers), AI vs human code attribution, compliance framework mapping (EU AI Act Article 12, SOC 2, HIPAA), one-click evidence export
- [ ] Z3 SMT solver integration for formal architectural constraint checking (encode "no circular dependencies" as transitivity constraints)
- [ ] Fine-tuned contradiction model trained on customer resolution data (after 5,000+ labeled judgments)
- [ ] Custom dimension taxonomies — teams define project-specific dimensions with detection patterns
- [ ] Temporal decision graph — `valid_from`, `valid_until`, `superseded_by` edges, "what was the architecture at timestamp T?" queries
- [ ] W3C PROV decision provenance — who made the decision, what influenced it, alternatives considered, rationale
- [ ] Agent registry — every AI agent registered with type, version, capabilities, permissions, owner
- [ ] **Zero Standing Privileges for agents** (from Gemini) — issue dynamic, time-bound identity tokens scoped to current task ("you can modify auth files for 30 minutes"), not static credentials. Active permission enforcement, not just tracking
- [ ] Auto-generated architecture fitness tests — "Decision: API is REST" → test fails if GraphQL schema appears, run in CI as Jest/pytest/JUnit
- [ ] **Dynamic graph pruning with Salsa durability vectors** (from Gemini) — mark superseded decisions as "durable/cold" so they're never re-evaluated during active queries but remain queryable for audit. Prevents graph bloat as decisions accumulate

### Lattice

- [ ] **NEW: Intent drift detection** — every N tool calls, lightweight LLM check: "is this agent still working on original task? Score 1-10." Dual-threshold circuit breakers: warning at 70% scope, hard stop at 100%
- [ ] **NEW: Duplication detection across agent sessions** — "A similar function already exists at utils/format_date.py"
- [ ] **NEW: Ghost dependency detection** — flag unmaintained packages (<100 weekly downloads, no updates in 2 years), overlapping deps (moment.js + dayjs)
- [ ] **NEW: API consumer impact detection** — agent changes function signature, 3 other services call that API, breaking change invisible within repo
- [ ] Session replay — tree view (hierarchical operations), waterfall/timeline (parallel vs sequential), graph view (node-based flow)
- [ ] Hybrid Logical Clocks replacing pure vector clocks (2 integers per event instead of O(n), deployed in CockroachDB)
- [ ] OpenTelemetry GenAI conventions for interoperability

### Enterprise Features

- [ ] SSO (SAML/OIDC) + SCIM provisioning
- [ ] On-premise/VPC deployment option (single Docker container or Helm chart)
- [ ] SOC 2 Type II certification
- [ ] Multi-repo support with cross-service contradiction detection
- [ ] Webhook-based event system — every decision, contradiction, resolution emits webhook
- [ ] OpenTelemetry export for SIEM integration

### Novel Detection Features (from research)

- [ ] **Scope creep reporting** — "You asked for user login. The agent built login + registration + forgot password + OAuth + profile management + admin panel. 80% of output was not requested."
- [ ] **Decision cleanup tasks** — after resolution, generate follow-up: "These 4 files still reference the superseded SQLite decision. Here's what needs to change."
- [ ] **Pattern consistency scoring** — "The project uses repository pattern everywhere except this new file which does direct SQL queries. Not wrong, just inconsistent."

---

## Phase 4 — Platform (Months 18-36)

**Goal:** $5M ARR. Category definition. Analyst recognition.

### Architecture

- [ ] Full Code Property Graphs with Joern (AST + CFG + PDG merged) for deep code understanding
- [ ] GumTree AST diffs — detect structural moves, not just line-level adds/deletes
- [ ] Salsa-style incremental computation (demand-driven, early cutoff) — never re-analyze unchanged code
- [ ] eBPF trust layer via Tetragon — kernel-level unforgeable observation of agent actions
- [ ] LTL runtime monitors for the 10 most common governance constraints (Dwyer patterns)

### Multi-Agent Coordination

- [ ] Cross-agent causal intelligence — when Agent A's output feeds Agent B's input
- [ ] Decision collision detection — two agents independently make conflicting decisions on same dimension at same time (concurrent in vector-clock terms)
- [ ] Resolution broadcast — when a decision is resolved, actively inject into ALL active agent sessions via MCP
- [ ] Multi-agent session governance — monitor trajectory of long-running agents, alert on drift
- [ ] **Automated negotiation broker** (from Gemini) — auto-resolve low-level technical disputes between agents based on predefined architectural principles, only escalate to humans when programmatic reconciliation fails. Platform acts as centralized arbiter using dimension graph rules

### Platform Extension

- [ ] Infrastructure governance (Terraform, Kubernetes, cloud configs) — new dimensions: cost-impact, blast-radius, security-posture, compliance-zone, data-residency
- [ ] Business logic extraction — detect pricing decisions, eligibility rules, risk thresholds in code
- [ ] Bias/fairness monitoring for AI-written recommendation/risk algorithms
- [ ] Cross-company architectural intelligence (anonymized) — "When teams use FastAPI + SQLite and add workers, 87% switch to Postgres within 3 months"
- [ ] Architecture DNA fingerprinting — compact comparable fingerprint for M&A due diligence
- [ ] Shadow MCP discovery — inventory unmonitored MCP servers across the org

### Social/Engagement Layer

- [ ] Architecture coherence score tracking over time (belongs in PR comment from day one)
- [ ] Resolution feed — like GitHub activity stream but for architectural decisions
- [ ] Weekly digest — "Your team resolved 12 contradictions. Coherence: 87% → 91%"

---

## Phase 5 — Category Winner (Years 3-5)

**Goal:** $20M+ ARR. IPO-track or strategic acquisition.

- [ ] AI-powered predictive governance — predict contradictions BEFORE they happen based on historical patterns
- [ ] Auto-resolve low-risk contradictions with human review of the resolution
- [ ] Marketplace for custom dimension taxonomies and constraint rules
- [ ] Industry-specific compliance modules (healthcare, finance, government)
- [ ] FedRAMP authorization
- [ ] Open agent identity specification — the cross-vendor standard for AI agent registration
- [ ] Global deployment with EU data residency for GDPR

---

## Pricing

| Tier | Price | What's included |
|------|-------|-----------------|
| **Free / Open Source** | $0 | Lattice observation + 500 decisions + personal dashboard + basic PR comments + auto-generated AGENTS.md |
| **Team** | $49/seat/month (min 3) | Routing engine, Slack, role views, quality gates, exception handling, Jira/Linear, unlimited decisions |
| **Enterprise** | $30-150K/year | Merkle audit trails, compliance exports, SSO/SCIM, on-prem, Z3 verification, agent registry, custom dimensions, fitness tests |

---

## Key Technical Decisions (Locked)

- **LLM stays as judgment engine** — dimension graph ROUTES what's compared, LLM JUDGES the comparison. Single call, structured output, ~$0.002 per check
- **Prevention over detection** — auto-generate agent instruction files from decision graph, MCP server agents query BEFORE coding
- **PR-native delivery** — GitHub App posting architecture review comments, not a separate dashboard
- **PostgreSQL first** — junction table handles workload up to 10K decisions, no graph DB needed until proven otherwise
- **Freeze on adoption** — never require teams to fix legacy architectural debt, only enforce on new changes
- **5 MCP tools max** — each tool delivers a complete answer in one call, earns its 550-1400 token context budget
- **Remote HTTP + OAuth 2.1** — team-wide deployment via committed JSON config, stdio as fallback only

---

## Fundraising Narrative

"AI agents write 42% of code today, heading to 80%. Nobody governs what they decide. We built the decision graph — catches contradictions across agents, routes them to the right human, prevents problems before they happen by injecting architectural context into every agent session. EU AI Act requires audit trails by August 2026. We're the only platform that provides this for AI coding agents."

**Target raise:** $2-4M pre-seed/seed
**Target investors:** YC, Felicis (backed Letta), Lightspeed (backed Langfuse + Composio), Basis Set (backed Mem0)
-e 

---
---
---


# Axiom Hub + Lattice: Technical Roadmap

**Every technical decision in this document answers one question: does this make us the assumed layer that everything else plugs into?**

Not "is this technically impressive." Not "is this hard to copy." Not "does this accumulate data." The question is: does this make us Docker — the thing every project has, every tool integrates with, and removing feels wrong?

---

## The Standard We're Creating

**Own the authoring format. Open the output format.**

| What we own | What we open |
|-------------|--------------|
| `governance.yaml` — the canonical governance config | AGENTS.md, CLAUDE.md, .cursorrules — outputs agents consume |
| `.smm/` directory — governance state | Decision record format — open spec anyone can implement |
| `smm` CLI — the authoring experience | Architectural Decision Graph Protocol — donated to Linux Foundation |
| Axiom Hub graph — contradiction detection engine | MCP server protocol — standard tools/resources interface |
| Dimension taxonomy — how decisions are classified | Plugin/rule interface — community builds governance rules |

---

## T1 — The Format (Months 1-2)

*Goal: make `governance.yaml` + `.smm/` the convention every AI-governed project uses.*

### `governance.yaml` — Our Dockerfile Moment

- [ ] Design the file format: YAML with constrained schema (not custom DSL — YAML is Git-diff-friendly, universally parseable, requires no new tooling)
- [ ] Keep it readable without documentation — a developer should understand a `governance.yaml` in 30 seconds
- [ ] Support `extends` for shareable configs: `extends: ["@smm/recommended"]` — one line loads entire governance policy (ESLint's `"extends": "airbnb"` pattern)
- [ ] Core fields: `rules` (governance constraints), `dimensions` (what to track), `agents` (output targets), `team` (routing config)
- [ ] Ship `@smm/recommended` as the opinionated default — the Prettier approach (works out of the box, zero config required)
- [ ] Make the 42-dimension taxonomy INTERNAL — developer-facing config exposes 5-7 top-level facets only. Power users access full taxonomy via `dimensions: detailed`

```yaml
# governance.yaml — this is what developers see
extends: "@smm/recommended"

agents:
  claude: true
  cursor: true
  copilot: true

rules:
  freeze-on-adopt: true        # SonarQube CaYC — only enforce on new changes
  contradiction-threshold: 0.7  # confidence below this → human review
  
decisions:
  path: ".smm/decisions/"       # where decision records live
```

### `.smm/` Directory — Our `.git/` Moment

- [ ] Auto-created by `smm init` (never manually)
- [ ] Hidden via `.` prefix, self-contained, cleanly deletable
- [ ] Structure:
  - `.smm/decisions/` — individual decision YAML files (tracked in git)
  - `.smm/cache/` — graph cache, embeddings (gitignored)
  - `.smm/generated/` — CLAUDE.md, .cursorrules, AGENTS.md outputs (tracked in git)
  - `.smm/audit/` — Merkle tree log (gitignored, synced to cloud for team tier)
- [ ] `governance.yaml` lives at project ROOT (not inside .smm/) — visible, like `package.json`

### Decision Record Format — Our Container Image Spec

- [ ] Superset of Michael Nygard's ADR format — every existing ADR is valid
- [ ] Adds: dimension tags, graph edges (supersedes/relates-to), agent source, contradiction status
- [ ] Individual files in `.smm/decisions/001-use-postgresql.yaml`
- [ ] Publish format as open specification from day one
- [ ] YAML frontmatter + Markdown body (familiar to every developer)

```yaml
# .smm/decisions/003-use-jwt-auth.yaml
id: "003"
title: "Use JWT for API authentication"
status: active
dimensions: [security.authn]
decided: 2026-04-15
agent: claude-code
supersedes: null
context: "Session 7 — implementing user login"
---
We chose JWT tokens over session-based auth because the API
needs to be stateless for horizontal scaling. Tokens are signed
with RS256 and expire after 1 hour.

Alternatives considered:
- Session-based auth (rejected: requires sticky sessions)
- API keys (rejected: no user identity)
```

---

## T2 — The CLI (Months 1-2, parallel with T1)

*Goal: `smm init / check / apply` becomes muscle memory — our `git add / commit / push`.*

### `smm init` — Zero-Friction Entry

- [ ] Creates `.smm/` directory with default structure
- [ ] Generates `governance.yaml` with `extends: "@smm/recommended"`
- [ ] Auto-detects existing architecture from code (scans package.json, imports, config files — the 30+ auto-detectable dimensions)
- [ ] Creates initial decision records from what it finds ("Detected: PostgreSQL, FastAPI, JWT auth — 3 decisions recorded")
- [ ] Generates `.mcp.json` for Claude Code/Cursor, adds MCP server config
- [ ] Detects existing CLAUDE.md/.cursorrules/AGENTS.md and offers to import
- [ ] **Time to first value: <60 seconds** (npm init takes 30 seconds — match that)

### `smm check` — The `terraform plan` Equivalent (TRUST BUILDER)

- [ ] Shows what governance WOULD flag — contradictions, scope creep, pattern violations — WITHOUT blocking anything
- [ ] Output is human-readable Markdown, PR-comment-friendly
- [ ] Shows decisions detected, dimensions tagged, contradictions found, and what `apply` would generate
- [ ] **This is the single most important command.** Terraform's `plan` was what made infrastructure teams trust IaC. `smm check` is what makes dev teams trust governance.
- [ ] Exit code 0 (pass) or 1 (violations) for CI integration: `smm check || exit 1`

### `smm apply` — Generate Agent Files from Decision Graph

- [ ] Reads `governance.yaml` + `.smm/decisions/`
- [ ] Generates CLAUDE.md, .cursor/rules/*.mdc, AGENTS.md
- [ ] Three-tier context injection: Always (<15 rules) / Auto-attached (glob patterns) / On-demand (MCP)
- [ ] Respects instruction budget (~100-150 effective rules)
- [ ] Runs contradiction detection against the graph
- [ ] Idempotent — safe to run twice

### `smm sync` — Pull from Team Registry

- [ ] Downloads latest shared decisions from team/org registry
- [ ] Updates local governance state
- [ ] Merges remote decisions with local
- [ ] Only matters for Team tier — solo developers don't need this

### Additional Commands (not part of core 3)

- [ ] `smm dashboard` — launch web UI (existing)
- [ ] `smm add` — manually record a decision
- [ ] `smm resolve` — resolve a contradiction interactively
- [ ] `smm diff` — show what changed since last check

---

## T3 — The Plugin Ecosystem (Months 3-6)

*Goal: community builds governance rules, not us. Our ESLint plugins moment.*

### Shareable Governance Configs (npm packages)

- [ ] Distribute via npm as `@smm/config-*` packages
- [ ] `@smm/config-recommended` — opinionated defaults, ships day one
- [ ] `@smm/config-security-baseline` — auth, encryption, secrets rules
- [ ] `@smm/config-startup` — lightweight rules for small teams
- [ ] `@smm/config-enterprise` — strict rules with compliance mapping
- [ ] One-line adoption: `extends: ["@smm/config-recommended", "@smm/config-security-baseline"]`

### Agent Providers (Terraform provider pattern)

- [ ] `@smm/provider-claude` — translates governance.yaml → CLAUDE.md + .claude/rules/
- [ ] `@smm/provider-cursor` — translates governance.yaml → .cursor/rules/*.mdc
- [ ] `@smm/provider-copilot` — translates governance.yaml → copilot-instructions.md
- [ ] `@smm/provider-agents-md` — translates governance.yaml → AGENTS.md
- [ ] `@smm/provider-windsurf` — translates governance.yaml → .windsurf/rules/*.md
- [ ] New agents emerge → community builds providers → platform stays relevant without our work

### Custom Rules Interface

- [ ] Define rule contract: `(decision, context) → {pass: bool, message: str, severity: str}`
- [ ] Rules are npm packages: `@smm/rule-no-orm-mixing`, `@smm/rule-single-auth-strategy`
- [ ] Community creates rules → download counts signal trust → popular rules get promoted to `recommended` config
- [ ] **This is the ESLint 3,000-plugin flywheel**: we don't need to write every rule. We need the interface that lets others write rules.

### Get the "Airbnb Config" Moment

- [ ] Partner with 2-3 visible engineering teams to publish their governance standards
- [ ] `extends: "@vercel/governance"` or `extends: "@stripe/architecture-rules"` 
- [ ] One high-profile shareable config creates more adoption than 100 features
- [ ] This is the Airbnb ESLint style guide play — standardization without central authority

---

## T4 — The Integration Density (Months 2-6)

*Goal: embed in every workflow touchpoint. Removing governance means unwiring 5 systems.*

### VS Code Extension (CRITICAL — the Flow vs TypeScript lesson)

- [ ] Real-time governance feedback — ESLint squiggly-line pattern
- [ ] When developer writes code contradicting a decision, show inline warning
- [ ] Quick-fix suggestions: "This contradicts Decision #003. Click to view."
- [ ] **Flow died because VS Code integration was terrible. TypeScript won because it was built into VS Code.** This is non-negotiable.

### Git Hooks (Prettier's invisible infrastructure pattern)

- [ ] Pre-commit: `smm check` runs automatically (via husky/lint-staged or native git hooks)
- [ ] Post-commit: auto-tag commit with decision metadata via git trailers (existing)
- [ ] Pre-push: full contradiction check before code leaves local machine
- [ ] `smm init` installs hooks automatically — developer doesn't configure anything

### CI/CD Pipeline Gate

- [ ] GitHub Action: `uses: axiom-hub/governance-check@v1`
- [ ] GitLab CI template: `include: smm-governance.yml`
- [ ] Exit code integration: `smm check` returns 0/1 for pass/fail
- [ ] Start advisory (warn but don't block) → teams enable blocking when ready
- [ ] **This is the Prettier pre-commit + CI pipeline double-loop**: individual feedback in editor, organizational enforcement in CI

### GitHub App / PR Comments (existing — double down)

- [ ] One-click install from GitHub Marketplace
- [ ] Auto-creates `.smm/` and `governance.yaml` on first install
- [ ] Every PR gets architecture review comment
- [ ] Status check blocks merge for critical contradictions
- [ ] Edit existing comments (don't spam with new ones)

### MCP Server (already designed — 5 tools)

- [ ] `check_before_coding`, `validate_change`, `get_project_decisions`, `report_decision`, `get_resolution`
- [ ] MCP has 97M+ monthly SDK downloads — being an MCP server means accessibility to every AI agent
- [ ] Ship as remote Streamable HTTP — team-wide via committed `.mcp.json`
- [ ] This is our USB-C moment — universal connector to every AI agent

### Framework Scaffolding Integration (the npm-ships-with-Node play)

- [ ] Get `smm init` into `create-next-app`, `create-vite`, `django startproject`, `rails new`
- [ ] When a developer scaffolds a new project, governance is THERE by default
- [ ] npm won because it shipped with Node.js — governance should ship with project creation
- [ ] PR to popular scaffolding tools: "Add governance config for AI agents"

---

## T5 — The Open Specification (Months 4-8)

*Goal: standardize the decision graph format so other tools build compatibility — our OCI moment.*

### Architectural Decision Graph Protocol (ADGP)

- [ ] Publish as open RFC-style specification
- [ ] Covers: decision record schema, dimension taxonomy, graph edge types, contradiction detection interface, agent configuration generation contract
- [ ] Submit to Linux Foundation's Agentic AI Foundation (governs MCP already)
- [ ] Docker's OCI lesson: "standardize the width of the train tracks, so everyone can build the fastest engines"
- [ ] If the spec is open, every tool builds compatibility with us
- [ ] If the spec is proprietary, competitors create an alternative and the ecosystem rallies around it (Docker Swarm death)

### Relationship to Existing Standards

- [ ] AGENTS.md — we are the best GENERATOR, not a competitor. AGENTS.md is our output format, not our authoring format
- [ ] MCP — we are a NATIVE participant, not a wrapper. Our MCP server is a first-class citizen
- [ ] ADR format — we are a SUPERSET, not a replacement. Every existing ADR works in our system
- [ ] FINOS CALM — evaluate alignment with the Common Architecture Language Model spec

---

## T6 — The Network (Months 6-12)

*Goal: create the compounding loops that make the platform more valuable with every user.*

### Governance Rule Registry (Our Docker Hub / npm Registry)

- [ ] Public registry of community governance rules and shareable configs
- [ ] Download counts as trust signals (npm pattern)
- [ ] "Official" badge for verified configs from known companies (Docker Hub official images pattern)
- [ ] Search by dimension, framework, language
- [ ] This is where the network effect lives — more rules → more users → more rules

### Decision Template Library (Our Official Images)

- [ ] Shareable decision templates for common stacks:
  - "Next.js + Prisma + Vercel" starter decisions
  - "Django + PostgreSQL + AWS" starter decisions
  - "FastAPI + SQLAlchemy + Docker" starter decisions
- [ ] Teams pull a template, customize, ship — governance in 2 minutes
- [ ] Canonical starting points nobody debates (like nginx/postgres Docker images)

### Cross-Project Intelligence (Our Network Effect Moat)

- [ ] Anonymized pattern aggregation across projects (opt-in)
- [ ] "73% of projects using Next.js + Postgres choose connection pooling via PgBouncer"
- [ ] "Teams that add Celery workers with SQLite switch to Postgres within 3 months in 87% of cases"
- [ ] This intelligence only exists at scale — cannot be bootstrapped by a competitor

---

## T7 — The Underlying Engine (Months 1-4, parallel with everything above)

*These are the technical systems that make the product work. They serve the standard, not the other way around.*

### Graph Database (serves the decision graph)

- [ ] PostgreSQL junction table for Phase 1 (up to 10K decisions)
- [ ] FalkorDB via Cypher abstraction when needed
- [ ] Shared-dimension-count × recency-multiplier ranking query
- [ ] Attention-bias reordering (best match first, second-best last)
- [ ] The graph is INTERNAL — developers interact through governance.yaml and CLI, never the graph directly

### Contradiction Detection (serves `smm check`)

- [ ] Single LLM call with structured output (reasoning before verdict, ternary judgment)
- [ ] NLI cross-encoder pre-filter cuts 60-80% of LLM calls
- [ ] ~$0.002/check blended cost
- [ ] "Default to COMPATIBLE" instruction, mandatory evidence citation
- [ ] Freeze existing violations on adoption
- [ ] The detection engine is INTERNAL — developers see pass/fail in `smm check` output

### Observation Engine (Lattice — serves real-time monitoring)

- [ ] SDK monkey-patching for LLM calls (existing)
- [ ] TaintedStr provenance tracking (existing, fix 6 breakage points)
- [ ] File change tracking via git diff
- [ ] Dependency mutation tracking
- [ ] Scope creep detection via embedding similarity
- [ ] Intent drift detection via periodic LLM check
- [ ] Pattern violation detection via lint before/after
- [ ] HLC replacing vector clocks at >5 agents
- [ ] Lattice is INTERNAL — developers see the seven golden signals summarized as traffic-light indicators

### Prevention Layer (serves `smm apply`)

- [ ] Auto-generate CLAUDE.md, .cursorrules, AGENTS.md from decision graph
- [ ] Three-tier context injection (Always/Auto-attached/On-demand)
- [ ] Priority scoring: violation frequency × severity × recency × file relevance
- [ ] Rule conflict resolution (later decisions override earlier ones on same dimension)
- [ ] Regeneration via git hooks on decision change

### Audit Infrastructure (serves compliance)

- [ ] Merkle-tree audit log (RFC 6962) replacing JSONL
- [ ] Ed25519 signing of tree heads
- [ ] RFC 3161 external anchoring (weekly)
- [ ] Inclusion/consistency proofs for evidence packages
- [ ] Hot/warm/cold storage tiers

---

## T8 — The Defensive Features (Months 9-18)

*These are the technically hard systems that become relevant only AFTER the standard is established. Do NOT build these before the format, CLI, plugins, and integrations are adopted.*

### Formal Methods (Z3 + LTL) — Month 9+

- [ ] Z3 Boolean SAT for top 10 architectural rules
- [ ] 3 Dwyer patterns as Python governance monitors
- [ ] Only build when `smm check` needs provably correct answers for enterprise customers

### Code Property Graphs (Joern) — Month 12+

- [ ] Full AST + CFG + PDG for deep code understanding
- [ ] Only build when Tree-sitter queries can't detect a governance violation that matters

### eBPF Trust Layer (Tetragon) — Month 18+

- [ ] Kernel-level unforgeable observation
- [ ] Only build when enterprise customers require "prove what the agent actually did at the OS level"

### Fine-Tuned Contradiction Model — Month 12+

- [ ] Train on customer resolution data + ContraGen synthetic data
- [ ] Only build when you have 5,000+ labeled examples

---

## The Anti-Pattern Checklist

Before building any feature, check against the four deaths:

| Anti-Pattern | What Killed | Check |
|---|---|---|
| **Owning the whole stack** | Docker Swarm | Am I trying to own a layer the ecosystem wants open? |
| **No social platform** | Mercurial | Does this create network effects or just better technology? |
| **Value absorbable as a feature** | Bower | Could GitHub/JetBrains add this as a checkbox? |
| **Bad DX / IDE neglect** | Flow | Does this make the VS Code experience worse? |

---

## Success Metrics (Not Revenue — Adoption)

| Metric | 6 months | 12 months | 18 months |
|--------|----------|-----------|-----------|
| Projects with `governance.yaml` | 500 | 5,000 | 25,000 |
| Community governance rules published | 10 | 50 | 200 |
| Shareable configs on npm | 3 | 15 | 50 |
| CI/CD pipelines running `smm check` | 50 | 500 | 5,000 |
| MCP server installations | 200 | 2,000 | 10,000 |

Revenue follows adoption. Adoption follows becoming the assumed layer.

---

## The One-Sentence Technical Strategy

**Build the format (`governance.yaml`), the CLI (`smm init/check/apply`), and the plugin ecosystem (`extends: "@smm/recommended"`) — then make it so deeply integrated into VS Code, Git hooks, CI pipelines, and MCP that removing governance feels like removing Git.**

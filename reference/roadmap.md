# VT Protocol: Product Roadmap

**Core narrative: "Your AI agents are silently changing your product. We stop that."**

**Marketing thesis (METR RCT Study 2026):** Developers perceive a 20% speedup with AI coding tools, but controlled trials show a 19% actual SLOWDOWN — because AI generates code that violates unseen codebase constraints. VT Protocol closes this gap by constraining agents to the decision graph.

Not token savings. Not observability. Product integrity.

---

## Phase 1 — The Demo (Months 1-4) ✅ COMPLETE

**493 tests passing. 5 sprints delivered.**

### Axiom Hub (Decision Engine) ✅

- [x] Migrated from archived Kuzu to PostgreSQL junction tables with Cypher abstraction layer
- [x] 46-dimension taxonomy across 7 facets, 30+ auto-detectable without LLM
- [x] Two-stage contradiction detection: NLI cross-encoder pre-filter (threshold 0.3, eliminates 60-80%) → Claude Haiku 4.5 structured output (reasoning BEFORE verdict, ternary judgment, mandatory evidence citation, ~$0.002/check)
- [x] "Default to COMPATIBLE" in system prompt
- [x] PostgreSQL shared-dimension-count × recency-multiplier ranking, attention-bias reordering
- [x] `isError` gate — agent must address conflicts before proceeding
- [x] Freeze existing violations on adoption (SonarQube CaYC)
- [x] Web dashboard (Cytoscape.js graph, contradiction resolution, Cmd+K palette)
- [x] Merkle-tree audit log (RFC 6962, Ed25519 signing, inclusion proofs)
- [x] ContraGen synthetic contradiction generation for training data
- [x] Self-consistency voting when confidence < 0.6 (3 calls, majority vote)
- [x] governance.yaml schema and parser with extends support

### Lattice (Observation Engine) ✅

- [x] SDK monkey-patching for Anthropic + OpenAI (sync + async, streaming wrappers)
- [x] TaintedStr with 30+ method overrides and 6 breakage workarounds
- [x] Vector clocks for causal ordering
- [x] File change tracking (SHA-256 snapshot diffing, path categorization)
- [x] Dependency mutation tracking
- [x] Tree-sitter + regex fallback for Python + TypeScript
- [x] Seven golden signals detection
- [x] Secrets detection and redaction (<1ms, 12 regex patterns)
- [x] Async event bus (asyncio.Queue pub/sub, 6 event types)

### Prevention Layer ✅

- [x] RuleSync: auto-generate CLAUDE.md, .cursor/rules/*.mdc, AGENTS.md
- [x] Three-tier context injection (Always / Auto-attached / On-demand)
- [x] Priority scoring algorithm
- [x] Regeneration via git hooks

### MCP Server ✅

- [x] 5 tools via FastMCP (check_before_coding, validate_change, get_project_decisions, report_decision, get_resolution)

### Integrations ✅

- [x] GitHub App — PR architecture review comments with status checks
- [x] Git hooks — pre-commit check, post-commit audit log + git trailers

### Infrastructure ✅

- [x] Docker Compose (PostgreSQL + FalkorDB optional + VT Protocol server)
- [x] Integration test suite (159 tests, end-to-end flows)

---

## Phase 2 — Team Product (Months 5-8)

**Goal:** 3 paying teams. $5K MRR. Launch Team tier at $29/seat/month.

**Persona focus: TECH LEAD ONLY.** Building PM/QA/CISO views simultaneously is fatal for a solo founder. Bottom-up adoption driven by a single champion. (Gemini critique, validated by Snyk's early playbook.)

### Axiom Hub — Tech Lead Experience

- [ ] Tech lead view with blast radius visualization — the ONE view that matters
- [ ] Architecture quality gates — binary pass/fail, 2 conditions only
- [ ] **Snyk-style backlog trickle** — one auto-fix PR per sprint
- [ ] **CodeRabbit-style one-click resolution** in PR comments — ABSOLUTE UX PRIORITY
- [ ] Each contradiction offers 2-3 resolution paths
- [ ] **Learn from dismissals** — auto-tune thresholds per dimension
- [ ] Confidence scoring — categorical (high/medium/low)
- [ ] Team management (invites, roles, permissions)
- [ ] Basic routing via CODEOWNERS file (not role-based yet)

### LLM Judge Improvements (from Gemini academic critique)

- [ ] **IRT calibration** (Chen et al. 2026) — Item Response Theory Graded Response Model. Track Wasserstein distance between Haiku judge verdicts and human resolutions. Trigger mandatory human review when judge drifts.
- [ ] **Soft-label NLI distributions** (Madaan et al. 2025) — full softmax distribution instead of hard 0.3 cutoff. Graceful degradation for vague constraints.
- [ ] **DocInfer hierarchical document graphs** (Mathur et al. 2022) — maintain inter-sentence relations in NLI pre-filtering instead of naive chunking
- [ ] **POLARIS validator-gated execution** (Moslemi et al. 2026) — validate agent's proposed plan via MCP PreToolUse hook before code generation

### Lattice

- [ ] Configuration change sensitivity scoring (critical / warning / info)
- [ ] Scope creep detection (embedding similarity vs task prompt)
- [ ] Pattern violation detection (lint before/after)
- [ ] PagerDuty Events → Alerts → Incidents abstraction
- [ ] Agent Trajectory Monitors (sequence analysis, loop detection, LTL-lite)
- [ ] Add Go and Java language support

### Integrations

- [ ] Slack — notifications to code owners, inline triage actions
- [ ] Jira/Linear — bidirectional sync

### Exception Handling (four-tier model)

- [ ] Auto-waivers (permanent, logged)
- [ ] Standard exceptions (30-day, single approval)
- [ ] Elevated exceptions (90-day, dual approval)
- [ ] Break-glass overrides (24-72 hours, mandatory review)
- [ ] Recurring exceptions → "rule needs updating" report

---

## Phase 3 — Enterprise Moat (Months 9-18)

**Goal:** 2 enterprise contracts. $500K ARR. Launch Enterprise tier.

### Expand Beyond Tech Lead

- [ ] **CISO compliance view** — Merkle audit trail + external RFC 3161 anchoring, AI vs human code attribution, EU AI Act / SOC 2 / HIPAA mapping, one-click evidence export. THIS is the enterprise sale.
- [ ] Role-based routing engine — dimension → role mapping (database → tech lead, auth → tech lead + CISO)
- [ ] **Dual-authorization sign-off** for role disagreements

### Axiom Hub

- [ ] Z3 SMT solver — Boolean SAT only, <100 variables, 5s timeout, 10 rules. 50 lines. Enterprise "formal verification" story.
- [ ] Fine-tuned contradiction model (after 5,000+ labeled examples)
- [ ] **DRAFT approach** (Dhar et al. 2025) — auto-generate governance.yaml rules from legacy codebases. Lowers adoption barrier dramatically.
- [ ] Temporal decision graph (valid_from/valid_until/superseded_by)
- [ ] Agent registry + Zero Standing Privileges (dynamic time-bound tokens)
- [ ] Auto-generated architecture fitness tests
- [ ] Dynamic graph pruning with Salsa durability vectors
- [ ] Custom dimension taxonomies

### Platform Integrations

- [ ] **Microsoft Agent Governance Toolkit** — integrate via PolicyProviderInterface. Draft off their distribution.
- [ ] **JetBrains Central** — serve as architectural history backend for their semantic layer
- [ ] Remain standalone product that ALSO plugs into these platforms

### Open Specification (moved from months 4-8 per Gemini)

- [ ] Publish ADGP — only AFTER 5,000+ projects use governance.yaml
- [ ] Submit to Linux Foundation Agentic AI Foundation
- [ ] Docker donated containerd years after dominance. Don't donate leverage prematurely.

### Lattice

- [ ] Intent drift detection
- [ ] Duplication detection across sessions
- [ ] Ghost dependency detection
- [ ] API consumer impact detection
- [ ] Session replay (tree/waterfall/graph views)
- [ ] Hybrid Logical Clocks (replacing vector clocks)
- [ ] OpenTelemetry GenAI conventions
- [ ] sys.monitoring (PEP 669) as supplementary observation layer

### Enterprise Features

- [ ] SSO (SAML/OIDC) + SCIM provisioning
- [ ] On-premise/VPC (Helm chart)
- [ ] SOC 2 Type II certification
- [ ] Multi-repo support
- [ ] Webhook event system
- [ ] OpenTelemetry SIEM export

---

## Phase 4 — Platform (Months 18-36)

**Goal:** $5M ARR. Category definition.

### Additional Role Views (moved from Phase 2 per Gemini)

- [ ] **PM view** — Living Specifications, PRD-to-code mapping, requirements coverage
- [ ] **QA view** — behavioral contract violations, testing dashboard

### Deep Analysis (requires engineering maturity)

- [ ] Full Code Property Graphs with Joern (AST + CFG + PDG)
- [ ] GumTree AST diffs
- [ ] Salsa-style incremental computation
- [ ] eBPF trust layer via Tetragon
- [ ] LTL runtime monitors (Dwyer patterns)
- [ ] **Agent Behavioral Contracts** (Bhardwaj 2026) — Lyapunov stability for bounding drift

### Multi-Agent Coordination

- [ ] Cross-agent causal intelligence
- [ ] Decision collision detection
- [ ] Resolution broadcast to all active sessions
- [ ] **Automated negotiation broker**

### Platform Extension

- [ ] Infrastructure governance (Terraform, K8s)
- [ ] Business logic extraction
- [ ] Bias/fairness monitoring
- [ ] Cross-company architectural intelligence (anonymized)
- [ ] Architecture DNA fingerprinting
- [ ] Shadow MCP discovery

---

## Phase 5 — Category Winner (Years 3-5)

**Goal:** $20M+ ARR.

- [ ] Predictive governance
- [ ] Auto-resolve low-risk contradictions
- [ ] Marketplace for governance rules and dimension taxonomies
- [ ] Industry compliance modules (HIPAA, PCI-DSS, FedRAMP)
- [ ] Open agent identity specification
- [ ] EU data residency

---

## Pricing (Updated per Gemini)

| Tier | Price | What's included |
|------|-------|-----------------|
| **Free** | $0 | **Unlimited decisions for single repo.** Dashboard, PR comments, agent file generation, Merkle audit. NO ceiling — let adoption compound. |
| **Team** | $29/seat/month (min 3) | Cross-repo analytics, routing via CODEOWNERS, Slack, quality gates, exceptions, Jira/Linear. |
| **Enterprise** | $30-150K/year | CISO compliance view, external audit anchoring, SSO/SCIM, on-prem, Z3 verification, agent registry, Microsoft AGT + JetBrains Central integrations. |

---

## Key Technical Decisions (Locked)

- **LLM stays as judgment engine** — NLI pre-filter ROUTES (kills 60-80%), Claude Haiku 4.5 JUDGES (structured output, reasoning before verdict). ~$0.002/check. IRT calibration tracks drift.
- **Prevention over detection** — auto-generate agent files, MCP preflight checks
- **PR-native delivery** — GitHub App comments, not a separate dashboard
- **PostgreSQL first** — junction tables handle 100K decisions at <15ms. FalkorDB optional.
- **Freeze on adoption** — never require fixing legacy debt
- **5 MCP tools** — each delivers complete answer in one call
- **TaintedStr stays** — 30+ overrides. Add sys.monitoring in Phase 3. YASA-style static taint in Phase 4 with CPGs.
- **YAML stays** — governance.yaml. JSON Schema validation. CUE optional for enterprise in Phase 4.
- **Tech Lead first** — sole persona Phase 1-2. CISO Phase 3. PM/QA Phase 4.

---

## Fundraising Narrative

"AI agents write 42% of code heading to 80%. A randomized controlled trial showed developers PERCEIVE a 20% speedup but ACTUALLY slow down 19% — because AI generates code that violates unseen constraints. VT Protocol eliminates this gap. We built the decision graph — catches contradictions across agents, routes to the right human, prevents problems by injecting context into every session. EU AI Act requires audit trails by August 2026. Phase 1 shipped: 493 tests, two-stage contradiction detection at $0.002/check, Merkle-tree audit with Ed25519 signatures. We're the only cross-agent governance platform."

**Target raise:** $2-4M pre-seed/seed
**Target investors:** YC, Felicis, Lightspeed, Basis Set

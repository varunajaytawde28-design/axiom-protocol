# VT Protocol: Technical Roadmap

**Every technical decision answers: does this make us the assumed layer?**

**Phase 1 status: ✅ COMPLETE — 493 tests, 0 failures.**

---

## T1 — The Format ✅ DONE

- [x] `governance.yaml` — YAML with constrained schema, `extends` for shareable configs, kebab/snake case support
- [x] `.smm/` directory — auto-created, hidden, self-contained (decisions/, cache/, generated/, audit/)
- [x] Decision record format — superset of ADR with YAML frontmatter + dimension tags + graph edges
- [x] governance.yaml parser → validated Pydantic model (GovernanceConfig)

**YAML stays** (Gemini recommended CUE — rejected because nobody knows CUE, YAML is universally tooled). Add JSON Schema validation in Phase 2. Consider CUE as optional enterprise format in Phase 4.

---

## T2 — The CLI ✅ DONE

- [x] `smm init` — creates .smm/, governance.yaml, detects architecture via taxonomy, generates decisions, installs hooks, creates .mcp.json
- [x] `smm check` — terraform plan equivalent. Shows contradictions without blocking. Exit code 0/1.
- [x] `smm apply` — generates CLAUDE.md, .cursorrules, AGENTS.md via RuleSync
- [x] `smm dashboard` — launches FastAPI dashboard
- [x] `smm serve` — starts MCP server

---

## T3 — The Plugin Ecosystem (Months 5-8)

- [ ] **Dual-channel distribution** (Gemini correction: npm-only is ecosystem mismatch for Python tool)
  - PyPI for Python projects: `pip install smm-config-recommended`
  - npm for JS/TS projects: `npm install @smm/config-recommended`
- [ ] `@smm/config-recommended` — opinionated defaults, ships day one
- [ ] `@smm/config-security-baseline` — auth, encryption, secrets rules
- [ ] Agent providers: `smm-provider-claude`, `smm-provider-cursor`, `smm-provider-agents-md`
- [ ] Custom rule interface: `(decision, context) → {pass, message, severity}`
- [ ] Get 2-3 visible companies to publish governance configs (Airbnb ESLint moment)

---

## T4 — The Integration Density ✅ PARTIALLY DONE + Months 5-8

### Done ✅
- [x] Git hooks (pre-commit check, post-commit audit)
- [x] GitHub App (PR comments, status checks)
- [x] MCP server (5 tools via FastMCP)
- [x] Docker Compose for team deployment

### Phase 2
- [ ] VS Code extension — real-time governance feedback (ESLint squiggly-line pattern). **Flow died because VS Code integration was terrible. Non-negotiable.**
- [ ] CI/CD pipeline gate — GitHub Action: `uses: vt-protocol/governance-check@v1`
- [ ] Framework scaffolding — get `smm init` into create-next-app, create-vite, etc.
- [ ] MCP Gateway/Proxy mode — sit between agent and existing MCP servers, intercept transparently

---

## T5 — The Open Specification (Months 9-18, MOVED per Gemini)

**Was months 4-8. Moved to Phase 3.** Docker donated containerd years after dominance. Terraform went BSL after a decade. Don't donate leverage at pre-seed.

- [ ] Publish ADGP only after 5,000+ projects use governance.yaml
- [ ] Decision record format, dimension taxonomy, graph edges, contradiction interface
- [ ] Submit to Linux Foundation Agentic AI Foundation
- [ ] Publish as RFC-style specification

---

## T6 — The Network (Months 6-12)

- [ ] Governance rule registry — community rules with download counts as trust signals
- [ ] Decision template library — starter decisions for common stacks (Next.js + Prisma, Django + Postgres, etc.)
- [ ] Cross-project intelligence (anonymized, opt-in)

---

## T7 — The Underlying Engine ✅ DONE + Phase 2 improvements

### Done ✅
- [x] PostgreSQL junction tables with Cypher abstraction (shared-dimension-count × recency ranking)
- [x] Two-stage contradiction detection (NLI cross-encoder → Claude Haiku 4.5 structured output)
- [x] Tree-sitter + regex fallback (Python + TypeScript, imports/classes/functions/decorators)
- [x] Merkle-tree audit log (RFC 6962, Ed25519 signing, inclusion proofs, SQLite backend)
- [x] File-hash cache for incremental analysis
- [x] Asyncio event bus (Lattice → Axiom Hub)
- [x] TaintedStr with 30+ overrides and 6 breakage workarounds
- [x] SDK monkey-patching (Anthropic + OpenAI, sync + async + streaming)
- [x] Seven golden signals detection
- [x] Secrets detection and redaction (<1ms)
- [x] Self-consistency voting (confidence < 0.6 triggers 3-call majority vote)
- [x] ContraGen synthetic contradiction generation

### Phase 2 Improvements
- [ ] **IRT calibration for LLM judge** (Chen et al. 2026) — Wasserstein distance tracking between Haiku verdicts and human resolutions. Auto-trigger human review on drift.
- [ ] **Soft-label NLI** (Madaan et al. 2025) — full softmax distribution instead of hard 0.3 threshold
- [ ] **DocInfer hierarchical NLI** (Mathur et al. 2022) — inter-sentence relations during pre-filtering
- [ ] **POLARIS validator-gated execution** (Moslemi et al. 2026) — validate agent plan before code generation
- [ ] NLI fine-tuning on architecture corpus (Schopf et al. 2025) — domain-specific NLI instead of generic cross-encoder

### Phase 3 Improvements
- [ ] Z3 Boolean SAT for 10 architectural rules (50 lines, <100 variables, 5s timeout)
- [ ] DRAFT approach for auto-generating governance rules from legacy code
- [ ] Temporal edges (valid_from/valid_until)
- [ ] Dynamic graph pruning with durability vectors
- [ ] sys.monitoring (PEP 669) supplementary observation
- [ ] HLC replacing vector clocks

---

## T8 — The Defensive Features (Months 18+)

Only build AFTER the standard is established:

- [ ] Full Code Property Graphs with Joern (requires JDK 21, 10GB+ RAM — too heavy for Phase 1-2)
- [ ] GumTree AST diffs (structural moves, not line-level)
- [ ] Salsa-style incremental computation (demand-driven, early cutoff)
- [ ] eBPF trust layer via Tetragon (Linux-only, needs root — Phase 4 enterprise differentiator)
- [ ] LTL runtime monitors (Dwyer patterns, 3 patterns as Python functions first)
- [ ] Agent Behavioral Contracts (Bhardwaj 2026) — Lyapunov stability analysis
- [ ] Fine-tuned contradiction model (Llama 3 8B with QLoRA, after 5K+ labeled examples)
- [ ] YASA-style Unified AST static taint analysis (replaces TaintedStr for deep analysis when CPGs arrive)

---

## Anti-Pattern Checklist

Before building any feature:

| Anti-Pattern | What It Killed | Check |
|---|---|---|
| Owning the whole stack | Docker Swarm | Am I trying to own a layer the ecosystem wants open? |
| No social platform | Mercurial | Does this create network effects or just better tech? |
| Value absorbable as a feature | Bower | Could GitHub/JetBrains add this as a checkbox? |
| Bad DX / IDE neglect | Flow | Does this make the VS Code experience worse? |
| Premature standardization | N/A (Gemini) | Am I donating leverage before proving dominance? |

---

## Success Metrics

| Metric | 6 months | 12 months | 18 months |
|--------|----------|-----------|-----------|
| Projects with governance.yaml | 500 | 5,000 | 25,000 |
| Community governance rules | 10 | 50 | 200 |
| CI/CD pipelines running smm check | 50 | 500 | 5,000 |
| MCP server installations | 200 | 2,000 | 10,000 |
| Contradictions detected | 5,000 | 100,000 | 1,000,000 |
| Human resolutions (training data) | 500 | 5,000 | 50,000 |

Revenue follows adoption. Adoption follows becoming the assumed layer.

---

## The One-Sentence Strategy

**Build the format (governance.yaml), the CLI (smm init/check/apply), and the plugin ecosystem — then embed so deeply into VS Code, Git hooks, CI pipelines, and MCP that removing governance feels like removing Git.**

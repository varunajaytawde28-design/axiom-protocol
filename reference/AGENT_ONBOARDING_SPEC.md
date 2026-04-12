# VT Protocol — LLM Provider Selection & Agent Onboarding Spec

## Feature 1: LLM Provider Selection

### The Problem

Contradiction detection is hardcoded to Claude Haiku 4.5 via the Anthropic API. Users might have:
- Only an Anthropic subscription (Claude)
- Only an OpenAI key (GPT)
- Ollama running locally (free, no API key)
- No LLM access at all

### CLI Flow (during `vt init`)

```
Contradiction Detection Setup
─────────────────────────────
VT Protocol uses an LLM to judge architectural contradictions.
The NLI pre-filter (local, no API needed) always runs first.

Which LLM provider for deep contradiction analysis?

  1. Anthropic (Claude Haiku 4.5) — fast, $0.002/check
     Requires: ANTHROPIC_API_KEY environment variable
  
  2. OpenAI (GPT-4o-mini) — comparable speed and cost
     Requires: OPENAI_API_KEY environment variable
  
  3. Ollama (local) — free, private, no data leaves your machine
     Requires: Ollama running at localhost:11434
     Recommended model: llama3:8b or mistral:7b
  
  4. None — NLI pre-filter only, no LLM judgment
     Lower accuracy but zero cost and zero external calls
     Good for: evaluation, air-gapped environments, cost-sensitive teams

Choose [1-4]: 3

Ollama selected. Testing connection...
  ✓ Ollama running at localhost:11434
  Available models: llama3:8b, mistral:7b, codellama:13b
  
Which model? [llama3:8b]: llama3:8b
  ✓ Model responds correctly.

LLM provider saved to governance.yaml.
```

### Standalone Command

```bash
vt config llm              # Re-run the LLM provider wizard
vt config llm --provider openai --model gpt-4o-mini   # Non-interactive
```

### governance.yaml Schema

```yaml
model:
  provider: ollama          # anthropic | openai | ollama | none
  model: llama3:8b          # model identifier
  api_key_env: null         # env var name (null for ollama/none)
  base_url: http://localhost:11434/v1   # custom endpoint (ollama, Azure, etc.)
  temperature: 0.0          # 0.0 for deterministic, 0.7 for voting
  timeout_seconds: 10       # max wait per LLM call
  fallback: nli-only        # what to do if LLM fails (nli-only | error | skip)
```

### Dashboard UI (/settings page)

```
┌─────────────────────────────────────────────────────┐
│  LLM Provider Settings                              │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Provider:  [Ollama      ▾]                        │
│  Model:     [llama3:8b   ▾]                        │
│  Endpoint:  [localhost:11434  ]                     │
│  Timeout:   [10] seconds                           │
│  Fallback:  [NLI-only    ▾]                        │
│                                                     │
│  Status: ● Connected (avg 340ms/check)             │
│                                                     │
│  [Test Connection]  [Save]                          │
│                                                     │
│  ── Usage Stats ──────────────────────────────      │
│  Today: 47 checks | $0.00 (local)                  │
│  This week: 312 checks | $0.00                     │
│  NLI pre-filtered: 68% (212 skipped LLM)           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Contradiction Pipeline Behavior Per Provider

| Provider | NLI Pre-filter | LLM Judge | Confidence Range | Cost |
|----------|---------------|-----------|-----------------|------|
| Anthropic | Always (local) | Claude Haiku 4.5 structured output | 0.0 - 1.0 | ~$0.002/check |
| OpenAI | Always (local) | GPT-4o-mini structured output | 0.0 - 1.0 | ~$0.002/check |
| Ollama | Always (local) | Local model, JSON mode | 0.0 - 0.85 (capped — local models less reliable) | Free |
| None | Always (local) | Skipped | 0.0 - 0.6 (NLI-only scores capped) | Free |

When provider = `none`, contradictions are flagged but with lower confidence scores and a note: "NLI-only detection — LLM verification recommended for critical decisions."

---

## Feature 2: Agent Onboarding

### The Vision

Every AI agent working on your codebase goes through onboarding — just like a new team member. You decide:
- What it can see (context scope)
- What it can touch (file access)
- What it can decide (dimension permissions)
- What requires your approval (restricted dimensions)
- How long it can work unsupervised (session limits)

This isn't abstract token management. It's a guided conversation: "Here's the new agent. What should it be allowed to do?"

### CLI Flow (during `vt init` or standalone `vt onboard`)

```
Agent Onboarding
────────────────
Let's set up the AI agents that will work on this project.
Think of this like onboarding a new team member — what should they know,
what can they touch, and what needs your sign-off?

Which agents will work on this codebase?
  [x] Claude Code
  [x] Cursor
  [ ] Copilot
  [ ] Devin
  [ ] Windsurf
  [ ] Other (specify)

──────────────────────────────────────────

Setting up: Claude Code
  
  Give this agent a name (used in logs and dashboard):
  Agent name [claude-main]: claude-backend

  What's this agent's role?
    1. Full-stack developer — can touch everything
    2. Backend developer — server code, APIs, databases
    3. Frontend developer — UI, components, styles
    4. Infrastructure — Terraform, K8s, Docker, CI/CD
    5. Security reviewer — audit, compliance, auth
    6. Custom — define your own scope
  Choose [1-6]: 2

  ── File Access ──────────────────────────────

  Which files can "claude-backend" modify?
    Based on your role selection, we suggest:
    ✓ src/**
    ✓ api/**  
    ✓ services/**
    ✓ tests/**
    ✓ migrations/**
  
  Add more paths (comma-separated, or Enter to accept): 

  Which files are OFF LIMITS? (agent will be blocked)
    We suggest protecting:
    ✓ .env, .env.*
    ✓ secrets/**
    ✓ terraform/**
    ✓ .github/workflows/**
    ✓ infrastructure/**
  
  Add more blocked paths (or Enter to accept): production.yaml, k8s/**

  ── Decision Authority ───────────────────────

  What architectural decisions can "claude-backend" make on its own?
  
    ✅ Allowed (agent decides, logged in graph):
      [x] database
      [x] api-style
      [x] caching
      [x] concurrency
      [x] error-handling
      [x] logging
      [x] testing
      [x] state-management
    
    🔒 Restricted (agent proposes, human approves):
      [x] security
      [x] auth
      [x] deployment
      [x] infrastructure

  Want to customize? (y/n): n

  ── Context & Behavior ───────────────────────

  How much project context should "claude-backend" receive?
    1. Full — all decisions, full history, all dimensions
       ⚠ High token cost (~3,000-5,000 tokens per session)
    2. Relevant — only decisions matching files it's working on
       ✓ Recommended (~500-1,500 tokens per session)
    3. Minimal — only active contradictions and critical constraints
       Low token cost (~200-500 tokens per session)
  Choose [1-3]: 2

  Can "claude-backend" auto-resolve low-risk tensions?
    (TENSION only, non-critical dimensions, confidence > 0.85)
    y/n [n]: n

  Session time limit?
    How long can this agent work before requiring a fresh context load?
    Minutes (0 = unlimited) [60]: 60

  Should unresolved contradictions BLOCK this agent?
    If yes, agent cannot proceed until a human resolves the conflict.
    If no, agent gets a warning but can continue.
    y/n [y]: y

──────────────────────────────────────────

Setting up: Cursor

  Agent name [cursor-main]: cursor-frontend

  What's this agent's role?
  Choose [1-6]: 3

  ... (same flow, frontend-specific defaults) ...

──────────────────────────────────────────

Summary
═══════

  ┌─────────────────┬───────────────────┬──────────────────┐
  │ Agent           │ claude-backend    │ cursor-frontend  │
  ├─────────────────┼───────────────────┼──────────────────┤
  │ Type            │ Claude Code       │ Cursor           │
  │ Role            │ Backend           │ Frontend         │
  │ Can modify      │ src/, api/,       │ ui/, components/ │
  │                 │ services/, tests/ │ pages/, styles/  │
  │ Blocked from    │ .env, secrets/,   │ .env, secrets/,  │
  │                 │ terraform/, k8s/  │ api/, services/  │
  │ Decides alone   │ 8 dimensions      │ 6 dimensions     │
  │ Needs approval  │ security, auth,   │ security, auth,  │
  │                 │ deployment, infra │ api-style, data  │
  │ Context         │ Relevant          │ Relevant         │
  │ Auto-resolve    │ No                │ No               │
  │ Session TTL     │ 60 min            │ 60 min           │
  │ Block on conflict│ Yes              │ Yes              │
  └─────────────────┴───────────────────┴──────────────────┘

  Save this configuration? (y/n): y
  
  ✓ Agent profiles saved to governance.yaml
  ✓ MCP server will enforce these scopes
  ✓ Dashboard agent view updated

  To modify later: vt onboard --edit claude-backend
  To add an agent: vt onboard
  To view in dashboard: vt dashboard → /agents
```

### governance.yaml Schema

```yaml
agents:
  claude-backend:
    type: claude-code
    role: backend
    display_name: "Claude Backend"
    
    # File access control
    allowed_paths:
      - "src/**"
      - "api/**"
      - "services/**"
      - "tests/**"
      - "migrations/**"
    blocked_paths:
      - ".env"
      - ".env.*"
      - "secrets/**"
      - "terraform/**"
      - ".github/workflows/**"
      - "infrastructure/**"
      - "production.yaml"
      - "k8s/**"
    
    # Decision authority
    allowed_dimensions:
      - database
      - api-style
      - caching
      - concurrency
      - error-handling
      - logging
      - testing
      - state-management
    restricted_dimensions:
      - security        # Agent proposes, human approves
      - auth            # Agent proposes, human approves
      - deployment      # Agent proposes, human approves
      - infrastructure  # Agent proposes, human approves
    
    # Context behavior
    context_level: relevant   # full | relevant | minimal
    auto_resolve: false
    session_ttl_minutes: 60
    block_on_contradiction: true
    
    # Metadata
    owner: "techlead@company.com"    # Human accountable for this agent
    created_at: "2026-04-12"
    last_active: null

  cursor-frontend:
    type: cursor
    role: frontend
    display_name: "Cursor Frontend"
    allowed_paths:
      - "ui/**"
      - "components/**"
      - "pages/**"
      - "styles/**"
      - "public/**"
    blocked_paths:
      - ".env"
      - "secrets/**"
      - "api/**"
      - "services/**"
      - "migrations/**"
    allowed_dimensions:
      - frontend-framework
      - styling
      - bundler
      - state-management
      - routing
      - testing
    restricted_dimensions:
      - security
      - auth
      - api-style
      - database
    context_level: relevant
    auto_resolve: false
    session_ttl_minutes: 60
    block_on_contradiction: true
    owner: "frontend-lead@company.com"
```

### Dashboard UI — Agent Management (/agents)

```
┌─────────────────────────────────────────────────────────────────┐
│  Onboarded Agents                                    [+ Add]   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────────────────────────────────────┐       │
│  │  🟢 claude-backend               Claude Code         │       │
│  │                                                      │       │
│  │  Role: Backend Developer                             │       │
│  │  Scope: src/, api/, services/, tests/                │       │
│  │  Blocked: .env, secrets/, terraform/, k8s/           │       │
│  │  Dimensions: 8 allowed, 4 restricted                 │       │
│  │  Context: Relevant (~1,200 tokens/session)           │       │
│  │                                                      │       │
│  │  ── Activity ──────────────────────────────────       │       │
│  │  Last active: 2 hours ago                            │       │
│  │  Sessions this week: 14                              │       │
│  │  Decisions made: 23                                  │       │
│  │  Contradictions triggered: 3                         │       │
│  │  Blocked attempts: 1 (tried to modify .env)          │       │
│  │                                                      │       │
│  │  ── Health ────────────────────────────────────       │       │
│  │  Drift score: 0.12 / 0.70 (healthy)                  │       │
│  │  Scope violations: 0                                 │       │
│  │  Avg session duration: 34 min / 60 min limit         │       │
│  │                                                      │       │
│  │  [Edit Scope]  [View History]  [Revoke Access]       │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                 │
│  ┌──────────────────────────────────────────────────────┐       │
│  │  🟡 cursor-frontend              Cursor              │       │
│  │                                                      │       │
│  │  Role: Frontend Developer                            │       │
│  │  Scope: ui/, components/, pages/, styles/            │       │
│  │  Blocked: .env, secrets/, api/, services/            │       │
│  │  Dimensions: 6 allowed, 4 restricted                 │       │
│  │                                                      │       │
│  │  ── Activity ──────────────────────────────────       │       │
│  │  Last active: 5 hours ago                            │       │
│  │  Sessions this week: 8                               │       │
│  │  Decisions made: 11                                  │       │
│  │  Contradictions triggered: 1                         │       │
│  │  Blocked attempts: 0                                 │       │
│  │                                                      │       │
│  │  ── Pending Approvals ─────────────────────────       │       │
│  │  ⚠ cursor-frontend wants to decide on "api-style"   │       │
│  │    Proposed: Switch from REST to GraphQL             │       │
│  │    [Approve]  [Deny]  [Discuss]                      │       │
│  │                                                      │       │
│  │  [Edit Scope]  [View History]  [Revoke Access]       │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                 │
│  ┌──────────────────────────────────────────────────────┐       │
│  │  ⚫ devin-infra                   Devin (inactive)   │       │
│  │                                                      │       │
│  │  Role: Infrastructure                                │       │
│  │  Last active: 12 days ago                            │       │
│  │  Access expires: 3 days (ZSP token renewal needed)   │       │
│  │                                                      │       │
│  │  [Renew Access]  [Remove Agent]                      │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Agent Access Timeline (last 7 days)                            │
│  ─────────────────────────────────────────────────────────      │
│  Mon ████████░░░░░░░░░░░░ claude-backend (4 sessions)          │
│      ███░░░░░░░░░░░░░░░░░ cursor-frontend (2 sessions)        │
│  Tue ██████████████░░░░░░ claude-backend (7 sessions)          │
│      █████░░░░░░░░░░░░░░░ cursor-frontend (3 sessions)        │
│  Wed ████████░░░░░░░░░░░░ claude-backend (4 sessions)          │
│      ████████████░░░░░░░░ cursor-frontend (6 sessions)        │
│  ...                                                            │
│                                                                 │
│  Blocked Actions Log                                            │
│  ─────────────────────────────────────────────────────────      │
│  Apr 12 09:14  claude-backend tried to modify .env              │
│                → BLOCKED (file in blocked_paths)                │
│  Apr 11 16:22  cursor-frontend tried to decide on "database"   │
│                → BLOCKED (dimension restricted, sent to human)  │
│  Apr 10 11:03  cursor-frontend session expired (60 min TTL)     │
│                → SESSION_ENDED (context reload required)        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### How Agent Onboarding Connects to Everything Else

```
                    ┌─────────────────┐
                    │   vt onboard    │
                    │   (CLI wizard)  │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ governance.yaml │
                    │ agents: section │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │ MCP Server │  │ Dashboard  │  │  RuleSync  │
     │            │  │            │  │            │
     │ Enforces:  │  │ Shows:     │  │ Generates: │
     │ • Path     │  │ • Agent    │  │ • Per-agent│
     │   access   │  │   cards    │  │   CLAUDE.md│
     │ • Dimension│  │ • Activity │  │   with only│
     │   perms    │  │   timeline │  │   allowed  │
     │ • Session  │  │ • Blocked  │  │   rules    │
     │   TTL      │  │   actions  │  │            │
     │ • Context  │  │ • Drift    │  │            │
     │   level    │  │   score    │  │            │
     └────────────┘  └────────────┘  └────────────┘
              │              │              │
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │ Audit Log  │  │ Slack      │  │ Agent Files│
     │            │  │            │  │            │
     │ Every      │  │ "claude-   │  │ CLAUDE.md  │
     │ blocked    │  │  backend   │  │ only has   │
     │ action     │  │  tried to  │  │ rules for  │
     │ logged in  │  │  modify    │  │ backend    │
     │ Merkle     │  │  .env"     │  │ dimensions │
     │ tree       │  │            │  │            │
     └────────────┘  └────────────┘  └────────────┘
```

### MCP Server Enforcement

When an agent connects via MCP, every tool call checks the agent profile:

| Tool | What's checked |
|------|---------------|
| `check_before_coding(file_paths)` | Are ALL file_paths in `allowed_paths`? Is NONE in `blocked_paths`? Return only decisions for `allowed_dimensions`. |
| `validate_change(file_path, summary)` | Is file_path allowed? Does the change touch restricted dimensions? If restricted → return `isError: true` with "This dimension requires human approval." |
| `report_decision(dimensions)` | Are ALL dimensions in `allowed_dimensions`? If any are in `restricted_dimensions` → accept as "PROPOSED" status, route to human for approval. |
| `get_project_decisions(query)` | Filter results by `context_level`. Full = everything. Relevant = only matching dimensions. Minimal = only contradictions. |
| `get_resolution(id)` | No restriction — any agent can read resolutions. |

### Per-Agent Generated Files

When `vt apply` runs, it generates DIFFERENT files per agent:

```
.smm/generated/
  claude-backend/
    CLAUDE.md           ← Only backend rules (database, API, concurrency)
    .cursorrules        ← Not generated (not a Cursor agent)
  cursor-frontend/
    CLAUDE.md           ← Not generated (not a Claude agent)
    .cursor/rules/
      frontend.mdc      ← Only frontend rules (framework, styling, state)
  shared/
    AGENTS.md           ← Universal rules all agents read
```

Each agent gets a CLAUDE.md or .cursorrules tailored to its scope — not a global dump of every rule. A frontend agent doesn't see database rules. A backend agent doesn't see styling rules. This reduces token waste and prevents agents from making decisions outside their scope.

### Standalone Commands

```bash
vt onboard                           # Interactive wizard for new agent
vt onboard --edit claude-backend     # Edit existing agent profile
vt onboard --remove cursor-frontend  # Remove an agent
vt onboard --list                    # Show all onboarded agents
vt onboard --export                  # Export agent profiles as JSON
```

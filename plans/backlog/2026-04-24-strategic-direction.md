# Strategic-Direction Backlog — agent-plus

| Field | Value |
| :--- | :--- |
| Date | 2026-04-24 (filed 2026-04-26) |
| Branch | `claude/agent-plus-strategic-plan-M4iWS` |
| Status | **BACKLOG / NEEDS PREMISE CONFIRMATION** |
| Source | Two strategic-review documents pasted at session start |
| Restore-point A | `./2026-04-24-strategic-direction.appendix-a.md` (verbatim original review) |
| Restore-point B | `./2026-04-24-strategic-direction.appendix-b.md` (verbatim counter-review + extension question) |
| Format | Mimics gstack `/autoplan` output shape so a future formal autoplan run can layer dual-voice review on top without restructuring. |

**One-paragraph summary.** Two competing strategic framings landed back-to-back. The original review wants to evolve agent-plus into a "deterministic agent runtime" (workflow engine, state, execution loop, governance, observability). The counter-review rejects that framing and proposes the opposite: stay a deterministic *token-efficiency layer* — *"don't build the brain, build the reflexes"* — and instead expand the primitive set so the repo can be useful to others the way gstack is. This document adopts the counter-review's framing as the strategic direction (subject to user premise confirmation), triages every recommendation from the original review through the repo's six design patterns, and translates the agreed direction into a ranked, actionable backlog.

---

## Phase 0 — Intake & Scope Detection

**UI scope:** NO. There is no UI in this repo — every plugin is a single-file stdlib Python 3 CLI.
**DX scope:** YES. The repo *is* a developer/agent tool. DX is its core surface and gets a dedicated review phase below (3.5).

### Premises (the assumptions the rest of this plan rides on)

The conclusions below are only as good as these premises. Each is flagged with a confidence level. Items marked **REQUIRES CONFIRMATION** are the gate the user has to clear before anything in the ranked backlog gets built.

1. **P-IDENTITY** — agent-plus is a *deterministic token-efficiency layer for AI coding agents*, not a workflow runtime. Source: `AGENTS.md` ("The five design patterns" + "Philosophy: deterministic work belongs in scripts, not prompts"), `README.md` opener, and the counter-review. *Confidence: high. Status: documented in repo, treat as ratified unless user overrides.*
2. **P-CLAUDE-CODE-IS-THE-RUNTIME** — Claude Code (or any equivalent harness) is the runtime; agent-plus tools live underneath it as primitives. Anything that duplicates Claude Code's job (looping, state, permissioning, hooks, observability of the agent itself) is out of scope. *Confidence: high. Source: counter-review §"Path A duplicates Claude Code".*
3. **P-AUDIENCE** — the primary audience is "AI coding agents driving infra APIs," not human operators. Humans are a secondary read-only audience via `--json` → `jq`. *Confidence: high. Source: AGENTS.md "Writing style for READMEs" + every plugin's `--json` flag.*
4. **P-LEVERAGE-METRIC** — the unit of value is **agent-token cost reduction × tool-call reduction**. Every accepted backlog item must move at least one of these. *Confidence: high. Source: README "time savings, concretely" table.*
5. **P-DETERMINISM-OVER-FEATURES** — better to ship five `bin/` commands that are bulletproof and predictable than fifty that need an LLM in the loop to decide what they did. *Confidence: high. Source: AGENTS.md "Philosophy".*
6. **P-COMMUNITY-AMBITION** — the user is open to agent-plus becoming broadly useful (gstack-style) as long as that doesn't mean rebuilding it as a runtime. **REQUIRES CONFIRMATION** — this is the new strategic question and the answer determines whether the universal-primitive plugins in Phase 3.5 get prioritised.
7. **P-NO-RUNTIME-AMBITIONS** — the user does not want agent-plus to evolve into "the framework that runs the agents." **REQUIRES CONFIRMATION** — implicit in the counter-review but worth surfacing because the original review's pull is real and the market signal (LangGraph, CrewAI, etc.) is non-zero.
8. **P-PYTHON-STDLIB-ONLY** — every plugin stays single-file stdlib Python. No `requirements.txt`. No `pip install`. *Confidence: high. Source: AGENTS.md per-plugin conventions.*
9. **P-ENVELOPE-CONTRACT** — the existing `_with_tool_meta()` envelope (with `tool: {name,version}`, `--output` offload, `payloadShape`, `--version`) is a public contract surface, not an implementation detail. *Confidence: medium-high — implemented across plugins but not yet documented as such. Phase 3.5 proposes formalising it.*

### PREMISE GATE

> **The user must confirm or override premises P-COMMUNITY-AMBITION and P-NO-RUNTIME-AMBITIONS before any backlog item below `B-DOC-1` is started.** Everything ranked from `B-PLUGIN-*` onward depends on these two answers. The other premises are documented in repo identity files and treated as ratified.

---

## Phase 1 — CEO Review (strategy & scope)

Five strategic questions, structured findings.

### Right problem?

**Yes.** Token efficiency for agents driving infra APIs is a measurable, recurring, expensive pain. The README's "time savings, concretely" table monetises it: `~$10/12h → ~$0.03/12h` for hermes-remote, `40s → 8s` for railway-ops, `4 calls → 1 call` for langfuse-remote. Every row is a pain point that got codified after burning real time. This is the right problem because:

- It compounds — every plugin ships a permanent cut to the agent's tool-call budget.
- It's measurable — bytes-in, bytes-out, tool-calls-saved.
- It's underserved — Claude Code, Cursor, etc. ship the orchestrator, not the API-specific reflexes.

### Premises valid?

P-IDENTITY through P-ENVELOPE-CONTRACT all hold under the counter-review's logic. The two flagged for **REQUIRES CONFIRMATION** are the only structural risk.

### 6-month regret scenario

**The thing we'd most regret six months from now is having spent two months building a workflow-engine / state-layer / execution-loop only to discover Claude Code shipped equivalents and the primitive layer never got the universal plugins it needed to be broadly useful.** This is the original review's path. The counter-review's path has a smaller regret surface: the worst case is "we built five primitives, three of them turned out to be niche, two of them nobody else needs" — every primitive is independently valuable to the user even if zero others adopt it.

### Alternatives dismissed

**Path A: agent runtime.** Build workflow engine, persistent state, execution loop, tool governance, validation gates, replay/debug. Rejected because:

- Duplicates Claude Code's responsibilities (P-CLAUDE-CODE-IS-THE-RUNTIME).
- Violates AGENTS.md's "deterministic work in scripts, not prompts" — a runtime *needs* an LLM in the loop to be useful, which makes the system non-deterministic exactly where determinism is the value prop.
- Concentrates differentiation in a layer that's already commoditising (LangGraph, Mastra, AutoGen, CrewAI, Inngest, Temporal-for-agents).
- 6× the surface area for 0.5× the per-feature leverage.

**Path C: ship as managed SaaS.** Out of scope for this branch. Worth revisiting only after the primitive set is broad enough that *someone else* would pay for managed delivery.

### Competitive risk

- **Claude Code / Cursor ship equivalent built-ins.** Mitigant: agent-plus's leverage lives in API-specific scripts that no general-purpose harness will write for free. Even if Claude Code ships a generic "run script and capture output" primitive, the railway-ops `overview` payload shape isn't going to be in their stdlib.
- **OpenAI / Anthropic standardise tool-use schemas.** Helps us, doesn't hurt. The envelope already passes through any schema layer unmodified.
- **gstack moves down-stack into primitives.** Real risk — gstack is the closest existing comp. Mitigant: agent-plus's primitives are API-anchored (Railway, Vercel, etc.), gstack's are workflow-anchored (planning, review). The two could end up complementary rather than competitive.

---

## USER CHALLENGES (decision required)

This is the central call the user has to make. Surfaced in autoplan format.

### Challenge 1 — Runtime vs. tool layer

- **What the original review said.** Evolve agent-plus into a deterministic agent runtime: workflow engine, persistent state, execution loop, tool-governance plane, observability of agent decisions, validation gates between steps, optional replay/debug. ("You are one iteration away from building a system that defines how AI-assisted development actually runs.")
- **What both perspectives (counter-review + AGENTS.md) recommend instead.** Stay a deterministic *tool layer*. Don't build the brain — build the reflexes. The repo's identity (`AGENTS.md`: "deterministic work belongs in scripts, not prompts") and the documented design patterns (server-side aggregation, name-resolution, `--wait`, `--json`, value-stripping) describe a primitive set. A runtime would replace those patterns rather than reinforce them.
- **Why the counter-framing wins, on the principles already in repo.**
  - Pattern 1 (aggregate server-side) and Pattern 4 (`--json` everywhere) describe a tool, not a runtime.
  - Pattern 5 (strip values the agent shouldn't see) is enforcement at the *output boundary*, which is the only place a deterministic script can enforce anything. A workflow engine would enforce at *step boundaries* — but the agent making the step decisions is non-deterministic, so the enforcement decays.
  - The Stop hook (`.claude/hooks/check-readme-drift.sh`) already gives the user the one piece of "enforcement" they wanted (docs-stay-honest) at the right layer (Claude Code), not inside `bin/`.
- **What we might be missing.** The original review may be reading external market signals (the agent-runtime category is heating up; LangGraph, Mastra, CrewAI all funded) that the user is closer to than this analysis is. If the user's *personal* roadmap includes turning agent-plus into a product/SaaS one day, a runtime layer becomes the moat. The counter-review's framing optimises for usefulness; the original review's framing optimises for defensibility.
- **If we pick the tool-layer path and we're wrong, the cost is.** ~6 months building primitives while a runtime competitor captures the user's exact slot. Mitigant: token-efficiency primitives are still useful *inside* any runtime — worst case they become inputs to someone else's runtime, not orphaned.
- **If we pick the runtime path and we're wrong, the cost is.** ~6 months building workflow infrastructure that Claude Code makes redundant (or that the market converges on a different schema for), with no primitive set to fall back on. Higher tail risk.
- **Recommendation.** Adopt the counter-review's framing. Ratify P-IDENTITY, P-CLAUDE-CODE-IS-THE-RUNTIME, P-DETERMINISM-OVER-FEATURES, P-NO-RUNTIME-AMBITIONS. Treat any future runtime work as a strictly separate repo so it doesn't muddy this one's value prop.
- **User decision required.** Confirm Path B (tool layer) is the chosen direction, or override with Path A and re-run this plan.

### Challenge 2 — "Useful to other people the way gstack is?"

- **What the user asked.** "Could agent-plus be useful like this to other people?"
- **Honest answer.** Today, partially. Six of ten plugins (GitHub, Vercel, Linear, Supabase, Langfuse, OpenRouter) are directly useful to anyone on those stacks. Four (Hermes Agent, Coolify, Hetzner, Railway) are useful only to overlapping users. **Zero of the ten are universal primitives** — there's nothing in the repo that helps an agent operate on a *generic* codebase rather than a specific service.
- **What that means.** The structural gap between "personal infra layer" and "stdlib for AI coding agents" is *the missing universal-primitive tier*, plus a discoverability layer so users can find what's relevant to them.
- **What we might be missing.** Maybe the user *doesn't* want the broad audience. Building for others has a real maintenance tax (issues, PRs, version compat, doc rigour) and the user's stated priority is their own leverage. The honest answer to "could it be useful to others" is "yes, with work" — but whether that work is worth doing is the user's call.
- **Recommendation.** Treat broad usefulness as a *side-effect* of the universal-primitive tier, not the goal. If `repo-analyze` and `dep-graph` ship because they save the user's own agent tokens on every coding session, the fact that they're also broadly useful is a free win. Don't build any plugin *because* it's broadly useful.
- **User decision required.** Confirm P-COMMUNITY-AMBITION — are you in the "broad usefulness is a free side-effect, build the primitives anyway" camp, or the "I want to keep this personal, so don't build a plugin unless I'd use it tomorrow" camp? The ranked backlog below changes ordering depending on the answer.

---

## Phase 3 — Eng Review (architecture)

Each of the original review's "evolution" recommendations is triaged through the six design patterns documented in `AGENTS.md` ("aggregate server-side", "resolve by name", "`--wait` on async", "`--json` everywhere", "strip values"), the seventh in the README ("self-diagnosing output"), plus the AGENTS.md philosophy line ("deterministic work belongs in scripts, not prompts"). Decision codes: **REJECT** (auto-decided against principle), **ACCEPT** (auto-decided in favour), **PARTIAL** (in-scope under a narrowed framing), **DEFER** (out of scope this round, revisit).

| ID | Recommendation | Decision | Principle hit | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| `E-1` | Workflow engine (analyze→plan→validate→execute→review) | **REJECT** | Philosophy + Pattern 4 | Needs an LLM in the loop to choose transitions, which makes the system non-deterministic at exactly the layer the repo claims is deterministic. Claude Code already orchestrates step transitions. |
| `E-2` | Persistent state layer (JSON / SQLite / markdown) | **REJECT** | Philosophy | Stateful agents are non-deterministic by construction (state shapes the next decision). Stateless tools are the value prop — any plugin that needs context gets it via flags, not a stateful store. |
| `E-3` | Execution loop ("load state → execute → validate → store → continue") | **REJECT** | Pattern 4 (DRY w/ harness) | This is what Claude Code already does. Re-implementing it in agent-plus competes with the harness instead of complementing it. |
| `E-4` | Tool governance / sandboxing / whitelisting | **REJECT (in-plugin scope)** | Wrong layer | Tool permissioning belongs to the harness (`~/.claude/settings.json`), not to a CLI that the harness invokes. The `--output PATH` payload-offload pattern already covers the "agent shouldn't see this" subset of the same concern at the right layer. |
| `E-5` | Observability of agent decisions | **PARTIAL** | Pattern 6 + Pattern 1 | Reject the "agent observability" framing. **Accept** an opt-in `AGENT_PLUS_TELEMETRY=langfuse` env var that emits one Langfuse trace per CLI run with `{cmd, exit_code, payload_bytes, duration_ms}`. Reuses the existing `langfuse-remote` plugin. Marked **TASTE DECISION**: close call — could be done as nothing if the user wants to keep the layer truly stateless. |
| `E-6` | Validation gates between steps | **PARTIAL** | Pattern 7 | Reject the "step gates" framing. **Accept** extending the existing `payloadShape` in the `--output` envelope with a `confidence` and `validatedAgainst` field where the underlying API gives schema info (e.g. Vercel's REST schema). Same envelope, richer self-description. |
| `E-7` | Workflow versioning / replay / debug | **REJECT** | Pragmatic | Solves a problem stateless tools don't have. Replay = re-run the same command with the same args. Debug = read the JSON output. |
| `E-8` | Permission controls for tools | **DEFER** | Wrong layer | Same reasoning as E-4. Revisit only if a plugin needs to gate something *internally* (e.g. Supabase `rls-audit` already-implicitly read-only) — even then this is a flag, not a governance plane. |
| `E-9` | Audit trail for tool calls | **DEFER** | Pattern 6 | Already partially solved by the `tool: {name, version}` envelope field plus harness-level transcript. Revisit only if E-5 telemetry reveals a real gap. |

**Net effect.** Two `PARTIAL` accepts (telemetry, schema-aware envelope) — both consume existing plumbing rather than introducing new infrastructure. Five `REJECT`s — all auto-decided on documented principles, no user gate needed. Two `DEFER`s.

---

## Phase 3.5 — DX Review ("useful to other people?")

The new strategic question from the counter-review's extension. Treated as its own phase because it's the only place the plan's ranked output changes shape based on a user decision (P-COMMUNITY-AMBITION).

### Plugin reusability triage

Going through each of the ten plugins in `.claude-plugin/marketplace.json` and asking: "if a stranger landed on this repo today, would this plugin be useful to them without changes?"

| Plugin | Reusable for whom? | Verdict |
| :--- | :--- | :--- |
| `github-remote` | Anyone with a GitHub workflow | **Universal** |
| `vercel-remote` | Any Vercel user | **Universal** |
| `linear-remote` | Any Linear user | **Universal** |
| `supabase-remote` | Any Supabase user | **Universal** |
| `langfuse-remote` | Any Langfuse user | **Universal** |
| `openrouter-remote` | Any OpenRouter user | **Universal** |
| `coolify-remote` | Coolify users (smaller PaaS audience) | **Niche but well-scoped** |
| `railway-ops` | Railway users | **Niche but well-scoped** |
| `hcloud-remote` | Hetzner Cloud users | **Niche but well-scoped** |
| `hermes-remote` | Hermes Agent operators (very small group) | **Personal / overlapping users only** |

**Count: 6 universal-with-stack-match, 4 niche-but-clean, 0 generic primitives.** The structural gap between "personal infra layer" and "stdlib for AI coding agents" is *the missing universal-primitive tier* — plugins that help an agent on *any* codebase, regardless of which third-party APIs that codebase uses.

### Time-to-hello-world for a new user

- Install one plugin you already need: ~5 min (find marketplace, run `claude plugin install <name>@agent-plus`, set 1–2 env vars).
- Discover which of the ten plugins applies to your stack: ~30 min (read root README's table, click into each plugin's README).

The friction sits in **discoverability**, not in any individual plugin's setup. This matters because adding more plugins makes the discoverability problem worse unless we add a lookup tool.

### Five universal-primitive plugins worth proposing

Each of these would help the user's *own* agent today (so it passes the "would I use this tomorrow?" filter) and is also broadly useful (so it passes the side-effect-of-broad-usefulness filter). Each follows the established envelope contract: stdlib Python, single file, `bin/`, `skills/`, `--json`, `--output PATH`, `--version`, `tool: {name, version}` envelope.

1. **`repo-analyze`** — file-tree, language mix, framework detection, build-tool detection, top-level deps, entrypoints, README highlights. *Justification: today's "tell me about this repo" expands to 50+ Read tool calls before the agent has a working mental model. One JSON blob would replace that.* Reinforces Pattern 1 (aggregate). High agent-token-savings ceiling.
2. **`dep-graph`** — language-aware import graph + reverse-import lookup (`dep-graph imports-of src/foo.ts`, `dep-graph imported-by src/foo.ts`). *Replaces ad-hoc grep loops that the agent rebuilds from scratch every session.* Reinforces Pattern 1. Medium agent-token-savings, high tool-call savings.
3. **`diff-summary`** — structured classification of an open diff: file-level role labels (test/config/doc/source), risk-tier per file, public-API-touched flag, lines-added/removed/moved. *Lets the agent triage a PR or a working tree without reading every file.* Reinforces Pattern 1 + Pattern 4. Direct fit for code-review and pre-commit flows.
4. **`log-parse`** — generic stack-trace + error-bucket extraction across formats (Python tracebacks, Node `Error: ... at ...`, JSON-line logs, plain text). Outputs ranked errors with frequencies and sample line-numbers. *Today the agent reads raw log files and reinvents the parsing in-prompt every time.* Reinforces Pattern 1 + Pattern 5 (strip noise). Direct fit alongside existing `railway-ops build-logs`.
5. **`schema-extract`** — DB / API schema introspection from any of `*.sql`, `openapi.yaml`, `*.graphql`, Prisma, Drizzle, into one envelope. *Today the agent reads each of these files differently. A normalised envelope means downstream tools (codegen, doc gen, query helpers) get one input shape.* Reinforces Pattern 1 + Pattern 7 (self-diagnosing). Synergy with `supabase-remote gen-types`.

### Discoverability layer

`agent-plus list` and `agent-plus search "<keyword>"`, both reading from `.claude-plugin/marketplace.json`. Without this, growing the collection has diminishing returns — users can't find what they don't know exists. Tiny wrapper script, ~150 lines.

Stretch: `agent-plus search "deploy log"` returns ranked plugins whose READMEs hit the keyword, with the matched section quoted. Deterministic ranking (BM25 over README text, no LLM call).

### Standardised envelope as the public contract

The existing `_with_tool_meta()` helper (consistent across `bin/coolify-remote:75-81`, `bin/github-remote`, `bin/hcloud-remote`, `bin/railway-ops`) is a **de facto contract** — every plugin's JSON output has `tool: {name, version}` plus `--output` payload-offload semantics plus `payloadShape`. Today this is implicit: documented in commits, inconsistently surfaced in READMEs.

Promote it to an explicit, documented public contract. New section in root `README.md` — "Envelope Contract" — that says:

- Every plugin's `--json` output is a JSON object with these top-level keys: `tool`, `payload` (or, when `--output` is used, `payloadPath` and `payloadShape`).
- `tool.name` and `tool.version` are read from the plugin's `plugin.json` at runtime.
- `--output PATH` writes the payload to disk and returns a shape descriptor instead of the payload, so large blobs never touch the agent transcript.
- `--shape-depth N` controls how deep the shape descriptor recurses.
- `--version` prints `tool.version` and exits 0.

This becomes the surface third parties can rely on, the surface the proposed `agent-plus list` / `dep-graph` / `repo-analyze` plugins all extend, and the surface a future telemetry layer keys off.

### Token-savings telemetry as north star

Opt-in `AGENT_PLUS_TELEMETRY=langfuse` env var. When set, every plugin run emits one Langfuse trace via the existing `langfuse-remote` plugin: `{cmd, args, exit_code, payload_bytes, duration_ms, started_at}`. Aggregating these answers the question "how useful are these plugins to other people" *empirically* instead of by claim.

Off by default — privacy default is no network egress beyond the API the plugin already calls. `--telemetry off` overrides the env var. Same pattern as the existing `--env-file` precedence rule.

This is the only piece of "observability" that survives the principle triage: it observes *the tool*, not *the agent*. Aligns with E-5 PARTIAL.

---

## Tooling ideas surfaced from session mining

Side-channel input. The user asked: "useful tools / scripts / hooks / skills we can create that would really help people (myself included)? E.g. session-start hooks could auto-load project data within a `.agent-plus` folder (Supabase, Vercel, Railway overviews) so we have local details available from day one. We could put a pointer from `CLAUDE.md` / `AGENTS.md` to this overview explaining the tech stack and IDs and structure of things."

To inform this, an Explore agent mined 6 Claude Code session transcripts (`/root/.claude/projects/-home-user-agent-plus/*.jsonl`, ~700KB each) for repeated tool-call patterns. It found ~67 grep operations and ~60 directory-discovery operations across 6 sessions, most of them collapsible. Synthesised into the user's framing, the high-value ideas are:

### T-1 — `.agent-plus/` workspace + session-start manifest hook (highest leverage)

**Pain.** Every session begins with the agent re-discovering: which plugins are installed, which env vars are set, what the project's Supabase project ID is, which Vercel project corresponds to this folder, which Railway services exist, which branch we're on. Identical `find`, `ls`, `git status`, and per-plugin `*-remote ... list` calls every cold start.

**Proposal.** A `.agent-plus/` directory at the repo root with one or more JSON files:

- `.agent-plus/manifest.json` — installed plugins, versions, available skills, current branch.
- `.agent-plus/services.json` — for each plugin the user opts in, the *resolved* identity for this project (Supabase project ref, Vercel project ID, Railway env names, GitHub repo, Linear team ID). Values cached locally; secrets stay out.
- `.agent-plus/env-status.json` — which required env vars are set (names only), which are missing.

Populated by a new `agent-plus init` command (one-off discovery: hits each configured plugin once, caches answers). Refreshed by `agent-plus refresh` (idempotent). A SessionStart hook reads these files and surfaces them in the agent's first turn so day-one context is correct without rediscovery.

**CLAUDE.md / AGENTS.md pointer.** A new `## Project services` block in `AGENTS.md` (or the gstack-generated `CLAUDE.md` if installed) reads from `.agent-plus/services.json` so the agent learns "this repo's Supabase project is `abc`, Vercel project is `my-app`, Railway env is `production`" without burning tokens to find it.

**Pattern reinforced.** P1 (aggregate), P2 (resolve by name), P5 (strip values — only NAMES and IDs land in the manifest, never secrets). High agent-token-savings.

### T-2 — `git-context` skill / aggregator

**Pain.** Every session: `git status && git branch --show-current && git log --oneline -10` (7 occurrences across the mined sessions, sometimes more). Sequential, parses-prone, rebuilds context the harness already has.

**Proposal.** Skill `git-context` (or a flag on the SessionStart hook) returns one JSON: `{branch, isDirty, lastCommits[], stagedFiles, unstagedFiles, upstream, divergence}`. Stdlib subprocess wrapper around `git status --porcelain=v2 --branch` + `git log --oneline -n N`. ~80 lines.

**Pattern reinforced.** P1 (aggregate), P4 (`--json` everywhere).

### T-3 — `envcheck` validator

**Pain.** Mined transcripts show 21+ references to credentials/tokens; agent sometimes proceeds with a misconfigured env var and discovers the failure mid-task. No central validation.

**Proposal.** `agent-plus envcheck` walks every installed plugin's required env-var prefix list, verifies presence + format (length, prefix sanity), reports missing names — names only, never values. Output goes into `.agent-plus/env-status.json` so the SessionStart hook can warn at turn 1. Reuses the per-plugin "missing-config errors point to both `.env` and `~/.claude/settings.json`" convention.

**Pattern reinforced.** P5 (no value leakage), P7 (self-diagnosing).

### T-4 — Plugin checksum / drift hook

**Pain.** Plugins update; the agent uses a stale CLI signature; failure mode is silent (a flag the agent thought existed silently no-ops, or a renamed subcommand returns "command not found"). No early-warning.

**Proposal.** SessionStart hook compares `.agent-plus/plugin-checksums.json` against the actual `bin/<plugin>` files; if drift, regenerates and prints a one-line "plugin X updated since last session — re-read SKILL.md if behaviour changed" notice.

**Pattern reinforced.** P6 (self-diagnosing), guardrail.

### T-5 — `agent-plus list` / `agent-plus search` (discoverability)

Already proposed in Phase 3.5 — restated here because session mining confirmed the cost: 67 grep operations across 6 sessions, ~60% of which were the agent searching the agent-plus tree to figure out what's available. A discoverability tool collapses that to one call.

**Pattern reinforced.** P1 (aggregate), P2 (resolve by name).

### T-6 — `repo-context` (universal primitive, also surfaced under Phase 3.5 as `repo-analyze`)

The session-mined version of this plugin includes the cwd-specific bits: file-tree, language mix, framework detection, top-level deps, entrypoints, README highlights, **plus** the contents of `.agent-plus/services.json` if present. Same file as Phase 3.5's `repo-analyze` — one tool, two entry points (call it from the SessionStart hook for context bootstrapping, or call it ad-hoc when the agent lands in an unfamiliar repo).

### T-7 — Multi-step `curl|jq` collapser plugin

**Pain.** The agent still hand-rolls `curl ... | jq` chains for any service that doesn't have a plugin yet. Same pattern, recurring.

**Proposal.** Not actually a new plugin — better captured as a *guideline* for which APIs to wrap next. Use this section as a backlog feeder: track recurring `curl|jq` patterns from session mining as candidates for the next plugin.

### T-8 — `agent-plus doctor` (consolidated diagnostic)

Bundles T-3 (envcheck), T-4 (drift), T-2 (git-context), and a `--json` summary of `.agent-plus/manifest.json`. One command answers "why is my agent struggling on this repo" without running four separate checks.

**Pattern reinforced.** P1 (aggregate), P7 (self-diagnosing).

### Two ideas explicitly *not* taken from session mining

- **"Plugin registry shipped to agent as context"** (Explore agent's idea #8): rejected. Claude Code's harness already loads SKILL.md per plugin on demand. Pre-loading the whole registry is the kind of "context-bloat" the project's Pattern 5 explicitly fights.
- **"Workspace bootstrap that mkdir's planning folders"** (idea #4): folded into T-1 as a side-effect of `agent-plus init` rather than a standalone tool.

---





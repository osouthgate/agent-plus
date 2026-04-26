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

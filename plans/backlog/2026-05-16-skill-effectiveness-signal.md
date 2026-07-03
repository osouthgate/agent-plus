# Implementation Plan — Session-derived skill-effectiveness signal

| Field | Value |
| :--- | :--- |
| Date | 2026-05-16 |
| Status | Scoped, not started |
| Effort | Medium |
| Touches | `skill-plus` only — `feedback.py`, tests, possibly one shared walker helper |
| Triggered by | Competitor (Nua) review — see `PLANS.md` entry |

## 1. Problem

`skill-plus feedback` already joins two streams keyed by skill name
(`skill-plus/bin/_subcommands/feedback.py`):

- **stream-1** — explicit `skill-feedback` ratings (`_read_stream1`, line 69).
- **stream-2** — implicit session-mining signals (`_read_stream2`, line 243).

stream-2 has two structural limits that make it blind to **user-authored**
skills:

1. **Bash-only extraction.** `_walk_for_bash` (line 174) and
   `_identify_invocation` (line 219) only recognise a skill when it appears as
   the first token of a `Bash` tool_use command (`agent-plus <plugin>` or
   `<plugin>`). A skill invoked via the **Skill tool** or a **slash command**
   leaves no trace.
2. **Hard-coded plugin table.** Fallback / re-invocation tracking keys off
   `_FALLBACK_INDICATORS` / `_KNOWN_PLUGINS` (lines 32–51) — five framework
   plugins. An arbitrary user skill gets, at best, a raw invocation count and
   no "did it work?" signal.

`skill-feedback`'s README states the root weakness plainly: *"No retroactive
transcript scraping. Agent has to log explicitly."* The whole "flag if your
skill works" promise currently rests on the agent remembering to call
`skill-feedback log`.

## 2. Design boundary (read before designing)

`plans/backlog/2026-04-24-strategic-direction.md` records a hard, principled
rejection: **"Observability of the agent's decisions. Rejected."** Accepted
instead: deterministic *tool* signals (exit code / structural facts), opt-in.

This plan stays strictly on the accepted side:

- **In:** structural facts from the transcript — *this skill was invoked*; *the
  immediately-following `tool_result` had `is_error: true`*; *the same skill
  was invoked again within N tool calls*. These are deterministic, like an
  exit code.
- **Out (v0 non-goal):** any signal that requires inferring *why* the agent
  acted — "the agent abandoned the skill", "the user seemed unhappy",
  summarising agent reasoning. That is the rejected observability plane.

Privacy invariants are unchanged and reused, not re-litigated:
local-only, project-scope **consent gate already in place**
(`has_consent_for`, used at `scan.py:182`), `scrub_text` before any persistence,
**only derived counts persisted — never raw transcript**. We extract a boolean
(`is_error`) from `tool_result`, not its content. `skill-feedback`'s separate
"no transcript ingestion" contract is untouched (different tool); `skill-plus`
already ingests transcripts under consent today.

## 3. Proposed change (v0)

All changes confined to `skill-plus/bin/_subcommands/feedback.py` + tests.

### 3.1 Generalise the session walker

Replace the Bash-only `_walk_for_bash` (feedback.py:174) with a walker that
records, in file order, a typed event stream per session:

- `bash` — existing `Bash` tool_use command (unchanged behaviour).
- `skill` — `tool_use` with `name == "Skill"`; skill name from
  `input.skill` / `input.command`.
- `tool` — any other `tool_use`; capture `name` + `id` (to match a skill whose
  binary == name, and to correlate the result).
- `result` — `tool_result`; capture `tool_use_id` + `is_error` (and the
  error-marked-content fallback some schema variants use).
- `user_command` — user-turn slash-command markers (`<command-name>` tag).

Keep the existing duplication stance: do **not** import `scan`. Either inline
the new walker in `feedback.py` (consistent with today's deliberate copy) or
factor a single injected helper `_session_walk.py` — decide in the spike
(§6, open question).

### 3.2 Dynamic known-skill set

In `run()` (feedback.py:370), today's `known` set is hardcoded plugins +
stream-1 names + marketplace.json names (lines 390–405). Add: resolve the
project/global/plugin skill inventory via the existing `skill-plus where` /
`list` machinery and union those skill names + their declared `bin/` names into
`known`. Result: arbitrary user skills become first-class in stream-2.

### 3.3 New deterministic signal: error rate

Per skill, per invocation, look ahead for the matching `result` event by
`tool_use_id` (skill/tool events) or, for `bash` skill invocations lacking an
id, the next `result` in order. Count `is_error: true` → `errorCount`. Emit in
the stream-2 row:

```jsonc
"stream2": {
  "invocations": N,
  "errorCount": E, "errorRate": E/N,
  "reInvocationCount": R, "reInvocationRate": R/N,   // generalised to any skill
  "fallbackCount": F, "fallbackRate": F/N            // still table-driven, unchanged
}
```

`reInvocation` (feedback.py:291) generalises for free once §3.1/§3.2 land —
same-skill-within-window already works on the `known` set.

### 3.4 Feed the concern score

Extend `_concern_score` (feedback.py:349) with an `errorRate` term, weighted
below `fallbackRate` (fallback = "had a tool and didn't use it" is a stronger
negative than "used it and it errored once"). Suggested start:
`score += errorRate * 2.0`. Tune against fixtures (§6).

## 4. Scope

**In v0:** §3.1–§3.4. Skill-tool + slash-command detection, error signal,
generalised re-invocation, dynamic known-skill set, concern-score term, tests.

**Explicit non-goals:** abandonment / agent-intent inference; any new network
or upload; cross-machine or team aggregation server; changes to
`skill-feedback`'s contract or storage; new consent prompt (reuse the existing
project-scope grant — same files, additional tool types).

## 5. File touchpoints

| File | Change |
| :--- | :--- |
| `skill-plus/bin/_subcommands/feedback.py` | New typed walker; dynamic `known` set via `where`/`list`; `errorCount`/`errorRate` in stream-2 row; `_concern_score` term |
| `skill-plus/bin/_subcommands/_session_walk.py` *(maybe)* | Only if spike picks shared helper over deliberate duplication |
| `skill-plus/test/test_feedback.py` | Fixtures: Skill tool_use, slash-command turn, `tool_result` `is_error`, re-invocation; assert `errorRate` + concern ordering; assert user-authored skill (not in plugin table) now scored |
| `skill-plus/README.md` / `skills/skill-plus/SKILL.md` | Document the new stream-2 signal + reaffirm the deterministic-only boundary |
| `skill-plus/CHANGELOG.md` + `plugin.json#version` | Independent plugin version bump |

## 6. Spikes / open questions (resolve before coding)

1. **Transcript shape capture.** Record a real session that invokes a skill via
   the Skill tool and via a slash command; capture the exact JSONL for
   `tool_use` (`name:"Skill"`), the slash-command user turn, and the
   `tool_result` `is_error` field across current Claude Code versions. The
   walker must be schema-tolerant like the existing one (`_line_has_known_envelope`,
   scan.py:98).
2. **`tool_use_id` ↔ `tool_result` correlation** reliability across schema
   variants; define the ordered-fallback rule when no id is present.
3. **Shared walker vs duplication.** `feedback.py:171` deliberately forbids
   importing `scan` (slice independence / no merge conflicts). Decide: inline
   copy (consistent, more drift) vs one injected helper (DRY, couples slices).
4. **Consent.** Confirm reading additional tool types from the *same* session
   files under the *same* project grant needs no new consent surface; if legal/
   privacy nuance, add a one-line scope note to the consent record rather than
   a new prompt.
5. **Concern weight** for `errorRate` — tune on fixtures so a high-error
   user skill sorts above a low-rating-but-working one, without drowning the
   `fallbackRate` signal.

## 7. Validation

- `python3 -m pytest skill-plus/test/test_feedback.py -v` green, including new
  fixtures.
- Full suite: `python3 -m pytest skill-plus/test/ -v`.
- Manual: in a repo with a project-tier user skill that has no
  `skill-feedback` entries, `skill-plus feedback --pretty` now produces a
  stream-2 row with `invocations`/`errorRate` for that skill (today: nothing).

## 8. Out-of-band note

The competitor framing that triggered this is a hosted/telemetry product.
Copying the *capability* is in scope; copying the SaaS/telemetry model is an
explicit non-goal — it contradicts the local-first positioning that
`skill-feedback`'s README sells against ("Hosted alternatives ... post
telemetry to a third-party service").

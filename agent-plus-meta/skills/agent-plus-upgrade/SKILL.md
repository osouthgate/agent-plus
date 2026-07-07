---
name: agent-plus-upgrade
description: agent-plus | Trigger doctrine for Claude Code — when to proactively probe for and offer agent-plus framework upgrades. Pure markdown skill; the runtime is the existing `agent-plus-meta upgrade-check` / `agent-plus-meta upgrade` subcommands (frozen v0.13.5 contract) — snooze ladder, `update_check`/`silent_upgrade` config, `.bak` rollback, and a post-upgrade doctor gate are already built. Probes at most once per session, only surfaces an offer on `upgrade_available` with no active snooze, and never mutates without an explicit user choice.
when_to_use: |
  Two trigger paths — explicit (always wins, no cap) and ambient (soft, once
  per session):

  **Explicit** — the user directly asks. Trigger immediately, regardless of
  AGENT_PLUS_UPGRADE_CHECKED, on: "is agent-plus up to date", "check for
  agent-plus updates", "update agent-plus", "upgrade agent-plus", "am I on
  the latest agent-plus version", or equivalent phrasing naming the framework
  itself (not a sibling plugin like vercel-remote). Add `--force` if an
  earlier ambient probe this session may have cached a stale result.

  **Ambient** — once per session, after the FIRST agent-plus skill/tool call
  in the session has finished its own work (don't delay the user's actual ask
  to run an upgrade probe first). Trigger the probe when ALL hold:

  (a) an agent-plus skill has been invoked this session (agent-plus-meta,
      repo-analyze, diff-summary, skill-feedback, skill-plus, or a
      marketplace-installed skill), AND
  (b) this skill has not already probed this session via the ambient path
      (AGENT_PLUS_UPGRADE_CHECKED flag in conversation memory — explicit
      requests are exempt from this cap), AND
  (c) the environment is not CI/ephemeral ($CI, $GITHUB_ACTIONS, $CODESPACES
      unset; cwd not under /tmp).

  After probing on either path, only surface an unsolicited offer when the
  envelope shows ALL of: `verdict == "upgrade_available"`,
  `config.update_check != false`, and `snooze.active != true`. On the ambient
  path, anything short of that means stay silent — don't mention the probe
  happened at all. On the explicit path, always answer the question asked
  (e.g. plainly report `up_to_date`) even when there's nothing to offer.
allowed-tools: Bash(agent-plus-meta:*)
---

# agent-plus-upgrade

Trigger doctrine for *when* to check whether the agent-plus framework itself
is out of date, and how to offer the fix — same shape as
[agent-plus-installer](../agent-plus-installer/SKILL.md), but for the upgrade
path instead of the install path. No new mechanism: `agent-plus-meta
upgrade-check` and `agent-plus-meta upgrade` already exist, fully built, with
a frozen v0.13.5 JSON contract (snooze ladder, `.bak` rollback, migrations,
in-process post-upgrade `doctor` gate).

This skill exists because, until 2026-07-07, nothing ever *called*
`upgrade-check` — it wasn't wired into any `nextSteps` chain and no skill
triggered it, so every installed user silently drifted from the latest
release with no signal. Since then, `envcheck`'s clean-path `nextSteps` also
appends `upgrade-check` as a suggested command (see the root README's funnel
table) — that closes *reach* for sessions that run the standard bootstrap,
but a `nextSteps` entry is only ever a suggested string, never an automatic
action, and it only fires when `envcheck` itself runs with nothing missing.
This skill still owns the *judgment* neither the funnel nor the CLI provides
on its own: deciding whether an upgrade is actually worth surfacing
(`verdict` / `config.update_check` / `snooze.active` gating), presenting the
right one of the four choices, and driving the `silent_upgrade` fast path
correctly. The two are complementary, not redundant — funnel widens reach,
this skill supplies the decision.

## The probe

```bash
agent-plus-meta upgrade-check
```

Cheap and safe to run proactively: reads a cache first (TTL 60min when
up to date, 720min once an upgrade is already known to be available), falls
back to a capped ≤3s network GET of a single-root `VERSION` file, and
**fails silently** on any network error (never raises, never blocks). Read
the JSON envelope's gating fields before deciding whether to say anything:

| Field | Meaning | Your action |
|---|---|---|
| `verdict` | `up_to_date` \| `upgrade_available` \| `unknown` (`just_upgraded` is reserved in the frozen contract but not currently emitted by `cmd_upgrade_check` — don't branch on it) | Only `upgrade_available` is worth surfacing. |
| `config.update_check` | `false` means the user already chose "Never ask again" | If `false`, stay silent — don't surface anything, don't re-probe later this session either. |
| `snooze.active` | `true` means the user is inside an active 24h/48h/7d/never snooze window | If `true`, stay silent even when `verdict == upgrade_available`. |
| `errors` | Non-empty on network failure | Stay silent; this probe is best-effort by design. |

**This table is the authority, not the envelope's own `nextSteps` field.**
The JSON `agent-plus-meta upgrade-check` returns carries a `nextSteps`
suggestion of its own (part of the framework's general funnel convention) —
when it says `verdict == upgrade_available`, that field does now also check
`config.update_check`/`snooze.active` before suggesting `upgrade` (fixed
2026-07-07), but decide your own next action from the four rows above
regardless of what `nextSteps` says. Don't chain into a second, redundant
`agent-plus-meta doctor` call just because the envelope's `nextSteps`
mentions one — you already have everything you need from this one probe.

Set `AGENT_PLUS_UPGRADE_CHECKED=1` in conversation memory immediately after
the probe (regardless of outcome) so this skill does not re-probe again this
session.

## Offering the upgrade

Only when the table above clears do you act — and the first branch is
`config.silent_upgrade`, not a question:

- **`config.silent_upgrade == true`** — the user already chose "Always" in
  an earlier session. Don't ask again; run
  `agent-plus-meta upgrade --non-interactive --auto` directly. The CLI's own
  `--auto` logic does the right thing without you computing anything: it
  applies patch bumps immediately, and still degrades minor/major bumps to
  `snooze` even under `--auto` (it never silently lands those). This is the
  *only* case where `AskUserQuestion` is skipped, and it's skipped because
  the user already answered — durably, via config, not just this session.
- **`config.silent_upgrade == false`** (the default, and true for anyone who
  has never chosen "Always" through this skill before) — present the *same
  four options* the CLI's own interactive prompt (`_prompt_choice()`)
  defines, so the user's choice maps 1:1 onto `--user-choice`:

  | Choice | `--user-choice` | Effect |
  |---|---|---|
  | Yes — upgrade now | `yes` | Applies immediately. |
  | Always — silent on patch bumps | `always` | Applies immediately (same as "Yes") AND sets `silent_upgrade: true`; future minor/major bumps still ask. |
  | Snooze — remind me later | `snooze` | Advances the ladder (24h → 48h → 7d → never); no upgrade. |
  | Never ask again | `never` | Sets `update_check: false`; no upgrade. |

  Present via `AskUserQuestion` (all four options, not a yes/no). Only after
  the user picks, run:

  ```bash
  agent-plus-meta upgrade --user-choice <yes|always|snooze|never>
  ```

## Do NOT use this for

- **`config.update_check == false`.** The user already said never ask again.
  Don't re-surface it, don't mention it, don't ask "are you sure" — that's
  exactly the nagging this flag exists to prevent.
- **An active snooze.** Even with `verdict == upgrade_available`, if
  `snooze.active == true` stay silent. Snoozing means nothing if the next
  skill invocation just asks anyway.
- **CI or ephemeral environments.** If `$CI`, `$GITHUB_ACTIONS`,
  `$CODESPACES`, or similar are set, or cwd is under `/tmp`, skip the probe
  entirely — nothing will persist and it wastes a network call.
- **Already probed this session — ambient path only.** Honor
  `AGENT_PLUS_UPGRADE_CHECKED` for the ambient trigger: one ambient probe per
  session, not one per skill invocation. An explicit user request ("is
  agent-plus up to date?") always re-probes regardless of this flag; add
  `--force` if the cached result might be stale.
- **`--non-interactive --auto` without `config.silent_upgrade == true`.**
  That's the only condition allowed to skip `AskUserQuestion` (see
  "Offering the upgrade"). Never take the `--auto` fast path just because
  `verdict == upgrade_available` — that would apply a patch bump without
  the user ever having consented, even once.

## Safety rules

1. **Surface, never auto-execute.** This skill produces an offer via
   `AskUserQuestion`. Claude MUST let the user pick one of the four options
   before running any mutating Bash call. The Claude Code permission prompt
   on the `upgrade` Bash call is a second gate, not the first.
2. **Permission-prompt per invocation.** The probe and (if chosen) the
   upgrade are two separate Bash calls, each through Claude Code's normal
   permission prompt. Never bundle them into one approval.
3. **Trust the CLI's own safety net — don't work around it.** `upgrade`
   already snapshots every primitive to `.bak` before replacing it, runs an
   in-process `doctor` gate afterward, and auto-rolls-back on
   `verdict: "rolled_back"` or a `broken` doctor verdict. Never manually
   delete `.bak` snapshots, never pass `--rollback` without an explicit user
   ask, never retry a failed upgrade in a loop.
4. **The `silent_upgrade` check is this skill's own gate, not something the
   CLI enforces for you.** `agent-plus-meta upgrade --non-interactive --auto`
   applies a patch bump regardless of `config.silent_upgrade` — that flag
   only changes which label the CLI records (`"always"` vs `"yes"`), not
   whether the bump lands. This skill is the one that must never invoke
   `--auto` except when it has already confirmed `config.silent_upgrade ==
   true` (per "Offering the upgrade" above) — invoking it "just because
   `verdict == upgrade_available`" would apply a patch bump nobody ever
   consented to. Minor/major bumps are the one thing the CLI itself still
   refuses to silently land under `--auto` (degrades to `snooze`
   regardless of `silent_upgrade`) — but don't rely on that as your only
   guard; the skill's own gate above is what actually protects patch bumps
   on a first encounter.
5. **Report failures verbatim.** If `upgrade` returns `verdict: "rolled_back"`
   or `"error"`, paste the envelope's `errors[]` back to the user exactly.
   Do not retry silently. Do not paraphrase the error away.

## Architecture

The ambient path (the explicit path is simpler — skip straight to the probe,
skip the flag checks, always answer):

```
First agent-plus skill/tool call this session completes its own work
   |
   v
AGENT_PLUS_UPGRADE_CHECKED already set? --yes--> stop
   | no
   v
CI/ephemeral env, or cwd under /tmp? --yes--> stop (don't set the flag; retry next real session)
   | no
   v
Claude runs (permission-gated): agent-plus-meta upgrade-check
   |
   v
Set AGENT_PLUS_UPGRADE_CHECKED=1 regardless of outcome
   |
   v
verdict=="upgrade_available" AND config.update_check!=false AND snooze.active!=true?
   | no                                                            | yes
   v                                                                v
 stay silent, don't mention the probe              config.silent_upgrade == true?
                                                     | yes                    | no
                                                     v                        v
                                    agent-plus-meta upgrade         AskUserQuestion: 4-option prompt
                                      --non-interactive --auto                |
                                    (CLI applies patch bumps    +---------------+-----------+-----------+
                                     silently, still degrades   v               v           v           v
                                     minor/major to snooze)   "yes"         "always"    "snooze"     "never"
                                                     |            |               |           |           |
                                                     |            v               v           v           v
                                                     |  agent-plus-meta upgrade --user-choice <yes|always|snooze|never>
                                                     |            |
                                                     +------------+
                                                                  v
                                        Report the envelope's verdict verbatim.
                                success -> done.  rolled_back / error -> paste errors[] verbatim, don't retry.
```

**Why this lives in a separate skill, not folded into `agent-plus-meta`'s own
`SKILL.md`.** Same reasoning [agent-plus-installer](../agent-plus-installer/SKILL.md)
already documents for the install path: upgrade is its own concern with its
own trigger cadence (once per session, conditional on a prior skill call)
distinct from `agent-plus-meta`'s session-start bootstrap doctrine
(`init`/`envcheck`/`refresh`, run unconditionally at session start). Keeping
them separate means declining or snoozing an upgrade offer never touches the
bootstrap flow, and vice versa.

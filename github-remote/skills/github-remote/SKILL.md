---
name: github-remote
description: Read-first wrapper around the GitHub REST API. Single-call PR/branch overviews (PR state + checks + reviews + workflow runs + mergeable) for triage. Use whenever the user wants the state of a PR, a branch's CI, or a failing run — without you chaining `gh pr view`, `gh pr checks`, `gh run view`, `gh pr list --head` per investigation. Resolves PRs and issues by branch/title. `run wait` polls on a run ID or branch. One write surface, `pr comment`.
when_to_use: Trigger on phrases like "is this PR green", "what's the state of <branch>", "is this mergeable", "why did CI fail", "what's blocking this PR", "resolve the PR for <branch>", "tail the failing job logs", "wait for the run on <branch>", "post a comment on PR <N>".
allowed-tools: Bash(github-remote:*) Bash(python3 *github-remote*:*)
---

# github-remote

Project-scoped CLI that wraps the GitHub REST API into a read-first, JSON-output triage tool. Stdlib-only Python 3 (no pip installs, no venvs). Designed for agent-driven PR/CI inspection — one call returns PR state + checks + reviews + runs so you don't burn tool calls chaining `gh pr view` / `gh pr checks` / `gh run view` per investigation.

Lives at `${CLAUDE_SKILL_DIR}/../../bin/github-remote`; the plugin auto-adds `bin/` to PATH, so just run `github-remote ...`.

## Prerequisites

- **`GITHUB_TOKEN`** set in project `.env` / `.env.local` / shell env, OR `gh` logged in (the CLI falls back to `gh auth token`). Get a PAT at https://github.com/settings/tokens.
- **Repo resolution:** pass `--repo owner/name`, or set `GITHUB_REPO`, or run from a git checkout whose `origin` points at github.com.

The CLI bails with a clear missing-config message if neither auth nor repo is resolvable.

## When to reach for this

- User asks **"is this PR green / mergeable"** → run `overview <branch-or-pr-number> --pretty`. One call → PR + checks + reviews + runs + mergeable + review comment count.
- User asks **"why did CI fail on <branch>"** → run `overview <branch>` to see failing job names, then `run logs <run-id> --errors-only --tail 100`.
- User asks **"resolve the PR for <branch>"** → run `pr resolve <branch>`. Exits non-zero with candidates on ambiguity; never auto-picks.
- User asks **"wait for the run on main"** → run `run wait main --timeout 1800`. Accepts a branch or a run ID. Exits non-zero on timeout or non-success conclusion.
- User asks **"post a comment on PR 498"** → run `pr comment 498 --body '…'`. Posts via `/issues/{n}/comments` (issue-style comments on PRs, which is what `gh pr comment` does).

## Headline commands

```bash
github-remote pr list [--state open|closed|merged|all] [--base <branch>] [--author <u>] [--limit 50]
github-remote pr resolve <branch-or-partial-title>
github-remote pr show <N-or-branch>
github-remote pr comment <N-or-branch> --body <text>

github-remote issue list [--state open|closed|all] [--assignee <u>] [--label <l>] [--limit 50]
github-remote issue resolve <title-pattern>
github-remote issue show <N>

github-remote run list [--branch <b>] [--status in_progress|completed|queued] [--limit 20]
github-remote run show <RUN_ID>
github-remote run logs <RUN_ID> [--job <name>] [--errors-only] [--tail 50]
github-remote run wait <RUN_ID-or-branch> [--timeout 1800] [--poll-interval 10]

github-remote overview <branch-or-pr-number>
```

All list/show commands emit JSON to stdout. Use `--pretty` for indentation.

**jq-friendly.** Default compact JSON pipes cleanly into `jq`. Every dict payload also carries a top-level `tool: {name, version}` field (injected by `_with_tool_meta`) so agents can self-diagnose version drift from the output alone — no extra `--version` subprocess call needed. Prefer `jq` against stable keys (`.pr.number`, `.checks.failing_jobs[]`, `.runs[].conclusion`) rather than parsing human output.

## Offloading large responses with `--output`

GitHub responses balloon quickly — `run logs` on a failing build, long `pr list`, `run show` with dozens of check annotations. Pulling the full payload through the model's context wastes tokens when you only need a slice.

**Pass `--output <path>` before the subcommand** (it's a top-level flag, same position as `--pretty`):

```bash
github-remote --output /tmp/run.json run logs 1234567890 --errors-only
github-remote --output /tmp/prs.json pr list --state open --limit 100
```

Stdout returns a compact envelope instead of the full payload:

```json
{
  "tool": {"name": "github-remote", "version": "..."},
  "savedTo": "/tmp/run.json",
  "bytes": 93420,
  "fileLineCount": 2104,
  "payloadKeys": ["run_id", "jobs", "failing_jobs"],
  "payloadShape": {
    "run_id": {"type": "number"},
    "jobs": {"type": "list", "length": 12,
      "sample": {"type": "dict", "keys": 6,
        "shape": {"name": {"type": "string", "length": 8},
                  "conclusion": {"type": "string", "length": 7},
                  "annotations": {"type": "list", "length": 23}}}},
    "failing_jobs": {"type": "list", "length": 2}
  }
}
```

**How to act on it:**

1. `payloadShape` tells you what's in the file without reading it. Agent sees `jobs[0].annotations.length: 23` and knows the interesting data is nested under each job.
2. Use `Read` with offset/limit to pull only the slice you need.
3. For list-shaped responses (`pr list`, `run list`), the envelope has `payloadType: "list"` + `payloadLength` + `sampleShape`.

**`--shape-depth <1|2|3>`** controls recursion depth. Default is `3` (two layers — surfaces `checks[0].annotations.length` or `prs[0].head.sha`). `--shape-depth 1` gives a minimal top-level-only envelope.

**When NOT to use `--output`:** small responses (`pr resolve`, `issue show` for a single item), or when you need the data in the same turn to act on.

## Design rules (agent-plus patterns)

1. **Aggregate server-side.** `overview` returns PR state + mergeable + check-runs rollup + review summary + latest runs in one call — replaces 4-6 `gh` invocations.
2. **Resolve by name.** `pr resolve feat/foo` and `issue resolve 'flaky test'` — you never copy PR numbers between commands. Ambiguity never auto-picks: exits non-zero with up to 10 candidates so the agent can re-query.
3. **`--wait` on async flows.** `run wait` polls on a run ID or branch name with a 30-min default timeout and 10s poll interval. On timeout: non-zero exit with partial JSON including last-known state. Never hangs.
4. **`--json` is the default.** No human-prose output paths. Pipe to `jq` freely.
5. **Zero token leakage.** Every API response walks through `_scrub()`, which redacts `token`, `password`, `authorization`, `client_secret`, `private_key`, `webhook_url_with_secret`, `access_token`, `refresh_token`, `secret`, `api_key`. Free-text log blobs from `run logs` are regex-scrubbed for `ghp_…`, `github_pat_…`, `gho_…`, `ghu_…`, `ghs_…`, `ghr_…`, `AKIA…`, and `Bearer …` patterns. A canary no-leak test asserts a known secret substring cannot appear on any output path.

## Overview output caps

Documented in `--help` and honoured by the CLI so agents can budget context:

- Reviews: 10 latest
- Failing jobs: 20
- Workflow runs tied to head SHA: 5

## Config precedence (highest first)

1. `--token` / `--repo` CLI flags
2. `--env-file <path>` if passed
3. `.env.local` / `.env` walked up from cwd (closest wins)
4. Shell env

Only `GITHUB_*` prefixed vars are picked up.

## Auth precedence (highest first)

1. `GITHUB_TOKEN` env var (explicit, CI-friendly; fine-grained and classic PATs both flow here)
2. `gh auth token` subprocess (inherits your local `gh` login — best DX on dev machines)
3. Fail with a missing-config message pointing at `.env`, `~/.claude/settings.json`, and `gh auth login`.

## Rate limits

- `X-RateLimit-Remaining` / `X-RateLimit-Reset` headers are read on every response. When remaining < 50, a warning goes to stderr.
- **Primary rate limit (429 or 403 + `Retry-After`):** auto-retried once with the backoff the header suggests.
- **Secondary rate limit (403 + 'secondary rate limit' / 'abuse' in body):** NOT auto-retried. Surfaces a clear error — back off several minutes, not seconds.

## Safety

- **Read-only by default.** `pr comment` is the sole write in v1.
- **`pr comment` requires non-empty `--body`**; rejects whitespace-only.
- **Out of scope for v1:** `pr create`, `pr merge`, `issue create`, `issue close`. Those carry distinct confirmation contracts and are deferred.

## Error message contract

Every error emits problem + cause + fix + link:

- Missing token → "GITHUB_TOKEN not set and 'gh auth token' returned nothing. Set in .env / ~/.claude/settings.json, or run 'gh auth login'. Get a PAT: https://github.com/settings/tokens"
- 401 → "Token rejected. Regenerate or run 'gh auth refresh'."
- 403 scope → "Token lacks required scope. Classic PATs need 'repo'+'workflow'; fine-grained PATs need Contents+Pull requests+Actions(read)+Issues+Metadata."
- 404 → "Repo not found or token lacks access. Private repos require 'repo' scope."
- 422 → "GitHub rejected the request. Common cause: field validation (e.g. empty body on pr comment)."
- 429 / primary → "Primary rate limit hit. See X-RateLimit-Reset for when it resets."
- 403 secondary → "Secondary rate limit (abuse detection). Back off several minutes."

## When NOT to use this — fall back to `gh` CLI or the GitHub REST API directly

**This wrapper is read-first by design.** The only write surface is `pr comment`. Every other state-changing GitHub operation is deliberately unwrapped — if the user wants to DO something (merge, close, create, dispatch), skip `github-remote` entirely and use `gh` (already authed on their machine) or `curl https://api.github.com/*` with `Authorization: Bearer $GITHUB_TOKEN`.

Specific cases where you should use `gh ...` or the REST API directly, not `github-remote`:

- **Creating, merging, closing, or reviewing PRs.** → `gh pr create`, `gh pr merge`, `gh pr close`, `gh pr review --approve|--request-changes`. `github-remote` intentionally ships none of these (distinct safety/confirmation contracts).
- **Creating, closing, or commenting on issues.** → `gh issue create`, `gh issue close`, `gh issue comment`. Only `pr comment` is wrapped; issue writes are not.
- **Running or dispatching workflows.** → `gh workflow run <name>`, `gh workflow list`, or `POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches`. `github-remote run *` is read-only (list/show/logs/wait).
- **Releases, tags, team/repo admin, branch protection, secrets management.** → `gh release ...`, `gh api ...`, or the dashboard. Entirely out of wrapper scope.
- **GitHub Enterprise Server** (non-`api.github.com` hosts). → `gh` with `GH_HOST` set, or `curl` against your GHES base URL. `GITHUB_API_URL` is reserved but not wired up in v1.
- **Git operations** — cloning, checkouts, pushing, branch creation, local diffs. → plain `git` and `gh repo clone`. The wrapper has no git surface at all.

**Don't get stuck in a loop.** If the user's request obviously needs a write `github-remote` doesn't support (merge, create, dispatch, close), immediately switch to `gh` or `curl` rather than hunting for a wrapper flag that doesn't exist. The wrapper exists to make *reading* PR/CI state faster and safer — it is not a replacement for `gh`.

## What it doesn't do

Deliberately out of scope for v1:

- `pr create`, `pr merge`, `issue create`, `issue close` (deferred — distinct safety contracts)
- Workflow authoring or `workflow dispatch` / `workflow run`
- Team, releases, repo admin, secrets management
- GitHub Enterprise Server (v1 assumes `api.github.com`; `GITHUB_API_URL` is reserved for future GHES support)
- GraphQL migration (REST covers every v1 command)

Use the `gh` CLI or dashboard for those. This plugin is read-first PR/CI triage plus one write.

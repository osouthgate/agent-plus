# github-remote

Read-first wrapper around the [GitHub REST API](https://docs.github.com/en/rest) for PR and CI triage. Stdlib-only Python 3, no dependencies.

Part of [agent-plus](../README.md) — Claude Code plugins that cut the tool-call and token cost of driving APIs from an agent.

## Why

Without this, an agent investigating a PR chains three to four `gh` calls — `gh pr view <N> --json state,mergedAt`, `gh pr checks <N>`, `gh run view <RUN_ID>`, `gh pr list --head <branch>` — parses each output, copy/pastes run IDs between invocations, and still has no single JSON blob it can reason over. Evidence from one real session: **137 `gh` invocations**, including 26 `gh pr view --json`, 7 `gh run list`, and multi-call triage patterns like `gh pr view 498 --json state` immediately followed by `gh pr checks 498 --watch`. `github-remote overview` replaces that with one call.

**Measured wins**

- `overview <branch-or-pr>` returns PR state + mergeable status + check-runs rollup + review summary + latest workflow runs in a single aggregated payload. Agent-side: **4-6 tool calls → 1.** Reviews capped at 10, failing jobs at 20, runs at 5, so context budgeting is predictable.
- **Name-resolved.** `pr resolve feat/google-drive-connector` replaces the `gh pr list --head <branch> --json number,url,title` + manual-ID-extraction pattern seen 9+ times in transcripts. Ambiguity never auto-picks — exits non-zero with up to 10 candidates so the agent can re-query.
- **`run wait` polls on a branch or a run ID.** 30-min default timeout matches real CI (GH Actions routinely exceeds 10-15 min). On timeout: non-zero exit with partial JSON including last-known state. No hand-rolled `until curl ... | jq ...` loops (which break on the Windows bash shim anyway).
- **Token leakage is impossible.** Every API response walks through `_scrub()` — redacts `token`, `password`, `authorization`, `client_secret`, `private_key`, `webhook_url_with_secret`, and related keys. Free-text log output from `run logs` is regex-scrubbed for `ghp_…`, `github_pat_…`, `gho_/ghu_/ghs_/ghr_…`, AWS `AKIA…`, and generic `Bearer …` patterns. A canary no-leak test asserts a known secret substring cannot appear on any output path.
- **One write, no ocean.** `pr comment` is the only write in v1 — the most common agent write op in transcripts, narrow blast radius. `pr create`, `pr merge`, `issue create` are deliberately deferred.

## Install

### Recommended — marketplace install

```bash
claude plugin marketplace add osouthgate/agent-plus
claude plugin install github-remote@agent-plus
```

Adds `github-remote` to PATH and loads the skill so Claude reaches for it automatically.

### Session-only (dev / try-before-install)

```bash
git clone https://github.com/osouthgate/agent-plus
claude --plugin-dir ./agent-plus/github-remote
```

### Standalone — no Claude Code

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/github-remote/bin/github-remote
chmod +x github-remote
./github-remote overview main --pretty
```

## Prerequisites

- **`GITHUB_TOKEN`** with appropriate scope, OR `gh` logged in locally (the CLI falls back to `gh auth token`).
  - Classic PATs need `repo` (private) or `public_repo` (public) + `workflow` for runs.
  - Fine-grained PATs need Contents, Pull requests, Issues, Actions (read), Metadata.
- **Python 3.9+** (stdlib only).

Config precedence (highest first):
1. `--token` / `--repo` CLI flags
2. `--env-file <path>`
3. `.env.local` / `.env` walked up from cwd (closest wins)
4. Shell env

Auth precedence (highest first):
1. `GITHUB_TOKEN` env var
2. `gh auth token` subprocess fallback
3. Fail with a missing-config message

Repo resolution precedence (highest first):
1. `--repo owner/name` flag
2. `GITHUB_REPO` env var
3. `git config --get remote.origin.url` parsed in cwd

Only `GITHUB_*` prefixed vars are read.

## Usage

```bash
# One-call snapshot — PR + mergeable + checks + reviews + runs
github-remote overview feat/my-branch --pretty

# List open PRs
github-remote pr list --state open --limit 10

# Resolve by branch (non-zero on ambiguity with candidates listed)
github-remote pr resolve feat/google-drive-connector

# Show a PR by number or branch
github-remote pr show 498 --pretty

# Post a comment (the sole v1 write)
github-remote pr comment 498 --body 'CI green on main, ready to merge'

# Workflow runs on a branch
github-remote run list --branch main --status in_progress --limit 5

# Run logs, errors only, last 100 lines per job
github-remote run logs 24187042542 --errors-only --tail 100

# Wait on a run, or on the latest run for a branch
github-remote run wait main --timeout 1800 --poll-interval 10

# Issues
github-remote issue list --state open --label bug --limit 20
github-remote issue resolve 'flaky test'
github-remote issue show 42
```

## Rate limits

Authenticated primary limit is 5000 req/hour. `_api()`:

- Reads `X-RateLimit-Remaining` / `X-RateLimit-Reset` on every response; warns to stderr when remaining < 50.
- Retries once on 429 or 403-with-`Retry-After` (primary rate limit). Capped at 60s backoff.
- **Does NOT auto-retry on secondary rate limit** (403 with 'secondary rate limit' or 'abuse' in body). Surfaces a clear error — these need minute-scale backoff, not seconds.

`run wait` at the default 10s poll interval spends at most 180 calls over 30 minutes, safely under budget.

## What it doesn't do

Deliberately out of scope for v1:

- `pr create`, `pr merge`, `issue create`, `issue close` — distinct safety/confirmation contracts; deferred to v2. Transcripts show ~45 combined hits, so demand is real and documented.
- Workflow authoring / `workflow dispatch` / `workflow run`
- Team, releases, repo admin, secrets management
- **GitHub Enterprise Server** — v1 assumes `api.github.com`. `GITHUB_API_URL` is reserved for future GHES plumbing.
- **GraphQL** — REST covers every v1 command; GraphQL would cut round-trips further but doubles the `_api()` surface.

Use the `gh` CLI directly for those. This plugin is read-first PR/CI triage plus the minimum write surface needed by agents.

## Gotchas the plugin collapses

- **Branch → PR resolution.** `gh pr list --head <branch> --json number` + manual ID extraction is replaced by `pr resolve <branch>`.
- **PR comments live under `/issues/{n}/comments`.** This is a REST quirk (the issues endpoint handles issue-style PR comments). `pr comment` hides it; you pass the PR number.
- **Jobs logs return a 302 to a signed URL.** The CLI follows the redirect automatically. If the signed URL rejects the auth header, the CLI transparently falls back to a two-step fetch.
- **Combined status vs check-runs.** `overview` reports both: `checks.combined_state` (legacy statuses API) and `checks.passing/failing/pending` (check-runs API). Agents see whichever signal their CI emits.
- **Check-runs for the head SHA, not the PR number.** The CLI projects head SHA from the PR response, then fetches check-runs for that SHA — otherwise you'd miss status entirely.

## License

MIT. See repo root.

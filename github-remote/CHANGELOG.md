# github-remote — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## Unreleased

### Fixed
- `--output` no longer silently drops list-shaped payloads (e.g. `pr list`). The raw list is written to disk unchanged; the envelope reports `payloadType: "list"` + `payloadLength` instead of `payloadKeys`/`payloadShape`, plus head/tail item previews. [2026-04-24]

### Added
- `--output <path>` global flag — writes the full JSON payload to disk and prints a compact envelope (`savedTo`, `bytes`, `payloadKeys`, `payloadShape`, head/tail previews for log-shaped payloads). Use for large responses (`run logs`, long `pr list`, fat `run show`) that are wasteful to route through the model's context window. [2026-04-24]
- `payloadShape` field on the `--output` envelope — shallow type + size map for each top-level key (e.g. `{"checks": {"type": "list", "length": 30}}`) so the agent can decide which key to drill into without scanning the file. [2026-04-24]

## 0.1.0 - 2026-04-24

Initial release.

### Added
- `overview <branch-or-pr-number>` — single-call snapshot: PR state + mergeable + head commit + check-runs rollup (failing jobs capped at 20) + review summary (capped at 10 latest) + workflow runs tied to head SHA (capped at 5) + review comment count. Replaces 4-6 `gh` invocations per triage. Caps are documented in `--help` and the README so agents can budget context.
- `pr list` with `--state open|closed|merged|all` / `--base` / `--author` / `--limit` filters; `pr show <N-or-branch>`; `pr resolve <branch-or-partial-title>` with the ambiguity contract (non-zero exit + JSON candidates on 2+ matches, never auto-picks); `pr comment <N-or-branch> --body <text>` as the sole v1 write (posts via `/issues/{n}/comments`, the REST path `gh pr comment` uses).
- `issue list` / `issue resolve <title-pattern>` / `issue show <N>` — same resolve-by-name and ambiguity contract as PRs. Filters out entries that are PRs (GitHub's `/issues` endpoint returns both).
- `run list` / `run show <RUN_ID>` (with jobs) / `run logs <RUN_ID> [--job <name>] [--errors-only] [--tail 50]` — log output is regex-scrubbed for `ghp_`, `github_pat_`, `gho_`, `ghu_`, `ghs_`, `ghr_`, AWS `AKIA`, and generic `Bearer …` token patterns before emission.
- `run wait <RUN_ID-or-branch>` — accepts a numeric run ID or a branch name (branch resolves to latest run on HEAD SHA before polling). Default timeout 1800s (30 min, matching real CI durations), poll interval 10s (min 5s). Non-zero exit on timeout with partial JSON including last-known state. Non-zero exit on non-success conclusion.
- Auth precedence: `GITHUB_TOKEN` env var → `gh auth token` subprocess fallback → fail with the documented missing-config message. Classic and fine-grained PATs both flow through `GITHUB_TOKEN`. Minimum scopes documented in the README.
- Repo resolution precedence: `--repo owner/name` flag → `GITHUB_REPO` env → `git config --get remote.origin.url` parsed in cwd. Missing repo: non-zero with a fix-suggesting message pointing at all three sources.
- Rate-limit handling: reads `X-RateLimit-Remaining` / `X-RateLimit-Reset` on every response, warns on stderr when remaining < 50. Primary rate limit (429 or 403 + `Retry-After`) auto-retried once with header-suggested backoff (capped 60s). Secondary rate limit (403 with 'secondary rate limit' / 'abuse' in body) surfaces a clear error and does NOT auto-retry — these need minute-scale backoff.
- `_scrub()` response filter strips `token`, `password`, `authorization`, `client_secret`, `private_key`, `webhook_url_with_secret`, `access_token`, `refresh_token`, `secret`, `api_key` keys (case-insensitive) from every API response before emission. Walks nested dicts/lists.
- `_scrub_text()` regex-scrub applied to run log blobs and error bodies before emission.
- Layered `.env` autoload: `--env-file` > project `.env.local` / `.env` walked up from cwd > shell env. Only `GITHUB_*` prefixed vars are picked up.
- Error message contract: missing token / 401 / 403-scope / 404-private / 422 / 429-primary / 403-secondary each emit problem + cause + fix + link.
- Worked example in every command's `--help` string.
- Ambiguity contract on `pr resolve` / `issue resolve`: exact match → return; unique substring → return; 2+ matches → non-zero exit with `{error: 'ambiguous', matches: [...up to 10]}`; no match → non-zero `{error: 'not_found'}`.

### Safety
- Read-only by default. `pr comment` is the sole write, requires non-empty `--body`, rejects whitespace-only bodies with a 422-style message before the API call.
- Canary no-leak test: a known secret substring injected into fake response bodies and log blobs never appears on any stdout/stderr path across any command, including error paths.

### Deliberate out-of-scope for v1
- `pr create`, `pr merge`, `issue create`, `issue close` — distinct safety contracts; deferred to v2.
- Workflow authoring / `workflow dispatch`.
- Team, releases, repo admin, secrets.
- GitHub Enterprise Server (`GITHUB_API_URL` is reserved for future GHES support).
- GraphQL migration (REST covers every v1 command).

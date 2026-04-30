# diff-summary

> Part of [**agent-plus**](../README.md) · siblings: [`agent-plus`](../agent-plus) · [`repo-analyze`](../repo-analyze) · [`skill-feedback`](../skill-feedback) · [`skill-plus`](../skill-plus)

Triaging a PR or asking "what changed in this branch", an agent reads each modified file individually — five, ten, twenty Read calls — to figure out: is this a test or source? a config or a migration? did the public API change? were tests updated alongside? is this a 300-line refactor or a one-line typo fix?

`diff-summary` collapses that into one call. **A 12-file PR that would have burned ~12 Reads becomes one structured payload** — per-file role classification, risk tier with reasons, public-API touch detection, co-changed-test detection, secret-risk path flagging, aggregate stats. Stdlib Python only. No network. Stateless.

## Headline command

```bash
diff-summary                              # working tree (default)
diff-summary --staged                     # staged index
diff-summary --base main                  # <base>...HEAD (3-dot merge-base)
diff-summary --range A..B                 # explicit range, passed verbatim to git

diff-summary --max-files 200              # cap files in files[] (default 200)
diff-summary --include-patches            # include raw unified diff per file (opt-in)
diff-summary --public-api-only            # filter files[] to publicApiTouched only
diff-summary --risk medium                # filter files[] to risk >= MIN
diff-summary --output /tmp/d.json --shape-depth 2
diff-summary --pretty | --json
diff-summary --version
```

The four diff-source flags are mutually exclusive. Default is the working-tree diff (uncommitted unstaged changes), matching `git diff` with no flags.

## Worked example

```bash
$ diff-summary --base main --pretty
{
  "tool": {"name": "diff-summary", "version": "0.2.1"},
  "mode": "base",
  "base": "main",
  "head": "abc1234",
  "branch": "feature/tag-docs",
  "summarizedAt": "2026-04-28T12:34:56Z",
  "stats": {"files": 6, "insertions": 184, "deletions": 23, "movedLinesEstimate": 12},
  "files": [
    {
      "path": "src/lib/tags.ts",
      "status": "modified",
      "role": "source",
      "language": "typescript",
      "insertions": 87, "deletions": 4,
      "binary": false,
      "publicApiTouched": true,
      "risk": "high",
      "riskReasons": ["touches-public-api", "no-test-changes"]
    },
    {
      "path": "src/lib/index.ts",
      "status": "modified",
      "role": "source",
      "language": "typescript",
      "insertions": 3, "deletions": 0,
      "binary": false,
      "publicApiTouched": true,
      "risk": "medium",
      "riskReasons": ["no-test-changes"]
    },
    ...
  ],
  "summary": {
    "byRole": {"source": 3, "doc": 2, "config": 1},
    "highRiskFiles": ["src/lib/tags.ts"],
    "testFilesTouched": 0,
    "sourceFilesWithoutTestChanges": ["src/lib/tags.ts", "src/lib/index.ts", "src/api/route.ts"],
    "publicApiTouches": ["src/lib/tags.ts", "src/lib/index.ts"],
    "migrationsTouched": [],
    "secretsRiskFiles": []
  }
}
```

The agent now knows where to focus: read `src/lib/tags.ts` carefully (HIGH, public API, no test), skim the doc and config, and prompt the user "no tests for this PR — intentional?".

## Role classification

First match wins, in priority order:

| Role | Triggers |
| :--- | :--- |
| `test` | path matches `**/test/**`, `**/tests/**`, `**/__tests__/**`, `**/spec/**`; basename matches `*.test.*`, `*.spec.*`, `*_test.{py,go,rs}`, `test_*.py`, `*Test.java` |
| `migration` | `**/migrations/**`, `**/db/migrate/**`, `supabase/migrations/**`, anything with `alembic` in the path, `V<n>__*.sql` |
| `generated` | `**/dist/**`, `**/build/**`, `**/.next/**`, `**/.turbo/**`, `**/target/**`, `**/__pycache__/**`, `*.pyc`, lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `Cargo.lock`, `poetry.lock`, `uv.lock`, `Gemfile.lock`, `composer.lock`, `bun.lockb`, `go.sum`, `yarn.lock`) |
| `build` | `Dockerfile`, `Makefile`, `CMakeLists.txt`, `build.gradle`, `pom.xml`, `setup.py`, `Dockerfile.*`, `**/.github/workflows/*` |
| `config` | extension `.json`/`.yaml`/`.yml`/`.toml`/`.ini`/`.conf`/`.cfg`; basename starts with `.env`; basename matches `*.config.*`; `tsconfig.json`, `jest.config.*`, `vitest.config.*`, `eslint.config.*`, `.prettierrc.*`, `tailwind.config.*` |
| `doc` | extension `.md`/`.rst`/`.txt`/`.adoc`; basename in `{LICENSE, CHANGELOG, AUTHORS, NOTICE, CONTRIBUTING, README}`; path matches `**/docs/**` |
| `fixture` | `**/fixtures/**`, `**/seeds/**`, basename starts with `seed_` |
| `source` | everything else |

## Risk tiers

Combine all triggered reasons into `riskReasons`; final tier is the highest triggered:

- **HIGH** if any of: role is `migration`; path is a secret-risk path (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `secrets.*`); path matches `**/.github/workflows/*`; role is `source` AND `insertions + deletions > 200` AND no co-changed test; `publicApiTouched: true` AND no co-changed test; status is `deleted` AND role is `source`.
- **MEDIUM** if any of (and not already HIGH): role is `source` AND no co-changed test; role is `config`; status is `renamed` AND role is `source`.
- **LOW**: everything else.

A "co-changed test" exists when any test file in the same diff has a name stem matching the source file's stem (after stripping `test_`/`_test`/`.test`/`.spec` suffixes/prefixes), or when a test file lives in the same directory as the source file. Heuristic — biased toward false-positive ("no test detected") over false-negative.

## Public-API detection

Heuristic. We flag `publicApiTouched: true` when:

- The path's basename is a well-known entrypoint (`index.{ts,tsx,js,jsx}`, `mod.rs`, `lib.rs`, `__init__.py`, `main.go`), or matches `cmd/*/main.go`.
- The added lines (lines starting with `+` in the unified diff) include any of: `+export ` (TS/JS), `+pub fn|struct|mod|enum|trait|const|static` (Rust), `+func [A-Z]` (Go — capitalized = exported), `+def <name>(` where `<name>` doesn't start with `_` (Python top-level public def), `+class ` (TS/Python).

Will miss public-API touches in unusual layouts. Will over-flag test fixtures named `index.ts`. Treat as a hint, not a verdict.

## Honest limits

- **Public-API detection is heuristic.** Path-based + naive added-line regex. We don't parse ASTs.
- **Risk tiers are advisory.** They tell you where to look first; they don't replace review judgment.
- **Co-changed-test detection is name-stem-based.** Test reorganizations that move tests across directories will look like "tests removed" until the next commit. Prefer false-positive over false-negative — if a test exists but we missed it, the agent reads one extra file; if we claim a test exists when none does, you ship a regression.
- **Moved-lines estimate is naive.** Whitespace-trimmed line matching across files. Useful for spotting refactor-shaped diffs; not authoritative.
- **We don't read .env contents.** Pattern 5: secret-risk paths (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `secrets.*`) appear in `summary.secretsRiskFiles` so the agent can prompt the user "are you sure you want to commit `.env`?", but we never separately scan their content. With `--include-patches` the patch text passes through verbatim — the user owns what they're committing — but the default mode never echoes patch content.
- **No fetch.** Base ref must already exist locally. If `--base origin/main` errors with "base ref not found locally", run `git fetch origin main` first.
- **Single git diff invocation per call.** Performance budget: <1s on a typical 50-file PR diff.

## Offloading large responses

For 100-file PRs or `--include-patches` invocations:

```bash
diff-summary --base main --include-patches --output /tmp/diff.json --shape-depth 3
```

Full payload to disk. Stdout returns `payloadPath`, `bytes`, `payloadKeys`, `payloadShape` — same envelope shape as `repo-analyze`, `railway-ops`, `vercel-remote`.

## Install

### Marketplace

```bash
claude plugin install diff-summary@agent-plus
```

### Standalone

```bash
curl -O https://raw.githubusercontent.com/osouthgate/agent-plus/main/diff-summary/bin/diff-summary
chmod +x diff-summary
./diff-summary --pretty
```

Stdlib Python 3.11+ (uses `tomllib` from stdlib for the manifest read).

## Tests

```bash
python3 -m pytest diff-summary/test/ -v
```

## What it doesn't do

- **No coverage analysis.** We flag "no co-changed test in this diff", which is not "no test coverage exists anywhere".
- **No content scanning.** No cyclomatic-complexity, no lint, no AST parsing, no secret-content detection. Different tools.
- **No subcommands.** Single command for v0.1.0; if a `stats`-only or `summary`-only mode becomes useful, it can be added without breaking the default.
- **Won't run on non-git directories.** Errors with a clear message and a JSON error envelope.
- **No fetch.** Base must already exist locally.

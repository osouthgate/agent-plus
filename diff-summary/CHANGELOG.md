# diff-summary — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.1.0 - 2026-04-28

Initial release. Universal-primitive plugin (B-PLUGIN-3). Replaces the per-file Read sweep an agent does to triage a PR ("test or source? config or migration? did the public API change? were tests updated alongside?") with one structured JSON call.

### Added
- `diff-summary` — single-command CLI. Default behaviour: summarise the working-tree diff. Mutually-exclusive diff-source flags `--staged`, `--base BRANCH` (3-dot merge-base diff), `--range A..B` (verbatim passthrough). Refinement flags `--max-files`, `--include-patches`/`--no-patches` (default off), `--public-api-only`, `--risk low|medium|high`. Output flags `--json`/`--pretty`, `--output PATH`, `--shape-depth 1|2|3`, `--version`.
- **Role classification.** Per-file role: `source`, `test`, `config`, `doc`, `generated`, `build`, `fixture`, `migration`. First-match-wins predicate list, ~90% accuracy target. Covers test path conventions across Python/JS/TS/Go/Rust/Java, lockfiles + build directories for `generated`, `**/.github/workflows/*` + `Dockerfile.*` for `build`, `*.config.*` + `.env*` for `config`, `**/migrations/**` + `V<n>__*.sql` + alembic for `migration`.
- **Risk tiers.** `low`/`medium`/`high` per file with a `riskReasons` array for explainability. HIGH triggers: migration, secret-risk path, CI workflow, large source change without co-changed test, public-API change without co-changed test, source deletion. MEDIUM triggers: source without test, config change, source rename. LOW: everything else.
- **Public-API detection.** Heuristic. Well-known entrypoint basenames (`index.{ts,tsx,js,jsx}`, `mod.rs`, `lib.rs`, `__init__.py`, `main.go`, `cmd/*/main.go`) plus added-line regex (`+export `, `+pub fn|struct|mod|enum|trait|const|static`, `+func [A-Z]`, `+def <name>(`, `+class `). Patches parsed internally for this even when `--include-patches` is off; just not echoed.
- **Co-changed-test detection.** Name-stem matching across test files in the same diff (strips `test_`/`_test`/`.test`/`.spec`); same-directory match also counts. Biased toward false-positive ("no test detected") over false-negative.
- **Moved-lines estimate.** Naive whitespace-trimmed cross-file matching of added vs removed lines (>3 chars). Surfaced as `stats.movedLinesEstimate`.
- **Secret-risk paths.** `.env*`, `*.pem`, `*.key`, `id_rsa*`, `secrets.*` flagged in `summary.secretsRiskFiles`. Pattern 5: paths only — file content never read.
- **Aggregate summary.** `byRole` counts, `highRiskFiles`, `testFilesTouched`, `sourceFilesWithoutTestChanges`, `publicApiTouches`, `migrationsTouched`, `secretsRiskFiles`.
- **Renames + binary files.** `git diff -M` rename detection populates `status: "renamed"` + `renamedFrom`. Binary files surface as `binary: true`, `insertions: 0`, `deletions: 0`.
- **Envelope contract.** Top-level `tool.{name, version}` injected on every payload. `--output PATH` writes the full JSON to disk and returns a compact envelope (`savedTo`, `bytes`, `fileLineCount`, `payloadKeys`, `payloadShape`). `--shape-depth 1|2|3` controls payload-shape recursion (default 3). Matches the `railway-ops` / `vercel-remote` / `repo-analyze` shape exactly.
- **Pattern 5 canary.** `.env` paths in the diff appear in `summary.secretsRiskFiles` but content is not echoed when `--include-patches` is off (default). Tested.
- **27 unit tests** covering envelope contract, mode flags (working/staged/base/range), role classification across all eight categories, risk-tier logic for each trigger, public-API detection, co-changed-test detection, moved-lines estimate, renames, binary files, `--max-files` truncation, `--include-patches`, `--public-api-only`, `--risk` filtering, `--output` offload, `--version`, Pattern 5 no-leakage, and non-git-directory error handling.

### Deliberately out of scope
- No coverage analysis. We only flag "no co-changed test in this diff".
- No file content reading. Even for risk scoring — patch content is enough.
- No cyclomatic complexity, no lint of changed lines, no AST parsing. Different tools.
- No security analysis of patch content. We flag secret-risk PATHS (`.env*`, `*.pem`, etc.) only.
- No subcommands. Single command for v0.1.0.
- No git fetch. Base ref must already exist locally; if it doesn't, error with "base ref not found locally; run `git fetch origin <base>` first."

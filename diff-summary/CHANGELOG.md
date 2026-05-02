# diff-summary — changelog

All notable changes to this plugin.

Format: one entry per change, most recent first. Date format `YYYY-MM-DD`.

## 0.3.0 - 2026-05-02

### Added
- **`nextSteps[]` in output envelope.** Every `diff-summary` invocation now includes a `nextSteps` array. When high-risk files are present, the first entry names them (up to five, with overflow count). Remaining entries suggest `skill-feedback log diff-summary` to record usefulness. Lets Claude surface the review focus and close the feedback loop automatically.

## 0.2.1 - 2026-04-29

### Fixed
- Python public-API heuristic no longer flags underscore-prefixed defs (`_helper`) as public. Previous regex `[a-z_][a-zA-Z0-9_]*` accepted leading `_`; tightened to `[a-z][a-zA-Z0-9_]*` so private-by-convention names are correctly excluded.

### Tests
- Explicit LOW-tier risk-classification test (`test_doc_only_change_is_low`) — README.md doc-only diff asserts `risk=="low"` and empty `riskReasons`. Closes a slice-8 gap where LOW was only covered implicitly by the risk-filter test.
- Python public-API regression tests: `test_python_public_def_flagged` (positive) and `test_python_underscore_def_not_flagged` (negative).

## 0.2.0 - 2026-04-28

Coordinated framework-plugin envelope-contract bump (Track A slice A0).

### Changed
- **Envelope field rename: `savedTo` → `payloadPath`.** The `--output` envelope now returns `payloadPath` instead of `savedTo` — same semantics (absolute path of the written JSON file), clearer name. Pre-1.0 breaking surface change, hence the minor bump per the project README's stability clause. CLI help text and tests updated to match. [2026-04-28]

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
- **Envelope contract.** Top-level `tool.{name, version}` injected on every payload. `--output PATH` writes the full JSON to disk and returns a compact envelope (`payloadPath`, `bytes`, `fileLineCount`, `payloadKeys`, `payloadShape`). `--shape-depth 1|2|3` controls payload-shape recursion (default 3). Matches the `railway-ops` / `vercel-remote` / `repo-analyze` shape exactly.
- **Pattern 5 canary.** `.env` paths in the diff appear in `summary.secretsRiskFiles` but content is not echoed when `--include-patches` is off (default). Tested.
- **27 unit tests** covering envelope contract, mode flags (working/staged/base/range), role classification across all eight categories, risk-tier logic for each trigger, public-API detection, co-changed-test detection, moved-lines estimate, renames, binary files, `--max-files` truncation, `--include-patches`, `--public-api-only`, `--risk` filtering, `--output` offload, `--version`, Pattern 5 no-leakage, and non-git-directory error handling.

### Deliberately out of scope
- No coverage analysis. We only flag "no co-changed test in this diff".
- No file content reading. Even for risk scoring — patch content is enough.
- No cyclomatic complexity, no lint of changed lines, no AST parsing. Different tools.
- No security analysis of patch content. We flag secret-risk PATHS (`.env*`, `*.pem`, etc.) only.
- No subcommands. Single command for v0.1.0.
- No git fetch. Base ref must already exist locally; if it doesn't, error with "base ref not found locally; run `git fetch origin <base>` first."

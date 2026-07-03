# TODOs

Deferred items that don't fit active plans but must not be forgotten.

---

## Hook content-hash check in init.py (small)

**What:** When `agent-plus-meta init` installs the cold-repo hook, it must check file content hash, not just file existence. A file-existence-only guard means upgrades leave stale hook content in place silently.

**Why:** The idempotent hook install (Plan #2) needs to handle the case where the hook script changes between framework versions. Without a content check, a user who upgraded agent-plus-meta will keep running the old hook version indefinitely.

**Depends on:** Plan #2 implementation complete.

**How to apply:** In the hook-install step of `init.py`, compute a hash of the to-be-installed hook content and compare against the existing file's hash. If different, overwrite and report "hook updated".

---

## Windows path audit: scan for raw Path(git_output) calls (small)

**What:** The MSYS `/c/dev/foo` -> `C:/dev/foo` normalisation was only added to `_write_stamp` and `_git_toplevel` in the hook. Any other place in the codebase that does `Path(subprocess_git_output.strip())` on Windows is vulnerable to the same silent wrong-path bug.

**Why:** Discovered when the stamp was silently written to `C:\c\dev\Tinker-Tailor\.agent-plus\` instead of `C:\dev\Tinker-Tailor\.agent-plus\` because `git rev-parse --show-toplevel` returned `/c/dev/Tinker-Tailor` via Git Bash. The fix was narrowly applied; a codebase-wide grep is needed.

**How to apply:** `grep -rn "Path(out.stdout" --include="*.py" .` and `grep -rn "git rev-parse" --include="*.py" .`. For each hit: confirm the output is either (a) passed through `_msys_to_windows()` before `Path()`, or (b) only ever called from non-Windows paths. Centralise the helper in a shared util if multiple callsites need it.

---

## marketplaces/ path verification (small)

**What:** Verify where `.agent-plus/marketplaces/` lands and whether it's project-local or global. Marketplace installs shouldn't reinstall per repo.

**Why:** Left as an open audit item by the skill-feedback global-path migration (since shipped), which made no claim and gave no resolution path. If marketplaces are per-project, users who switch repos find their marketplace settings missing.

**How to apply:** Read `agent-plus-meta` marketplace install code. Check where it writes. If project-local, move to `~/.agent-plus/marketplaces/` and update any path resolution logic.

---

## Windows + macOS CI runners (small)

**What:** `.github/workflows/ci.yml` only runs on `ubuntu-latest` (matrixed on Python version, not OS). The v0.19.8 Windows regression fixes (project-dir slug encoding, MSYS path normalisation, redaction patterns) are only exercised locally on a real Windows box, never in CI.

**Why:** Cross-platform is a hard requirement for agent-plus (Windows + macOS + Linux). A Windows-only regression -- like the collapsed/stripped/re-prepended slug encoder that silently found 0 sessions -- can ship again without CI ever noticing, because nothing in the pipeline runs on `windows-latest` or `macos-latest`.

**How to apply:** Add `windows-latest` (and ideally `macos-latest`) to the `runs-on` matrix in `ci.yml` for the plugin test job(s). Watch for POSIX-isms (path separators, shell built-ins) that pass today only because they've never run on Windows in CI.

---

## bootstrap_fixtures.py must scrub secret-shaped strings on copy (small, security)

**What:** `evals/scripts/bootstrap_fixtures.py` copies real local repos verbatim into `evals/fixtures/<name>/` (gitignored, local-only). A 2026-07-03 adversarial review found a real Langfuse `sk-lf-`/`pk-lf-` key pair materialized into `evals/fixtures/rainshift/archive/plans/langfuse-otel-observability.md` this way.

**Why:** the fixture dirs are gitignored so nothing ships, but plaintext credentials on disk outside their source repo is silent risk, and it recurs on every re-bootstrap of a real repo.

**How to apply:** route every text file through the same `scrub_text`/`_SECRET_PATTERNS` machinery skill-plus uses (or a copy of the pattern list) during the bootstrap copy; add a test with a seeded fake `sk-lf-` token. Rotate the specific leaked key at the Langfuse instance regardless (its true source is the rainshift repo itself).

---

## `--json` dest mismatch on upgrade-check / upgrade / uninstall (small)

**What:** Those three subparsers each define their own `--json` as a plain `store_true` (dest `json`), separate from the top-level `--json` (dest `force_json`). Passing the flag AFTER the subcommand (`agent-plus-meta uninstall --json`) sets `args.json`, which `main()`'s envelope-suppression check never reads -- so documented "script mode" still suppresses the JSON envelope on an interactive terminal. Additionally, `cmd_uninstall` computes an `interactive` flag from this `json`/`non_interactive` pair but only records it in the envelope; the y/N confirmation is gated solely by `non_interactive`, so `uninstall --json` without `--non-interactive` can still block on `input()`.

**Why:** Scripts using the documented `--json` script mode get a silently-suppressed envelope from a terminal, and automation can hang on a hidden prompt. Found during the 2026-07 nextSteps contract work; both defects are pre-existing and were left alone because fixing them changes frozen envelope-timing/prompt behavior.

**How to apply:** Point the three subparser `--json` flags at `dest="force_json"` (or hoist into the shared pretty/json parent parser); add a regression test that `uninstall --json` on a fake TTY prints the envelope; decide whether `--json` should imply `--non-interactive` for the prompt (contract change -- document in the uninstall envelope doc if so).

# TODOs

Deferred items that don't fit active plans but must not be forgotten.

---

## Hook content-hash check in init.py (small)

**What:** When `agent-plus-meta init` installs the cold-repo hook, it must check file content hash, not just file existence. A file-existence-only guard means upgrades leave stale hook content in place silently.

**Why:** The idempotent hook install (Plan #2) needs to handle the case where the hook script changes between framework versions. Without a content check, a user who upgraded agent-plus-meta will keep running the old hook version indefinitely.

**Depends on:** Plan #2 implementation complete.

**How to apply:** In the hook-install step of `init.py`, compute a hash of the to-be-installed hook content and compare against the existing file's hash. If different, overwrite and report "hook updated".

---

## Plan #3 (skill-feedback global path) must complete before public launch

**What:** The "no migration needed" assumption in Plan #3 is only valid while there are no live users. If Plan #3 is not completed before any public release, it becomes a migration problem.

**Why:** Once users have ratings in `<project>/.agent-plus/skill-feedback/`, moving to the global path requires a migration script. The plan skips the migration step because there are no live users today — this assumption decays the moment anything ships.

**How to apply:** Block any public release / marketplace listing on Plan #3 completion. Add this to the release checklist.

---

## Windows path audit: scan for raw Path(git_output) calls (small)

**What:** The MSYS `/c/dev/foo` -> `C:/dev/foo` normalisation was only added to `_write_stamp` and `_git_toplevel` in the hook. Any other place in the codebase that does `Path(subprocess_git_output.strip())` on Windows is vulnerable to the same silent wrong-path bug.

**Why:** Discovered when the stamp was silently written to `C:\c\dev\Tinker-Tailor\.agent-plus\` instead of `C:\dev\Tinker-Tailor\.agent-plus\` because `git rev-parse --show-toplevel` returned `/c/dev/Tinker-Tailor` via Git Bash. The fix was narrowly applied; a codebase-wide grep is needed.

**How to apply:** `grep -rn "Path(out.stdout" --include="*.py" .` and `grep -rn "git rev-parse" --include="*.py" .`. For each hit: confirm the output is either (a) passed through `_msys_to_windows()` before `Path()`, or (b) only ever called from non-Windows paths. Centralise the helper in a shared util if multiple callsites need it.

---

## marketplaces/ path verification (small)

**What:** Verify where `.agent-plus/marketplaces/` lands and whether it's project-local or global. Marketplace installs shouldn't reinstall per repo.

**Why:** Plan #3 identifies this as an open audit item but makes no claim and gives no resolution path. If marketplaces are per-project, users who switch repos find their marketplace settings missing.

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

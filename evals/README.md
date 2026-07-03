# Evals

## Local structured report (recommended before shipping)

Run **`python evals/scripts/run_local_eval_report.py`** from the repo root (optional: configure [`evals/ollama.env`](./ollama.env.example) for a narrative). Output lands in **`evals/reports/<UTC-slug>/`** (gitignored): machine-readable **`manifest.json`**, **`pytest_junit.xml`**, logs, merged **`benchmark.json`**, and **`USER_JOURNEY_REPORT.md`** (Ollama explains what the run meant for a real user, or a short fallback if the model is skipped). **CI does not run evals** — keep that contract local so fixtures + Ollama stay under your control.

## User journey (website → what this folder proves)

End-to-end intent on the site: **install → register plugins → `agent-plus-meta init` → parallel orientation (`repo-analyze` ∥ `skill-plus scan`) → `skill-plus propose` → `skill-plus scaffold` → daily tools (`diff-summary`, skills in-session) → `skill-feedback` → repeat.**

This folder proves **merge-safe contracts** in the **middle** of that arc: real subprocesses for **`repo-analyze`**, **`diff-summary`**, **`doctor --json`**, **`skill-plus scan`**, **`skill-plus propose`** (JSON ranking from a seeded `candidates.jsonl`), **`skill-plus scaffold`** (emit `.claude/skills/…`), **`skill-feedback`** (`log` → `report`), **`detect_suggested_skills`**, plus optional **Ollama** smoke for judge-shaped prompts. It does **not** run `install.sh`, interactive **`agent-plus-meta init`**, **`skill-feedback submit`** to GitHub, **`promote`**, or **`propose`** as an interactive UI — deep edge cases stay in **plugin tests**. The table under [Lifecycle on the site vs this folder](#lifecycle-on-the-site-vs-this-folder) is the detailed stage-by-stage map.

## Are these “real world”?

**Partially.** Evals prove **deterministic CLI ↔ JSON contracts** on **minimal git repos** and **curated synthetic session logs**. They do **not** replay a full human install, Claude Code UI, or your actual `~/.claude/projects/` tree.

| Feels like production | Deliberately not |
|----------------------|------------------|
| Real `git` history (`bootstrap_fixtures.py` → two commits per fixture), real subprocess calls to `repo-analyze`, `diff-summary`, envelope parsers | `install.sh`, interactive `agent-plus-meta init`, scanning **live** project dirs |
| Same plugins/binaries as a checkout (tests invoke repo-root `*/bin/*`) | Multi-day accumulation — **partial**: phased JSONL + second `scan` pass (`test_skill_plus_scan_second_pass_new_cluster`) exercises new clusters + dedupe; not real clock time |
| `skill-plus scan` on **committed synthetic JSONL**; **`propose` / `scaffold`** as **subprocess JSON + filesystem** smoke (`test_plugin_cli_contracts.py`) | **`promote`**, **`skill-feedback submit`** (GitHub), ranking **UX** polish — full depth in **`skill-plus/test`**, **`skill-feedback/test`** |
| Nested session discovery (`subagents/*.jsonl`) is covered in **plugin tests** (`skill-plus/test/test_scan.py`); eval contract uses flat fixtures but README documents parity | Marketplace install UX, interactive **`init`** wizard |

So: if a user installed agent-plus and worked in repos **similar** to the fixtures, **`repo-analyze` / `diff-summary` output shape** and **`detect_suggested_skills` markers** match what CI asserts. Rolling clock time (“weeks”) is **not** simulated — only **additional synthetic sessions** appended between scans. See [Roadmap](#roadmap-multi-session-and-lifecycle).

## Lifecycle on the site vs this folder

The marketing flow is **install → init → parallel (repo-analyze ∥ skill-plus scan) → propose → scaffold → use → feedback → loops**. This repo’s **evals** sit in the **middle**: post-bootstrap, command-level correctness — **not** the installer nor the full skill authoring loop.

| Stage | Website intent | Covered by `evals/tests/`? | Where else |
|-------|----------------|-----------------------------|------------|
| Install (`install.sh`, plugin register) | One-shot setup | No | Manual / future optional E2E tier |
| `agent-plus-meta init` / `doctor` | Workspace bootstrap | **`doctor --json`** envelope + no-secret string scan (`test_envelope.py`); not interactive `init` wizard | Plugin tests for broader init |
| `repo-analyze` | “What the repo IS” | Yes — goldens + lifecycle hints in JSON | — |
| `skill-plus scan` | “What you DO” from logs | Yes — synthetic JSONL contract (`test_skill_plus_scan_contract.py`) | `skill-plus/test` for CLI edge cases |
| `skill-plus propose` | Rank candidates | **Yes** — JSON envelope + ranked rows from hermetic `candidates.jsonl` (`test_plugin_cli_contracts.py`); not the interactive picker | `skill-plus/test/test_propose.py` for ranking edge cases |
| `skill-plus scaffold` | Emit SKILL + bin | **Yes** — one scaffold run + on-disk tree (`test_plugin_cli_contracts.py`) | `skill-plus/test/test_scaffold.py` for overwrite / `from-candidate` / etc. |
| Daily use (`skill-feedback`, `diff-summary`) | Triage + ratings | **Both** — `diff-summary` on fixtures; **`skill-feedback`** `log` → `report` JSON (`test_plugin_cli_contracts.py`) | Deeper coverage in plugin tests |
| Multi-session loop (“scan finds more next week”) | Accumulation over time | **Partial** — `test_skill_plus_scan_second_pass_new_cluster` (append sessions, rescan, new + updated candidates) | Full calendar-time soak only in manual / future E2E tier |

**Bottom line:** evals are **not** full end‑to‑end from curl install through promoted skill. They **are** merge-safe regression gates for **JSON contracts + git-backed tools** that the lifecycle depends on.

## What is / is not covered

| Covered | How |
|--------|-----|
| **Envelope contract** (`tool.name`, `tool.version`, no `savedTo`, `payloadPath` on `--output`) | `test_envelope.py` |
| **`repo-analyze` shape** vs committed goldens + language/build hints | `test_repo_analyze.py` |
| **`repo-analyze` lifecycle hints** (`nextSteps` mentions skill-plus scan + diff-summary) | `test_repo_analyze.py` |
| **`diff-summary`** on real git history in fixtures (`HEAD~1..HEAD`) | `test_diff_summary.py` |
| **Marketplace skill suggestions** (`detect_suggested_skills`: markers → `github-remote`, etc.) | `test_suggested_skills.py` |
| **`skill-plus scan`** (Bash mining → cluster key + `candidates.jsonl`; phased second pass) | `test_skill_plus_scan_contract.py` + `evals/fixtures/skill-plus-scan/*.jsonl` |
| **Langfuse bridge** (markers + scan on same project path) | `test_langfuse_bridge.py` + `evals/fixtures/langfuse-bridge/` |
| **`skill-plus propose`**, **`skill-plus scaffold`**, **`skill-feedback`** (`log` / `report`) | `test_plugin_cli_contracts.py` (tmp `SKILL_FEEDBACK_DIR`, `SKILL_PLUS_DIR`, git init) |
| **Optional Ollama** “judge-shaped” smoke | `test_ollama_skill_judge_smoke.py` |

**Not** full end-to-end install UX: no `install.sh`, no interactive `init` wizard, no `skill-plus scan` against your **real** `~/.claude/projects/` tree (use plugin tests or manual). Evals use **synthetic JSONL** for scan contract tests (flat layout; production `scan` also mines nested `subagents/*.jsonl`, exercised under `skill-plus/test`). Evals target **deterministic CLI ↔ JSON contracts** so CI stays fast and hermetic.

**Suggested marketplace skills** (Vercel, GitHub Actions, …) come from **filesystem markers + `package.json` deps** inside `agent-plus-meta` — tested via `detect_suggested_skills`. Production **`propose`** reads real session-mined candidates; evals **seed** `candidates.jsonl` to assert JSON ranking shape without mining `~/.claude/projects/`.

## Roadmap: multi-session and lifecycle

1. **Hermetic multi-session fixtures** — **Done** — `test_skill_plus_scan_second_pass_new_cluster` adds `day2-a` / `day2-b` JSONL after an initial railway-only scan; asserts a **new** kubectl cluster and **updated** existing rows (same machinery as `skill-plus/test_scan.py::test_dedupe_on_second_run`).
2. **`init --non-interactive --auto`** — **Deferred** — `doctor --json` is already in `test_envelope.py`. Full `init` needs a hermetic `--dir` + fake `HOME` story; behaviour is covered in **`agent-plus-meta/test`**. Optional future: subprocess smoke behind env flag if CI flakiness is solved per OS.
3. **Thin integration tier (optional)** — Behind `AGENT_PLUS_E2E=1` or nightly only: `install.sh --unattended` and/or real project-dir scan; **not** required for every PR.

Acceptance for (1): no network; tmp `HOME` + `SKILL_PLUS_DIR` only; runtime similar to other eval subprocess tests.

## Commands

On **Windows**, if `python` is missing or opens the Microsoft Store stub, call your installed interpreter by path (adjust `Python312` to match your install):

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pytest evals/tests/ -v
```

Use that same prefix instead of `python` for the scripts in the table below.

### Pytest alone vs structured report

| What you run | What you get |
|--------------|----------------|
| **`pytest evals/tests/`** | Console output only (pass/fail/skipped). **Does not** create `evals/reports/` — that is intentional; pytest is the fast regression loop. |
| **`evals/scripts/run_local_eval_report.py`** | Runs pytest **inside** the script, then writes **`evals/reports/<UTC-slug>/`**: `manifest.json`, `pytest_junit.xml`, stdout/stderr logs, `benchmark.json`, optional **`USER_JOURNEY_REPORT.md`**. Use this when you want an artifact to read after the run (“did this simulate a real user?”). |

So: **quick check** → pytest. **Report folder + narrative** → run the script (same Python interpreter as above):

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" evals/scripts/run_local_eval_report.py
```

Optional: `--skip-ollama` if you only want files without calling Ollama. Open the newest directory under `evals/reports/` and read `USER_JOURNEY_REPORT.md` (or `manifest.json`).

| Command | Purpose |
|---------|---------|
| `python -m pytest evals/tests/ -v` | Full suite (Ollama tests skip unless configured). Console only — no report directory. |
| `python evals/scripts/run_local_eval_report.py` | **Produces `evals/reports/<timestamp>/`** — `manifest.json`, JUnit + logs, `benchmark.json`, optional **`USER_JOURNEY_REPORT.md`** (Ollama). **Evals are not run in GitHub Actions** — use this for a structured “real user” artifact. |
| `python evals/scripts/bootstrap_fixtures.py` | Rebuild minimal `evals/fixtures/*` git repos. |
| `python evals/scripts/regenerate_goldens.py` | Refresh `evals/golden/*-repo-analyze.json`. |
| `python evals/scripts/verify_fixture_budget.py` | Ensure fixtures are still tiny (CI). |
| `python evals/scripts/benchmark_evals.py` | Append timing JSONL (see `benchmarks/README.md`). |

## Ollama (optional LLM smoke / judge prototype)

For local runs with [Ollama](https://ollama.com/) (`ollama serve`), either:

1. **Config file (recommended):** copy [`evals/ollama.env.example`](./ollama.env.example) to `evals/ollama.env` and set `OLLAMA_CHAT_MODEL` / `OLLAMA_BASE_URL`. Pytest loads `evals/ollama.env` before tests (existing shell env wins on conflicts).

2. **One-off:** `set OLLAMA_CHAT_MODEL=llama3.2` and `set OLLAMA_BASE_URL=http://localhost:11434` (cmd), or the equivalent in PowerShell.

```bash
python -m pytest evals/tests/test_ollama_skill_judge_smoke.py -v
```

If `OLLAMA_CHAT_MODEL` is unset, those tests **skip** (default for CI). They use **framework** `SKILL.md` text from `repo-analyze/skills/…`, not fixture repo contents.

### Production / Claude Code

- **CI and shipped installs never depend on Ollama.** Optional tests live behind env vars; merging without a local LLM is unchanged.
- **SKILL.md files stay authored for Claude** (Haiku / Sonnet / Opus). We do not slim them down to suit tiny models — that would weaken real-user UX.
- **Ollama evals are a separate calibration lane:** if extraction smoke passes on `llama3.2`-class models, stronger models remain covered; if small models fail, tune **eval prompts/excerpts** here, not plugin SKILL copy (unless you intentionally land a clarity improvement that helps everyone).

### Model limits

Small local models may drift on vague judges; the tests use **short excerpts + extraction** (name the `--json` flag) rather than subjective grades. For flaky setups, confirm `ollama list` shows your tag and try `ollama pull <model>`.

### Can Ollama “simulate” `propose`, `scaffold`, `skill-feedback`, or `diff-summary`?

**No — production paths stay deterministic.** Those plugins are **stdlib Python, no network**; they do not call Ollama. **Merge gates** for JSON + filesystem behaviour are **`test_diff_summary.py`**, **`test_plugin_cli_contracts.py`**, and plugin suites — not an LLM.

| Command | Contract coverage in evals | Ollama? |
|--------|---------------------------|--------|
| **`diff-summary`** | **Real** subprocess on minimal fixtures (`test_diff_summary.py`). | Not used. Optional **semantic grader** on summary text would be research-only, not a substitute for structured output checks. |
| **`skill-feedback`** | **`log` → `report`** in a tmp `SKILL_FEEDBACK_DIR` (`test_plugin_cli_contracts.py`). | Not used. |
| **`skill-plus propose`** | **JSON** ranking from a **seeded** `candidates.jsonl` (`test_plugin_cli_contracts.py`). Same CLI as production; eval skips mining real session logs. | Not used. Does not replace an interactive **picker** UX — that remains manual / product. |
| **`skill-plus scaffold`** | One **full emit** under `.claude/skills/…` (`test_plugin_cli_contracts.py`). | Not used. Optional **prose-quality** comparison vs a model would be separate from correctness. |

Keep **Ollama evals** (`test_ollama_skill_judge_smoke.py`) for the narrow **judge / extraction** experiments only; do not route these CLIs through Ollama for CI.

## Public repo — fixtures must stay minimal

See [`fixtures/README.md`](./fixtures/README.md). Never commit full copies of external projects under `evals/fixtures/`.

Minimal **`osdb` / `rainshift` / `tinker-tailor`** git trees are **generated** (`bootstrap_fixtures.py`) and gitignored — only goldens + scripts are versioned.

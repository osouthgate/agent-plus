# Eval benchmarks (local history)

Run from repo root:

```bash
python evals/scripts/benchmark_evals.py
```

This times `repo-analyze` and `diff-summary` on each minimal fixture under `evals/fixtures/`, then runs `pytest evals/tests/` unless you pass `--no-pytest`. Each run appends one JSON object as a line to `history.jsonl` (gitignored).

**Why JSONL + commit:** Each line includes `commit` / `commitShort` from `git rev-parse HEAD` plus `timingsMs`. You can diff runs by revision without checking a growing file into git (noisy merges). For a **single run folder** with pytest + merged timings + optional Ollama Markdown, use **`python evals/scripts/run_local_eval_report.py`** (output under `evals/reports/`, gitignored).

**Quick CLI-only timing:**

```bash
python evals/scripts/benchmark_evals.py --no-pytest
```

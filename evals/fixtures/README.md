# Eval git fixtures (public repo policy)

The **`osdb`**, **`rainshift`**, and **`tinker-tailor`** dirs are **small synthetic git repositories**. They are **not committed** to this repo (each contains a nested `.git`; Git would record them as submodule/gitlink blobs). They are created locally and in CI by:

```bash
python evals/scripts/bootstrap_fixtures.py
```

Pytest auto-runs that script when `evals/tests/` runs and the dirs are missing.

**Do not** replace them with `robocopy` (or similar) snapshots of real customer or internal monorepos when preparing PRs against the public `agent-plus` framework. Large copies bloat the repo, risk leaking paths or code, and are unnecessary — `repo-analyze` / `diff-summary` behaviour is covered by minimal trees plus golden JSON.

Ephemeral paths such as `.agent-plus/repo-analyze.stamp` may appear after local runs; they are gitignored under `.agent-plus/` and stripped in normalized golden tests.

Committed **`skill-plus-scan/`** holds static JSONL-only fixtures for scan contract tests (no nested git).

Committed **`langfuse-bridge/`** combines a root **`langfuse.yaml`** marker (for `detect_suggested_skills` → `langfuse-remote`) with **`sessions/*.jsonl`** copies used by `test_langfuse_bridge.py` — one layout proving stack markers and Bash mining together (no nested `.git`).

# skill-plus scan fixture

Synthetic Claude Code session JSONL fragments used by `evals/tests/test_skill_plus_scan_contract.py`.

Shape matches `skill-plus/test/test_scan.py` (`tool_use` / `Bash` / `input.command`).

- **`s1.jsonl` / `s2.jsonl`** — railway Bash cluster (repeat count + two sessions).
- **`day2-a.jsonl` / `day2-b.jsonl`** — second-wave `kubectl get pods` cluster (used only after first scan in `test_skill_plus_scan_second_pass_new_cluster`).

Not copied from real `~/.claude/projects/` logs — hand-authored minimal lines only.

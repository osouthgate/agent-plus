"""claude_code transcript adapter.

Parses Claude Code JSONL transcripts at `~/.claude/projects/<slug>/*.jsonl`.

Schema (verified against a real session 2026-05-01):
  Each line is a JSON object. Assistant messages with tool calls have shape:
    {
      "type": "assistant",
      "timestamp": "2026-04-27T20:11:53.084Z",
      "message": {
        "content": [
          {
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": "...", "description": "..."}
          },
          ...
        ],
        ...
      },
      ...
    }

We extract every Bash tool_use; non-Bash tool_use entries are also yielded
(useful future signal: `Read`, `Write`, `Edit` etc.). A.2 can filter.

Stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Tuple


def iter_tuples(path: Path) -> Iterator[Tuple[str, str, str, str, dict]]:
    """Yield (timestamp, source_path, tool, command, args) per tool_use entry."""
    src = str(path)
    try:
        f = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    try:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") != "assistant":
                continue
            ts = obj.get("timestamp") or ""
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "tool_use":
                    continue
                name = item.get("name") or ""
                inp = item.get("input") or {}
                if not isinstance(inp, dict):
                    inp = {}
                command = ""
                if name == "Bash":
                    command = inp.get("command") or ""
                else:
                    # For non-Bash, surface the most command-like field if present.
                    for k in ("command", "query", "pattern", "url", "file_path"):
                        v = inp.get(k)
                        if isinstance(v, str):
                            command = v
                            break
                args = {k: v for k, v in inp.items() if k != "command"}
                yield (ts, src, str(name), str(command), args)
    finally:
        f.close()

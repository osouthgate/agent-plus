#!/usr/bin/env python3
"""Generate assets/tour.cast — a synthetic asciinema v2 recording of the
90-second tour shown in the root README. Output is a deterministic JSON
file so the GIF rebuild is reproducible.

Usage:
    python3 assets/generate_tour_cast.py     # writes assets/tour.cast
    docker run --rm -v "$PWD/assets:/data" ghcr.io/asciinema/agg /data/tour.cast /data/tour.gif

Or use the wrapper:
    bash assets/build_tour_gif.sh

Cast format (v2):
    line 1: header dict — version, width, height, timestamp, title, env
    line 2..N: event arrays — [time_offset_seconds, "o", "output_text"]
"""
from __future__ import annotations

import json
from pathlib import Path

# ─── ANSI shorthand ──────────────────────────────────────────────────────────

DIM = "\x1b[2m"
BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"
PROMPT = f"{CYAN}${RESET} "


def _line(t: float, text: str) -> list:
    return [round(t, 3), "o", text]


def build_events() -> list:
    """Build the timeline. Each command pauses for typing-realism + result."""
    events: list = []
    t = 0.0

    def emit(text: str, dwell: float = 0.0) -> None:
        nonlocal t
        events.append(_line(t, text))
        t += dwell

    # Initial banner
    emit(f"{DIM}# agent-plus framework — 90-second tour{RESET}\r\n", dwell=0.8)
    emit(f"{DIM}# (synthetic recording; output is illustrative){RESET}\r\n\r\n", dwell=0.6)

    # 1. init
    emit(PROMPT + "agent-plus-meta init\r\n", dwell=0.7)
    emit(f"{GREEN}✓{RESET} created manifest.json, services.json, env-status.json\r\n", dwell=0.6)
    emit(f"{DIM}  workspace: /repo/.agent-plus  (source: git){RESET}\r\n\r\n", dwell=1.0)

    # 2. envcheck
    emit(PROMPT + "agent-plus-meta envcheck\r\n", dwell=0.7)
    emit(f"{GREEN}✓{RESET} ready: github-remote, vercel-remote, langfuse-remote\r\n", dwell=0.5)
    emit(f"{RED}✗{RESET} missing: SUPABASE_ACCESS_TOKEN  →  supabase-remote unconfigured\r\n\r\n", dwell=1.2)

    # 3. refresh
    emit(PROMPT + "agent-plus-meta refresh\r\n", dwell=0.7)
    emit(f"{GREEN}✓{RESET} services: 6 ok, 1 unconfigured  ({DIM}4.2s{RESET})\r\n", dwell=0.5)
    emit(f"{DIM}  written to .agent-plus/services.json{RESET}\r\n\r\n", dwell=1.2)

    # 4. repo-analyze
    emit(PROMPT + "repo-analyze --pretty | jq '.frameworks'\r\n", dwell=0.8)
    emit(f"[\r\n", dwell=0.1)
    emit(f"  {{\"name\": \"{BOLD}Next.js{RESET}\", \"evidence\": \"package.json:next\", \"confidence\": \"high\"}},\r\n", dwell=0.3)
    emit(f"  {{\"name\": \"{BOLD}TailwindCSS{RESET}\", \"evidence\": \"package.json:tailwindcss\", \"confidence\": \"high\"}}\r\n", dwell=0.3)
    emit(f"]\r\n\r\n", dwell=1.2)

    # 5. diff-summary
    emit(PROMPT + "diff-summary --base main\r\n", dwell=0.8)
    emit(f"{{\r\n", dwell=0.1)
    emit(f"  \"summary\": {{\r\n", dwell=0.1)
    emit(f"    \"highRisk\": {GREEN}0{RESET},\r\n", dwell=0.15)
    emit(f"    \"publicApiTouches\": {YELLOW}1{RESET},\r\n", dwell=0.15)
    emit(f"    \"missingTests\": {GREEN}0{RESET},\r\n", dwell=0.15)
    emit(f"    \"filesChanged\": 12\r\n", dwell=0.15)
    emit(f"  }}\r\n", dwell=0.1)
    emit(f"}}\r\n\r\n", dwell=1.2)

    # 6. skill-plus scan
    emit(PROMPT + "skill-plus scan --pretty\r\n", dwell=0.8)
    emit(f"{{\r\n", dwell=0.1)
    emit(f"  \"candidatesNew\": {BOLD}3{RESET},\r\n", dwell=0.2)
    emit(f"  \"candidates\": [\r\n", dwell=0.1)
    emit(f"    {{\"key\": \"railway logs --service\", \"count\": {YELLOW}14{RESET}, \"sessions\": 3}},\r\n", dwell=0.3)
    emit(f"    {{\"key\": \"psql -h staging\",        \"count\": {YELLOW} 9{RESET}, \"sessions\": 2}},\r\n", dwell=0.3)
    emit(f"    {{\"key\": \"gh pr view --json\",      \"count\": {YELLOW} 6{RESET}, \"sessions\": 4}}\r\n", dwell=0.3)
    emit(f"  ]\r\n}}\r\n\r\n", dwell=1.4)

    # 7. skill-plus scaffold
    emit(PROMPT + "skill-plus scaffold railway-probe --from-candidate 8ad12e3f9be1\r\n", dwell=0.9)
    emit(f"{GREEN}✓{RESET} wrote {BOLD}.claude/skills/railway-probe/{RESET}{{SKILL.md, bin/, ...}}\r\n", dwell=0.5)
    emit(f"{DIM}  killer-command pre-filled from 14 mined invocations{RESET}\r\n\r\n", dwell=1.5)

    # closing pause
    emit(f"{DIM}# That's the loop: session log → mined candidate → scaffolded skill{RESET}\r\n", dwell=2.0)

    return events


def build_cast() -> str:
    header = {
        "version": 2,
        "width": 96,
        "height": 28,
        "timestamp": 1714478400,  # 2024-04-30T12:00:00Z, deterministic
        "title": "agent-plus — 90-second tour",
        "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
    }
    lines = [json.dumps(header, separators=(",", ":"))]
    for ev in build_events():
        lines.append(json.dumps(ev, separators=(",", ":")))
    return "\n".join(lines) + "\n"


def main() -> None:
    out = Path(__file__).resolve().parent / "tour.cast"
    out.write_text(build_cast(), encoding="utf-8", newline="\n")
    size = out.stat().st_size
    print(f"wrote {out} ({size} bytes)")


if __name__ == "__main__":
    main()

"""skill-plus propose — read+rank surface over the candidate log.

Reads the project's candidates.jsonl (written by `scan`), ranks candidates by
count + distinct-session breadth + recency, augments each row with a derived
proposed skill name and an existence-check against `.claude/skills/`, and emits
a structured envelope.

Slice 3.3: read-only. Interactive y/n loop is a follow-up refinement.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any


def _resolve_project(args) -> Path:
    if getattr(args, "project", None):
        return Path(args.project).expanduser().resolve()
    top = _git_toplevel()  # noqa: F821 — injected
    if top is not None:
        return top
    return Path.cwd().resolve()


def _read_candidates(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _parse_iso(s: Any) -> _dt.datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        # Accept trailing Z or offset
        if s.endswith("Z"):
            return _dt.datetime.fromisoformat(s[:-1]).replace(tzinfo=_dt.timezone.utc)
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _days_since(last_seen: Any, now: _dt.datetime) -> float:
    dt = _parse_iso(last_seen)
    if dt is None:
        return 999.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    delta = now - dt
    return max(0.0, delta.total_seconds() / 86400.0)


_FLAG_RE = re.compile(r"^-")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _propose_name(key: Any, cand_id: Any) -> str:
    """Take first non-flag token from the cluster key, slugify. Fallback to skill-<id[:6]>."""
    if isinstance(key, str) and key.strip():
        tokens = key.strip().split()
        for tok in tokens:
            if _FLAG_RE.match(tok):
                continue
            slug = _NON_ALNUM_RE.sub("-", tok.lower()).strip("-")
            if slug:
                # Optional second token (also non-flag, alnum) for command-subcommand style.
                idx = tokens.index(tok)
                second = ""
                for nxt in tokens[idx + 1:]:
                    if _FLAG_RE.match(nxt):
                        break
                    second_slug = _NON_ALNUM_RE.sub("-", nxt.lower()).strip("-")
                    if second_slug:
                        second = second_slug
                    break
                return f"{slug}-{second}" if second else slug
    # Fallback
    sid = str(cand_id) if cand_id is not None else ""
    sid = re.sub(r"[^A-Za-z0-9]", "", sid)[:6] or "unknown"
    return f"skill-{sid}"


def _skill_exists(project: Path, name: str) -> bool:
    return (project / ".claude" / "skills" / name).is_dir()


def run(args, emit_fn):
    project = _resolve_project(args)
    log_path = candidates_log_path()  # noqa: F821 — injected

    limit = int(getattr(args, "limit", 10) or 10)
    if limit < 1:
        limit = 10

    rows = _read_candidates(log_path)

    if not rows:
        emit_fn({
            "project": str(project),
            "candidatesTotal": 0,
            "candidatesShown": 0,
            "candidates": [],
            "note": "no candidates yet — run skill-plus scan first",
        })
        return 0

    now = _dt.datetime.now(_dt.timezone.utc)

    augmented: list[dict] = []
    for row in rows:
        count = float(row.get("count", 0) or 0)
        sessions = row.get("sessions", 0) or 0
        try:
            distinct_sessions = float(len(sessions)) if isinstance(sessions, (list, set, tuple)) else float(sessions)
        except (TypeError, ValueError):
            distinct_sessions = 0.0
        days = _days_since(row.get("lastSeen"), now)
        recency_boost = max(0.0, 7.0 - days)
        score = count * 1.0 + distinct_sessions * 0.5 + recency_boost

        name = _propose_name(row.get("key"), row.get("id"))
        existing = _skill_exists(project, name)

        out = dict(row)
        out["score"] = round(score, 4)
        out["daysSinceLastSeen"] = round(days, 1)
        out["proposedSkillName"] = name
        out["existing"] = existing
        out["kind"] = "enhance" if existing else "new"
        augmented.append(out)

    augmented.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    shown = augmented[:limit]

    emit_fn({
        "project": str(project),
        "candidatesTotal": len(augmented),
        "candidatesShown": len(shown),
        "candidates": shown,
    })
    return 0

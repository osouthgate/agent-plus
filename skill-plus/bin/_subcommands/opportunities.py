"""skill-plus opportunities -- weekly self-improvement report.

Composes the existing mining outputs into one read-side report:
candidate skills, cadence/habit candidates, friction blocks, and explicit
skill-feedback ratings. Optional --run-scan refreshes the mining logs first.

Helpers (candidates_log_path, routine_candidates_log_path, blocks_log_path,
_git_toplevel, _now_iso, etc.) are injected by the bin shell.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def _resolve_project(args) -> Path:
    if getattr(args, "project", None):
        return Path(args.project).expanduser().resolve()
    top = _git_toplevel()  # noqa: F821 -- injected
    return (top if top is not None else Path.cwd()).resolve()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
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
        dt: _dt.datetime
        if s.endswith("Z"):
            dt = _dt.datetime.fromisoformat(s[:-1]).replace(tzinfo=_dt.timezone.utc)
        else:
            dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _days_since(last_seen: Any) -> float:
    dt = _parse_iso(last_seen)
    if dt is None:
        return 999.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    delta = _dt.datetime.now(_dt.timezone.utc) - dt
    return max(0.0, delta.total_seconds() / 86400.0)


_FLAG_RE = re.compile(r"^-")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _propose_name(key: Any, cand_id: Any) -> str:
    if isinstance(key, str) and key.strip():
        tokens = key.strip().split()
        for i, tok in enumerate(tokens):
            if _FLAG_RE.match(tok):
                continue
            slug = _NON_ALNUM_RE.sub("-", tok.lower()).strip("-")
            if not slug:
                continue
            second = ""
            for nxt in tokens[i + 1:]:
                if _FLAG_RE.match(nxt):
                    break
                second = _NON_ALNUM_RE.sub("-", nxt.lower()).strip("-")
                break
            return f"{slug}-{second}" if second else slug
    sid = str(cand_id) if cand_id is not None else ""
    sid = re.sub(r"[^A-Za-z0-9]", "", sid)[:6] or "unknown"
    return f"skill-{sid}"


def _sessions_count(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _skill_exists(project: Path, name: str) -> bool:
    return (project / ".claude" / "skills" / name).is_dir()


def _opportunity(kind: str, title: str, reason: str, command: str,
                 score: float, evidence: dict) -> dict:
    return {
        "id": f"{kind}:{evidence.get('id') or evidence.get('key') or title}",
        "kind": kind,
        "title": title,
        "reason": reason,
        "command": command,
        "priorityScore": round(score, 3),
        "evidence": evidence,
    }


def _in_window(row: dict, since_days: int) -> bool:
    if since_days <= 0:
        return False
    for key in ("lastSeen", "scannedAt"):
        if row.get(key):
            return _days_since(row.get(key)) <= since_days
    return True


def _skill_opportunities(project: Path, rows: list[dict], since_days: int) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if not _in_window(row, since_days):
            continue
        cand_id = str(row.get("id") or "")
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        count = int(row.get("count", 0) or 0)
        sessions = _sessions_count(row.get("sessions"))
        name = _propose_name(key, cand_id)
        existing = _skill_exists(project, name)
        days = _days_since(row.get("lastSeen"))
        recency = max(0.0, 7.0 - days)
        score = count + sessions * 0.5 + recency + (1.0 if existing else 2.0)
        if existing:
            title = f"Improve existing skill `{name}` from repeated `{key}` usage"
            command = f"skill-plus where {name}"
        else:
            title = f"Create skill `{name}` for repeated `{key}` usage"
            command = f"skill-plus scaffold {name} --from-candidate {cand_id}"
        reason = f"{count} invocation(s) across {sessions} session(s)"
        out.append(_opportunity("skill", title, reason, command, score, {
            "id": cand_id,
            "key": key,
            "count": count,
            "sessions": sessions,
            "existing": existing,
            "lastSeen": row.get("lastSeen"),
        }))
    return out


def _cadence_opportunities(rows: list[dict], since_days: int) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if not _in_window(row, since_days):
            continue
        kind = row.get("kind")
        if kind not in ("routine", "habit"):
            continue
        if row.get("dismissed"):
            continue
        cand_id = str(row.get("id") or "")
        key = str(row.get("key") or "").strip()
        count = int(row.get("count", 0) or 0)
        dates = int(row.get("distinctDates", 0) or 0)
        if kind == "routine":
            title = f"Schedule recurring `{key}` workflow"
            command = "skill-plus propose --kind routine --pretty"
            reason = f"routine-like cadence on {dates} date(s); review the schedule string before adopting"
            score = 20.0 + count + dates
            evidence = {
                "id": cand_id,
                "key": key,
                "count": count,
                "distinctDates": dates,
                "scheduleString": row.get("scheduleString"),
                "regularity": row.get("regularity"),
            }
        else:
            title = f"Consider a skill for frequent `{key}` habit"
            command = "skill-plus propose --kind habit --pretty"
            reason = f"repeats on {dates} date(s) without a tight clock trigger"
            score = 12.0 + count + dates
            evidence = {
                "id": cand_id,
                "key": key,
                "count": count,
                "distinctDates": dates,
                "suggestion": row.get("suggestion"),
            }
        out.append(_opportunity(str(kind), title, reason, command, score, evidence))
    return out


def _block_opportunities(rows: list[dict], since_days: int) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if not _in_window(row, since_days):
            continue
        count = int(row.get("count", 0) or 0)
        if count <= 0:
            continue
        sig = str(row.get("signature") or row.get("tool") or "permission block")
        cls = str(row.get("class") or "unknown")
        fixable = bool(row.get("configFixable"))
        title = f"Reduce repeated permission friction for `{sig}`"
        command = "skill-plus propose --kind blocks --pretty"
        reason = f"{count} block(s), class={cls}" + (", likely settings-fixable" if fixable else "")
        score = 10.0 + count * (2.0 if fixable else 1.0)
        out.append(_opportunity("friction", title, reason, command, score, {
            "id": row.get("id"),
            "signature": sig,
            "class": cls,
            "count": count,
            "configFixable": fixable,
            "categories": row.get("categories") or [],
        }))
    return out


def _feedback_dir() -> Path:
    override = os.environ.get("SKILL_FEEDBACK_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".agent-plus" / "skill-feedback").resolve()


def _feedback_opportunities(path: Path, since_days: int) -> list[dict]:
    out: list[dict] = []
    if not path.is_dir():
        return out
    now = _dt.datetime.now(_dt.timezone.utc)
    cutoff = now - _dt.timedelta(days=since_days) if since_days > 0 else now
    for jf in sorted(path.glob("*.jsonl")):
        ratings: list[int] = []
        outcomes: dict[str, int] = {}
        frictions: dict[str, int] = {}
        try:
            lines = jf.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = _parse_iso(rec.get("ts"))
            if ts is None or since_days == 0 or ts < cutoff:
                continue
            rating = rec.get("rating")
            if isinstance(rating, int) and 1 <= rating <= 5:
                ratings.append(rating)
            outcome = rec.get("outcome")
            if isinstance(outcome, str):
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            friction = rec.get("friction")
            if isinstance(friction, str) and friction.strip():
                key = friction.strip().lower()
                frictions[key] = frictions.get(key, 0) + 1
        if not ratings and not outcomes and not frictions:
            continue
        mean = round(sum(ratings) / len(ratings), 2) if ratings else None
        negative = sum(v for k, v in outcomes.items() if k not in {"success", "ok", "useful"})
        should_surface = (mean is not None and mean < 4.0) or negative or frictions
        if not should_surface:
            continue
        skill = jf.stem
        score = 8.0 + negative * 2.0 + len(frictions)
        if mean is not None:
            score += max(0.0, 5.0 - mean)
        out.append(_opportunity(
            "feedback",
            f"Improve `{skill}` based on feedback signals",
            f"mean rating={mean}, negative outcomes={negative}, friction labels={len(frictions)}",
            f"skill-plus feedback --skill {skill} --pretty",
            score,
            {
                "id": skill,
                "skill": skill,
                "meanRating": mean,
                "outcomes": outcomes,
                "frictions": frictions,
                "records": len(ratings) or sum(outcomes.values()),
            },
        ))
    return out


def _bin_path() -> Path:
    return (Path(__file__).resolve().parents[1] / "skill-plus").resolve()


def _run_scan(args, project: Path) -> dict:
    if not getattr(args, "run_scan", False):
        return {"ran": False}
    cmd = [
        sys.executable, str(_bin_path()), "scan",
        "--project", str(project),
        "--since-days", str(int(getattr(args, "since_days", 30) or 30)),
        "--max-sessions", str(int(getattr(args, "max_sessions", 50) or 50)),
        "--min-count", str(int(getattr(args, "min_count", 3) or 3)),
        "--min-sessions", str(int(getattr(args, "min_sessions", 2) or 2)),
    ]
    if getattr(args, "all_projects", False):
        cmd.append("--all-projects")
    if getattr(args, "accept_consent", False):
        cmd.append("--accept-consent")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": True, "ok": False, "error": type(exc).__name__, "message": str(exc)}
    parsed: dict | None = None
    try:
        obj = json.loads(res.stdout or "{}")
        if isinstance(obj, dict):
            parsed = obj
    except json.JSONDecodeError:
        parsed = None
    meta = {
        "ran": True,
        "ok": res.returncode == 0,
        "returnCode": res.returncode,
        "stderr": (res.stderr or "").strip()[:1000],
    }
    if parsed is not None:
        meta["summary"] = {
            "sessionsScanned": parsed.get("sessionsScanned"),
            "candidatesNew": parsed.get("candidatesNew"),
            "candidatesUpdated": parsed.get("candidatesUpdated"),
            "routineCandidates": parsed.get("routineCandidates"),
            "blocks": parsed.get("blocks"),
        }
        if parsed.get("ok") is False:
            meta["payload"] = parsed
    else:
        meta["stdoutTail"] = (res.stdout or "")[-1000:]
    return meta


def run(args, emit_fn) -> int:
    project = _resolve_project(args)
    limit = int(getattr(args, "limit", 10) or 10)
    if limit < 1:
        limit = 10
    since_days = int(getattr(args, "since_days", 30) or 30)

    scan_meta = _run_scan(args, project)
    if scan_meta.get("ran") and scan_meta.get("ok") is False:
        emit_fn({
            "ok": False,
            "project": str(project),
            "error": "scan_failed",
            "scan": scan_meta,
        })
        return int(scan_meta.get("returnCode") or 1)

    skill_rows = _read_jsonl(candidates_log_path())  # noqa: F821 -- injected
    cadence_rows = _read_jsonl(routine_candidates_log_path())  # noqa: F821 -- injected
    block_rows = _read_jsonl(blocks_log_path())  # noqa: F821 -- injected
    feedback_path = _feedback_dir()

    opportunities: list[dict] = []
    opportunities.extend(_skill_opportunities(project, skill_rows, since_days))
    opportunities.extend(_cadence_opportunities(cadence_rows, since_days))
    opportunities.extend(_block_opportunities(block_rows, since_days))
    opportunities.extend(_feedback_opportunities(feedback_path, since_days))
    opportunities.sort(key=lambda r: r.get("priorityScore", 0), reverse=True)
    shown = opportunities[:limit]

    by_kind: dict[str, int] = {}
    for row in opportunities:
        kind = str(row.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1

    payload = {
        "ok": True,
        "project": str(project),
        "generatedAt": _now_iso(),  # noqa: F821 -- injected
        "scan": scan_meta,
        "sources": {
            "candidates": str(candidates_log_path()),  # noqa: F821 -- injected
            "routineCandidates": str(routine_candidates_log_path()),  # noqa: F821
            "blocks": str(blocks_log_path()),  # noqa: F821 -- injected
            "feedback": str(feedback_path),
        },
        "summary": {
            "total": len(opportunities),
            "shown": len(shown),
            "byKind": dict(sorted(by_kind.items())),
            "topAction": shown[0]["command"] if shown else None,
        },
        "opportunities": shown,
    }
    if not shown:
        payload["note"] = "no opportunities yet -- run more sessions, then skill-plus opportunities --run-scan"
    emit_fn(payload)
    return 0

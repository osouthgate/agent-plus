"""skill-plus scan — v0 deterministic Bash-cluster mining of session JSONL.

Slice 3.2: implements the design's v0 clustering (first-3-tokens of command),
deny+allow lists, secret scrubbing, threshold filter, dedupe-by-id persistence,
and a last-scan watermark for incremental runs.

Helpers (project_state_root, candidates_log_path, last_scan_path,
session_files_for_project, has_consent_for, grant_consent_for, scrub_text,
_now_iso, _git_toplevel, claude_projects_root, _ensure_dir) are injected into
this module's namespace by the bin shell.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path

# ─── clustering policy ────────────────────────────────────────────────────────

_DENY_FIRST_TOKEN = {
    "git", "ls", "cat", "head", "tail", "grep", "find", "pwd", "cd", "echo",
    "which", "whoami", "clear", "mkdir", "rm", "cp", "mv", "chmod", "touch",
    "wc", "awk", "sed", "tr", "sort", "uniq", "xargs", "tee", "man", "printf",
    "node", "python", "python3", "pip", "pip3",
}

_ALLOW_TOKEN_SUBSTR = ("--service", "--env", "--project", "--deployment", "--region")


def _tokens(cmd: str) -> list[str]:
    return cmd.strip().split()


def _passes_filter(tokens: list[str]) -> bool:
    if not tokens:
        return False
    # allowlist bias — overrides denylist
    for t in tokens:
        if t.startswith("mcp__"):
            return True
        for sub in _ALLOW_TOKEN_SUBSTR:
            if sub in t:
                return True
    if tokens[0] in _DENY_FIRST_TOKEN:
        return False
    return True


def _cluster_key(tokens: list[str]) -> str:
    return " ".join(tokens[:3])


def _id_for(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ─── JSONL extraction ────────────────────────────────────────────────────────


def _walk_for_bash(obj, out: list[str], depth: int = 0) -> bool:
    """Append every Bash tool_use command found anywhere in obj. Returns True
    if the line had at least one recognizable structural shape (so we can flag
    truly alien lines as format_unsupported)."""
    recognized = False
    if depth > 8:
        return recognized
    if isinstance(obj, dict):
        # Direct shape: {"type":"tool_use","name":"Bash","input":{"command":...}}
        if obj.get("type") == "tool_use" and obj.get("name") == "Bash":
            recognized = True
            inp = obj.get("input")
            if isinstance(inp, dict):
                cmd = inp.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    out.append(cmd)
        # Top-level toolUseResult shape
        if obj.get("toolName") == "Bash" or obj.get("tool_name") == "Bash":
            recognized = True
            inp = obj.get("input") or obj.get("toolInput") or {}
            if isinstance(inp, dict):
                cmd = inp.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    out.append(cmd)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                if _walk_for_bash(v, out, depth + 1):
                    recognized = True
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                if _walk_for_bash(v, out, depth + 1):
                    recognized = True
    return recognized


def _line_has_known_envelope(obj) -> bool:
    """Cheap heuristic: any line with a `type` key or a `message`/`content`
    shape is a known envelope, even if no Bash inside."""
    if not isinstance(obj, dict):
        return False
    if "type" in obj or "message" in obj or "content" in obj or "toolUseResult" in obj:
        return True
    return False


# ─── persistence ─────────────────────────────────────────────────────────────


def _read_existing(log_path: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    if not log_path.exists():
        return by_id
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return by_id
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = rec.get("id")
        if isinstance(rid, str):
            by_id[rid] = rec
    return by_id


def _write_atomic(log_path: Path, records: list[dict]) -> None:
    _ensure_dir(log_path)
    tmp = log_path.with_suffix(log_path.suffix + ".tmp")
    body = "\n".join(json.dumps(r, default=str) for r in records)
    if body:
        body += "\n"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, log_path)


# ─── last-scan watermark ─────────────────────────────────────────────────────


def _read_last_scan() -> _dt.datetime | None:
    p = last_scan_path()
    if not p.exists():
        return None
    try:
        s = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _write_last_scan(when: _dt.datetime) -> None:
    p = last_scan_path()
    _ensure_dir(p)
    p.write_text(when.strftime("%Y-%m-%dT%H:%M:%SZ"), encoding="utf-8")


# ─── main entry ──────────────────────────────────────────────────────────────


def run(args, emit_fn) -> int:
    # 1. Resolve project path
    if args.project:
        project_path = Path(args.project).expanduser().resolve()
    else:
        top = _git_toplevel()
        project_path = (top if top is not None else Path.cwd()).resolve()

    # 2. Consent gate
    if not has_consent_for(project_path):
        if not getattr(args, "accept_consent", False):
            emit_fn({
                "ok": False,
                "error": "consent_required",
                "needsConsentFor": str(project_path),
                "hint": "re-run with --accept-consent or run interactively",
            })
            return 2
        grant_consent_for(project_path, source="scan-flag")

    # 3. List sessions
    if getattr(args, "all_projects", False):
        root = claude_projects_root()
        sessions = sorted(root.glob("*/*.jsonl")) if root.is_dir() else []
    else:
        sessions = session_files_for_project(project_path)

    now = _dt.datetime.now(_dt.timezone.utc)
    since_days = int(getattr(args, "since_days", 30))
    cutoff_window = now - _dt.timedelta(days=since_days)
    last_scan = _read_last_scan()
    # Default: respect both — mtime >= max(last_scan, now - since_days)
    cutoff = cutoff_window
    if last_scan is not None and last_scan > cutoff_window:
        cutoff = last_scan

    def _mtime(p: Path) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(p.stat().st_mtime, _dt.timezone.utc)

    filtered: list[Path] = []
    for s in sessions:
        try:
            mt = _mtime(s)
        except OSError:
            continue
        if mt >= cutoff:
            filtered.append(s)
    # newest first, cap
    filtered.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    max_sessions = int(getattr(args, "max_sessions", 50))
    filtered = filtered[:max_sessions]

    # 4. Parse + extract
    parse_errors = 0
    format_unsupported_lines = 0
    # cluster_key -> aggregator
    agg: dict[str, dict] = {}
    now_iso = _now_iso()

    for sess in filtered:
        sess_id = sess.stem
        try:
            with sess.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        parse_errors += 1
                        continue
                    found: list[str] = []
                    recognized = _walk_for_bash(obj, found)
                    if not recognized and not _line_has_known_envelope(obj):
                        format_unsupported_lines += 1
                        continue
                    for cmd in found:
                        scrubbed = scrub_text(cmd) or ""
                        toks = _tokens(scrubbed)
                        if not _passes_filter(toks):
                            continue
                        key = _cluster_key(toks)
                        if not key:
                            continue
                        slot = agg.setdefault(key, {
                            "key": key,
                            "count": 0,
                            "sessions": set(),
                            "examples": [],
                        })
                        slot["count"] += 1
                        slot["sessions"].add(sess_id)
                        if len(slot["examples"]) < 3 and scrubbed not in slot["examples"]:
                            slot["examples"].append(scrubbed)
        except OSError:
            continue

    # 6. Threshold
    min_count = int(getattr(args, "min_count", 3))
    min_sessions = int(getattr(args, "min_sessions", 2))
    surviving = [
        s for s in agg.values()
        if s["count"] >= min_count and len(s["sessions"]) >= min_sessions
    ]

    # 7. Persist with dedupe-by-id
    log_path = candidates_log_path()
    existing = _read_existing(log_path)
    new_count = 0
    updated_count = 0
    for slot in surviving:
        rid = _id_for(slot["key"])
        sess_list = sorted(slot["sessions"])
        if rid in existing:
            rec = existing[rid]
            rec["count"] = int(rec.get("count", 0)) + slot["count"]
            merged_sessions = set(rec.get("sessions") or []) | set(sess_list)
            rec["sessions"] = sorted(merged_sessions)
            rec["lastSeen"] = now_iso
            ex = list(rec.get("examples") or [])
            for e in slot["examples"]:
                if len(ex) >= 3:
                    break
                if e not in ex:
                    ex.append(e)
            rec["examples"] = ex
            rec["scannedAt"] = now_iso
            updated_count += 1
        else:
            existing[rid] = {
                "id": rid,
                "key": slot["key"],
                "count": slot["count"],
                "sessions": sess_list,
                "examples": slot["examples"][:3],
                "firstSeen": now_iso,
                "lastSeen": now_iso,
                "scannedAt": now_iso,
                "sourceProject": str(project_path),
            }
            new_count += 1

    # rewrite file atomically — sorted by count desc for stable readability
    all_records = sorted(existing.values(), key=lambda r: -int(r.get("count", 0)))
    _write_atomic(log_path, all_records)

    # 9. Watermark
    _write_last_scan(now)

    # 10. Emit envelope (top 10 candidates by count from this scan's surviving)
    top = sorted(
        (existing[_id_for(s["key"])] for s in surviving),
        key=lambda r: -int(r.get("count", 0)),
    )[:10]

    payload = {
        "project": str(project_path),
        "sessionsScanned": len(filtered),
        "parseErrors": parse_errors,
        "formatUnsupportedLines": format_unsupported_lines,
        "candidatesNew": new_count,
        "candidatesUpdated": updated_count,
        "candidatesTotal": len(existing),
        "candidates": top,
    }
    emit_fn(payload)
    return 0

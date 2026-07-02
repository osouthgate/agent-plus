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


def _scrub_value(v):
    """Recursively scrub every string found in v (dict/list/str/other)."""
    if isinstance(v, str):
        return scrub_text(v)
    if isinstance(v, list):
        return [_scrub_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _scrub_value(x) for k, x in v.items()}
    return v


def _scrub_record(rec: dict) -> dict:
    """Rescrub every text field of a persisted candidate record. Applied to
    EVERY record on EVERY rewrite (not just newly-added examples) so a
    secret that leaked into candidates.jsonl before a redaction-pattern gap
    was fixed doesn't survive forever just because that record wasn't
    touched by the current scan."""
    return {k: _scrub_value(v) for k, v in rec.items()}


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

    def _mtime_or_none(p: Path) -> _dt.datetime | None:
        try:
            return _mtime(p)
        except OSError:
            return None

    filtered: list[Path] = []
    for s in sessions:
        mt = _mtime_or_none(s)
        if mt is not None and mt >= cutoff:
            filtered.append(s)
    # newest first, cap
    filtered.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    after_cutoff_count = len(filtered)
    max_sessions = int(getattr(args, "max_sessions", 50))
    filtered = filtered[:max_sessions]

    # Self-diagnosis, always computed (v0.19.7 hotfix): scoped to
    # project_path regardless of --all-projects, so "0 sessions" always
    # answers "does my resolved project even have a session directory" --
    # independent of whatever --all-projects widened `sessions` to.
    own_slug = encoded_cwd_for(project_path)
    own_dir = claude_projects_root() / own_slug
    own_raw_sessions = session_files_for_project(project_path)
    own_after_cutoff = 0
    for s in own_raw_sessions:
        mt = _mtime_or_none(s)
        if mt is not None and mt >= cutoff:
            own_after_cutoff += 1
    diagnostics = {
        "slug": own_slug,
        "projectDir": str(own_dir),
        "projectDirExists": own_dir.is_dir(),
        "rawSessionFiles": len(own_raw_sessions),
        "filteredByCutoff": len(own_raw_sessions) - own_after_cutoff,
    }

    # zeroReason explains *why* sessionsScanned (== len(filtered)) came out
    # to 0, for the actual scan that ran (mode-aware: respects
    # --all-projects, unlike `diagnostics` above). Order matters: no raw
    # files at all outranks "all older than cutoff", which outranks "cutoff
    # passed but the --max-sessions cap zeroed it out".
    zero_reason: str | None = None
    if not filtered:
        if not sessions:
            zero_reason = "project_dir_missing"
        elif after_cutoff_count == 0:
            zero_reason = "all_before_cutoff"
        else:
            zero_reason = "filtered_by_caps"

    # Canary hint: 0 sessions for this slug, but ~/.claude/projects/ has
    # other project dirs with history -- likely means project_path resolved
    # to the wrong slug (e.g. cwd/git-toplevel mismatch) rather than "no
    # Claude Code history exists yet". ASCII only.
    hint: str | None = None
    if not filtered:
        other_projects = 0
        proj_root = claude_projects_root()
        if proj_root.is_dir():
            other_projects = sum(
                1 for d in proj_root.iterdir()
                if d.is_dir() and d.name != own_slug
            )
        if other_projects > 0:
            hint = (
                f"0 sessions for slug {own_slug} but {other_projects} other "
                f"projects have history - possible slug mismatch; use "
                f"--since-days N for backfill"
            )

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

    # Rescrub every persisted record's text fields on every rewrite -- not
    # just this scan's new/updated examples. An old record this scan never
    # touched (didn't match this run's clusters) still gets rewritten
    # verbatim below, so if it was written before a redaction-pattern gap
    # was fixed, a leaked secret would otherwise survive forever. Mutates
    # `existing` in place so `top` (built from `existing` right after) also
    # reflects the scrubbed values, not just what lands on disk.
    for rid in list(existing.keys()):
        existing[rid] = _scrub_record(existing[rid])

    # rewrite file atomically — sorted by count desc for stable readability
    all_records = sorted(existing.values(), key=lambda r: -int(r.get("count", 0)))
    _write_atomic(log_path, all_records)

    # 9. Watermark -- only advance when sessions were actually parsed this
    # run. Advancing on a 0-session run (e.g. slug mismatch, or every
    # session older than the cutoff) would silently burn the scan window:
    # the next scan's cutoff becomes "now", so anything before it is never
    # revisited even after the underlying problem (e.g. the slug) is fixed.
    # See zeroReason for why sessionsScanned came out to 0.
    if filtered:
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
        "zeroReason": zero_reason,
        "diagnostics": diagnostics,
    }
    if hint:
        payload["hint"] = hint
    emit_fn(payload)
    return 0

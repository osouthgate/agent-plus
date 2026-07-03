"""skill-plus scan — v0 deterministic Bash-cluster mining of session JSONL.

Slice 3.2: implements the design's v0 clustering (first-3-tokens of command),
deny+allow lists, secret scrubbing, threshold filter, dedupe-by-id persistence,
and a last-scan watermark for incremental runs.

Temporal pass (v1 extension): a SEPARATE routine aggregator accumulates per-
occurrence (date, hour, weekday, cwd) data for each cluster, using FULL history
within the since-days window (ignores the last_scan watermark). After
aggregation, clusters with enough distinct dates are classified as routine
(tight cadence) or habit (diffuse).

Friction pass (v1 extension): a THIRD aggregator joins every blocked
`tool_result` to the `tool_use` that triggered it (by tool_use_id), classifies
why it was blocked (auto-mode classifier veto, other permission denial, user-
rejected prompt), scrubs, and persists to a separate blocks.jsonl. Like the
temporal pass, it runs over the FULL since-days window regardless of the
last_scan watermark -- blocks are sparse, so a watermark-gated window would
under-report friction.

Helpers (project_state_root, candidates_log_path, last_scan_path,
session_files_for_project, has_consent_for, grant_consent_for, scrub_text,
_now_iso, _git_toplevel, claude_projects_root, _ensure_dir, encoded_cwd_for,
routine_candidates_log_path, routines_adopted_log_path, blocks_log_path) are
injected into this module's namespace by the bin shell.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path

# ─── temporal aggregator constants ───────────────────────────────────────────

MIN_DISTINCT_DATES: int = 5
HOUR_BUCKET_HOURS: int = 3
HOURBUCKET_SHARE_MIN: float = 0.50
WEEKDAYCLASS_SHARE_MIN: float = 0.70

_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

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


# ─── temporal helpers ────────────────────────────────────────────────────────


def _parse_ts(ts_raw) -> "_dt.datetime | None":
    """Parse ISO-8601 timestamp from the envelope. Returns None on any failure."""
    if not isinstance(ts_raw, str):
        return None
    s = ts_raw.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _classify_weekday(occurrences: list[dict]) -> tuple[str, float]:
    """Return (weekdayClass, weekdayClassShare) for a list of occurrence dicts.

    Each occurrence has a 'weekday' key (0=Mon..6=Sun).
    Single dominant DOW (>=70%) -> ('Mon'..'Sun', share).
    Otherwise dominant class (weekday Mon-Fri vs weekend Sat-Sun).
    weekdayClassShare is the share of the dominant classification.
    """
    if not occurrences:
        return "weekday", 0.0
    total = len(occurrences)
    dow_counts: dict[int, int] = {}
    for occ in occurrences:
        d = occ.get("weekday")
        if isinstance(d, int):
            dow_counts[d] = dow_counts.get(d, 0) + 1

    # Check single dominant DOW
    best_dow = max(dow_counts, key=lambda k: dow_counts[k]) if dow_counts else 0
    best_dow_share = dow_counts.get(best_dow, 0) / total
    if best_dow_share >= WEEKDAYCLASS_SHARE_MIN:
        return _DOW_NAMES[best_dow], best_dow_share

    # Weekday vs weekend
    weekday_count = sum(v for k, v in dow_counts.items() if k < 5)
    weekend_count = sum(v for k, v in dow_counts.items() if k >= 5)
    if weekday_count >= weekend_count:
        return "weekday", weekday_count / total
    return "weekend", weekend_count / total


def _schedule_string(regularity: dict, examples: list[str]) -> str:
    """Build the deterministic scheduleString. ASCII-only. No LLM."""
    wc = regularity.get("weekdayClass", "weekday")
    bucket = int(regularity.get("hourBucket", 0))
    hh = bucket * HOUR_BUCKET_HOURS
    if hh < 12:
        part = "morning"
    elif hh < 18:
        part = "afternoon"
    else:
        part = "evening"

    if wc == "weekday":
        cadence = f"On weekday {part}s around {hh:02d}:00 UTC"
    elif wc == "weekend":
        cadence = f"On weekends around {hh:02d}:00 UTC"
    else:
        # single DOW name like "Mon", "Tue" etc.
        cadence = f"Every {wc} around {hh:02d}:00 UTC"

    example = (examples[0] if examples else "")[:80]
    return (
        f"{cadence}, run the workflow that does `{example}`"
        f" (refine this intent before saving)"
    )


def _build_routine_record(
    key: str,
    occurrences: list[dict],
    examples: list[str],
    now_iso: str,
    project_path: "Path",
    min_distinct_dates: int,
    hour_bucket_hours: int,
    hourbucket_share_min: float,
    weekdayclass_share_min: float,
) -> "dict | None":
    """Classify a cluster and return a routine-candidates record or None."""
    distinct_dates = len({occ["date"] for occ in occurrences if "date" in occ})
    if distinct_dates < min_distinct_dates:
        return None

    total = len(occurrences)
    # Hour bucket distribution
    bucket_counts: dict[int, int] = {}
    for occ in occurrences:
        h = occ.get("hour")
        if isinstance(h, int):
            b = h // hour_bucket_hours
            bucket_counts[b] = bucket_counts.get(b, 0) + 1

    best_bucket = max(bucket_counts, key=lambda k: bucket_counts[k]) if bucket_counts else 0
    best_bucket_count = bucket_counts.get(best_bucket, 0)
    hourbucket_share = best_bucket_count / total if total else 0.0

    weekday_class, weekday_class_share = _classify_weekday(occurrences)

    hh_start = best_bucket * hour_bucket_hours
    hh_end = hh_start + hour_bucket_hours - 1
    hour_bucket_utc = f"{hh_start:02d}:00-{hh_end:02d}:59"

    regularity = {
        "weekdayClass": weekday_class,
        "weekdayClassShare": round(weekday_class_share, 4),
        "hourBucket": best_bucket,
        "hourBucketUtc": hour_bucket_utc,
        "hourBucketShare": round(hourbucket_share, 4),
    }

    is_routine = (
        hourbucket_share >= hourbucket_share_min
        and weekday_class_share >= weekdayclass_share_min
    )

    sessions_set = {occ.get("sessionId") for occ in occurrences if occ.get("sessionId")}

    first_seen = min(
        (occ["date"] for occ in occurrences if "date" in occ),
        default=now_iso[:10],
    )
    last_seen = max(
        (occ["date"] for occ in occurrences if "date" in occ),
        default=now_iso[:10],
    )

    rid = _id_for(key)
    record: dict = {
        "id": rid,
        "kind": "routine" if is_routine else "habit",
        "key": key,
        "count": total,
        "distinctDates": distinct_dates,
        "regularity": regularity,
        "examples": examples[:3],
        "sessions": sorted(s for s in sessions_set if s),
        "scheduleString": _schedule_string(regularity, examples) if is_routine else None,
        "suggestion": (
            None if is_routine
            else (
                f"recurs on {distinct_dates} dates with no consistent time;"
                f" not a routine -- run `skill-plus propose` to consider a skill"
            )
        ),
        "firstSeen": first_seen + "T00:00:00Z",
        "lastSeen": last_seen + "T00:00:00Z",
        "scannedAt": now_iso,
        "sourceProject": str(project_path),
    }
    return record


def _read_adopted_state(adopted_path: "Path") -> "tuple[dict[str, str], dict[str, int]]":
    """Read routines-adopted.jsonl. Returns ({id: last_status}, {id: dismiss_count})."""
    id_status: dict[str, str] = {}
    id_ts: dict[str, str] = {}
    dismiss_count: dict[str, int] = {}

    if not adopted_path.exists():
        return id_status, dismiss_count

    try:
        text = adopted_path.read_text(encoding="utf-8")
    except OSError:
        return id_status, dismiss_count

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = rec.get("id")
        status = rec.get("status", "")
        ts = rec.get("ts", "")
        if not isinstance(rid, str):
            continue
        # last-write-wins: keep only the most recent status
        if rid not in id_ts or ts > id_ts[rid]:
            id_ts[rid] = ts
            id_status[rid] = status
        # count all dismissed entries (historical tally)
        if status == "dismissed":
            dismiss_count[rid] = dismiss_count.get(rid, 0) + 1

    return id_status, dismiss_count


# ─── friction lens (tool-use permission blocks) ──────────────────────────────
# A third signal on the same JSONL pass: join every blocked `tool_result` to
# the `tool_use` that triggered it (by tool_use_id), classify why it was
# blocked, scrub, and persist to a SEPARATE blocks.jsonl (composite id, no
# collision with the frequency/routine SHA1 namespace). The analytical spine
# (classifier-vs-allowlist, tier-1/2/3 safe list) lives in the audit-tool-blocks
# SKILL.md that consumes this -- NOT here. scan only emits scrubbed evidence.

_AUTO_SIG = "denied by the Claude Code auto mode classifier"
_PERM_SIG = "Permission for this action was denied"
_REJECT_SIGS = (
    "The user doesn't want to proceed", "User rejected",
    "user doesn't want to take this action",
    "requested permissions", "haven't granted it yet",
)
# Locked decision: anchored known-category allowlist, NOT a greedy paren scrape
# (the standalone script's `\(([^)]{3,60})\)` pulled JSON keys / source
# fragments -- txt, inp, msg.get("content" -- into fake categories).
_KNOWN_CATEGORIES = (
    "External System Writes", "Git Destructive", "Self-Modification",
    "Production Reads", "Credential Exploration", "Blind Apply", "PII",
)
_REASON_RE = re.compile(r"Reason:\s*(.*?)(?:\.\s|$)", re.S)
_CMDHEAD_SUB = {"git", "gh", "npm", "pnpm", "docker", "npx", "node", "python3"}


def _classify_block(text: str) -> "str | None":
    if _AUTO_SIG in text:
        return "AUTO_MODE_CLASSIFIER"
    if _PERM_SIG in text:
        return "PERMISSION_DENIED_OTHER"
    if any(s in text for s in _REJECT_SIGS):
        return "USER_REJECTED_PROMPT"
    return None


def _walk_collect_uses(obj, uses: dict, ts, depth: int = 0) -> None:
    """Record uses[tool_use_id] = (tool_name, input, ts) for EVERY tool_use
    (all tools, not just Bash). Robust recursive walk like _walk_for_bash."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        if obj.get("type") == "tool_use" and obj.get("id"):
            uses[obj.get("id")] = (
                obj.get("name") or "(unknown-tool)", obj.get("input"), ts,
            )
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk_collect_uses(v, uses, ts, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                _walk_collect_uses(v, uses, ts, depth + 1)


def _walk_collect_results(obj, out: list, ts, depth: int = 0) -> None:
    """Append (tool_use_id, flattened_text, line_ts) for every tool_result."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        if obj.get("type") == "tool_result":
            c = obj.get("content")
            if isinstance(c, list):
                txt = " ".join(
                    x.get("text", "") if isinstance(x, dict) else str(x)
                    for x in c
                )
            elif isinstance(c, str):
                txt = c
            else:
                txt = ""
            out.append((obj.get("tool_use_id"), txt, ts))
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk_collect_results(v, out, ts, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                _walk_collect_results(v, out, ts, depth + 1)


def _cmd_head(inp) -> str:
    """First command token (+ subcommand for an allowlisted set). Port of the
    standalone audit_tool_blocks.cmd_head. Coarse by design -- v1 also persists
    signatureTokens for the v1.1 join."""
    if not isinstance(inp, dict):
        return "?"
    c = inp.get("command")
    if isinstance(c, str) and c.strip():
        toks = c.strip().split()
        head = toks[0]
        if head in ("sudo", "env") and len(toks) > 1:
            head = toks[1]
        sub = ""
        if head in _CMDHEAD_SUB and len(toks) > 1 and not toks[1].startswith("-"):
            sub = toks[1]
        return (head + (" " + sub if sub else "")).strip()
    return "(no-command)"


def _signature_tokens(inp) -> list:
    """Scrubbed first 5 command tokens -- join-grade fidelity for the v1.1
    frictionBoost probe (cmd_head is head-only outside the allowlist)."""
    if not isinstance(inp, dict):
        return []
    c = inp.get("command")
    if isinstance(c, str) and c.strip():
        scrubbed = scrub_text(c) or ""
        return scrubbed.strip().split()[:5]
    return []


def _extract_categories(text: str) -> list:
    """Anchored: only known categories (case-insensitive substring). AUTO
    blocks with no known category -> ['uncategorized'] (NOT a fake category)."""
    low = text.lower()
    cats = [c for c in _KNOWN_CATEGORIES if c.lower() in low]
    return cats or ["uncategorized"]


def _extract_reason(text: str) -> str:
    m = _REASON_RE.search(text)
    raw = m.group(1).strip() if m else text.strip()
    return (scrub_text(raw) or "")[:240]


def _blocks_id(tool: str, signature: str, cls: str) -> str:
    return hashlib.sha1(f"{tool}|{signature}|{cls}".encode("utf-8")).hexdigest()[:12]


def _resolve_session_blocks(
    sess_uses: dict, sess_results: list, sess_id: str, project: str,
    blocks_agg: dict, now_iso: str,
) -> None:
    """Join each blocked tool_result to its tool_use; accumulate into
    blocks_agg keyed by (tool, signature, class). Every persisted string is
    scrubbed here."""
    for tid, text, rts in sess_results:
        if not isinstance(text, str) or not text:
            continue
        cls = _classify_block(text)
        if cls is None:
            continue
        name, inp, uts = sess_uses.get(tid, (None, None, None))
        tool = name or "(unknown-tool)"
        if tool in ("Bash", "PowerShell"):
            raw_sig = f"{tool}: " + _cmd_head(inp)
            signature = scrub_text(raw_sig) or raw_sig
            sig_tokens = _signature_tokens(inp)
            head = _cmd_head(inp).split()[0] if _cmd_head(inp) else ""
            is_cd = head == "cd"
        else:
            signature = tool  # never echo non-Bash tool inputs (payloads/secrets)
            sig_tokens = [tool]
            is_cd = False
        ts = uts or rts
        mon = (ts or now_iso)[:7]
        cats = _extract_categories(text) if cls == "AUTO_MODE_CLASSIFIER" else []
        reason = _extract_reason(text)

        bid = _blocks_id(tool, signature, cls)
        slot = blocks_agg.setdefault(bid, {
            "id": bid, "tool": tool, "class": cls, "signature": signature,
            "signatureTokens": sig_tokens, "count": 0,
            "categories": set(), "sampleReasons": [],
            "projects": set(), "sessions": set(), "byMonth": {},
            # Spine encoded in data: AUTO is never allowlist-fixable; only
            # USER_REJECTED is. The cd-compound bucket additionally carries
            # the proven-ineffective-habit-note flag (hand-probe finding).
            "configFixable": cls == "USER_REJECTED_PROMPT",
            "habitNoteProvenIneffective": is_cd,
        })
        slot["count"] += 1
        for cat in cats:
            slot["categories"].add(cat)
        if reason and len(slot["sampleReasons"]) < 5 and reason not in slot["sampleReasons"]:
            slot["sampleReasons"].append(reason)
        slot["projects"].add(project)
        slot["sessions"].add(sess_id)
        slot["byMonth"][mon] = slot["byMonth"].get(mon, 0) + 1
        slot["habitNoteProvenIneffective"] = slot["habitNoteProvenIneffective"] or is_cd


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

    # Temporal pass needs the FULL since-days window regardless of the
    # last-scan watermark (cadence detection looks at all history within
    # the window, not just what's new since the last run). `cutoff` is
    # always >= `cutoff_window` (it's the max of the two), so every
    # freq-eligible session (mtime >= cutoff) is also within this wider
    # window and always sorts ahead of the rest -- freq_eligible_set below
    # reproduces `filtered`'s membership exactly, so the frequency-path
    # counters gated by it (parse_errors, formatUnsupportedLines, agg) are
    # unaffected by widening the loop to all_window.
    all_window: list[Path] = []
    for s in sessions:
        mt = _mtime_or_none(s)
        if mt is not None and mt >= cutoff_window:
            all_window.append(s)
    all_window.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    all_window = all_window[:max_sessions]
    freq_eligible_set = set(filtered)

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
    # Claude Code history exists yet". "With history" is checked directly
    # (at least one *.jsonl present) -- a sibling dir that exists but is
    # empty isn't evidence of a slug mismatch. ASCII only.
    hint: str | None = None
    if not filtered:
        other_projects = 0
        proj_root = claude_projects_root()
        if proj_root.is_dir():
            other_projects = sum(
                1 for d in proj_root.iterdir()
                if d.is_dir() and d.name != own_slug
                and next(d.glob("*.jsonl"), None) is not None
            )
        if other_projects > 0:
            hint = (
                f"0 sessions for slug {own_slug} but {other_projects} other "
                f"projects have history - possible slug mismatch; use "
                f"--since-days N for backfill"
            )

    # 4. Parse + extract (single pass over all_window; frequency-path side
    # effects -- parse_errors, formatUnsupportedLines, agg -- are gated by
    # eligible_for_freq so they only count freq_eligible_set members, i.e.
    # exactly what the old filtered-only loop counted).
    parse_errors = 0
    format_unsupported_lines = 0
    # cluster_key -> frequency aggregator
    agg: dict[str, dict] = {}
    # cluster_key -> list of temporal occurrence dicts (full window, no watermark)
    temporal_agg: dict[str, list] = {}
    # cluster_key -> {"examples": [...], "sessions": set()} (full window)
    temporal_meta: dict[str, dict] = {}
    # friction lens: (tool|signature|class) id -> aggregated block record.
    # Full-window pass like the temporal lens (NOT freq-watermark-gated):
    # blocks are sparse, a truncated window would under-report friction.
    blocks_agg: dict[str, dict] = {}
    now_iso = _now_iso()

    # Temporal thresholds -- support test-only overrides via hidden args.
    min_distinct_dates = int(getattr(args, "_min_dates", None) or MIN_DISTINCT_DATES)
    hour_bucket_hours = int(getattr(args, "_hour_bucket_hours", None) or HOUR_BUCKET_HOURS)
    hourbucket_share_min = float(getattr(args, "_hourbucket_share_min", None) or HOURBUCKET_SHARE_MIN)
    weekdayclass_share_min = float(getattr(args, "_weekdayclass_share_min", None) or WEEKDAYCLASS_SHARE_MIN)

    for sess in all_window:
        sess_id = sess.stem
        eligible_for_freq = sess in freq_eligible_set
        # Friction join state, per session. tool_use lines precede their
        # tool_result lines in the JSONL, so a single forward pass populates
        # sess_uses before sess_results is resolved at end-of-file.
        sess_uses: dict = {}
        sess_results: list = []
        try:
            with sess.open("r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        if eligible_for_freq:
                            parse_errors += 1
                        continue
                    # Extract timestamp + cwd from the envelope BEFORE
                    # walking for bash (temporal path needs both).
                    ts_raw = obj.get("timestamp") if isinstance(obj, dict) else None
                    ts_dt = _parse_ts(ts_raw)
                    cwd_raw = obj.get("cwd") if isinstance(obj, dict) else None

                    # Friction collection runs BEFORE the bash-path early
                    # continue so tool_result lines (no Bash) are never skipped.
                    _walk_collect_uses(obj, sess_uses, ts_raw)
                    _walk_collect_results(obj, sess_results, ts_raw)

                    found: list[str] = []
                    recognized = _walk_for_bash(obj, found)
                    if not recognized and not _line_has_known_envelope(obj):
                        if eligible_for_freq:
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

                        # Frequency path (incremental, watermark-gated)
                        if eligible_for_freq:
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

                        # Temporal path (full window, no watermark gate)
                        if ts_dt is not None:
                            date_str = ts_dt.strftime("%Y-%m-%d")
                            occ = {
                                "date": date_str,
                                "hour": ts_dt.hour,
                                "weekday": ts_dt.weekday(),  # 0=Mon..6=Sun
                                "cwd": str(cwd_raw) if cwd_raw else None,
                                "sessionId": sess_id,
                            }
                            temporal_agg.setdefault(key, []).append(occ)

                        # Temporal metadata (examples + sessions, full window)
                        tmeta = temporal_meta.setdefault(key, {"examples": [], "sessions": set()})
                        tmeta["sessions"].add(sess_id)
                        if len(tmeta["examples"]) < 3 and scrubbed not in tmeta["examples"]:
                            tmeta["examples"].append(scrubbed)
                # End of per-line loop: resolve this session's blocks once all
                # tool_use ids are known (forward pass; uses precede results).
                _resolve_session_blocks(
                    sess_uses, sess_results, sess_id, str(project_path),
                    blocks_agg, now_iso,
                )
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

    # 8. Temporal pass: classify and persist routine/habit candidates.
    # Independent of the frequency path above -- runs every scan, full
    # window, no watermark gate (see module docstring).
    routine_log_path = routine_candidates_log_path()
    existing_routine = _read_existing(routine_log_path)

    # Read adopted/dismissed state for suppression
    adopted_path = routines_adopted_log_path()
    id_status, dismiss_count_map = _read_adopted_state(adopted_path)

    routine_new = 0
    routine_updated = 0
    routine_records_this_run: dict[str, dict] = {}

    for key, occurrences in temporal_agg.items():
        tmeta = temporal_meta.get(key, {})
        examples = tmeta.get("examples") or []
        record = _build_routine_record(
            key=key,
            occurrences=occurrences,
            examples=examples,
            now_iso=now_iso,
            project_path=project_path,
            min_distinct_dates=min_distinct_dates,
            hour_bucket_hours=hour_bucket_hours,
            hourbucket_share_min=hourbucket_share_min,
            weekdayclass_share_min=weekdayclass_share_min,
        )
        if record is None:
            continue
        rid = record["id"]

        # Suppression: adopted candidates are completely skipped
        if id_status.get(rid) == "adopted":
            continue

        routine_records_this_run[rid] = record

        if rid in existing_routine:
            # Update in place — full recompute from temporal_agg replaces prior record
            existing_routine[rid] = record
            routine_updated += 1
        else:
            existing_routine[rid] = record
            routine_new += 1

    # Remove adopted records from the persisted log entirely
    for rid in list(existing_routine.keys()):
        if id_status.get(rid) == "adopted":
            del existing_routine[rid]

    # Apply dismissed annotation to dismissed records still in existing_routine
    # (records computed this run + records not seen this run but previously stored)
    for rid, rec in existing_routine.items():
        if id_status.get(rid) == "dismissed":
            dc = dismiss_count_map.get(rid, 1)
            rec["dismissed"] = dc

    # Rescrub every persisted routine/habit record's text fields on every
    # rewrite -- same rationale as the candidates.jsonl rescrub above (see
    # _scrub_record's docstring). A record whose cluster key didn't recur in
    # this run's temporal_agg (outside the since-days window this run, or a
    # dismissed record retained purely for its annotation) is loaded straight
    # off disk by _read_existing() and is never touched by the classify loop
    # above, so without this pass a secret written before a redaction-pattern
    # gap was fixed would survive in routine-candidates.jsonl forever even
    # after the gap is closed.
    for rid in list(existing_routine.keys()):
        existing_routine[rid] = _scrub_record(existing_routine[rid])

    # Sort: non-dismissed by count desc, then dismissed by count desc
    def _routine_sort_key(r: dict) -> tuple:
        is_dismissed = 1 if "dismissed" in r else 0
        return (is_dismissed, -int(r.get("count", 0)))

    all_routine_records = sorted(existing_routine.values(), key=_routine_sort_key)
    _write_atomic(routine_log_path, all_routine_records)

    # 8b. Friction pass: persist blocks.jsonl. Separate composite-id namespace
    # (tool|signature|class) -- no collision with frequency/routine SHA1(key).
    blocks_path = blocks_log_path()
    existing_blocks = _read_existing(blocks_path)
    blocks_new = 0
    blocks_updated = 0
    by_class_run: dict[str, int] = {}
    blocks_this_run: dict[str, dict] = {}
    for bid, slot in blocks_agg.items():
        by_class_run[slot["class"]] = (
            by_class_run.get(slot["class"], 0) + slot["count"]
        )
        rec_out = {
            "id": bid,
            "tool": slot["tool"],
            "class": slot["class"],
            "signature": slot["signature"],
            "signatureTokens": list(slot["signatureTokens"]),
            "count": slot["count"],
            "categories": sorted(slot["categories"]),
            "sampleReasons": list(slot["sampleReasons"]),
            "projects": sorted(slot["projects"]),
            "sessions": sorted(slot["sessions"]),
            "byMonth": dict(sorted(slot["byMonth"].items())),
            "configFixable": slot["configFixable"],
            "habitNoteProvenIneffective": slot["habitNoteProvenIneffective"],
        }
        # REPLACE-seen + PRESERVE-unseen, mirroring the routine lens
        # (existing_routine above). The friction pass is full-window (not
        # watermark-gated), so this run's agg already holds the complete
        # window count -- adding it to the prior count would double-count
        # every re-read session. Records NOT seen this run are left in
        # existing_blocks untouched (history outside the current window).
        if bid in existing_blocks:
            prev = existing_blocks[bid]
            rec_out["firstSeen"] = prev.get("firstSeen") or now_iso
            rec_out["lastSeen"] = now_iso
            rec_out["scannedAt"] = now_iso
            blocks_updated += 1
        else:
            rec_out["firstSeen"] = now_iso
            rec_out["lastSeen"] = now_iso
            rec_out["scannedAt"] = now_iso
            blocks_new += 1
        existing_blocks[bid] = rec_out
        blocks_this_run[bid] = rec_out

    # Rescrub every persisted block record's text fields on every rewrite --
    # same rationale as the candidates.jsonl / routine-candidates.jsonl
    # rescrub above (see _scrub_record's docstring). A block record whose id
    # didn't recur in this run's blocks_agg is loaded straight off disk by
    # _read_existing() and is never touched by the aggregation loop above, so
    # without this pass a secret written before a redaction-pattern gap was
    # fixed would survive in blocks.jsonl forever even after the gap is
    # closed. (The friction lens's original design predates the rescrub
    # hardening added for candidates.jsonl / routine-candidates.jsonl --
    # brought to parity here for the same reason.)
    for bid in list(existing_blocks.keys()):
        existing_blocks[bid] = _scrub_record(existing_blocks[bid])

    all_block_records = sorted(
        existing_blocks.values(), key=lambda r: -int(r.get("count", 0))
    )
    _write_atomic(blocks_path, all_block_records)

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

    # Routine summary: non-adopted records from this run (dismissed sorted last)
    routine_top = sorted(
        routine_records_this_run.values(),
        key=_routine_sort_key,
    )[:10]

    by_kind: dict[str, int] = {}
    for r in routine_records_this_run.values():
        k = str(r.get("kind", "unknown"))
        by_kind[k] = by_kind.get(k, 0) + 1

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
        "routineCandidates": {
            "new": routine_new,
            "updated": routine_updated,
            "total": len(existing_routine),
            "byKind": by_kind,
        },
        "routineCandidatesTop": routine_top,
        "blocks": {
            "new": blocks_new,
            "updated": blocks_updated,
            "total": len(existing_blocks),
            "byClass": by_class_run,
        },
        "blocksTop": sorted(
            blocks_this_run.values(), key=lambda r: -int(r.get("count", 0))
        )[:10],
    }
    if hint:
        payload["hint"] = hint
    emit_fn(payload)
    return 0

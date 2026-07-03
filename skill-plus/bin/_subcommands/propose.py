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


_REACHABILITY_NOTE = (
    "Routines require Pro/Max/Team/Enterprise + Claude Code on"
    " the web; cron minimum interval 1h. This detector runs offline regardless."
)


def _run_routine_kind(args, emit_fn) -> int:
    """Handle --kind routine|habit|all paths. Reads routine-candidates.jsonl."""
    project = _resolve_project(args)
    kind = getattr(args, "kind", "routine")
    limit = int(getattr(args, "limit", 10) or 10)
    if limit < 1:
        limit = 10

    log_path = routine_candidates_log_path()  # noqa: F821 — injected
    rows = _read_candidates(log_path)

    if not rows:
        emit_fn({
            "project": str(project),
            "kind": kind,
            "candidates": [],
            "note": "no routine/habit candidates yet -- run skill-plus scan first",
        })
        return 0

    # Filter by kind
    if kind == "all":
        accepted_kinds = {"routine", "habit"}
    else:
        accepted_kinds = {kind}

    filtered = [r for r in rows if r.get("kind") in accepted_kinds]

    # Sort: dismissed last, then by count desc
    filtered.sort(key=lambda r: (1 if r.get("dismissed") else 0, -int(r.get("count", 0) or 0)))

    shown = filtered[:limit]

    # reachabilityNote present iff any routine record is in the result
    has_routine = any(r.get("kind") == "routine" for r in shown)

    payload: dict = {
        "project": str(project),
        "kind": kind,
        "candidates": shown,
    }
    if has_routine:
        payload["reachabilityNote"] = _REACHABILITY_NOTE

    emit_fn(payload)
    return 0


_BASH_RULE_RE = re.compile(r"^(Bash|PowerShell)\((.+?)\)\s*$")
_BARE_TOOL_RE = re.compile(r"^[A-Za-z_]+$")


def _load_settings(no_settings: bool) -> dict:
    """Port of audit_tool_blocks.load_settings. Collects coverage facts from
    global + cwd-project settings (.json + .local.json). Computed at PROPOSE
    time against LIVE settings -- a deliberate improvement over the standalone
    script's scan-time computation: blocks.jsonl is historical, settings
    change, so coverage must be re-derived per call (no rescan, never stale)."""
    empty: dict = {"bash_heads": [], "mcp_allow": set(), "tool_allow": set(),
                   "hook_matchers": [], "automode_allow": [], "sources": []}
    if no_settings:
        return empty
    home = Path.home()
    cwd = Path.cwd()
    paths = [
        home / ".claude" / "settings.json",
        home / ".claude" / "settings.local.json",
        cwd / ".claude" / "settings.json",
        cwd / ".claude" / "settings.local.json",
    ]
    bash_heads: list[str] = []
    mcp_allow: set[str] = set()
    tool_allow: set[str] = set()
    hook_matchers: list[str] = []
    automode_allow: list[str] = []
    sources: list[str] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(s, dict):
            continue
        sources.append(str(p))
        for rule in ((s.get("permissions") or {}).get("allow") or []):
            if not isinstance(rule, str):
                continue
            m = _BASH_RULE_RE.match(rule)
            if m:
                spec = m.group(2)
                if spec.endswith(":*"):
                    spec = spec[:-2]
                bash_heads.append(spec.strip())
            elif rule.startswith("mcp__"):
                mcp_allow.add(rule.strip())
            elif _BARE_TOOL_RE.match(rule):
                tool_allow.add(rule.strip())
        for _ev, arr in ((s.get("hooks") or {}).items()):
            for entry in (arr or []):
                if isinstance(entry, dict):
                    mt = entry.get("matcher")
                    if isinstance(mt, str) and mt:
                        hook_matchers.append(mt)
        am = (s.get("autoMode") or {}).get("allow") or []
        automode_allow.extend([r for r in am if isinstance(r, str)])
    return {"bash_heads": bash_heads, "mcp_allow": mcp_allow,
            "tool_allow": tool_allow, "hook_matchers": hook_matchers,
            "automode_allow": automode_allow, "sources": sources}


def _coverage(tool: str, signature: str, S: dict) -> str:
    """Port of audit_tool_blocks.coverage. Returns
    not_covered|already_allowlisted|hook_managed for a block given settings S.
    signature is 'Bash: <head>' (same shape as the script's `head`). NOTE:
    cmd_head is coarse outside the git/gh/npm/... allowlist, so a multi-token
    rule like Bash(railway logs) won't match a single-token 'Bash: railway'
    signature -- this is the SCRIPT's existing limitation, preserved for
    parity. signatureTokens (persisted) is the v1.1 lever to sharpen this."""
    allowlisted = hookmanaged = False
    if tool in ("Bash", "PowerShell") and ":" in signature:
        bh = signature.split(":", 1)[1].strip()
        for spec in S["bash_heads"]:
            st = spec.split()
            bt = bh.split()
            if not st or not bt:
                continue
            if len(st) == 1 and bt[0] == st[0]:
                allowlisted = True
                break
            if bh == spec or bh.startswith(spec + " "):
                allowlisted = True
                break
    elif tool.startswith("mcp__"):
        if tool in S["mcp_allow"]:
            allowlisted = True
        for mt in S["hook_matchers"]:
            try:
                if re.search(mt, tool):
                    hookmanaged = True
                    break
            except re.error:
                if tool in mt.split("|"):
                    hookmanaged = True
                    break
    else:
        if tool in S["tool_allow"]:
            allowlisted = True
    return ("hook_managed" if hookmanaged else
            "already_allowlisted" if allowlisted else "not_covered")


def _run_blocks_kind(args, emit_fn) -> int:
    """Friction lens: read blocks.jsonl (written by scan), roll up the
    scrubbed evidence, and cross-reference each block against CURRENT
    settings.json coverage. The analytical spine (classifier-vs-allowlist,
    coverage-aware proposal rule, tier-1/2/3, autoMode R1-R4) lives in the
    audit-tool-blocks SKILL.md that CONSUMES this envelope -- propose only
    aggregates + tags coverage, never interprets."""
    limit = int(getattr(args, "limit", 10) or 10)
    if limit < 1:
        limit = 10
    no_settings = bool(getattr(args, "no_settings", False))

    rows = _read_candidates(blocks_log_path())  # noqa: F821 — injected
    S = _load_settings(no_settings)
    settings_xref = None if no_settings else S["sources"]

    if not rows:
        emit_fn({
            "project": "all-projects",
            "kind": "blocks",
            "totalBlocks": 0,
            "byClass": {}, "byTool": {}, "byToolClass": {},
            "bySignature": {}, "byCategory": {}, "byProject": {},
            "byMonth": {}, "byCoverage": {},
            "actionableAllowlistGaps": {},
            "autoModeAllowCurrent": S["automode_allow"],
            "hookMatchersCurrent": S["hook_matchers"],
            "settingsCrossReferenced": settings_xref,
            "sampleReasons": [],
            "frictionBoost": [],
            "candidates": [],
            "note": "no block records yet -- run skill-plus scan "
                    "--all-projects --accept-consent first",
        })
        return 0

    by_class: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    by_tool_class: dict[str, int] = {}
    by_signature: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_project: dict[str, int] = {}
    by_month: dict[str, int] = {}
    by_coverage: dict[str, int] = {}
    gaps: dict[str, int] = {}
    sample_reasons: list[str] = []
    total_blocks = 0

    for r in rows:
        cnt = int(r.get("count", 0) or 0)
        total_blocks += cnt
        cls = str(r.get("class", "unknown"))
        tool = str(r.get("tool", "(unknown-tool)"))
        sig = str(r.get("signature", "?"))
        cov = _coverage(tool, sig, S)
        r["coverage"] = cov  # per-candidate tag
        by_class[cls] = by_class.get(cls, 0) + cnt
        by_tool[tool] = by_tool.get(tool, 0) + cnt
        tc = f"{tool}|{cls}"
        by_tool_class[tc] = by_tool_class.get(tc, 0) + cnt
        by_signature[sig] = by_signature.get(sig, 0) + cnt
        ck = f"{cls}|{cov}"
        by_coverage[ck] = by_coverage.get(ck, 0) + cnt
        # a genuine allowlist gap = a declined prompt NOT already covered
        # (the ONLY safe-list candidates -- mirrors the script's `gaps`)
        if cls == "USER_REJECTED_PROMPT" and cov == "not_covered":
            gaps[sig] = gaps.get(sig, 0) + cnt
        for cat in (r.get("categories") or []):
            by_category[str(cat)] = by_category.get(str(cat), 0) + cnt
        for proj in (r.get("projects") or []):
            by_project[str(proj)] = by_project.get(str(proj), 0) + cnt
        for mon, n in (r.get("byMonth") or {}).items():
            by_month[str(mon)] = by_month.get(str(mon), 0) + int(n or 0)
        for sr in (r.get("sampleReasons") or []):
            if len(sample_reasons) < 40 and sr not in sample_reasons:
                sample_reasons.append(sr)

    def _top(d: dict, n: int) -> dict:
        return dict(sorted(d.items(), key=lambda kv: -kv[1])[:n])

    candidates = sorted(rows, key=lambda r: -int(r.get("count", 0) or 0))[:limit]

    emit_fn({
        "project": "all-projects",
        "kind": "blocks",
        "totalBlocks": total_blocks,
        "byClass": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "byTool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])),
        "byToolClass": dict(sorted(by_tool_class.items(), key=lambda kv: -kv[1])),
        "bySignature": _top(by_signature, 40),
        "byCategory": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "byProject": _top(by_project, 25),
        "byMonth": dict(sorted(by_month.items())),
        "byCoverage": dict(sorted(by_coverage.items(), key=lambda kv: -kv[1])),
        "actionableAllowlistGaps": _top(gaps, 40),
        "autoModeAllowCurrent": S["automode_allow"],
        "hookMatchersCurrent": S["hook_matchers"],
        "settingsCrossReferenced": settings_xref,
        "sampleReasons": sample_reasons,
        # v1: ALWAYS empty. v1.1 populates after the join hand-probe
        # (cmd_head collapses railway/supabase subcommands -> unprobed join).
        "frictionBoost": [],
        "candidates": candidates,
    })
    return 0


def run(args, emit_fn):
    # Dispatch by --kind. blocks -> friction lens; routine/habit/all ->
    # temporal lens; skill (default) -> frequency lens.
    kind = getattr(args, "kind", "skill") or "skill"
    if kind == "blocks":
        return _run_blocks_kind(args, emit_fn)
    if kind != "skill":
        return _run_routine_kind(args, emit_fn)

    # --- skill path (default) -- byte-identical to original ---
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

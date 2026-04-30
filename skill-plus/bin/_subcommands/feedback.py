"""skill-plus feedback — read-side cross-source aggregator (slice 3.7).

Joins two streams keyed by skill name:

  Stream 1: explicit ratings written by the `skill-feedback` plugin to
            <git-toplevel>/.agent-plus/skill-feedback/<skill>.jsonl
  Stream 2: implicit signals mined from Claude Code session JSONL logs
            under ~/.claude/projects/<encoded-cwd>/*.jsonl (consent-gated).

Plus a third "discoverability gap" signal: known plugins' obviation
patterns (e.g. raw `git diff` for `diff-summary`) appearing >=5x with
zero plugin invocations.

Read-only. Never mutates either stream's source files. Stdlib only.

Helpers (project_state_root, session_files_for_project, has_consent_for,
_git_toplevel, _now_iso, _ensure_dir, etc.) are injected by the bin shell.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

# ─── known-plugin tables (judgement calls — flagged for /review) ──────────────
# Hand-curated; deliberately small. If a plugin isn't here, stream-2 still
# tracks invocations + re-invocations but cannot detect fallbacks/discoverability.

# fallback indicators: plugin -> list of (first_token, optional_required_substr)
# A bash command counts as a "fallback" for the plugin if first token matches
# AND (no substr requirement OR command contains the substr).
_FALLBACK_INDICATORS: dict[str, list[tuple[str, str | None]]] = {
    "repo-analyze": [("find", None), ("grep", None), ("ls", None), ("rg", None)],
    "diff-summary": [("git", "diff")],
    "skill-feedback": [],  # no clear fallback — humans don't manually log
    "agent-plus": [],      # umbrella; covered by sub-tools
    "skill-plus": [],
}

# obviation patterns: plugin -> predicate(cmd_tokens, raw_cmd) -> bool.
# A raw bash usage that the plugin would replace. Used for the
# discoverability-gap signal (raw pattern present, plugin absent).
_OBVIATION_PATTERNS: dict[str, list[tuple[str, str | None]]] = {
    "diff-summary": [("git", "diff")],
    "repo-analyze": [("find", None), ("rg", None)],
}

# Plugin tokens we recognise as "this is an invocation of plugin X".
# We accept either "agent-plus <plugin>" or just "<plugin>" as the first
# token. Stream-1 skill names + args.skill also get added at runtime.
_KNOWN_PLUGINS = set(_FALLBACK_INDICATORS) | {"propose", "list", "scan"}


# ─── stream-1: skill-feedback JSONL ───────────────────────────────────────────


def _parse_iso(ts: str) -> _dt.datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    s = ts
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _read_stream1(feedback_dir: Path, since_days: int,
                  only_skill: str | None) -> dict[str, dict]:
    """Aggregate skill-feedback rows by skill name.

    Returns {skill: aggregate_dict}. Tolerates malformed lines.
    """
    out: dict[str, dict] = {}
    if not feedback_dir.is_dir():
        return out
    now = _dt.datetime.now(_dt.timezone.utc)
    # since_days == 0 -> empty window (nothing in the last 0 days).
    cutoff = now - _dt.timedelta(days=since_days) if since_days > 0 else now

    for jf in sorted(feedback_dir.glob("*.jsonl")):
        skill = jf.stem
        if only_skill and skill != only_skill:
            continue
        ratings: list[int] = []
        outcomes: dict[str, int] = {}
        frictions: dict[str, int] = {}
        last_ts: str | None = None
        try:
            text = jf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = _parse_iso(rec.get("ts", ""))
            if ts is None:
                continue
            if since_days == 0:
                continue  # 0-day window matches nothing
            if ts < cutoff:
                continue
            r = rec.get("rating")
            if isinstance(r, int) and 1 <= r <= 5:
                ratings.append(r)
            o = rec.get("outcome")
            if isinstance(o, str):
                outcomes[o] = outcomes.get(o, 0) + 1
            f = rec.get("friction")
            if isinstance(f, str) and f.strip():
                key = f.strip().lower()
                frictions[key] = frictions.get(key, 0) + 1
            ts_raw = rec.get("ts")
            if isinstance(ts_raw, str) and (last_ts is None or ts_raw > last_ts):
                last_ts = ts_raw
        count = sum(outcomes.values()) if outcomes else len(ratings)
        # Use the union: count = number of valid records (any of rating/outcome).
        # Re-walk briefly to get a single truthful count.
        valid = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = _parse_iso(rec.get("ts", ""))
            if ts is None or since_days == 0 or ts < cutoff:
                continue
            valid += 1
        if valid == 0:
            # Skill file exists but no in-window rows — still surface (skipped).
            out[skill] = {
                "skill": skill,
                "stream1": {
                    "count": 0,
                    "note": "no_entries_in_window",
                },
            }
            continue
        rh = {str(i): 0 for i in range(1, 6)}
        for r in ratings:
            rh[str(r)] += 1
        top_friction = sorted(frictions.items(), key=lambda kv: -kv[1])[:5]
        mean = round(sum(ratings) / len(ratings), 2) if ratings else None
        out[skill] = {
            "skill": skill,
            "stream1": {
                "count": valid,
                "ratingsHistogram": rh,
                "outcomesHistogram": outcomes,
                "frictionLabels": [{"label": k, "count": v} for k, v in top_friction],
                "meanRating": mean,
                "lastEntryAt": last_ts,
            },
        }
    return out


# ─── stream-2: session-log walker (inlined; do NOT import scan) ───────────────


def _walk_for_bash(obj, out: list[str], depth: int = 0) -> None:
    """Append every Bash tool_use command found anywhere in obj."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        if obj.get("type") == "tool_use" and obj.get("name") == "Bash":
            inp = obj.get("input")
            if isinstance(inp, dict):
                cmd = inp.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    out.append(cmd)
        if obj.get("toolName") == "Bash" or obj.get("tool_name") == "Bash":
            inp = obj.get("input") or obj.get("toolInput") or {}
            if isinstance(inp, dict):
                cmd = inp.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    out.append(cmd)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _walk_for_bash(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                _walk_for_bash(v, out, depth + 1)


def _ordered_bash_commands(session_path: Path) -> list[str]:
    """Return Bash commands from a session file in file order."""
    cmds: list[str] = []
    try:
        with session_path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                _walk_for_bash(obj, cmds)
    except OSError:
        return cmds
    return cmds


def _identify_invocation(tokens: list[str], known: set[str]) -> str | None:
    """If tokens represent an invocation of a known plugin, return its name."""
    if not tokens:
        return None
    if tokens[0] == "agent-plus" and len(tokens) >= 2 and tokens[1] in known:
        return tokens[1]
    if tokens[0] in known:
        return tokens[0]
    return None


def _matches_indicator(tokens: list[str], raw: str,
                       indicators: list[tuple[str, str | None]]) -> bool:
    if not tokens or not indicators:
        return False
    first = tokens[0]
    for tok, sub in indicators:
        if first != tok:
            continue
        if sub is None or sub in raw:
            return True
    return False


def _read_stream2(project: Path, since_days: int, known: set[str],
                  only_skill: str | None) -> dict[str, dict]:
    """Walk session files in the last `since_days`, build per-plugin signals."""
    sessions = session_files_for_project(project)  # type: ignore[name-defined]
    if not sessions:
        return {}
    now = _dt.datetime.now(_dt.timezone.utc)
    cutoff = now - _dt.timedelta(days=since_days) if since_days > 0 else now

    def _mtime(p: Path) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(p.stat().st_mtime, _dt.timezone.utc)

    in_window: list[Path] = []
    for s in sessions:
        if since_days == 0:
            continue
        try:
            if _mtime(s) >= cutoff:
                in_window.append(s)
        except OSError:
            continue

    # per-plugin counters
    invocations: dict[str, int] = {}
    fallbacks: dict[str, int] = {}
    re_invocations: dict[str, int] = {}
    # discoverability: per-plugin raw obviation-pattern hits (any session)
    obviation_hits: dict[str, int] = {p: 0 for p in _OBVIATION_PATTERNS}

    for sess in in_window:
        cmds = _ordered_bash_commands(sess)
        token_lists = [c.strip().split() for c in cmds]

        # invocations + look-ahead window of 10 tool calls
        for i, toks in enumerate(token_lists):
            plugin = _identify_invocation(toks, known)
            if plugin is None:
                continue
            invocations[plugin] = invocations.get(plugin, 0) + 1
            window = token_lists[i + 1:i + 11]
            window_raw = cmds[i + 1:i + 11]
            # fallback detection
            indicators = _FALLBACK_INDICATORS.get(plugin, [])
            for wt, wr in zip(window, window_raw):
                if _matches_indicator(wt, wr, indicators):
                    fallbacks[plugin] = fallbacks.get(plugin, 0) + 1
                    break  # one fallback per invocation
            # re-invocation detection: same plugin within next 5 calls
            for wt in window[:5]:
                if _identify_invocation(wt, known) == plugin:
                    re_invocations[plugin] = re_invocations.get(plugin, 0) + 1
                    break

        # obviation-pattern sweep (whole session)
        for plugin, indicators in _OBVIATION_PATTERNS.items():
            for toks, raw in zip(token_lists, cmds):
                if _matches_indicator(toks, raw, indicators):
                    obviation_hits[plugin] = obviation_hits.get(plugin, 0) + 1

    out: dict[str, dict] = {}
    threshold = 5

    # per-plugin invocation rows
    for plugin, inv in invocations.items():
        if only_skill and plugin != only_skill:
            continue
        if inv < threshold:
            out[plugin] = {
                "skill": plugin,
                "stream2": {
                    "invocations": inv,
                    "suppressed": "below_threshold",
                },
            }
            continue
        fc = fallbacks.get(plugin, 0)
        rc = re_invocations.get(plugin, 0)
        out[plugin] = {
            "skill": plugin,
            "stream2": {
                "invocations": inv,
                "fallbackCount": fc,
                "fallbackRate": round(fc / inv, 3) if inv else 0.0,
                "reInvocationCount": rc,
                "reInvocationRate": round(rc / inv, 3) if inv else 0.0,
            },
        }

    # discoverability gap rows (manual pattern present, no invocations)
    for plugin, hits in obviation_hits.items():
        if only_skill and plugin != only_skill:
            continue
        inv = invocations.get(plugin, 0)
        if hits >= 5 and inv == 0:
            row = out.setdefault(plugin, {"skill": plugin})
            row["discoverability"] = {
                "manualPatternCount": hits,
                "pluginInvocations": 0,
                "signal": "discoverability_gap",
            }
    return out


# ─── join + scoring ───────────────────────────────────────────────────────────


def _concern_score(row: dict) -> float:
    """Higher = more concerning. Used for sort. Combines low ratings,
    high fallback rate, and discoverability gap presence."""
    score = 0.0
    s1 = row.get("stream1") or {}
    mean = s1.get("meanRating")
    if isinstance(mean, (int, float)):
        # 5 is great (0 concern), 1 is bad (4 concern)
        score += max(0.0, 5.0 - float(mean))
    s2 = row.get("stream2") or {}
    fr = s2.get("fallbackRate")
    if isinstance(fr, (int, float)):
        score += float(fr) * 3.0  # weight fallbacks
    if row.get("discoverability"):
        score += 2.0
    return score


# ─── main entry ───────────────────────────────────────────────────────────────


def run(args, emit_fn) -> int:
    # 1. Resolve project
    if args.project:
        project = Path(args.project).expanduser().resolve()
    else:
        top = _git_toplevel()  # type: ignore[name-defined]
        project = (top if top is not None else Path.cwd()).resolve()

    since_days = int(getattr(args, "since_days", 30))
    only_skill = getattr(args, "skill", None) or None

    # 2. Stream 1 — skill-feedback rows (no extra consent; user's own repo)
    feedback_dir = project / ".agent-plus" / "skill-feedback"
    stream1 = _read_stream1(feedback_dir, since_days, only_skill)

    # 3. Stream 2 — session mining (consent-gated)
    stream2_meta: dict = {}
    stream2: dict[str, dict] = {}
    if has_consent_for(project):  # type: ignore[name-defined]
        # Build the known-plugin set: hardcoded + stream1 skills + --skill arg.
        known = set(_KNOWN_PLUGINS)
        known.update(stream1.keys())
        if only_skill:
            known.add(only_skill)
        # Also add any plugin names from a marketplace.json if present.
        mp = project / ".claude-plugin" / "marketplace.json"
        if mp.is_file():
            try:
                data = json.loads(mp.read_text(encoding="utf-8"))
                plugins = data.get("plugins") if isinstance(data, dict) else None
                if isinstance(plugins, list):
                    for p in plugins:
                        if isinstance(p, dict) and isinstance(p.get("name"), str):
                            known.add(p["name"])
            except (OSError, json.JSONDecodeError):
                pass
        stream2 = _read_stream2(project, since_days, known, only_skill)
        stream2_meta = {"status": "scanned"}
    else:
        stream2_meta = {"status": "skipped", "reason": "no_consent"}

    # 4. Join
    skills_index: dict[str, dict] = {}
    for name, row in stream1.items():
        skills_index.setdefault(name, {"skill": name}).update(row)
    for name, row in stream2.items():
        skills_index.setdefault(name, {"skill": name}).update(row)

    if only_skill:
        skills_index = {k: v for k, v in skills_index.items() if k == only_skill}

    rows = sorted(skills_index.values(), key=lambda r: -_concern_score(r))

    payload = {
        "project": str(project),
        "sinceDays": since_days,
        "skills": rows,
        "stream1Source": str(feedback_dir),
        "stream2Source": stream2_meta,
        "generatedAt": _now_iso(),  # type: ignore[name-defined]
    }
    emit_fn(payload)
    return 0

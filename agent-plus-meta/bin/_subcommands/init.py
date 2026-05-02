"""v0.12.0 onboarding wizard — `agent-plus-meta init`.

Implements:
- legacy idempotent workspace bootstrap (manifest.json / services.json /
  env-status.json) — preserved verbatim so the existing 7 init tests pass
- persona-aware state detection (NEW / RETURNING / SKILL-AUTHOR)
- cross-repo discovery from ~/.claude/projects/
- per-repo opt-in scan via `skill-plus scan --all-projects --project <p>`
- doctor finale (in-process call, wrapped in try/except)
- --non-interactive --auto deterministic JSON envelope
- frozen JSON envelope schema (see plan §"DX Review Additions / 1")
- Tier 1 + Tier 3 error format (see plan §"DX Review Additions / 2")
- observability: append a JSON line to <ws>/.agent-plus/init.log

Stdlib-only. Python 3.9+. The `bind(host)` mechanism wires this module to
the parent bin's helpers without requiring circular imports.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional


# ─── host bindings ───────────────────────────────────────────────────────────
#
# The parent bin calls bind(host) with itself before invoking cmd_init. This
# avoids re-importing the bin from within (the bin loads under module name
# "agent_plus" via SourceFileLoader in tests, but as __main__ when executed,
# so a clean import from here would re-run side effects).

_host: Any = None


def bind(host: Any) -> None:
    """Register the parent bin module so this submodule can call its
    helpers (resolve_workspace, load_env, cmd_doctor, etc.)."""
    global _host
    _host = host


def _h() -> Any:
    if _host is None:  # pragma: no cover — guard
        raise RuntimeError("init._subcommand.bind() not called by host bin")
    return _host


# ─── constants ───────────────────────────────────────────────────────────────

# Project markers used both for homeless detection (cwd≈home) and manual-paste
# warnings ("no markers detected — scan may yield nothing").
PROJECT_MARKERS: tuple[str, ...] = (
    "package.json", "pyproject.toml", "Cargo.toml",
    "go.mod", "deno.json", "requirements.txt",
)

# Stable error codes exposed in envelope.errors[].code (plan §DX/2).
ERR_CONSENT_REQUIRED = "consent_required"
ERR_CROSS_REPO_SCAN_FAILED = "cross_repo_scan_failed"
ERR_CROSS_REPO_INTERRUPTED = "cross_repo_interrupted"
ERR_STACK_DETECT_UNREADABLE = "stack_detect_unreadable_marker"
ERR_DOCTOR_UNREACHABLE = "doctor_unreachable"
ERR_SKILL_PLUS_MISSING = "skill_plus_missing"
ERR_AUTO_TIE_BREAK = "auto_tie_break"


# ─── time utilities ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── error helper ────────────────────────────────────────────────────────────

def _emit_error(
    code: str,
    message: str,
    hint: str,
    *,
    recoverable: bool,
    errors_list: list[dict],
    interactive: bool,
) -> None:
    """Append a structured error to envelope.errors[] AND, when interactive,
    print a Tier-1 line to stderr in `<problem> — <cause> — <fix>` shape.
    """
    errors_list.append({
        "code": code,
        "message": message,
        "hint": hint,
        "recoverable": recoverable,
    })
    if interactive:
        # ASCII-only " - " between fields — em-dashes break on Windows cp1252.
        try:
            sys.stderr.write(f"  ! {message} - {hint}\n")
            sys.stderr.flush()
        except UnicodeEncodeError:
            # Final fallback: strip non-ASCII.
            safe = (f"  ! {message} - {hint}\n").encode("ascii", "replace").decode("ascii")
            sys.stderr.write(safe)
            sys.stderr.flush()


# ─── state detection ─────────────────────────────────────────────────────────


def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except OSError:
        return False


def _safe_iterdir(p: Path) -> list[Path]:
    try:
        return list(p.iterdir())
    except OSError:
        return []


def _has_skills(project_root: Path) -> bool:
    """`<project>/.claude/skills/` exists with at least one entry."""
    skills_dir = project_root / ".claude" / "skills"
    if not _safe_exists(skills_dir) or not skills_dir.is_dir():
        return False
    return len(_safe_iterdir(skills_dir)) > 0


def _has_claude_projects_history(home: Optional[Path] = None) -> bool:
    home = home or Path.home()
    proj = home / ".claude" / "projects"
    if not _safe_exists(proj) or not proj.is_dir():
        return False
    return any(p.is_dir() for p in _safe_iterdir(proj))


def _env_vars_ready_count() -> int:
    """Count of plugins where ALL required env vars are set, reusing the
    parent bin's PLUGIN_ENV_SPEC + load_env."""
    h = _h()
    try:
        cfg = h.load_env(None)
    except Exception:  # noqa: BLE001
        cfg = dict(os.environ)
    spec: dict[str, dict[str, list[str]]] = h.PLUGIN_ENV_SPEC
    ready = 0
    for _plugin, ent in spec.items():
        req = ent.get("required") or []
        if not req:
            continue
        if all(cfg.get(name, "").strip() for name in req):
            ready += 1
    return ready


def _detect_homeless(cwd: Optional[Path] = None,
                    home: Optional[Path] = None) -> bool:
    """Homeless = no git toplevel for cwd AND no project markers in cwd
    AND cwd IS the user's home dir.

    We deliberately don't fire for cwd above home (e.g. cwd=`/` with home
    under it) — a user at `/` is in a weirder place than the wizard can
    confidently re-route, and the cross-repo offer + doctor finale already
    handle that case gracefully without the homeless pivot.
    """
    cwd = (cwd or Path.cwd()).resolve()
    home = (home or Path.home()).resolve()
    h = _h()
    try:
        toplevel = h._git_toplevel(cwd)
    except Exception:  # noqa: BLE001
        toplevel = None
    if toplevel is not None:
        return False
    for marker in PROJECT_MARKERS:
        if _safe_exists(cwd / marker):
            return False
    return cwd == home


def _detect_user_state(workspace: Path,
                       project_root: Optional[Path] = None,
                       home: Optional[Path] = None,
                       cwd: Optional[Path] = None) -> dict[str, Any]:
    """Return the wizard's state dict. All filesystem reads tolerate
    OSError → False/0 per the failure registry."""
    project_root = project_root or workspace.parent
    home = home or Path.home()
    return {
        "has_claude_projects_history": _has_claude_projects_history(home),
        "has_skills": _has_skills(project_root),
        "env_vars_ready_count": _env_vars_ready_count(),
        "agent_plus_already_init": _safe_exists(workspace / "manifest.json"),
        "homeless": _detect_homeless(cwd=cwd, home=home),
    }


# ─── cross-repo discovery ────────────────────────────────────────────────────


def _decode_claude_project_dir(name: str) -> Optional[Path]:
    """Claude Code's encoding for `~/.claude/projects/<dir>` is lossy: `/`
    becomes `-`, but a literal `-` in the path also remains `-`. So
    `C:/dev/agent-plus` and `C:/dev/agent/plus` collide on `C--dev-agent-plus`.

    Strategy: produce candidate decodings and return the first that exists
    on disk. Caller is responsible for the existence filter.

    Conventions handled:
      C--dev-foo                   → Windows drive: C:/dev/foo
      -home-user-foo               → POSIX absolute: /home/user/foo
      -Users-bob-foo               → POSIX absolute: /Users/bob/foo

    Returns the first candidate that resolves; if none exist, returns the
    most-likely candidate so the caller can decide.
    """
    if not name:
        return None

    candidates: list[Path] = []

    def _add(s: str) -> None:
        try:
            p = Path(s)
            candidates.append(p)
        except (OSError, ValueError):
            pass

    # POSIX-style: `-foo-bar-baz` → `/foo/bar/baz`
    if name.startswith("-"):
        _add("/" + name[1:].replace("-", "/"))

    # Windows drive: `X--rest` where X is a single char (drive letter)
    if len(name) >= 3 and name[1:3] == "--" and name[0].isalpha():
        drive = name[0]
        rest = name[3:]
        # Try: every `-` → `/`. Then progressively try: every `--` → `/.`
        # (leading-dot directories like `.claude`).
        base = drive + ":/" + rest.replace("-", "/")
        _add(base)
        # Heuristic: collapse `//` (from `--`) back to `/.` to recover
        # `.claude` style hidden dirs.
        if "//" in base:
            _add(base.replace("//", "/."))

    # Pick the first candidate that exists on disk.
    for c in candidates:
        try:
            if c.exists():
                return c
        except OSError:
            continue
    # Nothing exists; return the first candidate so caller can log/skip.
    return candidates[0] if candidates else None


def _project_dir_mtime(project_dir: Path) -> float:
    """Max mtime among *.jsonl files in the dir; fall back to dir mtime."""
    best = 0.0
    try:
        for child in project_dir.iterdir():
            if child.suffix == ".jsonl" and child.is_file():
                try:
                    m = child.stat().st_mtime
                    if m > best:
                        best = m
                except OSError:
                    pass
    except OSError:
        pass
    if best == 0.0:
        try:
            best = project_dir.stat().st_mtime
        except OSError:
            best = 0.0
    return best


def _discover_recent_claude_repos(
    limit: int = 4,
    days: int = 30,
    home: Optional[Path] = None,
    now: Optional[float] = None,
) -> list[Path]:
    """Walk ~/.claude/projects/, decode subdir names back to repo paths,
    sort by recency, return top `limit`. Skip dead paths."""
    home = home or Path.home()
    proj_root = home / ".claude" / "projects"
    if not _safe_exists(proj_root) or not proj_root.is_dir():
        return []
    now_ts = now if now is not None else time.time()
    cutoff = now_ts - days * 86400.0
    candidates: list[tuple[float, Path]] = []
    for child in _safe_iterdir(proj_root):
        if not child.is_dir():
            continue
        mtime = _project_dir_mtime(child)
        if mtime < cutoff:
            continue
        decoded = _decode_claude_project_dir(child.name)
        if decoded is None or not _safe_exists(decoded):
            continue
        candidates.append((mtime, decoded.resolve()))
    candidates.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    out: list[Path] = []
    for _m, p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out


# ─── interactive helpers ─────────────────────────────────────────────────────


def _eprint(msg: str = "") -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _prompt_line(prompt: str) -> str:
    """Read one line from stdin, default empty string on EOF / error.
    Compatible with cmd.exe (subprocess.Popen with shell=True) since we
    write the prompt to stderr and read raw from sys.stdin (not readline
    history)."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        line = sys.stdin.readline()
    except (OSError, KeyboardInterrupt):
        raise
    if not line:
        return ""
    return line.rstrip("\r\n")


def _prompt_yes_no(prompt: str, default_no: bool = True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        answer = _prompt_line(prompt + suffix).strip().lower()
    except KeyboardInterrupt:
        raise
    if not answer:
        return not default_no
    return answer in ("y", "yes")


def _resolve_cmd(name: str) -> tuple:
    """Return (exe_path_or_None, use_shell) for a command name.
    On Windows, falls back to <name>.cmd and sets shell=True so cmd.exe
    can execute .cmd wrappers (subprocess with shell=False cannot)."""
    exe = shutil.which(name)
    if exe is None and sys.platform == "win32":
        exe = shutil.which(name + ".cmd")
    use_shell = (sys.platform == "win32" and exe is not None
                 and exe.lower().endswith(".cmd"))
    return exe, use_shell


# ─── branch selection ────────────────────────────────────────────────────────


def _pick_branch(state: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Deterministic branch picker. Returns (branch, tie_break_reason).
    Priority (per plan): skill_author > returning > new.

    Special-case: homeless=True forces branch_chosen='new' with a
    tie_break_reason='homeless_no_repo_context' (per plan).
    """
    if state.get("homeless"):
        # Skill-author still wins if local skills are present; otherwise pivot.
        if state.get("has_skills"):
            return "skill_author", None
        return "new", "homeless_no_repo_context"

    if state.get("has_skills"):
        return "skill_author", None
    if state.get("agent_plus_already_init") or state.get("has_claude_projects_history"):
        return "returning", None
    return "new", None


# ─── doctor / skill-plus scan helpers ────────────────────────────────────────


def _doctor_has_blocking_issues(doc_payload: dict) -> bool:
    """True when doctor lists issues outside envcheck-only (matches pretty
    doctor's ACTION REQUIRED vs OPTIONAL SETUP split).
    """
    issues = doc_payload.get("issues") or []
    if not isinstance(issues, list):
        return False
    return any(
        isinstance(it, dict) and it.get("category") not in ("envcheck",)
        for it in issues
    )


def _missing_claude_plugins(doc_payload: dict) -> list[str]:
    """Return list of FRAMEWORK_PRIMITIVES not yet registered as Claude plugins.

    Uses the ``claude_plugin_registration`` dict that ``cmd_doctor`` puts on
    the payload ({prim: bool}).  Returns an empty list when the key is absent
    (e.g. claude CLI not on PATH — we can't tell, so we don't block).
    """
    cpr = doc_payload.get("claude_plugin_registration")
    if not isinstance(cpr, dict):
        return []
    return [p for p, registered in cpr.items() if not registered]


def _parse_skill_plus_scan_stdout(stdout: str) -> dict[str, Any]:
    """Parse skill-plus scan JSON stdout; tolerate partial / non-JSON."""
    empty: dict[str, Any] = {
        "sessions_scanned": None,
        "candidates_new": None,
        "candidates_updated": None,
        "candidates_total": None,
        "top_pattern_keys": None,
        "fallback_len": 0,
    }
    try:
        payload = json.loads(stdout.strip() or "{}")
    except (json.JSONDecodeError, ValueError):
        return empty
    if not isinstance(payload, dict):
        return empty
    ss = payload.get("sessionsScanned")
    cn = payload.get("candidatesNew")
    cu = payload.get("candidatesUpdated")
    ct = payload.get("candidatesTotal")
    cand = payload.get("candidates")
    keys: list[str] = []
    if isinstance(cand, list):
        empty["fallback_len"] = len(cand)
        for rec in cand[:5]:
            if isinstance(rec, dict) and rec.get("key"):
                keys.append(str(rec["key"]))
    if isinstance(ss, int):
        empty["sessions_scanned"] = ss
    if isinstance(cn, int):
        empty["candidates_new"] = cn
    if isinstance(cu, int):
        empty["candidates_updated"] = cu
    if isinstance(ct, int):
        empty["candidates_total"] = ct
    if keys:
        empty["top_pattern_keys"] = keys
    return empty


def _skill_plus_done_stderr_line(res: dict[str, Any]) -> str:
    """UK English one-line summary after a successful cross-repo scan."""
    if res.get("status") != "ok":
        return ""
    ss = res.get("sessions_scanned")
    cn = res.get("candidates_new")
    cu = res.get("candidates_updated")
    ct = res.get("candidates_total")
    keys = res.get("top_pattern_keys") or []
    cf = int(res.get("candidates_found") or 0)

    bits: list[str] = []
    if isinstance(ss, int):
        bits.append(f"{ss} session{'s' if ss != 1 else ''} scanned")
    if isinstance(cn, int) and isinstance(cu, int):
        bits.append(f"{cn} new / {cu} updated clusters")
    elif isinstance(cn, int) and cn:
        bits.append(f"{cn} new cluster{'s' if cn != 1 else ''}")
    elif isinstance(cu, int) and cu:
        bits.append(f"{cu} updated cluster{'s' if cu != 1 else ''}")

    if bits:
        msg = "; ".join(bits)
        if isinstance(ct, int):
            msg += f" ({ct} in candidate log)"
        if keys:
            shown = ", ".join(keys[:5])
            if len(keys) > 5:
                shown += ", ..."
            msg += f" -- top patterns: {shown}"
        return f"     done  -- {msg}"

    if cf:
        return (
            f"     done  -- {cf} skill candidate{'s' if cf != 1 else ''} found"
        )
    return "     done  -- workspace ready (no new skill candidates)"


def _cross_repo_result_row(path_str: str, res: dict[str, Any]) -> dict[str, Any]:
    """Envelope row for one cross-repo scan (additive fields when present)."""
    line: dict[str, Any] = {
        "path": path_str,
        "candidates_found": res.get("candidates_found", 0),
        "status": res.get("status"),
        "reason": res.get("reason", ""),
    }
    for opt in (
        "sessions_scanned",
        "candidates_new",
        "candidates_updated",
        "candidates_total",
        "top_pattern_keys",
    ):
        if opt in res and res[opt] is not None:
            line[opt] = res[opt]
    return line


# ─── subprocess wrappers (mockable in tests) ─────────────────────────────────


def _run_first_win(branch: str, project_root: Path,
                   *, timeout: int = 30) -> dict[str, Any]:
    """Shell out to the branch's first-win command. Returns
    {"command": str|None, "result": "ok"|"failed"|"skipped", "reason": ...}.
    Failure always recoverable — wizard continues to cross-repo + doctor."""
    cmds: dict[str, list[str]] = {
        "new": ["repo-analyze", str(project_root)],
        "returning": ["agent-plus-meta", "doctor"],
        "skill_author": ["skill-plus", "list", "--include-global"],
    }
    cmd = cmds.get(branch)
    if cmd is None:
        return {"command": None, "result": "skipped", "reason": "no_first_win"}
    exe, use_shell = _resolve_cmd(cmd[0])
    if exe is None:
        return {"command": " ".join(cmd), "result": "failed",
                "reason": f"{cmd[0]} not on PATH"}
    remaining_args = cmd[1:]
    try:
        if use_shell:
            # Quote exe path so cmd.exe doesn't split on spaces.
            # subprocess.list2cmdline handles the remaining args.
            import subprocess as _sp
            shell_cmd = '"' + exe + '" ' + _sp.list2cmdline(remaining_args)
            proc = subprocess.run(shell_cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout, check=False, shell=True)
        else:
            proc = subprocess.run([exe] + remaining_args, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return {"command": " ".join(cmd), "result": "failed",
                "reason": str(e)}
    if proc.returncode == 0:
        return {"command": " ".join(cmd), "result": "ok"}
    return {"command": " ".join(cmd), "result": "failed",
            "reason": f"exit {proc.returncode}"}


def _run_skill_plus_scan(project_path: Path,
                         *, timeout: int = 60) -> dict[str, Any]:
    """Invoke `skill-plus scan --all-projects --project <path>`. Returns a
    dict with keys: status ("ok"|"skipped"|"failed"), candidates_found,
    reason."""
    exe, use_shell = _resolve_cmd("skill-plus")
    if exe is None:
        return {"status": "skipped", "candidates_found": 0,
                "reason": "skill-plus not on PATH"}
    # --accept-consent: user already opted in by selecting this path in the
    # cross-repo wizard; consent is implied.
    remaining_args = ["scan", "--all-projects", "--accept-consent", "--project", str(project_path)]
    try:
        if use_shell:
            # Quote exe path so cmd.exe doesn't split on spaces.
            # subprocess.list2cmdline handles the remaining args.
            import subprocess as _sp
            shell_cmd = '"' + exe + '" ' + _sp.list2cmdline(remaining_args)
            proc = subprocess.run(shell_cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout, check=False, shell=True)
        else:
            proc = subprocess.run([exe] + remaining_args, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return {"status": "failed", "candidates_found": 0,
                "reason": str(e)}
    if proc.returncode != 0:
        return {"status": "failed", "candidates_found": 0,
                "reason": f"exit {proc.returncode}"}
    parsed = _parse_skill_plus_scan_stdout(proc.stdout)
    cn = parsed.get("candidates_new")
    cu = parsed.get("candidates_updated")
    if isinstance(cn, int) and isinstance(cu, int):
        cf = cn + cu
    elif isinstance(cn, int) or isinstance(cu, int):
        cf = int(cn or 0) + int(cu or 0)
    else:
        cf = int(parsed.get("fallback_len") or 0)

    result: dict[str, Any] = {"status": "ok", "candidates_found": cf}
    for k in (
        "sessions_scanned",
        "candidates_new",
        "candidates_updated",
        "candidates_total",
        "top_pattern_keys",
    ):
        v = parsed.get(k)
        if v is not None:
            result[k] = v
    return result


# ─── observability ───────────────────────────────────────────────────────────


def _append_init_log(workspace: Path,
                     entry: dict[str, Any]) -> None:
    """Append one JSON line to <workspace>/init.log. Best-effort — never
    let log failures propagate.

    Privacy: the entry includes repo paths under `cross_repo_accepted`.
    Users shipping a bug report attachment can opt out via
    `AGENT_PLUS_INIT_LOG=0` (any value other than `1`/empty disables).
    """
    if os.environ.get("AGENT_PLUS_INIT_LOG", "1").strip() not in ("1", ""):
        return
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        # newline="\n" so Windows doesn't insert CRLF into our JSONL stream.
        with (workspace / "init.log").open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ─── manual-paste validator ──────────────────────────────────────────────────


def _validate_manual_path(raw: str) -> tuple[Optional[Path], Optional[str]]:
    """Return (resolved_path, warning_or_error).

    - Path missing/unreadable → (None, "<msg>")
    - Path with no markers and no .git → (Path, "warn:no markers")
    - Otherwise → (Path, None)
    """
    if not raw.strip():
        return None, "empty path"
    try:
        p = Path(raw).expanduser().resolve()
    except (OSError, ValueError) as e:
        return None, str(e)
    if not _safe_exists(p):
        return None, f"{p}: not found"
    # Reject non-directories outright. Handing `/dev/null` or a regular file
    # to `skill-plus scan --project` is nonsense even if technically safe.
    try:
        if not p.is_dir():
            return None, f"{p}: not a directory"
    except OSError as e:
        return None, str(e)
    has_git = _safe_exists(p / ".git")
    has_marker = any(_safe_exists(p / m) for m in PROJECT_MARKERS)
    if not has_git and not has_marker:
        return p, "warn:no_markers"
    return p, None


# ─── --dir validation (F2 — v0.15.6) ─────────────────────────────────────────


# Common Git for Windows / MSYS install prefixes. If the user passed a POSIX
# path like `/foo` and resolution lands under one of these, MSYS rewrote the
# path before Python ever saw it. Detection is best-effort — false negatives
# are acceptable (the error still names the resolved path), false positives
# are not (we don't want to blame MSYS for a real perm denial).
_MSYS_PREFIXES = (
    r"C:\Program Files\Git",
    r"C:\Program Files (x86)\Git",
    r"C:\msys64",
    r"C:\msys32",
)


def _looks_msys_mangled(dir_flag: str, resolved: Path) -> bool:
    """True if the resolved path landed under a known Git/MSYS install
    prefix on Windows. Indicates Git Bash's MSYS layer rewrote the arg
    before Python saw it (a POSIX-absolute `/foo` becomes
    `C:\\Program Files\\Git\\foo`).

    Note we can't rely on `dir_flag.startswith("/")` — by the time Python
    sees argv the rewrite has already happened and `args.dir` is the
    mangled form. The resolved-path-prefix check is the only reliable
    signal. False positive risk (user genuinely passing a path under
    the Git install dir) is acceptable: that path also wouldn't be
    writable, so the hint is still actionable.

    Windows-only failure mode; on macOS/Linux this returns False.
    """
    if sys.platform != "win32":
        return False
    try:
        rstr = str(resolved)
    except Exception:  # noqa: BLE001
        return False
    return any(rstr.startswith(p) for p in _MSYS_PREFIXES)


def _safe_mkdir_or_raise(dir_flag: Optional[str], workspace: Path,
                         host: Any) -> None:
    """Create the workspace directory; on failure, raise StructuredError
    (caught by host main) with a three-tier envelope.

    Why this exists (F2 / v0.15.6): the old failure mode was to let
    `workspace.mkdir(...)` raise `OSError`/`PermissionError`, which the
    host's generic `except Exception` formatted as `{"error":
    "[WinError 5] Access is denied: 'C:\\Program Files\\Git\\this'"}`
    — both unhelpful (no fix hint) and misleading (the prefix wasn't in
    the user's command; Git Bash MSYS rewrote it). This wrapper catches
    the OSError and reformats with problem/cause/fix.

    Note: we don't pre-check with `os.access(W_OK)` because on Windows
    that doesn't actually check filesystem ACLs (only the read-only
    attribute), so it returns True for `C:\\Program Files\\Git` even when
    normal users can't write there. Try-then-catch is the only reliable
    cross-platform check.
    """
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Only reframe when the user explicitly passed --dir; for the
        # default resolution paths (git toplevel / cwd / home) the raw
        # error is likely correct and a structured envelope would be
        # misleading. Re-raise to let the host's generic handler format it.
        if not dir_flag:
            raise
        parent = workspace.parent
        msys_hint = ""
        if _looks_msys_mangled(dir_flag, parent):
            msys_hint = (
                " The path landed under the Git install prefix, a strong "
                "signal that Git Bash's MSYS layer rewrote a POSIX-style "
                "path (e.g. /foo) into a Windows path before Python saw "
                "it. To bypass MSYS path rewriting, pass a path that "
                "starts with `~/` or with a Windows drive letter."
            )
        raise host.StructuredError(
            "could not create workspace directory",
            problem=f"directory {parent} is not writable",
            cause=(
                f"the OS rejected mkdir with: {exc}." + msys_hint
            ),
            fix=(
                "use --dir with a path under your home directory, e.g.: "
                "agent-plus-meta init --dir ~/test/foo"
            ),
        ) from exc


# ─── main entry point ────────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> dict:
    """Persona-aware onboarding wizard. Always returns the v0.12.0 frozen
    JSON envelope (see plan §"DX Review Additions / 1"). Legacy fields
    (workspace/source/created/skipped/suggested_skills) are preserved at
    the top level so existing tests + scripts still work.
    """
    h = _h()
    t0 = time.time()

    non_interactive = bool(getattr(args, "non_interactive", False))
    auto = bool(getattr(args, "auto", False))
    interactive = not non_interactive and not auto

    errors: list[dict] = []

    # ── 0. plugin preflight — auto-fix when Claude plugins are missing ──
    #    Runs BEFORE any workspace changes.  When unregistered primitives are
    #    found we attempt to register them automatically via the claude CLI
    #    (claude plugin marketplace add + claude plugin install).  After the
    #    fix commands run the user still needs to /reload-plugins inside their
    #    Claude session, so we exit(1) with clear instructions.
    #    Skipped in non-interactive / auto mode so CI pipelines still get
    #    doctor_blocking in the JSON envelope.
    if interactive:
        _doc_pre: dict | None = None
        try:
            _pre_doctor_args = argparse.Namespace(
                dir=getattr(args, "dir", None),
                env_file=getattr(args, "env_file", None),
                pretty=False,
            )
            _doc_pre = h.cmd_doctor(_pre_doctor_args)
        except Exception:
            _doc_pre = None

        if isinstance(_doc_pre, dict):
            _missing = _missing_claude_plugins(_doc_pre)
            if _missing:
                _eprint("")
                _eprint("╔══════════════════════════════════════════════════════════════╗")
                _eprint("║  INSTALLATION INCOMPLETE — agent-plus is not fully set up   ║")
                _eprint("╚══════════════════════════════════════════════════════════════╝")
                _eprint("")
                _eprint(
                    f"  {len(_missing)} primitive(s) are not registered as Claude Code plugins:"
                )
                for _p in _missing:
                    _eprint(f"    • {_p}")
                _eprint("")
                _eprint("  Attempting to register them automatically …")
                _eprint("")

                _fix_ok = True

                # 1. Ensure the marketplace is registered first.
                _mp_cmd = ["claude", "plugin", "marketplace", "add", "osouthgate/agent-plus"]
                _eprint(f"  $ {' '.join(_mp_cmd)}")
                try:
                    _mp_result = subprocess.run(
                        _mp_cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if _mp_result.returncode == 0:
                        _eprint("    ✓ marketplace registered")
                    elif _mp_result.returncode == 1:
                        # exit code 1 typically means "already registered" — OK to continue
                        _eprint("    (already registered)")
                    else:
                        _eprint(f"    ✗ exit {_mp_result.returncode} — marketplace add failed")
                        _fix_ok = False
                except FileNotFoundError:
                    _eprint("    ✗ 'claude' binary not found on PATH")
                    _fix_ok = False

                # 2. Register each missing primitive.
                if _fix_ok:
                    for _p in _missing:
                        _inst_cmd = ["claude", "plugin", "install", f"{_p}@agent-plus"]
                        _eprint(f"  $ {' '.join(_inst_cmd)}")
                        try:
                            _inst = subprocess.run(
                                _inst_cmd,
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                            )
                            if _inst.returncode == 0:
                                _eprint(f"    ✓ {_p} installed")
                            else:
                                _out = (_inst.stdout + _inst.stderr).strip()
                                _eprint(f"    ✗ {_p} — exit {_inst.returncode}: {_out[:120]}")
                                _fix_ok = False
                        except FileNotFoundError:
                            _eprint("    ✗ 'claude' binary not found on PATH")
                            _fix_ok = False
                            break

                _eprint("")
                if _fix_ok:
                    _eprint("  ✓ Plugins registered successfully.")
                    _eprint("")
                    _eprint("  One manual step remains — run this inside Claude Code:")
                    _eprint("")
                    _eprint("    /reload-plugins")
                    _eprint("")
                    _eprint("  Then re-run:  agent-plus-meta init")
                else:
                    _eprint("  ✗ Auto-fix failed.  Run these commands manually:")
                    _eprint("")
                    _eprint("    claude plugin marketplace add osouthgate/agent-plus")
                    for _p in _missing:
                        _eprint(f"    claude plugin install {_p}@agent-plus")
                    _eprint("")
                    _eprint("  Then inside Claude Code run:  /reload-plugins")
                    _eprint("  Then re-run:  agent-plus-meta init")
                _eprint("")
                sys.exit(1)

    # ── 1. legacy bootstrap (preserves existing test contract) ──────────
    workspace, source = h.resolve_workspace(args.dir)
    _safe_mkdir_or_raise(args.dir, workspace, h)

    created: list[str] = []
    skipped: list[str] = []
    for fname, default in h._initial_files().items():
        path = workspace / fname
        if path.is_file():
            skipped.append(fname)
            continue
        path.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")
        created.append(fname)

    project_root = h._detect_project_root(workspace)
    suggested = h.detect_suggested_skills(project_root)

    # ── 2. state detection ──────────────────────────────────────────────
    state = _detect_user_state(workspace, project_root=project_root)

    # ── 3. branch pick ──────────────────────────────────────────────────
    branch, tie_reason = _pick_branch(state)
    if tie_reason:
        _emit_error(
            ERR_AUTO_TIE_BREAK,
            f"branch picked deterministically: {branch}",
            f"reason={tie_reason}",
            recoverable=True, errors_list=errors, interactive=interactive,
        )

    if interactive:
        _branch_desc = {
            "new":       "first-time setup  -- building your workspace",
            "returning": "welcome back  -- refreshing your workspace",
            "power":     "power-user setup  -- full configuration",
            "homeless":  "quick setup  -- no repo context detected",
        }.get(branch, branch)
        _eprint("")
        _eprint(f"agent-plus-meta init  -- {_branch_desc}")
        if tie_reason:
            _eprint(f"  (tie-break: {tie_reason})")
        _eprint("")

    # ── 4. first-win ────────────────────────────────────────────────────
    first_win_result: dict[str, Any]
    if state.get("homeless") and branch == "new":
        first_win_result = {"command": None, "result": "skipped",
                            "reason": "homeless_no_repo_context"}
    else:
        first_win_result = _run_first_win(branch, project_root)
        if first_win_result.get("result") == "failed":
            cmd_name = (first_win_result.get("command") or "").split(" ")[0]
            if cmd_name == "skill-plus":
                _emit_error(
                    ERR_SKILL_PLUS_MISSING,
                    "skill-plus not available for first-win",
                    "claude plugin install skill-plus@agent-plus",
                    recoverable=True, errors_list=errors, interactive=interactive,
                )

    # ── 5. cross-repo discovery + offer ────────────────────────────────
    cross_repo_offered_paths = _discover_recent_claude_repos()
    cross_repo_offered = [str(p) for p in cross_repo_offered_paths]
    cross_repo_accepted: list[str] = []
    cross_repo_results: list[dict[str, Any]] = []

    if interactive and cross_repo_offered:
        _eprint("Cross-repo setup  (optional, but powerful)")
        _eprint("-------------------------------------------")
        _eprint("agent-plus works across all your repos. Selecting repos here")
        _eprint("pre-warms each one so Claude already knows the stack and your")
        _eprint("patterns the next time you open them -- no cold-start scan needed.")
        _eprint("")
        _eprint("Repos from your Claude Code history (last 30 days):")
        for idx, p in enumerate(cross_repo_offered_paths, start=1):
            _eprint(f"  [{idx}] {p}")
        _eprint("  [a] all  [n] none  [m] add a path manually")
        _eprint("")
        try:
            sel = _prompt_line("Which repos should agent-plus set up now? (e.g. 1,3 or a/n): ").strip().lower()
        except KeyboardInterrupt:
            sel = "n"
            _emit_error(
                ERR_CROSS_REPO_INTERRUPTED,
                "cross-repo selection interrupted",
                "re-run agent-plus-meta init to retry",
                recoverable=True, errors_list=errors, interactive=interactive,
            )

        chosen: list[Path] = []
        if sel == "a" or sel == "all":
            chosen = list(cross_repo_offered_paths)
        elif sel in ("n", "none", ""):
            chosen = []
        else:
            # Parse comma-separated indices first.
            for tok in sel.split(","):
                tok = tok.strip()
                if tok.isdigit():
                    i = int(tok)
                    if 1 <= i <= len(cross_repo_offered_paths):
                        chosen.append(cross_repo_offered_paths[i - 1])

        # Manual-paste loop: triggered by exact 'm'/'manual' OR by an 'm'
        # token in a comma-list (e.g. "1,3,m"). Avoid substring matches like
        # "max" or "mine" tripping the loop.
        sel_tokens = {t.strip() for t in sel.split(",")}
        if sel in ("m", "manual") or "m" in sel_tokens:
            _eprint("Paste paths one per line (empty line to finish):")
            while True:
                try:
                    raw = _prompt_line("  path> ")
                except KeyboardInterrupt:
                    _emit_error(
                        ERR_CROSS_REPO_INTERRUPTED,
                        "manual-paste interrupted",
                        "re-run agent-plus-meta init to retry",
                        recoverable=True, errors_list=errors, interactive=interactive,
                    )
                    break
                if not raw.strip():
                    break
                p, warn = _validate_manual_path(raw)
                if p is None:
                    _eprint(f"  ! {warn}, skipped")
                    continue
                if warn and warn.startswith("warn:"):
                    _eprint(f"  ! {p}: no git or project markers detected — scan may yield nothing")
                chosen.append(p)

        # Run scans, streaming progress.
        if chosen:
            _eprint("")
            _eprint(f"Setting up {len(chosen)} repo(s) -- mining patterns and stack info...")
        try:
            for p in chosen:
                cross_repo_accepted.append(str(p))
                _eprint(f"  -> {p}")
                res = _run_skill_plus_scan(p)
                cross_repo_results.append(_cross_repo_result_row(str(p), res))
                if res.get("status") == "ok":
                    msg = _skill_plus_done_stderr_line(res)
                    if msg:
                        _eprint(msg)
                elif res.get("status") == "skipped":
                    _emit_error(
                        ERR_SKILL_PLUS_MISSING,
                        f"cross-repo scan skipped for {p}: {res.get('reason')}",
                        "claude plugin install skill-plus@agent-plus",
                        recoverable=True, errors_list=errors, interactive=interactive,
                    )
                else:
                    _emit_error(
                        ERR_CROSS_REPO_SCAN_FAILED,
                        f"cross-repo scan failed for {p}: {res.get('reason')}",
                        "re-run agent-plus-meta init or scan manually with skill-plus",
                        recoverable=True, errors_list=errors, interactive=interactive,
                    )
        except KeyboardInterrupt:
            _emit_error(
                ERR_CROSS_REPO_INTERRUPTED,
                "cross-repo loop interrupted",
                "completed scans preserved; re-run to finish remaining",
                recoverable=True, errors_list=errors, interactive=interactive,
            )

        # Pause so the user can read scan results before doctor output floods in.
        if chosen and interactive:
            _eprint("")
            _eprint("Repos scanned. Press Enter to continue to setup check...")
            try:
                _prompt_line("")
            except (KeyboardInterrupt, EOFError):
                pass

    elif auto and cross_repo_offered:
        # --auto: silently scan all auto-discovered repos. No manual paste.
        for p in cross_repo_offered_paths:
            cross_repo_accepted.append(str(p))
            res = _run_skill_plus_scan(p)
            cross_repo_results.append(_cross_repo_result_row(str(p), res))
            if res.get("status") == "skipped":
                _emit_error(
                    ERR_SKILL_PLUS_MISSING,
                    f"cross-repo scan skipped for {p}",
                    "claude plugin install skill-plus@agent-plus",
                    recoverable=True, errors_list=errors, interactive=interactive,
                )
            elif res.get("status") == "failed":
                _emit_error(
                    ERR_CROSS_REPO_SCAN_FAILED,
                    f"cross-repo scan failed for {p}: {res.get('reason')}",
                    "re-run with skill-plus on PATH",
                    recoverable=True, errors_list=errors, interactive=interactive,
                )

    # ── 6. doctor finale ────────────────────────────────────────────────
    doctor_verdict = "broken"
    doctor_blocking = False
    doctor_summary = {
        "primitives_installed": 0,
        "primitives_total": 0,
        "envcheck_ready": 0,
        "envcheck_total": 0,
        "marketplaces_installed": 0,
        "stale_services_count": 0,
    }
    try:
        # Build a lightweight Namespace mirroring doctor's expected args.
        doctor_args = argparse.Namespace(
            dir=getattr(args, "dir", None),
            env_file=getattr(args, "env_file", None),
            pretty=False,
        )
        doc_payload = h.cmd_doctor(doctor_args)
        if isinstance(doc_payload, dict):
            doctor_verdict = doc_payload.get("verdict", "broken")
            doctor_blocking = _doctor_has_blocking_issues(doc_payload)
            prims = doc_payload.get("primitives") or {}
            installed = sum(1 for v in prims.values() if v == "installed")
            ec = doc_payload.get("envcheck") or {}
            mp = doc_payload.get("marketplaces") or {}
            doctor_summary = {
                "primitives_installed": installed,
                "primitives_total": len(prims),
                "envcheck_ready": int(ec.get("ready_count", 0)),
                "envcheck_total": int(ec.get("ready_count", 0)) +
                                  int(ec.get("missing_count", 0)),
                "marketplaces_installed": len(mp.get("installed") or []),
                "stale_services_count": len(doc_payload.get("stale_services_entries") or []),
            }
            if interactive:
                try:
                    _eprint("")
                    _eprint(h._render_doctor_pretty(doc_payload))
                except Exception:  # noqa: BLE001 — render is best-effort
                    pass
                if doctor_blocking:
                    _eprint("")
                    _eprint("Required setup steps remain above under [ACTION REQUIRED].")
                    _eprint("Review those lines before continuing.")
                    _eprint("Press Enter when ready...")
                    try:
                        _prompt_line("")
                    except (KeyboardInterrupt, EOFError):
                        pass
    except Exception as e:  # noqa: BLE001 — CRITICAL GAP rescue
        _emit_error(
            ERR_DOCTOR_UNREACHABLE,
            f"doctor finale failed: {e}",
            "Run `agent-plus-meta doctor` manually to inspect state.",
            recoverable=True, errors_list=errors, interactive=interactive,
        )

    # ── 7. feedback invitation + Claude-side CTA (interactive only) ─────
    if interactive:
        _eprint("")
        _eprint("------------------------------------------------------------")
        if doctor_blocking:
            _eprint("Finish setup (required)")
            _eprint("-------------------------")
            _eprint("Scroll up to [ACTION REQUIRED] and run each `claude plugin` command listed.")
            _eprint("Then in Claude Code run: /reload-plugins")
            _eprint("")
        _eprint("Setup complete. What you just got:")
        if cross_repo_accepted:
            _eprint(f"  + {len(cross_repo_accepted)} repo(s) pre-warmed -- Claude starts cold in new repos,")
            _eprint("    now it won't in these ones")
        _eprint("  + doctor check run (see above)")
        _eprint("")
        _eprint("Want to rate this onboarding? "
                "skill-feedback log agent-plus-meta-init "
                "--rating <1-5> --outcome success")
        _eprint("")
        _eprint("+----------------------------------------------------------+")
        if doctor_blocking:
            _eprint("|  Finish plugin registration, then try these first:      |")
        else:
            _eprint("|  You're set up. Here's how to feel the magic:           |")
        _eprint("+----------------------------------------------------------+")
        _eprint("")
        _eprint("  In any Claude Code session, run these commands:")
        _eprint("")
        _eprint("  repo-analyze --pretty")
        _eprint("    -> scans the codebase and gives Claude full context in one call")
        _eprint("")
        _eprint("  diff-summary --base main")
        _eprint("    -> explains what changed on your branch in plain English")
        _eprint("")
        _eprint("  agent-plus-meta init")
        _eprint("    -> set up more repos or refresh this one")
        _eprint("")
        _eprint("  Not working? Run: agent-plus-meta doctor --pretty")
        _eprint("  Unexpected behavior? Ask Claude:")
        _eprint("    \"create a GitHub issue in osouthgate/agent-plus for: <your description>\"")

    # ── 8. envelope ─────────────────────────────────────────────────────
    if errors and any(not e.get("recoverable", True) for e in errors):
        verdict = "error"
    elif errors:
        verdict = "warn"
    else:
        verdict = "success"

    elapsed_ms = int((time.time() - t0) * 1000)
    envelope: dict[str, Any] = {
        # Legacy fields (preserved for back-compat with existing tests).
        "workspace": str(workspace),
        "source": source,
        "created": created,
        "skipped": skipped,
        "suggested_skills": suggested,
        # v0.12.0 frozen schema additions.
        "verdict": verdict,
        "branch_chosen": branch,
        "tie_break_reason": tie_reason,
        "detection": state,
        "cross_repo_offered": cross_repo_offered,
        "cross_repo_accepted": cross_repo_accepted,
        "cross_repo_results": cross_repo_results,
        "doctor_verdict": doctor_verdict,
        "doctor_blocking": doctor_blocking,
        "doctor_summary": doctor_summary,
        "first_win_command": first_win_result.get("command"),
        "first_win_result": first_win_result.get("result", "skipped"),
        "ttl_total_ms": elapsed_ms,
        "errors": errors,
    }

    # ── 9. observability log ────────────────────────────────────────────
    _append_init_log(workspace, {
        "ts": _now_iso(),
        "branch_chosen": branch,
        "detection": state,
        "cross_repo_accepted": cross_repo_accepted,
        "doctor_verdict": doctor_verdict,
    })

    return envelope

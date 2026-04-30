"""skill-plus list — audit existing project skills against the quality contract.

Reads `<project>/.claude/skills/<name>/SKILL.md` for each skill, evaluates
frontmatter completeness, required body sections, and bin/ launcher presence.
Stdlib-only; never modifies a skill on disk.

Helpers (`_git_toplevel`, etc.) are injected by the bin shell.
"""
from __future__ import annotations

import re
from pathlib import Path

# ─── lenient frontmatter aliases ──────────────────────────────────────────────

_DESCRIPTION_KEYS = ("description",)
_WHEN_TO_USE_KEYS = ("when_to_use", "when-to-use", "whenToUse")
_ALLOWED_TOOLS_KEYS = ("allowed_tools", "allowed-tools", "allowedTools")

# Required body section heading regexes (case-insensitive, match a line).
_KILLER_RE = re.compile(r"^\s{0,3}#{1,6}\s+killer\s+command\b", re.IGNORECASE | re.MULTILINE)
_DO_NOT_RE = re.compile(
    r"^\s{0,3}#{1,6}\s+(?:do\s+not\s+use\s+this\s+for|when\s+not\s+to\s+use)\b",
    re.IGNORECASE | re.MULTILINE,
)
_SAFETY_RE = re.compile(r"^\s{0,3}#{1,6}\s+safety(?:\s+rules)?\b", re.IGNORECASE | re.MULTILINE)

# Python 3.11 stdlib top-level package allowlist (for advisory non-stdlib check).
# Names commonly imported in skill launchers; not exhaustive but generous.
_STDLIB_ALLOWLIST = {
    "__future__", "abc", "argparse", "array", "ast", "asyncio", "atexit", "base64",
    "bdb", "binascii", "bisect", "builtins", "bz2", "calendar", "cmath", "cmd",
    "codecs", "collections", "colorsys", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "csv", "ctypes", "curses", "dataclasses",
    "datetime", "decimal", "difflib", "dis", "doctest", "email", "encodings",
    "enum", "errno", "faulthandler", "filecmp", "fileinput", "fnmatch",
    "fractions", "functools", "gc", "getopt", "getpass", "gettext", "glob",
    "graphlib", "grp", "gzip", "hashlib", "heapq", "hmac", "html", "http",
    "imaplib", "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "linecache", "locale", "logging", "lzma", "mailbox", "marshal",
    "math", "mimetypes", "mmap", "multiprocessing", "netrc", "numbers",
    "operator", "optparse", "os", "pathlib", "pickle", "pickletools", "pkgutil",
    "platform", "plistlib", "poplib", "posixpath", "pprint", "profile", "pstats",
    "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random",
    "re", "readline", "reprlib", "resource", "rlcompleter", "runpy", "sched",
    "secrets", "select", "selectors", "shelve", "shlex", "shutil", "signal",
    "site", "smtplib", "sndhdr", "socket", "socketserver", "sqlite3", "ssl",
    "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny", "tarfile",
    "telnetlib", "tempfile", "termios", "test", "textwrap", "threading", "time",
    "timeit", "tkinter", "token", "tokenize", "tomllib", "trace", "traceback",
    "tracemalloc", "tty", "turtle", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
    "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp",
    "zipfile", "zipimport", "zlib", "zoneinfo",
}


# ─── tiny stdlib frontmatter parser ───────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str, str | None]:
    """Return (frontmatter_dict, body_text, error_or_None).

    SKILL.md must start with `---\\n...\\n---\\n`. Each frontmatter line is
    parsed as `key: value` (quote-stripped, leading/trailing whitespace trimmed).
    Lines that don't match `key: value` are skipped silently. If the document
    doesn't open with `---`, the entire text is treated as body and we return
    a parse error so all frontmatter checks fail.
    """
    if not text.startswith("---"):
        return {}, text, "missing opening '---' frontmatter delimiter"

    # Split off the opening delimiter line, then find the next `---` line.
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text, "missing opening '---' frontmatter delimiter"

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text, "missing closing '---' frontmatter delimiter"

    fm_block = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1:])

    fm: dict = {}
    for raw in fm_block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        # Strip matched surrounding quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            fm[key] = val
    return fm, body, None


def _fm_get(fm: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in fm:
            return fm[k]
    return None


# ─── stdlib-only import scan ──────────────────────────────────────────────────

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w\.]*)\s+import\s+|import\s+([A-Za-z_][\w\.]*(?:\s*,\s*[A-Za-z_][\w\.]*)*))",
    re.MULTILINE,
)


def _scan_non_stdlib_imports(py_path: Path) -> list[str]:
    try:
        text = py_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _IMPORT_RE.finditer(text):
        if m.group(1):
            mods = [m.group(1)]
        else:
            mods = [s.strip() for s in m.group(2).split(",") if s.strip()]
        for mod in mods:
            top = mod.split(".", 1)[0]
            if not top or top.startswith("_"):
                # Underscore-prefixed top-levels (e.g. `_typeshed`) are stdlib-internal.
                continue
            if top in _STDLIB_ALLOWLIST:
                continue
            if top in seen:
                continue
            seen.add(top)
            found.append(top)
    return found


# ─── per-skill audit ──────────────────────────────────────────────────────────

def _is_python_launcher(launcher: Path) -> bool:
    """A bin/<name> is a Python launcher if extension is .py OR shebang has 'python'."""
    if launcher.suffix == ".py":
        return True
    try:
        with launcher.open("rb") as fh:
            head = fh.read(256)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    first_line = head.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    return "python" in first_line.lower()


def _audit_skill(skill_dir: Path) -> dict:
    name = skill_dir.name
    skill_md = skill_dir / "SKILL.md"
    bin_dir = skill_dir / "bin"

    result: dict = {
        "name": name,
        "path": str(skill_dir),
    }

    # Frontmatter
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        result["frontmatterParseError"] = f"could not read SKILL.md: {exc}"
        text = ""

    fm, body, fm_err = _parse_frontmatter(text) if text else ({}, "", "empty SKILL.md")
    if fm_err:
        result["frontmatterParseError"] = fm_err

    desc = _fm_get(fm, _DESCRIPTION_KEYS) or ""
    when = _fm_get(fm, _WHEN_TO_USE_KEYS) or ""
    allowed = _fm_get(fm, _ALLOWED_TOOLS_KEYS)

    fm_checks = {
        "description": fm_err is None and len(desc.strip()) >= 10,
        "whenToUse": fm_err is None and len(when.strip()) >= 10,
        "allowedTools": fm_err is None and allowed is not None and allowed != "",
    }

    # Body sections
    body_checks = {
        "killerCommand": bool(_KILLER_RE.search(body)),
        "doNotUseFor": bool(_DO_NOT_RE.search(body)),
        "safety": bool(_SAFETY_RE.search(body)),
    }

    # Bin launchers
    posix_launcher = bin_dir / name
    win_launcher = bin_dir / f"{name}.cmd"
    py_launcher = bin_dir / f"{name}.py"

    posix_exists = posix_launcher.exists()
    win_exists = win_launcher.exists()
    py_exists = py_launcher.exists()

    # Python detection: explicit .py OR posix launcher whose shebang says python.
    is_python = py_exists or (posix_exists and _is_python_launcher(posix_launcher))

    bin_checks = {
        "posixLauncher": posix_exists,
        "windowsLauncher": win_exists,
        "pythonScript": is_python,
    }

    # Non-stdlib imports — advisory only. Look at the .py file if present;
    # else at the posix launcher if it sniffs as python.
    py_target: Path | None = None
    if py_exists:
        py_target = py_launcher
    elif posix_exists and _is_python_launcher(posix_launcher):
        py_target = posix_launcher
    non_stdlib = _scan_non_stdlib_imports(py_target) if py_target else []

    # Score: sum required checks (windowsLauncher is advisory — count it but
    # don't drop a skill below passing because of Windows alone). Per spec we
    # score every check including windowsLauncher; the warn-not-fail nuance is
    # surfaced in the per-check booleans for the caller to display.
    all_checks = {**fm_checks, **body_checks, **bin_checks}
    passed = sum(1 for v in all_checks.values() if v)
    total = len(all_checks)
    score = round(passed / total, 4) if total else 0.0

    result.update({
        "passed": passed,
        "total": total,
        "score": score,
        "frontmatter": fm_checks,
        "body": body_checks,
        "bin": bin_checks,
        "nonStdlibImports": non_stdlib,
    })
    return result


# ─── scope walkers ────────────────────────────────────────────────────────────


def _audit_dir(skills_dir: Path, scope: str | None) -> list[dict]:
    """Walk one skills root and return audited rows. When `scope` is non-None,
    tag each row with it; pass None to preserve the pre-v0.12.0 envelope
    shape (no `scope` key — back-compat for the default `list` invocation)."""
    if not skills_dir.is_dir():
        return []
    rows: list[dict] = []
    try:
        entries = sorted(skills_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").exists():
            continue
        row = _audit_skill(entry)
        if scope is not None:
            row["scope"] = scope
        rows.append(row)
    return rows


def _global_skills_dir(home: Path | None = None) -> Path:
    """Resolve ~/.claude/skills/ — Claude Code's user-level skill directory."""
    return (home or Path.home()) / ".claude" / "skills"


def _mark_collisions(rows: list[dict]) -> None:
    """In-place: add `collision: true` to every row whose `name` appears in
    more than one scope. The wizard's SKILL-AUTHOR branch surfaces these so
    users know which name to disambiguate (resolved by v0.14.0's
    `skill-plus collisions` subcommand)."""
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)
    for name, group in by_name.items():
        if len(group) > 1:
            for r in group:
                r["collision"] = True


# ─── entry point ──────────────────────────────────────────────────────────────

def run(args, emit_fn) -> int:
    # 1. Resolve project
    if getattr(args, "project", None):
        project = Path(args.project).expanduser().resolve()
    else:
        top = _git_toplevel()  # injected
        project = (top if top is not None else Path.cwd()).resolve()

    skills_dir = project / ".claude" / "skills"
    include_global = bool(getattr(args, "include_global", False))

    # Tag scopes only when --include-global is on, so the default envelope
    # stays byte-identical to pre-v0.12.0 consumers.
    project_scope = "project" if include_global else None
    project_rows = _audit_dir(skills_dir, scope=project_scope)

    global_dir: Path | None = None
    global_rows: list[dict] = []
    if include_global:
        global_dir = _global_skills_dir()
        global_rows = _audit_dir(global_dir, scope="global")

    skills = project_rows + global_rows

    if include_global:
        _mark_collisions(skills)

    # Sort: worst score first, then by name. Stable across scopes.
    skills.sort(key=lambda s: (s.get("score", 0.0), s.get("name", "")))

    payload: dict = {
        "project": str(project),
        "skillsDir": str(skills_dir),
        "skillsTotal": len(skills),
        "skills": skills,
    }

    if include_global:
        collisions = sorted({r["name"] for r in skills if r.get("collision")})
        payload["scopes"] = {
            "project": {
                "path": str(skills_dir),
                "count": len(project_rows),
            },
            "global": {
                "path": str(global_dir),
                "count": len(global_rows),
            },
        }
        payload["collisions"] = collisions

    if not skills_dir.is_dir() and not skills:
        # Preserve the existing "nothing here" envelope so downstream
        # consumers (and the wizard's branch logic) can still detect the
        # empty case identically.
        payload["note"] = "no .claude/skills/ directory"

    emit_fn(payload)
    return 0

"""skill-plus scaffold — write .claude/skills/<name>/ skeleton.

Forces the agent-plus framework quality contract: non-skippable required slots,
stdlib bin launchers (POSIX + Windows), envelope-aware Python entry point.

Required slots (all must be filled OR seeded from --from-candidate):
  - description       (frontmatter, ≥10 chars)
  - when_to_use       (frontmatter, ≥10 chars)
  - killer_command    (## Killer command body, ≥5 chars)
  - do_not_use_for    (## Do NOT use this for body, ≥1 bullet)

Refuses to write if the target dir exists, unless --force.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Optional

# Helpers _git_toplevel, candidates_log_path injected by bin/skill-plus loader.

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _resolve_target_root() -> Path:
    top = _git_toplevel()  # noqa: F821 — injected
    if top is not None:
        return Path(top).resolve()
    return Path.cwd().resolve()


def _read_candidate(cand_id: str) -> Optional[dict]:
    path = candidates_log_path()  # noqa: F821 — injected
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("id") == cand_id:
            return obj
    return None


def _yaml_scalar(s: str) -> str:
    """Quote-safe single-line YAML scalar. Hand-rolled (no pyyaml)."""
    if s is None:
        return '""'
    needs_quote = (
        ":" in s or "#" in s or s.startswith(("-", "?", "!", "&", "*", "[", "]", "{", "}", "|", ">", "%", "@", "`", '"', "'"))
        or s.strip() != s
        or s == ""
    )
    if needs_quote:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _bullet_list(raw: str) -> list[str]:
    """Split `--do-not-use-for` value on newline or `;` into trimmed non-empty bullets."""
    if not raw:
        return []
    parts: list[str] = []
    for chunk in re.split(r"[\n;]+", raw):
        c = chunk.strip().lstrip("-").strip()
        if c:
            parts.append(c)
    return parts


# ─── templates ────────────────────────────────────────────────────────────────

DEFAULT_SAFETY_RULES = (
    "- Read-first; writes gated behind explicit `--write` / `--apply` flags.\n"
    "- Secrets never logged or printed; use the shared redactor.\n"
    "- Stdlib only; no network unless the skill's job IS network-IO.\n"
)

DEFAULT_ARCHITECTURE = (
    "- `bin/<name>` (POSIX) and `bin/<name>.cmd` (Windows) thin launchers exec `bin/<name>.py`.\n"
    "- `bin/<name>.py` is stdlib-only Python, emits a JSON envelope with `tool: {name, version}`.\n"
    "- Layered config: `--env-file` → `<repo>/.env.local` → `<repo>/.env` → `~/.agent-plus/.env` → shell.\n"
)


def _render_skill_md(name: str, description: str, when_to_use: str,
                     killer_command: str, do_not_use_for: list[str]) -> str:
    fm_lines = [
        "---",
        f"name: {_yaml_scalar(name)}",
        f"description: {_yaml_scalar(description)}",
        f"when_to_use: {_yaml_scalar(when_to_use)}",
        "allowed-tools: Bash",
        "---",
    ]
    bullets = "\n".join(f"- {b}" for b in do_not_use_for)
    body = f"""
# {name}

## Killer command

```bash
{killer_command}
```

## Do NOT use this for

{bullets}

## Safety rules

{DEFAULT_SAFETY_RULES}
## Architecture

{DEFAULT_ARCHITECTURE}"""
    return "\n".join(fm_lines) + body


def _render_posix_launcher(name: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        f'exec python3 "$(dirname "$0")/{name}.py" "$@"\n'
    )


def _render_windows_launcher(name: str) -> str:
    # CRLF-friendly is fine here; .cmd files tolerate either.
    return (
        "@echo off\r\n"
        f'python "%~dp0{name}.py" %*\r\n'
    )


# Generated bin/<name>.py — self-contained, no imports from skill-plus.
# Kept under 200 lines on purpose. Skeleton, not finished.
_GENERATED_PY_TEMPLATE = r'''#!/usr/bin/env python3
"""{name} — scaffolded by skill-plus. Stdlib only.

Replace the `do` subcommand body with the skill's real work. Keep the envelope
shape (`tool`, `--pretty`/`--json`, `--output`, `--shape-depth`) intact so this
skill plays nicely with agent-plus consumers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

TOOL_NAME = "{name}"
TOOL_VERSION = "0.1.0"


# ─── secret redaction ─────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{{20,}}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{{20,}}"),
    re.compile(r"AKIA[0-9A-Z]{{16}}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{{20,}}"),
    re.compile(r"sk-or-[A-Za-z0-9_-]{{20,}}"),
    re.compile(r"sk-[A-Za-z0-9_-]{{20,}}"),
    re.compile(r"AIza[0-9A-Za-z_-]{{35}}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{{10,}}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{{10,}}\.eyJ[A-Za-z0-9_-]{{10,}}\.[A-Za-z0-9_-]{{10,}}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{{20,}}", re.IGNORECASE),
    re.compile(r"(postgres|mysql|redis|mongodb(?:\+srv)?)://[^\s'\"]+@", re.IGNORECASE),
    re.compile(r"--(?:password|token|secret|api[-_]?key)[= ]\S+", re.IGNORECASE),
]


def scrub_text(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    out = s
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


# ─── layered env resolver ─────────────────────────────────────────────────────


def _parse_dotenv(path: Path) -> dict:
    out: dict = {{}}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        out[k.strip()] = v
    return out


def _git_toplevel() -> Optional[Path]:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    line = r.stdout.strip()
    return Path(line) if line else None


def resolve_env(env_file: Optional[str]) -> dict:
    layers: list[dict] = [dict(os.environ)]
    layers.append(_parse_dotenv(Path.home() / ".agent-plus" / ".env"))
    top = _git_toplevel() or Path.cwd()
    layers.append(_parse_dotenv(top / ".env"))
    layers.append(_parse_dotenv(top / ".env.local"))
    if env_file:
        layers.append(_parse_dotenv(Path(env_file).expanduser()))
    merged: dict = {{}}
    for layer in layers:
        merged.update(layer)
    return merged


# ─── envelope ─────────────────────────────────────────────────────────────────


def _tool_meta() -> dict:
    return {{"name": TOOL_NAME, "version": TOOL_VERSION}}


def _shape_value(v: Any, depth: int) -> dict:
    if isinstance(v, str):
        return {{"type": "string", "length": len(v)}}
    if isinstance(v, bool):
        return {{"type": "boolean"}}
    if isinstance(v, (int, float)):
        return {{"type": "number"}}
    if isinstance(v, list):
        return {{"type": "list", "length": len(v)}}
    if isinstance(v, dict):
        return {{"type": "dict", "keys": len(v)}}
    if v is None:
        return {{"type": "null"}}
    return {{"type": type(v).__name__}}


def _payload_shape(payload: dict, depth: int) -> dict:
    return {{k: _shape_value(v, depth) for k, v in payload.items() if k != "tool"}}


def emit(payload: Any, *, pretty: bool, output: Optional[str], shape_depth: int) -> None:
    if isinstance(payload, dict) and "tool" not in payload:
        payload = {{"tool": _tool_meta(), **payload}}
    if output and isinstance(payload, dict):
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, default=str)
        path.write_text(text, encoding="utf-8")
        summary = {{
            "tool": payload.get("tool", _tool_meta()),
            "payloadPath": str(path),
            "bytes": len(text.encode("utf-8")),
            "payloadKeys": [k for k in payload.keys() if k != "tool"],
            "payloadShape": _payload_shape(payload, shape_depth),
        }}
        payload = summary
    if pretty:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(json.dumps(payload, default=str))


# ─── subcommands ──────────────────────────────────────────────────────────────


def cmd_do(args) -> dict:
    return {{
        "ok": True,
        "command": "do",
        "note": "TODO: replace with real implementation",
        "args": list(getattr(args, "rest", []) or []),
    }}


# ─── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL_NAME, description="{description}")
    p.add_argument("--version", action="version", version=f"{{TOOL_NAME}} {{TOOL_VERSION}}")
    p.add_argument("--env-file", default=None)
    p.add_argument("--output", default=None)
    p.add_argument("--shape-depth", type=int, choices=[1, 2, 3], default=3, dest="shape_depth")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", dest="pretty", action="store_false", default=False)
    fmt.add_argument("--pretty", dest="pretty", action="store_true")
    sub = p.add_subparsers(dest="command")
    sd = sub.add_parser("do", help="run the skill's killer command (stub)")
    sd.add_argument("rest", nargs=argparse.REMAINDER)
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        print("usage: {name} do [args...]", file=sys.stderr)
        return 2
    if args.command == "do":
        payload = cmd_do(args)
    else:
        payload = {{"ok": False, "error": "unknown_command", "command": args.command}}
    emit(payload, pretty=args.pretty, output=args.output, shape_depth=args.shape_depth)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _render_python_entry(name: str, description: str) -> str:
    # Description could contain quotes; sanitize for safe embedding into a docstring/argparse.
    safe_desc = description.replace('"', "'")
    return _GENERATED_PY_TEMPLATE.format(name=name, description=safe_desc)


# ─── orchestration ────────────────────────────────────────────────────────────


def _write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use the binary mode for the windows .cmd to preserve CRLFs we set explicitly,
    # and a normal write_text otherwise.
    if "\r\n" in content:
        path.write_bytes(content.encode("utf-8"))
    else:
        path.write_text(content, encoding="utf-8")
    if executable:
        try:
            mode = path.stat().st_mode
            path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass  # Windows or restricted FS — best effort.


def run(args, emit_fn):
    name: str = args.name
    if not _NAME_RE.match(name or ""):
        emit_fn({
            "ok": False,
            "error": "invalid_name",
            "name": name,
            "hint": "skill name must match ^[a-z][a-z0-9-]{0,63}$ (lowercase, hyphenated)",
        })
        return 2

    target_root = _resolve_target_root()
    skill_dir = target_root / ".claude" / "skills" / name

    if skill_dir.exists() and not getattr(args, "force", False):
        emit_fn({
            "ok": False,
            "error": "skill_exists",
            "path": str(skill_dir),
            "hint": "pass --force to overwrite",
        })
        return 2

    description = (args.description or "").strip()
    when_to_use = (args.when_to_use or "").strip()
    killer_command = (args.killer_command or "").strip()
    do_not_use_for_raw = args.do_not_use_for or ""
    bullets = _bullet_list(do_not_use_for_raw)

    from_candidate: Optional[str] = getattr(args, "from_candidate", None)
    cand: Optional[dict] = None
    if from_candidate:
        cand = _read_candidate(from_candidate)
        if cand is None:
            emit_fn({
                "ok": False,
                "error": "candidate_not_found",
                "id": from_candidate,
                "hint": "id must match a row in the candidates log; run `skill-plus propose` to list",
            })
            return 2
        # Seed only the missing slots.
        if not killer_command:
            examples = cand.get("examples") or []
            if examples:
                killer_command = str(examples[0]).strip()
        if not description:
            count = cand.get("count", 0)
            sess_list = cand.get("sessions") or []
            sess_n = len(sess_list) if isinstance(sess_list, list) else int(sess_list or 0)
            key = cand.get("key", "")
            description = (
                f"Wraps repeated `{key}` invocations into a one-shot command. "
                f"{count} uses across {sess_n} sessions."
            )

    # Validate slot lengths/presence.
    missing: list[str] = []
    if len(description) < 10:
        missing.append("description")
    if len(when_to_use) < 10:
        missing.append("when_to_use")
    if len(killer_command) < 5:
        missing.append("killer_command")
    if not bullets:
        missing.append("do_not_use_for")

    if missing:
        emit_fn({
            "ok": False,
            "error": "missing_required_slots",
            "missing": missing,
            "hint": "pass --killer-command/--description/--when-to-use/--do-not-use-for, or use --from-candidate to seed from a mined pattern",
        })
        return 2

    # Write files.
    skill_md = skill_dir / "SKILL.md"
    bin_dir = skill_dir / "bin"
    posix_launcher = bin_dir / name
    cmd_launcher = bin_dir / f"{name}.cmd"
    py_entry = bin_dir / f"{name}.py"

    _write_text(skill_md, _render_skill_md(name, description, when_to_use, killer_command, bullets))
    _write_text(posix_launcher, _render_posix_launcher(name), executable=True)
    _write_text(cmd_launcher, _render_windows_launcher(name))
    _write_text(py_entry, _render_python_entry(name, description), executable=True)

    files_written = [str(skill_md), str(posix_launcher), str(cmd_launcher), str(py_entry)]

    emit_fn({
        "ok": True,
        "name": name,
        "path": str(skill_dir),
        "filesWritten": files_written,
        "requiredSlotsFilled": {
            "description": True,
            "when_to_use": True,
            "killer_command": True,
            "do_not_use_for": True,
        },
        "fromCandidate": from_candidate,
    })
    return 0

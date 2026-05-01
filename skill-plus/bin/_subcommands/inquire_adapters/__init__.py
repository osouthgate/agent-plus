"""Transcript adapter framework for skill-plus inquire (Gate A.1).

Each adapter is a callable: `adapter(path: Path) -> Iterator[Tuple]` that
yields canonical-shape tuples:

    (timestamp_iso, source_path_str, tool_name, command_string, args_dict)

- `timestamp_iso` is an ISO-8601 string ("" if unknown).
- `source_path_str` identifies the file the tuple came from.
- `tool_name` is the harness tool name (e.g. "Bash").
- `command_string` is the literal command/text invoked (may be "").
- `args_dict` carries extra structured args from the call (`description`, etc).

A.2 (clustering) consumes these tuples; A.1 just collects + stashes them.

Stdlib only. No subprocess. No network. ASCII-only stderr/stdout.

Auto-discovery walks well-known roots; user extension config can register
additional roots and custom adapters. Privacy posture: data is the user's
own local files; we do not transmit, we do not log raw prompt text in
envelopes (clustering will normalise to argument shapes + counts).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Tuple

# Canonical tuple shape: (timestamp, source_path, tool, command, args).
TranscriptTuple = Tuple[str, str, str, str, dict]
Adapter = Callable[[Path], Iterator[TranscriptTuple]]

# Module-relative imports of bundled adapters. We import lazily so a broken
# adapter can't take down the whole inquire run.
_BUILTIN_ADAPTER_NAMES = ("claude_code", "gstack", "codex", "cursor")


def _load_builtin(name: str) -> Optional[Adapter]:
    here = Path(__file__).resolve().parent
    candidate = here / f"{name}.py"
    if not candidate.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            f"_skill_plus_inquire_adapter_{name}", candidate
        )
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001 — adapter load is best-effort.
        return None
    fn = getattr(mod, "iter_tuples", None)
    if not callable(fn):
        return None
    return fn


# Default discovery roots. Each entry is (format-name, root-path-template,
# glob-pattern). `~` is expanded at call time.
DISCOVERY_ROOTS: list[tuple[str, str, str]] = [
    ("claude_code", "~/.claude/projects", "*/*.jsonl"),
    ("gstack", "~/.gstack/projects", "*/*.jsonl"),
    ("codex", "~/.codex/sessions", "*.jsonl"),
    ("cursor", "~/.cursor/chats", "*.jsonl"),
]


def _user_config_path() -> Path:
    return Path.home() / ".agent-plus" / "inquire-sources.json"


def _user_adapters_dir() -> Path:
    return Path.home() / ".agent-plus" / "inquire-adapters"


def load_user_config() -> dict:
    """Read ~/.agent-plus/inquire-sources.json. Returns {} on any failure
    (missing, malformed). Schema:

      {"sources": [{"name": str, "root": str, "format": str}, ...]}
    """
    p = _user_config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_user_adapters() -> dict[str, Adapter]:
    """Load any user-supplied adapters from ~/.agent-plus/inquire-adapters/<name>.py.

    Each module must expose `iter_tuples(path: Path) -> Iterator[tuple]`.
    Returns a {format-name: adapter_callable} dict. Failures are silent.
    """
    out: dict[str, Adapter] = {}
    d = _user_adapters_dir()
    if not d.is_dir():
        return out
    for entry in sorted(d.glob("*.py")):
        if entry.is_symlink():
            continue  # don't follow symlinks — adapter loader is not a sandbox
        name = entry.stem
        try:
            spec = importlib.util.spec_from_file_location(
                f"_skill_plus_inquire_user_adapter_{name}", entry
            )
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001
            continue
        fn = getattr(mod, "iter_tuples", None)
        if callable(fn):
            out[name] = fn
    return out


def build_registry() -> dict[str, Adapter]:
    """Assemble {format-name: adapter} from builtins + user adapters.
    User adapters override builtins of the same name."""
    reg: dict[str, Adapter] = {}
    for name in _BUILTIN_ADAPTER_NAMES:
        fn = _load_builtin(name)
        if fn is not None:
            reg[name] = fn
    reg.update(load_user_adapters())
    return reg


def _expand_root(template: str) -> Path:
    return Path(os.path.expanduser(template))


def discover_files(extra_sources: Optional[Iterable[dict]] = None
                   ) -> list[tuple[str, Path]]:
    """Walk default + user-configured roots, returning [(format, file_path), ...].
    Missing roots are skipped silently.
    """
    out: list[tuple[str, Path]] = []
    for fmt, root_tmpl, pattern in DISCOVERY_ROOTS:
        root = _expand_root(root_tmpl)
        if not root.is_dir():
            continue
        try:
            for f in root.glob(pattern):
                if f.is_file():
                    out.append((fmt, f))
        except OSError:
            continue
    if extra_sources:
        for src in extra_sources:
            if not isinstance(src, dict):
                continue
            fmt = src.get("format")
            root_str = src.get("root")
            if not fmt or not root_str:
                continue
            root = _expand_root(root_str)
            if not root.is_dir():
                continue
            try:
                for f in root.rglob("*.jsonl"):
                    if f.is_file():
                        out.append((fmt, f))
            except OSError:
                continue
    return out


def collect_tuples(*, max_files: int = 500,
                   max_tuples_per_file: int = 10000
                   ) -> dict:
    """Run auto-discovery + adapters and return a structured result.

    Shape:
      {
        "files_scanned": int,
        "files_skipped": int,
        "tuples": [TranscriptTuple, ...],
        "by_format": {format-name: count},
        "errors": [str, ...]   # ASCII-only short messages
      }

    A.1 just stores; A.2 will consume `tuples`.
    """
    cfg = load_user_config()
    extras = cfg.get("sources") if isinstance(cfg.get("sources"), list) else None
    registry = build_registry()
    files = discover_files(extra_sources=extras)
    tuples: list[TranscriptTuple] = []
    by_fmt: dict[str, int] = {}
    errors: list[str] = []
    scanned = 0
    skipped = 0
    for fmt, fpath in files[:max_files]:
        adapter = registry.get(fmt)
        if adapter is None:
            skipped += 1
            continue
        scanned += 1
        n = 0
        try:
            for t in adapter(fpath):
                if not isinstance(t, tuple) or len(t) != 5:
                    continue
                tuples.append(t)
                n += 1
                by_fmt[fmt] = by_fmt.get(fmt, 0) + 1
                if n >= max_tuples_per_file:
                    break
        except Exception as e:  # noqa: BLE001 — fail-soft per file.
            errors.append(f"{fmt}: {type(e).__name__}: {str(e)[:80]}")
            continue
    return {
        "files_scanned": scanned,
        "files_skipped": skipped,
        "tuples": tuples,
        "by_format": by_fmt,
        "errors": errors,
    }


__all__ = [
    "TranscriptTuple",
    "Adapter",
    "DISCOVERY_ROOTS",
    "build_registry",
    "discover_files",
    "collect_tuples",
    "load_user_config",
    "load_user_adapters",
]

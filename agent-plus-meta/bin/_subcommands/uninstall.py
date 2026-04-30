"""v0.15.0 — `agent-plus-meta uninstall` subcommand.

Safe-by-default uninstall with opt-in escalation. The default scope removes
only the 5 framework primitive bins. `--workspace`, `--marketplaces`,
`--all`, and `--purge` escalate explicitly. `--purge` is the one-way door:
it always prompts for the literal word `PURGE`, even under
`--non-interactive`.

Stdlib only. Pathlib everywhere. UTF-8 file I/O. `subprocess.run([list])`
shape only (we don't shell out at all today). `bind(host)` mirrors the
v0.12.0 init / v0.13.5 upgrade pattern so this submodule can reuse the
parent bin's helpers without circular imports.

Frozen JSON envelope schema is the public contract — see
`agent-plus-meta/docs/uninstall-envelope.md`. Implementation does NOT
emit the reserved `kind` slots (`settings_hook`, `daemon_pid`,
`migration_state`) in v0.15.0; the schema reservation is for additive
v0.16+ use.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Optional


# ─── host bindings (mirrors init.py / upgrade.py) ────────────────────────────

_host: Any = None


def bind(host: Any) -> None:
    global _host
    _host = host


def _h() -> Any:
    if _host is None:  # pragma: no cover — guard
        raise RuntimeError("uninstall.bind() not called by host bin")
    return _host


# ─── constants ───────────────────────────────────────────────────────────────

PRIMITIVES = (
    "agent-plus-meta",
    "repo-analyze",
    "diff-summary",
    "skill-feedback",
    "skill-plus",
)


# ─── path / scope resolution ─────────────────────────────────────────────────


def _resolve_install_dir(args: argparse.Namespace) -> Path:
    """Resolve INSTALL_DIR with the same precedence as install.sh:
    1. `--install-dir PATH` flag
    2. `AGENT_PLUS_INSTALL_DIR` env var
    3. `~/.local/bin`
    """
    explicit = getattr(args, "install_dir", None)
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("AGENT_PLUS_INSTALL_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".local" / "bin").resolve()


def _user_workspace() -> Path:
    return (Path.home() / ".agent-plus").resolve()


def _repo_workspace() -> Optional[Path]:
    """Resolve <git-toplevel>/.agent-plus/ if cwd is in a repo, else None."""
    host = _h()
    try:
        top = host._git_toplevel()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        top = None
    if top is None:
        return None
    return (Path(top) / ".agent-plus").resolve()


def _marketplaces_state_root() -> Path:
    """Reuse host's marketplace state root resolver."""
    host = _h()
    return host._marketplaces_root()  # noqa: SLF001


def _claude_plugins_cache_dir() -> Path:
    host = _h()
    return host._claude_plugins_cache_dir()  # noqa: SLF001


# ─── manifest builder ────────────────────────────────────────────────────────


def _scope_from_args(args: argparse.Namespace) -> str:
    """Reduce flag combination to a single mode string."""
    if getattr(args, "purge", False):
        return "purge"
    workspace = bool(getattr(args, "workspace", False))
    marketplaces = bool(getattr(args, "marketplaces", False))
    all_flag = bool(getattr(args, "all", False))
    if all_flag or (workspace and marketplaces):
        return "all"
    if workspace:
        return "workspace"
    if marketplaces:
        return "marketplaces"
    return "default"


def _list_claude_plugins() -> list[dict]:
    """Walk ~/.claude/plugins/cache/ for plugins tagged @agent-plus.

    Returns a list of {name, path, hint} dicts. Best-effort — never raises.
    """
    out: list[dict] = []
    cache = _claude_plugins_cache_dir()
    if not cache.is_dir():
        return out
    try:
        entries = sorted(p for p in cache.iterdir() if p.is_dir())
    except OSError:
        return out
    for entry in entries:
        # Tag detection: a plugin is "@agent-plus"-tagged if its
        # .claude-plugin/plugin.json declares a marketplace under
        # ~/.agent-plus/marketplaces/, OR its directory carries the suffix
        # "@agent-plus" (Claude Code's storage convention).
        name = entry.name
        # Strip @agent-plus suffix if present in dir name; otherwise inspect
        # plugin.json for keywords/source pointing at agent-plus.
        is_agent_plus = False
        bare_name = name
        if name.endswith("@agent-plus"):
            bare_name = name[: -len("@agent-plus")]
            is_agent_plus = True
        else:
            plugin_json = entry / ".claude-plugin" / "plugin.json"
            if plugin_json.is_file():
                try:
                    data = json.loads(plugin_json.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict):
                    keywords = data.get("keywords") or []
                    if isinstance(keywords, list) and "agent-plus" in keywords:
                        is_agent_plus = True
        if not is_agent_plus:
            continue
        out.append({
            "name": bare_name,
            "path": str(entry),
            "hint": f"claude plugin uninstall {bare_name}@agent-plus",
        })
    return out


def _list_marketplace_states() -> list[dict]:
    """Return [{slug, path}, ...] for each installed marketplace."""
    out: list[dict] = []
    root = _marketplaces_state_root()
    if not root.is_dir():
        return out
    try:
        entries = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for entry in entries:
        # Dirname is `<owner>-<name>` per _marketplace_state_dir; the owner
        # is the leading slug component before the FIRST hyphen the user
        # could not have used in their owner name. We can't recover slug
        # safely from arbitrary names, so we read the meta file when present.
        name = entry.name
        slug = name.replace("-", "/", 1)  # best-effort fallback
        meta_path = entry / ".agent-plus-meta.json"
        if meta_path.is_file():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict):
                meta_slug = data.get("slug")
                if isinstance(meta_slug, str) and "/" in meta_slug:
                    slug = meta_slug
        out.append({"slug": slug, "path": str(entry)})
    return out


def build_manifest(*, scope: str, install_dir: Path) -> list[dict]:
    """Build the ordered `paths[]` list for the requested scope.

    Pure (no side effects). Each entry has a pre-execution status of either
    `would_remove`, `missing`, `skipped`, or `kept`. The executor mutates
    these post-attempt.

    Ordering: default tier → workspace tier → marketplaces tier → out_of_scope.
    """
    paths: list[dict] = []

    # ── default tier: 5 primitive bins ──────────────────────────────────────
    for name in PRIMITIVES:
        target = install_dir / name
        exists = target.is_file() or target.is_symlink()
        paths.append({
            "path": str(target),
            "kind": "primitive_bin",
            "scope": "default",
            "status": "would_remove" if exists else "missing",
        })

    # ── workspace tier ──────────────────────────────────────────────────────
    in_workspace_scope = scope in ("workspace", "all", "purge")
    workspace_targets: list[Path] = []
    repo_ws = _repo_workspace()
    if repo_ws is not None:
        workspace_targets.append(repo_ws)
    workspace_targets.append(_user_workspace())
    for ws in workspace_targets:
        exists = ws.is_dir()
        if in_workspace_scope:
            status = "would_remove" if exists else "missing"
        else:
            status = "skipped"
        entry = {
            "path": str(ws),
            "kind": "workspace",
            "scope": "workspace",
            "status": status,
        }
        if not in_workspace_scope and exists:
            entry["note"] = "Pass --workspace to remove."
        paths.append(entry)

    # ── marketplaces tier ───────────────────────────────────────────────────
    in_market_scope = scope in ("marketplaces", "all", "purge")
    for state in _list_marketplace_states():
        if in_market_scope:
            status = "would_remove"
        else:
            status = "skipped"
        entry = {
            "path": state["path"],
            "kind": "marketplace_state",
            "scope": "marketplaces",
            "slug": state["slug"],
            "status": status,
        }
        if not in_market_scope:
            entry["note"] = "Pass --marketplaces to remove."
        paths.append(entry)

    # ── out_of_scope tier: claude plugins (always list-only, kept) ──────────
    for plugin in _list_claude_plugins():
        paths.append({
            "path": plugin["path"],
            "kind": "claude_plugin",
            "scope": "out_of_scope",
            "status": "kept",
            "hint": plugin["hint"],
        })

    return paths


# ─── confirmation layer ──────────────────────────────────────────────────────


def _read_purge_confirmation() -> bool:
    """Return True iff the user typed literal `PURGE`. EOF / KeyboardInterrupt
    aborts."""
    try:
        line = input("Type 'PURGE' to confirm: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return line.strip() == "PURGE"


def _print_default_preview(paths: list[dict], scope: str) -> None:
    """Human-readable manifest preview (stderr-style; written to stderr to
    keep stdout reserved for the JSON envelope when --json is set)."""
    out = sys.stderr
    out.write("\nagent-plus uninstall\n")
    out.write("====================\n")

    will_remove = [p for p in paths if p["status"] == "would_remove"]
    missing = [p for p in paths if p["status"] == "missing"]
    skipped = [p for p in paths if p["status"] == "skipped"]
    kept = [p for p in paths if p["status"] == "kept"]

    if will_remove:
        out.write("\nThe following will be removed:\n\n")
        for p in will_remove:
            out.write(f"  {p['path']}\n")
    else:
        out.write("\nNothing to remove (all targets already missing).\n")

    if missing:
        out.write("\nAlready missing:\n\n")
        for p in missing:
            out.write(f"  {p['path']}\n")

    if skipped:
        out.write("\nThe following will be KEPT (rerun with the listed flag to remove):\n\n")
        for p in skipped:
            note = p.get("note", "")
            label = ""
            if p["kind"] == "workspace":
                label = "[--workspace]"
            elif p["kind"] == "marketplace_state":
                label = "[--marketplaces]"
            line = f"  {p['path']:<60s} {label}".rstrip()
            out.write(line + "\n")
            if note:
                out.write(f"    ({note})\n")

    if kept:
        out.write("\nClaude Code plugins (we don't touch these — copy-paste the hint):\n\n")
        for p in kept:
            out.write(f"  {p['path']}\n")
            hint = p.get("hint")
            if hint:
                out.write(f"    {hint}\n")

    if scope == "purge":
        out.write(
            "\n[!]  --purge will remove all agent-plus state including your .agent-plus/ workspace,\n"
            "    feedback logs, and marketplace state. This is not reversible.\n\n"
            "    The following will NOT be touched:\n"
            "      - ~/.claude/projects/         (Claude Code session history)\n"
            "      - ~/.claude/skills/           (your authored skills)\n"
            "      - <repo>/.claude/skills/      (per-repo authored skills)\n"
            "      - ~/.claude/plugins/cache/    (Claude Code plugin cache)\n\n"
        )
    out.flush()


# ─── executor ────────────────────────────────────────────────────────────────


def _safe_unlink(path: Path) -> tuple[bool, Optional[str]]:
    """Unlink a file (or symlink). Never traverses. Returns (ok, error)."""
    try:
        path.unlink()
        return True, None
    except FileNotFoundError:
        return False, "missing"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"


def _is_self(path: Path) -> bool:
    """Return True if `path` resolves to the currently-running bin file."""
    try:
        host = _h()
        host_file = Path(host.__file__).resolve()
        return path.resolve() == host_file
    except Exception:  # noqa: BLE001
        return False


def execute_removals(paths: list[dict]) -> list[dict]:
    """Walk the manifest, performing the removal for each `would_remove`
    entry. Mutates and returns the same list with post-execution statuses."""
    host = _h()
    is_windows = platform.system() == "Windows"
    for entry in paths:
        status = entry.get("status")
        if status != "would_remove":
            # missing / skipped / kept are terminal pre-execution states.
            continue
        kind = entry["kind"]
        path = Path(entry["path"])
        if kind == "primitive_bin":
            # Self-delete handling (E1): on Windows, `os.remove()` on the
            # running .py/.exe may fail with PermissionError. Emit a
            # structured error with a manual hint; succeed for the others.
            if is_windows and _is_self(path):
                ok, err = _safe_unlink(path)
                if ok:
                    entry["status"] = "removed"
                else:
                    entry["status"] = "error"
                    entry["error"] = err or "self_delete_locked"
                    entry["note"] = (
                        "remove this file manually after the process exits"
                    )
                continue
            ok, err = _safe_unlink(path)
            if ok:
                entry["status"] = "removed"
            elif err == "missing":
                entry["status"] = "missing"
            else:
                entry["status"] = "error"
                entry["error"] = err
        elif kind in ("workspace", "marketplace_state"):
            if not path.is_dir():
                entry["status"] = "missing"
                continue
            try:
                host._rmtree_force(path)  # noqa: SLF001
                entry["status"] = "removed"
            except OSError as e:
                entry["status"] = "error"
                entry["error"] = f"{type(e).__name__}: {e}"
        else:
            # claude_plugin and any future kinds: list-only, no removal.
            continue
    return paths


# ─── envelope assembly ───────────────────────────────────────────────────────


def _summary(paths: list[dict]) -> dict:
    counts = {"removed": 0, "missing": 0, "skipped": 0, "kept": 0, "errors": 0}
    for p in paths:
        st = p.get("status")
        if st == "removed":
            counts["removed"] += 1
        elif st == "missing":
            counts["missing"] += 1
        elif st == "skipped":
            counts["skipped"] += 1
        elif st == "kept":
            counts["kept"] += 1
        elif st == "error":
            counts["errors"] += 1
    return counts


def _build_envelope(
    *,
    scope: str,
    dry_run: bool,
    interactive: bool,
    user_confirmed: bool,
    install_dir: Path,
    paths: list[dict],
    errors: list[dict],
) -> dict:
    host = _h()
    summary = _summary(paths)
    hints = [p["hint"] for p in paths if p.get("kind") == "claude_plugin" and p.get("hint")]
    next_steps = [
        "Re-install: curl -fsSL https://raw.githubusercontent.com/osouthgate/agent-plus/main/install.sh | sh"
    ]
    return {
        "tool": host._tool_meta(),  # noqa: SLF001
        "action": "uninstall",
        "mode": scope,
        "dry_run": dry_run,
        "interactive": interactive,
        "user_confirmed": user_confirmed,
        "install_dir": str(install_dir),
        "paths": paths,
        "summary": summary,
        "claude_plugin_hints": hints,
        "next_steps": next_steps,
        "errors": errors,
    }


# ─── telemetry ───────────────────────────────────────────────────────────────


def _telemetry_dir() -> Path:
    return Path.home() / ".agent-plus" / "analytics"


def _log_telemetry(mode: str, summary: dict) -> None:
    """Append one JSON line to ~/.agent-plus/analytics/uninstall.jsonl if the
    analytics directory exists. Names only — no paths."""
    d = _telemetry_dir()
    if not d.is_dir():
        return
    try:
        line = json.dumps({
            "ts": int(time.time()),
            "event": "uninstall_run",
            "mode": mode,
            "summary": summary,
        }, ensure_ascii=False)
        path = d / "uninstall.jsonl"
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ─── main entry point ────────────────────────────────────────────────────────


def cmd_uninstall(args: argparse.Namespace) -> dict:
    install_dir = _resolve_install_dir(args)
    scope = _scope_from_args(args)
    dry_run = bool(getattr(args, "dry_run", False))
    non_interactive = bool(getattr(args, "non_interactive", False))
    json_only = bool(getattr(args, "json", False))
    interactive = not (non_interactive or json_only)

    # Build manifest first (pure; safe to print even on dry-run / abort).
    paths = build_manifest(scope=scope, install_dir=install_dir)
    errors: list[dict] = []

    # ─── confirmation ───────────────────────────────────────────────────────
    user_confirmed = False
    if dry_run:
        # Dry-run: emit envelope only, no preview prompt, no removals.
        if interactive:
            _print_default_preview(paths, scope)
        return _build_envelope(
            scope=scope, dry_run=True,
            interactive=interactive, user_confirmed=False,
            install_dir=install_dir, paths=paths, errors=errors,
        )

    if scope == "purge":
        # Always prompt PURGE — even under --non-interactive (T6 one-way door).
        _print_default_preview(paths, scope)
        user_confirmed = _read_purge_confirmation()
        if not user_confirmed:
            sys.stderr.write("Aborted: confirmation not received.\n")
            return _build_envelope(
                scope=scope, dry_run=False,
                interactive=interactive, user_confirmed=False,
                install_dir=install_dir, paths=paths, errors=errors,
            )
    elif non_interactive:
        # No prompt; the explicit flag set IS the confirmation.
        user_confirmed = True
    else:
        # Interactive default: preview + y/N confirm.
        _print_default_preview(paths, scope)
        host = _h()
        user_confirmed = host._read_yes_no("Proceed? [y/N]: ")  # noqa: SLF001
        if not user_confirmed:
            sys.stderr.write("Aborted.\n")
            return _build_envelope(
                scope=scope, dry_run=False,
                interactive=interactive, user_confirmed=False,
                install_dir=install_dir, paths=paths, errors=errors,
            )

    # ─── execute ────────────────────────────────────────────────────────────
    paths = execute_removals(paths)

    # ─── telemetry ──────────────────────────────────────────────────────────
    summary = _summary(paths)
    _log_telemetry(scope, summary)

    # ─── errors aggregation ─────────────────────────────────────────────────
    for p in paths:
        if p.get("status") == "error":
            errors.append({
                "code": "uninstall_partial_failure",
                "message": f"failed to remove {p['path']}: {p.get('error', 'unknown')}",
                "hint": "check filesystem permissions and re-run",
                "recoverable": True,
            })

    return _build_envelope(
        scope=scope, dry_run=False,
        interactive=interactive, user_confirmed=user_confirmed,
        install_dir=install_dir, paths=paths, errors=errors,
    )

"""v0.13.5 — `agent-plus-meta upgrade` subcommand.

The upgrade action. Detects the install type (global / git_local — the
vendored branch was cut per /review C4), shows a 4-option AskUserQuestion
prompt (Yes / Always / Snooze / Never ask), replaces the 5 framework
primitive bins with `.bak` snapshots taken first, runs any pending
migrations from `agent-plus-meta/migrations/`, fires a post-test
in-process `cmd_doctor` gate, and rolls back from `.bak` if doctor says
broken.

Stdlib only. Pathlib everywhere. urllib.request for primitive downloads.
subprocess.run([list], shell=False) form only. UTF-8 file I/O.

Cuts honored (per /review C2-C6 + outside-opinion review):
- NO `vendored` install type (returns global / git_local only).
- NO `silent_upgrade_policy` config knob (hardcoded patch-only).
- NO `upgrade_user_declined` error code (4 stable codes only).
- NO `telemetry` envelope field (--no-telemetry is a no-op stub).

Error codes:
- upgrade_partial_failure         (one or more bins failed)
- upgrade_migration_failed        (a migration script raised)
- upgrade_rollback_required       (post-doctor verdict=broken)
- upgrade_check_network_failed    (only if we have to re-probe inline)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import shutil
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ─── host bindings (mirrors init.py) ─────────────────────────────────────────

_host: Any = None


def bind(host: Any) -> None:
    global _host
    _host = host


def _h() -> Any:
    if _host is None:  # pragma: no cover — guard
        raise RuntimeError("upgrade.bind() not called by host bin")
    return _host


# ─── constants ───────────────────────────────────────────────────────────────

PRIMITIVES = (
    "agent-plus-meta",
    "repo-analyze",
    "diff-summary",
    "skill-feedback",
    "skill-plus",
)

REPO_RAW = "https://raw.githubusercontent.com/osouthgate/agent-plus/main"

ERR_PARTIAL_FAILURE = "upgrade_partial_failure"
ERR_MIGRATION_FAILED = "upgrade_migration_failed"
ERR_ROLLBACK_REQUIRED = "upgrade_rollback_required"
ERR_NETWORK_FAILED = "upgrade_check_network_failed"


# ─── path helpers ────────────────────────────────────────────────────────────


def _agent_plus_root() -> Path:
    return Path.home() / ".agent-plus"


def _bak_root() -> Path:
    return _agent_plus_root() / ".bak"


def _migrations_history_path() -> Path:
    return _agent_plus_root() / "migrations.json"


def _config_path() -> Path:
    return _agent_plus_root() / "config.json"


def _cache_path() -> Path:
    return _agent_plus_root() / "upgrade" / "cache.json"


def _snooze_path() -> Path:
    return _agent_plus_root() / "upgrade" / "snooze.json"


def _last_setup_version_path() -> Path:
    return _agent_plus_root() / "upgrade" / "last-setup-version"


# ─── small JSON I/O ──────────────────────────────────────────────────────────


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


# ─── config ──────────────────────────────────────────────────────────────────


def _read_config() -> dict:
    cfg = _read_json(_config_path()) or {}
    return {
        "update_check": bool(cfg.get("update_check", True)),
        "silent_upgrade": bool(cfg.get("silent_upgrade", False)),
    }


def _write_config_field(key: str, value: Any) -> None:
    cfg = _read_json(_config_path()) or {}
    cfg[key] = value
    _write_json(_config_path(), cfg)


# ─── install type detection ──────────────────────────────────────────────────


def detect_install_type(meta_bin: Optional[str] = None) -> dict:
    """Detect how agent-plus is installed.

    Returns a dict with `install_type` ("global" | "git_local" | "unknown")
    and `bin_dir` (where the bins live, when known). Per /review C4 the
    vendored branch is CUT — only global and git_local are returned today.
    """
    candidate = meta_bin or shutil.which("agent-plus-meta")
    if candidate:
        path = Path(candidate).resolve()
        # global: lives under ~/.local/bin or under the env-var override
        global_root_env = os.environ.get("AGENT_PLUS_INSTALL_DIR")
        global_dirs = [Path.home() / ".local" / "bin"]
        if global_root_env:
            global_dirs.insert(0, Path(global_root_env).expanduser().resolve())
        for gd in global_dirs:
            try:
                gd_resolved = gd.resolve()
            except OSError:
                continue
            if path.parent == gd_resolved:
                return {"install_type": "global", "bin_dir": str(path.parent)}
        # git_local: parent dir's parent has a .git directory (running from a clone)
        try:
            grandparent = path.parent.parent
            if (grandparent / ".git").is_dir():
                return {"install_type": "git_local", "bin_dir": str(path.parent)}
        except OSError:
            pass
        # Fallback: if we got here we still know where the bin lives, just
        # not which install class it is. Treat as global so the upgrade can
        # still proceed against that directory.
        return {"install_type": "global", "bin_dir": str(path.parent)}
    return {"install_type": "unknown", "bin_dir": None}


# ─── .bak machinery ──────────────────────────────────────────────────────────


def _timestamp_dirname() -> str:
    """ISO-ish, filesystem-safe."""
    now = _dt.datetime.now(_dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%M-%S")


def _create_bak_dir() -> Path:
    bak_dir = _bak_root() / _timestamp_dirname()
    bak_dir.mkdir(parents=True, exist_ok=True)
    return bak_dir


def _backup_one(bin_dir: Path, name: str, bak_dir: Path) -> Optional[Path]:
    """Copy bin_dir/name into bak_dir/name.bak. Returns the .bak path on
    success, None if the source file doesn't exist."""
    src = bin_dir / name
    if not src.is_file():
        return None
    dst = bak_dir / f"{name}.bak"
    shutil.copy2(src, dst)
    return dst


def _restore_one(bak_path: Path, bin_dir: Path, name: str) -> bool:
    """Restore bak_path -> bin_dir/name. Returns True on success."""
    if not bak_path.is_file():
        return False
    dst = bin_dir / name
    try:
        shutil.copy2(bak_path, dst)
        return True
    except OSError:
        return False


def _most_recent_bak_set() -> Optional[Path]:
    root = _bak_root()
    if not root.is_dir():
        return None
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


# ─── primitive download ──────────────────────────────────────────────────────


def _download_primitive(name: str, *, timeout: float = 10.0) -> tuple[Optional[bytes], Optional[str]]:
    """GET https://raw.githubusercontent.com/.../<name>/bin/<name>.

    Returns (body_bytes, error). Per P4 / P7: never raises."""
    url = f"{REPO_RAW}/{name}/bin/{name}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "agent-plus-meta/upgrade"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = resp.getcode()
            body = resp.read()
        if status != 200:
            return None, f"http_{status}"
        if not body:
            return None, "empty_body"
        return body, None
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, type(e).__name__
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__


def _replace_primitive(bin_dir: Path, name: str, body: bytes) -> bool:
    """Atomically replace bin_dir/name with the new body. chmod 755 on POSIX."""
    target = bin_dir / name
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(body)
        try:
            os.chmod(tmp, 0o755)
        except OSError:
            # Windows: chmod is mostly cosmetic. Don't fail the upgrade on it.
            pass
        tmp.replace(target)
        return True
    except OSError:
        # Clean up the tmp if it lingered
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


# ─── migration runner ────────────────────────────────────────────────────────


def _migrations_dir() -> Path:
    """Path to agent-plus-meta/migrations/ relative to the running bin."""
    host = _h()
    bin_path = Path(host.__file__).resolve()
    # bin_path = .../agent-plus-meta/bin/agent-plus-meta
    return bin_path.parent.parent / "migrations"


def _parse_migration_id(stem: str) -> Optional[tuple[int, int, int]]:
    """`v0_13_5` -> (0, 13, 5). Returns None on any malformed name."""
    if not stem.startswith("v"):
        return None
    parts = stem[1:].split("_")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _list_migrations(mig_dir: Path) -> list[tuple[str, tuple[int, int, int], Path]]:
    """Return [(id, dest_version_tuple, path), ...] sorted ascending."""
    if not mig_dir.is_dir():
        return []
    out: list[tuple[str, tuple[int, int, int], Path]] = []
    for p in mig_dir.iterdir():
        if not p.is_file() or p.suffix != ".py":
            continue
        if p.name.startswith("_") or p.name == "README.md":
            continue
        stem = p.stem
        ver = _parse_migration_id(stem)
        if ver is None:
            continue
        out.append((stem, ver, p))
    out.sort(key=lambda t: t[1])
    return out


def _read_migrations_history() -> dict:
    h = _read_json(_migrations_history_path()) or {}
    if not isinstance(h.get("applied"), list):
        h["applied"] = []
    return h


def _record_migration_history(entry: dict) -> None:
    h = _read_migrations_history()
    h["applied"].append(entry)
    _write_json(_migrations_history_path(), h)


def _load_migration_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_migrations(
    *, from_version: str, to_version: str,
    mig_dir: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> tuple[list[dict], Optional[dict]]:
    """Apply pending migrations. Returns (results, error_entry_or_None).

    `results` is a list of envelope-shaped dicts:
      {id, status: 'ok'|'skipped_already_applied'|'failed', duration_ms}
    """
    mig_dir = mig_dir or _migrations_dir()
    workspace = workspace or _agent_plus_root()
    available = _list_migrations(mig_dir)
    if not available:
        return [], None

    history = _read_migrations_history()
    applied_ids = {a.get("id") for a in history.get("applied", []) if isinstance(a, dict)}

    from_tuple = _semver_tuple(from_version)
    to_tuple = _semver_tuple(to_version)

    results: list[dict] = []
    for mig_id, dest_ver, path in available:
        # Selection: from_version < dest_ver <= to_version
        if not (from_tuple < dest_ver <= to_tuple):
            continue
        if mig_id in applied_ids:
            results.append({
                "id": mig_id,
                "status": "skipped_already_applied",
                "duration_ms": 0,
            })
            continue
        started = time.monotonic()
        try:
            mod = _load_migration_module(path)
            if mod is None or not hasattr(mod, "migrate"):
                raise RuntimeError(
                    f"migration {mig_id} does not expose a migrate(workspace) callable"
                )
            ret = mod.migrate(workspace)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if not isinstance(ret, dict) or ret.get("status") not in (
                "ok", "skipped", "failed"
            ):
                raise RuntimeError(
                    f"migration {mig_id} returned malformed result: {ret!r}"
                )
            status = ret.get("status")
            if status == "failed":
                results.append({
                    "id": mig_id,
                    "status": "failed",
                    "duration_ms": elapsed_ms,
                })
                _record_migration_history({
                    "id": mig_id, "applied_ts": int(time.time()),
                    "from": from_version, "to": to_version,
                    "outcome": "failed", "message": ret.get("message", ""),
                })
                return results, {
                    "code": ERR_MIGRATION_FAILED,
                    "message": f"migration {mig_id} failed: {ret.get('message', '')}",
                    "hint": "rollback triggered; re-run upgrade to retry once the "
                            "underlying issue is fixed",
                    "recoverable": False,
                }
            results.append({
                "id": mig_id,
                "status": "skipped_already_applied" if status == "skipped" else "ok",
                "duration_ms": elapsed_ms,
            })
            _record_migration_history({
                "id": mig_id, "applied_ts": int(time.time()),
                "from": from_version, "to": to_version,
                "outcome": status,
            })
        except Exception as e:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - started) * 1000)
            results.append({
                "id": mig_id, "status": "failed", "duration_ms": elapsed_ms,
            })
            _record_migration_history({
                "id": mig_id, "applied_ts": int(time.time()),
                "from": from_version, "to": to_version,
                "outcome": "failed", "message": str(e),
                "traceback": traceback.format_exc()[-500:],
            })
            return results, {
                "code": ERR_MIGRATION_FAILED,
                "message": f"migration {mig_id} raised: {e}",
                "hint": "rollback triggered; check ~/.agent-plus/migrations.json for trace",
                "recoverable": False,
            }
    return results, None


def _semver_tuple(s: str) -> tuple[int, int, int]:
    core = s.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    out = [0, 0, 0]
    for i in range(min(3, len(parts))):
        try:
            out[i] = int(parts[i])
        except ValueError:
            return 0, 0, 0
    return out[0], out[1], out[2]


def _bump_kind(from_v: str, to_v: str) -> str:
    """Return 'patch' | 'minor' | 'major' | 'noop'."""
    a = _semver_tuple(from_v)
    b = _semver_tuple(to_v)
    if b == a:
        return "noop"
    if b[0] != a[0]:
        return "major"
    if b[1] != a[1]:
        return "minor"
    return "patch"


# ─── post-test gate ──────────────────────────────────────────────────────────


def _run_doctor_in_process() -> str:
    """Call host.cmd_doctor() and return its verdict.

    Returns 'broken' on any exception so the rollback path fires safely
    (better to trigger an unnecessary rollback than to skip it on a real
    breakage)."""
    host = _h()
    try:
        ns = argparse.Namespace(dir=None, env_file=None, pretty=False)
        result = host.cmd_doctor(ns)
        if isinstance(result, dict):
            return str(result.get("verdict", "broken"))
        return "broken"
    except Exception:  # noqa: BLE001
        return "broken"


# ─── 4-option AskUserQuestion (interactive) ──────────────────────────────────


def _prompt_choice() -> str:
    """Interactive 4-option prompt. Returns one of:
        'yes'   — go ahead with this upgrade
        'always' — set silent_upgrade=true and upgrade
        'snooze' — advance the snooze ladder, do not upgrade
        'never'  — set update_check=false, do not upgrade
    On EOF / Ctrl+C we treat the user as having declined → 'snooze'.
    """
    options = (
        ("y", "yes",    "Yes — upgrade now"),
        ("a", "always", "Always — silent upgrade on patch bumps from now on"),
        ("s", "snooze", "Snooze — remind me later (advances 24h → 48h → 7d → never)"),
        ("n", "never",  "Never ask again (sets update_check=false)"),
    )
    print("agent-plus upgrade — choose:", flush=True)
    for short, _, label in options:
        print(f"  [{short}] {label}", flush=True)
    while True:
        try:
            line = input("Choice [y/a/s/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "snooze"
        if not line:
            continue
        for short, value, _ in options:
            if line == short or line == value:
                return value


# ─── envelope assembly ───────────────────────────────────────────────────────


def _build_envelope(
    *,
    verdict: str,
    from_version: str,
    to_version: str,
    install_type: str,
    bins_replaced: list,
    migrations_applied: list,
    post_test: dict,
    user_choice: str,
    elapsed_ms_total: int,
    errors: list,
) -> dict:
    """Frozen JSON envelope for `upgrade` (v0.13.5 contract).

    Note: `telemetry` field omitted (--no-telemetry is a no-op stub).
    """
    host = _h()
    return {
        "tool": host._tool_meta(),
        "verdict": verdict,
        "from_version": from_version,
        "to_version": to_version,
        "install_type_detected": install_type,
        "bins_replaced": bins_replaced,
        "migrations_applied": migrations_applied,
        "post_test": post_test,
        "user_choice": user_choice,
        "ttl_total_ms": int(elapsed_ms_total),
        "errors": errors,
    }


# ─── --rollback branch ───────────────────────────────────────────────────────


def _do_rollback_only(install: dict) -> dict:
    """Restore the most recent .bak set without an upgrade. Returns the
    completed envelope payload."""
    started = time.monotonic()
    host = _h()
    current = host._plugin_version()
    bak_dir = _most_recent_bak_set()
    bins_replaced: list = []
    errors: list = []
    if bak_dir is None:
        errors.append({
            "code": ERR_PARTIAL_FAILURE,
            "message": "no .bak set found to roll back from",
            "hint": "rollback only works after at least one upgrade has been attempted",
            "recoverable": False,
        })
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="error",
            from_version=current,
            to_version=current,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=[],
            migrations_applied=[],
            post_test={"doctor_verdict": "skipped", "rollback_triggered": False},
            user_choice="auto",
            elapsed_ms_total=elapsed_ms,
            errors=errors,
        )

    bin_dir_str = install.get("bin_dir")
    bin_dir = Path(bin_dir_str) if bin_dir_str else None
    if bin_dir is None:
        errors.append({
            "code": ERR_PARTIAL_FAILURE,
            "message": "bin_dir not detected; cannot restore",
            "hint": "set AGENT_PLUS_INSTALL_DIR or re-run install.sh",
            "recoverable": False,
        })
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="error",
            from_version=current,
            to_version=current,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=[],
            migrations_applied=[],
            post_test={"doctor_verdict": "skipped", "rollback_triggered": False},
            user_choice="auto",
            elapsed_ms_total=elapsed_ms,
            errors=errors,
        )

    for name in PRIMITIVES:
        bak_path = bak_dir / f"{name}.bak"
        if not bak_path.is_file():
            bins_replaced.append({
                "name": name, "from": current, "to": current,
                "status": "skipped", "backup_path": str(bak_path),
            })
            continue
        ok = _restore_one(bak_path, bin_dir, name)
        bins_replaced.append({
            "name": name, "from": current, "to": current,
            "status": "ok" if ok else "failed",
            "backup_path": str(bak_path),
        })
        if not ok:
            errors.append({
                "code": ERR_PARTIAL_FAILURE,
                "message": f"failed to restore {name} from {bak_path}",
                "hint": "check filesystem permissions on " + str(bin_dir),
                "recoverable": False,
            })

    elapsed_ms = int((time.monotonic() - started) * 1000)
    any_failed = any(b["status"] == "failed" for b in bins_replaced)
    return _build_envelope(
        verdict="error" if any_failed else "rolled_back",
        from_version=current,
        to_version=current,
        install_type=install.get("install_type", "unknown"),
        bins_replaced=bins_replaced,
        migrations_applied=[],
        post_test={"doctor_verdict": "skipped", "rollback_triggered": True},
        user_choice="auto",
        elapsed_ms_total=elapsed_ms,
        errors=errors,
    )


# ─── --dry-run branch ────────────────────────────────────────────────────────


def _do_dry_run(install: dict, latest_version: str) -> dict:
    started = time.monotonic()
    host = _h()
    current = host._plugin_version()
    bins = [
        {
            "name": name,
            "from": current,
            "to": latest_version,
            "status": "skipped",
            "backup_path": None,
        }
        for name in PRIMITIVES
    ]
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return _build_envelope(
        verdict="noop",
        from_version=current,
        to_version=latest_version,
        install_type=install.get("install_type", "unknown"),
        bins_replaced=bins,
        migrations_applied=[],
        post_test={"doctor_verdict": "skipped", "rollback_triggered": False},
        user_choice="auto",
        elapsed_ms_total=elapsed_ms,
        errors=[],
    )


# ─── main upgrade flow ───────────────────────────────────────────────────────


def cmd_upgrade(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    host = _h()
    current = host._plugin_version()

    install = detect_install_type()

    # --rollback short-circuit
    if getattr(args, "rollback", False):
        return _do_rollback_only(install)

    # Determine target version. Reuse cache if we have one; otherwise
    # cheaply re-probe via the upgrade_check helper (loaded lazily so this
    # module stays usable in pure-rollback paths even when network is dead).
    cache = _read_json(_cache_path()) or {}
    latest_version = cache.get("latest_version")
    if not latest_version:
        # Inline lightweight probe — best-effort, capped 3s.
        from _subcommands import upgrade_check as _uc
        _uc.bind(host)
        latest, _err, _elapsed = _uc._fetch_latest_version(3.0)
        latest_version = latest or current

    # --dry-run short-circuit (no fs changes)
    if getattr(args, "dry_run", False):
        return _do_dry_run(install, latest_version)

    bump = _bump_kind(current, latest_version)
    config = _read_config()

    # ─── pick user_choice ────────────────────────────────────────────────────
    explicit = getattr(args, "user_choice", None)
    non_interactive = bool(getattr(args, "non_interactive", False))
    auto = bool(getattr(args, "auto", False))

    user_choice: str
    if explicit:
        user_choice = explicit
    elif non_interactive and auto:
        # T5: pre-1.0 safety. Silent on patch only when silent_upgrade=true.
        # For minor/major, --auto cannot ask (non-interactive) so we MUST NOT
        # silently land a potentially-breaking bump — degrade to snooze (noop).
        # The user's next interactive run will see UPGRADE_AVAILABLE and can
        # accept consciously. This is exactly the foot-gun T5 was designed to
        # prevent for someone running --auto in CI.
        if config.get("silent_upgrade") and bump == "patch":
            user_choice = "always"
        elif bump == "patch":
            user_choice = "yes"
        else:
            # bump == "minor" or "major" — refuse to silently land it.
            user_choice = "snooze"
    elif non_interactive and not auto:
        # No prompts allowed and no explicit choice — treat as snooze (noop).
        user_choice = "snooze"
    else:
        user_choice = _prompt_choice()

    # ─── short-circuit on noop when there's nothing to upgrade ───────────────
    if bump == "noop":
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="noop",
            from_version=current,
            to_version=latest_version,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=[],
            migrations_applied=[],
            post_test={"doctor_verdict": "skipped", "rollback_triggered": False},
            user_choice=user_choice,
            elapsed_ms_total=elapsed_ms,
            errors=[],
        )

    # ─── act on user_choice ──────────────────────────────────────────────────
    if user_choice == "always":
        _write_config_field("silent_upgrade", True)
    elif user_choice == "never":
        _write_config_field("update_check", False)

    if user_choice in ("snooze", "never"):
        # Advance the snooze ladder for snooze; never just records the
        # config knob and bails.
        if user_choice == "snooze":
            try:
                from _subcommands import upgrade_check as _uc2
                _uc2.bind(host)
                snooze = _uc2._read_snooze()
                step = _uc2._next_ladder_step(snooze.get("ladder_step", "none"))
                snooze = _uc2._apply_snooze(
                    step, latest_version=latest_version, now=int(time.time())
                )
                _uc2._write_snooze(snooze)
            except Exception:  # noqa: BLE001
                pass
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="noop",
            from_version=current,
            to_version=latest_version,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=[],
            migrations_applied=[],
            post_test={"doctor_verdict": "skipped", "rollback_triggered": False},
            user_choice=user_choice,
            elapsed_ms_total=elapsed_ms,
            errors=[],
        )

    # user_choice in ("yes", "always") → proceed with upgrade

    # ─── replace bins (with .bak first) ──────────────────────────────────────
    bin_dir_str = install.get("bin_dir")
    if not bin_dir_str:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="error",
            from_version=current,
            to_version=latest_version,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=[],
            migrations_applied=[],
            post_test={"doctor_verdict": "skipped", "rollback_triggered": False},
            user_choice=user_choice,
            elapsed_ms_total=elapsed_ms,
            errors=[{
                "code": ERR_PARTIAL_FAILURE,
                "message": "could not detect a bin directory to upgrade",
                "hint": "re-run install.sh to (re)install the framework",
                "recoverable": False,
            }],
        )
    bin_dir = Path(bin_dir_str)
    bak_dir = _create_bak_dir()

    bins_replaced: list = []
    bak_paths: dict[str, Path] = {}
    fail_count = 0

    for name in PRIMITIVES:
        bak_path = _backup_one(bin_dir, name, bak_dir)
        bak_paths[name] = bak_path  # may be None if source missing
        body, err = _download_primitive(name)
        if body is None:
            bins_replaced.append({
                "name": name, "from": current, "to": latest_version,
                "status": "failed",
                "backup_path": str(bak_path) if bak_path else None,
            })
            fail_count += 1
            continue
        ok = _replace_primitive(bin_dir, name, body)
        bins_replaced.append({
            "name": name, "from": current, "to": latest_version,
            "status": "ok" if ok else "failed",
            "backup_path": str(bak_path) if bak_path else None,
        })
        if not ok:
            fail_count += 1

    errors: list = []
    rollback_triggered = False
    if fail_count > 0:
        # Roll back any successful replacements; ALL bins return to .bak.
        for name in PRIMITIVES:
            bak = bak_paths.get(name)
            if bak is not None:
                _restore_one(bak, bin_dir, name)
        rollback_triggered = True
        errors.append({
            "code": ERR_PARTIAL_FAILURE,
            "message": f"{fail_count}/{len(PRIMITIVES)} primitive(s) failed to replace",
            "hint": "rolled back to previous version; re-run after the network "
                    "or filesystem issue is resolved",
            "recoverable": True,
        })
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="rolled_back",
            from_version=current,
            to_version=latest_version,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=bins_replaced,
            migrations_applied=[],
            post_test={"doctor_verdict": "skipped", "rollback_triggered": rollback_triggered},
            user_choice=user_choice,
            elapsed_ms_total=elapsed_ms,
            errors=errors,
        )

    # ─── migrations ──────────────────────────────────────────────────────────
    migrations_applied, mig_err = _run_migrations(
        from_version=current, to_version=latest_version,
    )
    if mig_err is not None:
        for name in PRIMITIVES:
            bak = bak_paths.get(name)
            if bak is not None:
                _restore_one(bak, bin_dir, name)
        rollback_triggered = True
        errors.append(mig_err)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="rolled_back",
            from_version=current,
            to_version=latest_version,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=bins_replaced,
            migrations_applied=migrations_applied,
            post_test={"doctor_verdict": "skipped", "rollback_triggered": True},
            user_choice=user_choice,
            elapsed_ms_total=elapsed_ms,
            errors=errors,
        )

    # ─── post-test gate (in-process doctor) ──────────────────────────────────
    doctor_verdict = _run_doctor_in_process()
    if doctor_verdict == "broken":
        for name in PRIMITIVES:
            bak = bak_paths.get(name)
            if bak is not None:
                _restore_one(bak, bin_dir, name)
        rollback_triggered = True
        errors.append({
            "code": ERR_ROLLBACK_REQUIRED,
            "message": "post-upgrade doctor verdict=broken; rolling back",
            "hint": "the previous bins have been restored from .bak",
            "recoverable": True,
        })
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _build_envelope(
            verdict="rolled_back",
            from_version=current,
            to_version=latest_version,
            install_type=install.get("install_type", "unknown"),
            bins_replaced=bins_replaced,
            migrations_applied=migrations_applied,
            post_test={"doctor_verdict": doctor_verdict, "rollback_triggered": True},
            user_choice=user_choice,
            elapsed_ms_total=elapsed_ms,
            errors=errors,
        )

    # ─── success — record last-setup-version + clean up old .bak sets ────────
    try:
        _last_setup_version_path().parent.mkdir(parents=True, exist_ok=True)
        _last_setup_version_path().write_text(latest_version + "\n", encoding="utf-8")
    except OSError:
        pass

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return _build_envelope(
        verdict="success",
        from_version=current,
        to_version=latest_version,
        install_type=install.get("install_type", "unknown"),
        bins_replaced=bins_replaced,
        migrations_applied=migrations_applied,
        post_test={"doctor_verdict": doctor_verdict, "rollback_triggered": False},
        user_choice=user_choice,
        elapsed_ms_total=elapsed_ms,
        errors=errors,
    )

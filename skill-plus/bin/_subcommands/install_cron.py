"""skill-plus install-cron — self-install scheduled scan.

POSIX: writes a crontab entry running `python <bin> scan --accept-consent --project <project>`.
Windows: registers a Task Scheduler task via schtasks.

Helpers (project_state_root, _git_toplevel, grant_consent_for, _ensure_dir, _now_iso)
are injected into this module's namespace by the bin shell.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ─── shared bits ──────────────────────────────────────────────────────────────


def _resolve_project(args) -> Path:
    if getattr(args, "project", None):
        return Path(args.project).expanduser().resolve()
    top = _git_toplevel()  # noqa: F821 — injected
    return (top if top is not None else Path.cwd()).resolve()


def _bin_path() -> Path:
    # this file is .../bin/_subcommands/install_cron.py — bin shell is parents[1]/skill-plus
    return (Path(__file__).resolve().parents[1] / "skill-plus").resolve()


def _cron_expression(frequency: str) -> str:
    if frequency == "daily":
        return "0 3 * * *"
    return "0 3 * * 0"  # weekly, Sunday 03:00


def _marker_for(project_path: Path) -> str:
    return f"# skill-plus auto-installed for {project_path}"


# ─── POSIX ────────────────────────────────────────────────────────────────────


def _posix_entry(project_path: Path, frequency: str) -> str:
    expr = _cron_expression(frequency)
    py = sys.executable
    bin_path = _bin_path()
    log_path = project_state_root() / "scan.log"  # noqa: F821 — injected
    return (
        f'{expr} "{py}" "{bin_path}" scan --accept-consent --project "{project_path}" '
        f'>> "{log_path}" 2>&1'
    )


def _posix_read_crontab(runner=None) -> tuple[str, bool]:
    if runner is None:
        runner = subprocess.run
    """Returns (current_crontab_text, had_existing). Empty if no crontab."""
    try:
        res = runner(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"failed to invoke crontab: {e}") from e
    if res.returncode != 0:
        # No crontab for user is a normal "empty" state.
        return "", False
    return res.stdout or "", True


def _posix_write_crontab(text: str, runner=None) -> None:
    if runner is None:
        runner = subprocess.run
    if text and not text.endswith("\n"):
        text += "\n"
    res = runner(["crontab", "-"], input=text, capture_output=True, text=True, timeout=10)
    if res.returncode != 0:
        raise RuntimeError(f"crontab - failed: {res.stderr.strip()}")


def _strip_block(current: str, marker: str) -> tuple[str, bool]:
    """Remove the marker line + the immediately following entry line. Idempotent."""
    lines = current.splitlines()
    out: list[str] = []
    removed = False
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            removed = True
            continue
        if line.strip() == marker:
            skip_next = True
            continue
        out.append(line)
    return "\n".join(out), removed


def _posix_action(project_path: Path, frequency: str, *, print_only: bool, uninstall: bool, runner=None) -> dict:
    if runner is None:
        runner = subprocess.run
    marker = _marker_for(project_path)
    entry_line = _posix_entry(project_path, frequency)
    block = f"{marker}\n{entry_line}"

    if print_only and not uninstall:
        return {
            "ok": True,
            "platform": "posix",
            "action": "print-only",
            "entry": block,
            "projectPath": str(project_path),
            "frequency": frequency,
        }

    current, _ = _posix_read_crontab(runner=runner)

    if uninstall:
        new, was_present = _strip_block(current, marker)
        if print_only:
            return {
                "ok": True,
                "platform": "posix",
                "action": "uninstall-print-only",
                "wasPresent": was_present,
                "wouldRemove": block if was_present else None,
            }
        if was_present:
            _posix_write_crontab(new, runner=runner)
        return {
            "ok": True,
            "platform": "posix",
            "action": "uninstalled",
            "wasPresent": was_present,
        }

    # install / reinstall
    stripped, was_present = _strip_block(current, marker)
    new_text = stripped
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    new_text += block + "\n"
    _posix_write_crontab(new_text, runner=runner)
    return {
        "ok": True,
        "platform": "posix",
        "action": "reinstalled" if was_present else "installed",
        "entry": block,
        "projectPath": str(project_path),
        "frequency": frequency,
    }


# ─── Windows ──────────────────────────────────────────────────────────────────


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _sanitize_slug(project_path: Path) -> str:
    s = _SLUG_RE.sub("-", str(project_path)).strip("-")
    return s.lower() or "project"


def _task_name(project_path: Path) -> str:
    return f"agent-plus-skill-plus-scan-{_sanitize_slug(project_path)}"


def _windows_command(project_path: Path) -> str:
    py = sys.executable
    bin_path = _bin_path()
    # schtasks /tr expects a single string. Wrap all paths in double-quotes.
    return f'"{py}" "{bin_path}" scan --accept-consent --project "{project_path}"'


def _windows_create_args(project_path: Path, frequency: str) -> list[str]:
    args = [
        "schtasks", "/create",
        "/tn", _task_name(project_path),
        "/tr", _windows_command(project_path),
    ]
    if frequency == "daily":
        args += ["/sc", "daily", "/st", "03:00"]
    else:
        args += ["/sc", "weekly", "/d", "SUN", "/st", "03:00"]
    args += ["/f"]
    return args


def _windows_delete_args(project_path: Path) -> list[str]:
    return ["schtasks", "/delete", "/tn", _task_name(project_path), "/f"]


def _windows_query_args(project_path: Path) -> list[str]:
    return ["schtasks", "/query", "/tn", _task_name(project_path)]


def _windows_task_exists(project_path: Path, runner) -> bool:
    """Probe whether the scheduled task exists. Locale-independent: relies
    on schtasks /query exit code, not stderr/stdout text."""
    args = _windows_query_args(project_path)
    try:
        res = runner(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"failed to invoke schtasks: {e}") from e
    return res.returncode == 0


def _windows_action(project_path: Path, frequency: str, *, print_only: bool, uninstall: bool, runner=None) -> dict:
    if runner is None:
        runner = subprocess.run
    if uninstall:
        args = _windows_delete_args(project_path)
        if print_only:
            return {
                "ok": True,
                "platform": "windows",
                "action": "uninstall-print-only",
                "entry": args,
                "taskName": _task_name(project_path),
            }
        # Pre-check existence via exit code, then only delete if present.
        was_present = _windows_task_exists(project_path, runner)
        if not was_present:
            return {
                "ok": True,
                "platform": "windows",
                "action": "uninstalled",
                "wasPresent": False,
            }
        try:
            res = runner(args, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"failed to invoke schtasks: {e}") from e
        if res.returncode != 0:
            stderr = (res.stderr or "") + (res.stdout or "")
            raise RuntimeError(f"schtasks /delete failed: {stderr.strip()}")
        return {
            "ok": True,
            "platform": "windows",
            "action": "uninstalled",
            "wasPresent": True,
        }

    args = _windows_create_args(project_path, frequency)
    if print_only:
        return {
            "ok": True,
            "platform": "windows",
            "action": "print-only",
            "entry": args,
            "projectPath": str(project_path),
            "frequency": frequency,
            "taskName": _task_name(project_path),
        }
    # Pre-check existence so we can distinguish install vs reinstall via exit code
    # (locale-independent — schtasks message text is localized, exit code is not).
    was_present = _windows_task_exists(project_path, runner)
    try:
        res = runner(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"failed to invoke schtasks: {e}") from e
    if res.returncode != 0:
        raise RuntimeError(f"schtasks /create failed: {(res.stderr or res.stdout).strip()}")
    return {
        "ok": True,
        "platform": "windows",
        "action": "reinstalled" if was_present else "installed",
        "entry": args,
        "projectPath": str(project_path),
        "frequency": frequency,
        "taskName": _task_name(project_path),
    }


# ─── entrypoint ───────────────────────────────────────────────────────────────


def run(args, emit_fn) -> int:
    project_path = _resolve_project(args)
    frequency = getattr(args, "frequency", "weekly") or "weekly"
    print_only = bool(getattr(args, "print_only", False))
    uninstall = bool(getattr(args, "uninstall", False))

    is_windows = sys.platform == "win32"

    try:
        if is_windows:
            payload = _windows_action(project_path, frequency,
                                      print_only=print_only, uninstall=uninstall)
        else:
            payload = _posix_action(project_path, frequency,
                                    print_only=print_only, uninstall=uninstall)
    except Exception as exc:  # noqa: BLE001
        emit_fn({
            "ok": False,
            "platform": "windows" if is_windows else "posix",
            "error": type(exc).__name__,
            "message": str(exc),
        })
        return 1

    # Capture consent on full install.
    if payload.get("action") in ("installed", "reinstalled"):
        try:
            grant_consent_for(project_path, source="install-cron")  # noqa: F821 — injected
            payload["consentGranted"] = True
        except Exception as exc:  # noqa: BLE001
            payload["consentGranted"] = False
            payload["consentError"] = f"{type(exc).__name__}: {exc}"

    emit_fn(payload)
    return 0

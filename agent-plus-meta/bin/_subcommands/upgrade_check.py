"""v0.13.5 — `agent-plus-meta upgrade-check` subcommand.

The cached probe. Reads ~/.agent-plus/upgrade/cache.json (or fetches the
single-root VERSION file from raw.githubusercontent.com when the cache
expires) and emits the frozen JSON envelope locked at v0.13.5.

Stdlib only. urllib.request for the network probe — works on Windows
without Git Bash. Pathlib everywhere; UTF-8 file I/O; subprocess.run is
unused here (no shell-out at all).

Public contract: the JSON envelope schema in README.md is FROZEN as of
v0.13.5. Additive enum widening is non-breaking; field renames or
removals require a major bump.

Cuts (per outside-opinion review and /review C1-C6):
- NO `--sentinel` mode (cut, defer to v0.16).
- NO `telemetry` envelope field (cut, --no-telemetry exists as a no-op
  forward-compat stub).
- NO `silent_upgrade_policy` envelope field (config knob cut; hardcoded
  patch-only).
- NO GitHub API fallback (T1 locked single-root VERSION file).
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ─── host bindings (mirrors init.py pattern) ─────────────────────────────────

_host: Any = None


def bind(host: Any) -> None:
    """Register the parent bin module so this submodule can call its
    helpers (notably _plugin_version + _tool_meta + _with_tool_meta)."""
    global _host
    _host = host


def _h() -> Any:
    if _host is None:  # pragma: no cover — guard
        raise RuntimeError("upgrade_check.bind() not called by host bin")
    return _host


# ─── constants ───────────────────────────────────────────────────────────────

VERSION_URL = (
    "https://raw.githubusercontent.com/osouthgate/agent-plus/main/VERSION"
)

# TTLs in seconds. up_to_date refreshes hourly; upgrade_available stays
# valid for 12h so we don't re-probe every minute when the user has
# explicitly snoozed or hasn't acted yet.
TTL_UP_TO_DATE_SEC = 60 * 60          # 60 min
TTL_UPGRADE_AVAILABLE_SEC = 12 * 60 * 60  # 720 min

DEFAULT_TIMEOUT_SEC = 3
MAX_TIMEOUT_SEC = 10

LADDER_STEPS = ("none", "24h", "48h", "7d", "never")
LADDER_DURATIONS = {
    "24h": 24 * 60 * 60,
    "48h": 48 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    # "never" -> sentinel (very far future); represented by `expires_ts: None`
    # plus active=True.
}

ERR_NETWORK_FAILED = "upgrade_check_network_failed"


# ─── path helpers ────────────────────────────────────────────────────────────


def _state_root() -> Path:
    """Resolve ~/.agent-plus/upgrade/. Tests override via host._state_root_for_test
    but the production path is fixed."""
    return Path.home() / ".agent-plus" / "upgrade"


def _cache_path() -> Path:
    return _state_root() / "cache.json"


def _snooze_path() -> Path:
    return _state_root() / "snooze.json"


def _config_path() -> Path:
    return Path.home() / ".agent-plus" / "config.json"


# ─── small JSON I/O (defensive on every read) ────────────────────────────────


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


# ─── config (silent_upgrade) ─────────────────────────────────────────────────


def _read_config() -> dict:
    """Read ~/.agent-plus/config.json. Hardcoded defaults on miss/corruption.

    Per /review C3: config key is `silent_upgrade` (NOT `auto_upgrade`).
    Per outside-opinion review: `silent_upgrade_policy` knob is CUT.
    Hardcoded patch-only when silent_upgrade=true. No user-facing policy."""
    cfg = _read_json(_config_path()) or {}
    out = {
        "update_check": bool(cfg.get("update_check", True)),
        "silent_upgrade": bool(cfg.get("silent_upgrade", False)),
    }
    return out


# ─── snooze ladder ───────────────────────────────────────────────────────────


def _read_snooze() -> dict:
    s = _read_json(_snooze_path()) or {}
    return {
        "active": bool(s.get("active", False)),
        "expires_ts": s.get("expires_ts"),  # int | None
        "ladder_step": s.get("ladder_step", "none"),
        "snoozed_for_version": s.get("snoozed_for_version"),
    }


def _write_snooze(snooze: dict) -> None:
    _write_json(_snooze_path(), snooze)


def _next_ladder_step(current: str) -> str:
    """Advance the ladder. Anything unknown resets to 24h."""
    if current not in LADDER_STEPS:
        return "24h"
    if current == "never":
        return "never"
    try:
        idx = LADDER_STEPS.index(current)
    except ValueError:  # pragma: no cover
        return "24h"
    if idx + 1 >= len(LADDER_STEPS):
        return "never"
    nxt = LADDER_STEPS[idx + 1]
    # 'none' should never be the next step — bump past it.
    return nxt if nxt != "none" else "24h"


def _apply_snooze(step: str, *, latest_version: Optional[str], now: int) -> dict:
    """Compose a snooze record for the requested ladder step."""
    if step == "never":
        return {
            "active": True,
            "expires_ts": None,
            "ladder_step": "never",
            "snoozed_for_version": latest_version,
        }
    duration = LADDER_DURATIONS.get(step)
    if duration is None:  # treat as 24h on unknown
        step, duration = "24h", LADDER_DURATIONS["24h"]
    return {
        "active": True,
        "expires_ts": now + duration,
        "ladder_step": step,
        "snoozed_for_version": latest_version,
    }


def _snooze_currently_active(snooze: dict, *, now: int) -> bool:
    if not snooze.get("active"):
        return False
    if snooze.get("ladder_step") == "never":
        return True
    expires = snooze.get("expires_ts")
    return isinstance(expires, int) and now < expires


# ─── network probe ───────────────────────────────────────────────────────────


def _fetch_latest_version(timeout_sec: float) -> tuple[Optional[str], Optional[str], int]:
    """Fetch the single-root VERSION file. Returns (version, error, elapsed_ms).

    On any failure (timeout, DNS, HTTP non-2xx, malformed body) returns
    (None, error_string, elapsed_ms). Per P4: never raises — the probe
    must fail silently."""
    start = time.monotonic()
    try:
        req = urllib.request.Request(
            VERSION_URL,
            headers={"User-Agent": "agent-plus-meta/upgrade-check"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if status != 200:
            return None, f"http_{status}", elapsed_ms
        version = body.strip()
        if not version:
            return None, "empty_version", elapsed_ms
        # Reject anything other than a semver-ish single line. Multi-line
        # bodies, binary payloads, etc. degrade to `unknown`.
        if "\n" in body.strip() or not _looks_like_version(version):
            return None, "malformed_version", elapsed_ms
        return version, None, elapsed_ms
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return None, f"http_{e.code}", elapsed_ms
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return None, type(e).__name__, elapsed_ms
    except Exception as e:  # noqa: BLE001 — never crash a workflow on the probe
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return None, type(e).__name__, elapsed_ms


def _looks_like_version(s: str) -> bool:
    """Cheap semver-ish check: starts with a digit, only contains
    digits / dots / a-z / + / - / underscore. Permissive to accept
    `0.13.5+rc1` and similar."""
    if not s or not s[0].isdigit():
        return False
    allowed = set("0123456789.+-_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return all(ch in allowed for ch in s)


# ─── version comparison ──────────────────────────────────────────────────────


def _parse_semver(s: str) -> tuple[int, int, int]:
    """Parse `MAJOR.MINOR.PATCH` (ignoring +build / -prerelease suffixes).
    Falls back to (0, 0, 0) on any failure."""
    core = s.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    out = [0, 0, 0]
    for i in range(min(3, len(parts))):
        try:
            out[i] = int(parts[i])
        except ValueError:
            return 0, 0, 0
    return out[0], out[1], out[2]


def _compare_versions(current: str, latest: str) -> str:
    """Returns one of: 'up_to_date', 'upgrade_available', 'unknown'."""
    if not current or not latest:
        return "unknown"
    cur = _parse_semver(current)
    lat = _parse_semver(latest)
    if cur == (0, 0, 0) or lat == (0, 0, 0):
        # Couldn't parse one side → treat as unknown rather than guessing.
        return "unknown" if current != latest else "up_to_date"
    if cur >= lat:
        return "up_to_date"
    return "upgrade_available"


# ─── envelope assembly ───────────────────────────────────────────────────────


def _build_envelope(
    *,
    verdict: str,
    current: str,
    latest: Optional[str],
    cache_hit: bool,
    cache_age_sec: int,
    cache_ttl_sec: int,
    snooze: dict,
    config: dict,
    network: dict,
    elapsed_ms_total: int,
    errors: list,
) -> dict:
    """Frozen JSON envelope for `upgrade-check` (v0.13.5 contract).

    Note (per /review C5): `telemetry` field is OMITTED. The
    --no-telemetry flag exists as a no-op stub for forward-compat, but
    the envelope does not advertise a telemetry slot today.
    """
    host = _h()
    payload: dict = {
        "tool": host._tool_meta(),
        "verdict": verdict,
        "current_version": current,
        "latest_version": latest,
        "version_source": "root_VERSION_file",
        "cache": {
            "hit": cache_hit,
            "age_sec": int(cache_age_sec),
            "ttl_sec": int(cache_ttl_sec),
        },
        "snooze": {
            "active": bool(snooze.get("active", False)),
            "expires_ts": snooze.get("expires_ts"),
            "ladder_step": snooze.get("ladder_step", "none"),
        },
        "config": {
            "update_check": config.get("update_check", True),
            "silent_upgrade": config.get("silent_upgrade", False),
        },
        "network": network,
        "ttl_total_ms": int(elapsed_ms_total),
        "errors": errors,
    }
    return payload


# ─── main entry ──────────────────────────────────────────────────────────────


def cmd_upgrade_check(args: argparse.Namespace) -> dict:
    """Read or refresh the cached upgrade verdict and emit the frozen envelope."""
    host = _h()
    started = time.monotonic()
    now = int(time.time())

    # Resolve the requested timeout, clamped to [0.1, MAX].
    requested_timeout = float(getattr(args, "timeout", DEFAULT_TIMEOUT_SEC) or DEFAULT_TIMEOUT_SEC)
    timeout_sec = max(0.1, min(requested_timeout, MAX_TIMEOUT_SEC))

    current_version = host._plugin_version()

    # ─── flag side-effects: snooze management ────────────────────────────────
    if getattr(args, "clear_snooze", False):
        _write_snooze({
            "active": False,
            "expires_ts": None,
            "ladder_step": "none",
            "snoozed_for_version": None,
        })

    # ─── load cache + snooze + config (after side-effects) ───────────────────
    cache = _read_json(_cache_path())
    snooze = _read_snooze()
    config = _read_config()

    # If the on-disk snooze was for an older version and we have a cached
    # latest_version that has since moved on, reset the ladder. (We re-check
    # this further down once we know the *current* latest_version from cache
    # or fresh probe.)
    forced = bool(getattr(args, "force", False))

    # ─── decide cache validity ───────────────────────────────────────────────
    use_cache = False
    cache_age_sec = 0
    cache_ttl_sec = 0
    cached_verdict: Optional[str] = None
    cached_latest: Optional[str] = None
    if cache and not forced:
        last_check = cache.get("last_check_ts")
        ttl = cache.get("ttl_sec")
        cached_verdict = cache.get("result")
        cached_latest = cache.get("latest_version")
        if isinstance(last_check, int) and isinstance(ttl, int):
            cache_age_sec = max(0, now - last_check)
            cache_ttl_sec = ttl
            if cache_age_sec < ttl and cached_verdict in ("up_to_date", "upgrade_available"):
                use_cache = True

    # ─── retrieve latest version ─────────────────────────────────────────────
    network = {
        "attempted": False,
        "ok": False,
        "elapsed_ms": 0,
        "error": None,
    }
    errors: list = []

    if use_cache:
        latest_version = cached_latest
        verdict = cached_verdict or "unknown"
    else:
        network["attempted"] = True
        latest, err, elapsed_ms = _fetch_latest_version(timeout_sec)
        network["elapsed_ms"] = elapsed_ms
        if latest is not None:
            network["ok"] = True
            latest_version = latest
            verdict = _compare_versions(current_version, latest_version)
        else:
            network["error"] = err
            errors.append({
                "code": ERR_NETWORK_FAILED,
                "message": f"network probe failed: {err}",
                "hint": "agent-plus-meta upgrade-check is best-effort; "
                        "next probe in ~60min retries",
                "recoverable": True,
            })
            # On failure, prefer the prior cache if any, else verdict=unknown.
            if cache and isinstance(cache.get("latest_version"), str):
                latest_version = cache.get("latest_version")
                verdict = "unknown"
            else:
                latest_version = None
                verdict = "unknown"

    # ─── snooze housekeeping: reset when latest_version moved past snooze ───
    if (
        snooze.get("active")
        and snooze.get("snoozed_for_version")
        and latest_version
        and snooze.get("snoozed_for_version") != latest_version
        and snooze.get("ladder_step") != "never"
    ):
        snooze = {
            "active": False,
            "expires_ts": None,
            "ladder_step": "none",
            "snoozed_for_version": None,
        }
        _write_snooze(snooze)
    elif snooze.get("active") and not _snooze_currently_active(snooze, now=now):
        # Snooze TTL elapsed — clear it. (ladder_step="never" stays active.)
        snooze = {
            "active": False,
            "expires_ts": None,
            "ladder_step": "none",
            "snoozed_for_version": None,
        }
        _write_snooze(snooze)

    # ─── handle --snooze flag (after the above housekeeping) ─────────────────
    # argparse `choices=("24h","48h","7d","never")` rejects unknown values
    # before this code runs, so we don't need a defensive validation branch.
    snooze_flag = getattr(args, "snooze", None)
    if snooze_flag:
        # Advance the ladder when the requested step matches the canonical
        # progression; otherwise snap to the requested step.
        current_step = snooze.get("ladder_step", "none")
        if current_step == "none":
            target_step = snooze_flag
        elif _next_ladder_step(current_step) == snooze_flag:
            target_step = snooze_flag
        else:
            target_step = snooze_flag
        snooze = _apply_snooze(target_step, latest_version=latest_version, now=now)
        _write_snooze(snooze)

    # ─── persist cache (only when we got a usable verdict from the network) ──
    if not use_cache and verdict in ("up_to_date", "upgrade_available"):
        ttl_to_persist = (
            TTL_UP_TO_DATE_SEC if verdict == "up_to_date" else TTL_UPGRADE_AVAILABLE_SEC
        )
        try:
            _write_json(_cache_path(), {
                "last_check_ts": now,
                "ttl_sec": ttl_to_persist,
                "current_version": current_version,
                "latest_version": latest_version,
                "result": verdict,
            })
            if cache_ttl_sec == 0:
                cache_ttl_sec = ttl_to_persist
        except OSError:
            # Don't fail the probe if the cache write fails.
            pass

    elapsed_ms_total = int((time.monotonic() - started) * 1000)

    return _build_envelope(
        verdict=verdict,
        current=current_version,
        latest=latest_version,
        cache_hit=use_cache,
        cache_age_sec=cache_age_sec,
        cache_ttl_sec=cache_ttl_sec,
        snooze=snooze,
        config=config,
        network=network,
        elapsed_ms_total=elapsed_ms_total,
        errors=errors,
    )

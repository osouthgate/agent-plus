"""skill-plus routine -- boomerang subcommand: adopt or dismiss a routine/habit candidate.

Reads routine_candidates_log_path() to look up a candidate by id, then appends
one record to routines_adopted_log_path() (append-only, last-write-wins per id).

Helpers (routine_candidates_log_path, routines_adopted_log_path, _ensure_dir,
_now_iso, scrub_text) are injected into this module's namespace by the bin
shell.
"""
from __future__ import annotations

import json
from pathlib import Path


def _read_candidates(path: Path) -> dict[str, dict]:
    """Return {id: record} from a JSONL file. Returns {} if absent or unreadable."""
    by_id: dict[str, dict] = {}
    if not path.exists():
        return by_id
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return by_id
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rid = rec.get("id")
            if isinstance(rid, str):
                by_id[rid] = rec
    return by_id


def _append_record(path: Path, record: dict) -> None:
    """Append one JSON line to the log. Creates parent dirs if needed."""
    _ensure_dir(path)  # noqa: F821 -- injected
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def run(args, emit_fn) -> int:
    candidate_id: str | None = getattr(args, "adopt", None) or None
    status = "adopted"
    if candidate_id is None:
        candidate_id = getattr(args, "dismiss", None) or None
        status = "dismissed"

    # bin enforces mutex-required, so one of the two is set; guard anyway
    if not candidate_id:
        emit_fn({
            "ok": False,
            "error": "missing_id",
            "message": "one of --adopt or --dismiss is required",
        })
        return 1

    # Look up candidate in routine-candidates log
    candidates_path = routine_candidates_log_path()  # noqa: F821 -- injected
    by_id = _read_candidates(candidates_path)

    if candidate_id not in by_id:
        emit_fn({
            "ok": False,
            "error": "unknown_id",
            "id": candidate_id,
            "message": (
                f"id '{candidate_id}' not found in routine candidates log"
                " -- run skill-plus propose --kind all to list available ids"
            ),
        })
        return 1

    rec = by_id[candidate_id]
    # routines-adopted.jsonl is append-only (no full-rewrite pass like
    # candidates.jsonl/routine-candidates.jsonl gets), so it never has a
    # later chance to rescrub what lands here -- scrub at write time instead.
    cluster_key: str = scrub_text(rec.get("key") or "") or ""  # noqa: F821 -- injected

    # For habit kind: scheduleString is null; use suggestion text instead.
    schedule_string: str = scrub_text(
        rec.get("scheduleString") or rec.get("suggestion") or ""
    ) or ""  # noqa: F821 -- injected

    ts = _now_iso()  # noqa: F821 -- injected

    adopted_record = {
        "id": candidate_id,
        "clusterKey": cluster_key,
        "scheduleString": schedule_string,
        "status": status,
        "ts": ts,
    }

    adopted_path = routines_adopted_log_path()  # noqa: F821 -- injected
    _append_record(adopted_path, adopted_record)

    emit_fn({
        "ok": True,
        "id": candidate_id,
        "status": status,
        "scheduleString": schedule_string,
        "recordedAt": ts,
    })
    return 0

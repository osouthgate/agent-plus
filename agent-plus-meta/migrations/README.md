# agent-plus-meta migrations

Drop-in directory for one-time per-workspace migration scripts that the
`agent-plus-meta upgrade` runner applies between framework releases.

Empty on day one (v0.13.5). The runner ships, the directory exists, and the
contract below is documented so the first breaking change has somewhere to
land.

## Contract

A migration is a single Python module named after its **destination
version** with `.` replaced by `_`:

```
agent-plus-meta/migrations/v0_13_5.py        # dest version 0.13.5
agent-plus-meta/migrations/v0_14_0.py        # dest version 0.14.0
```

Each module MUST expose:

```python
from pathlib import Path

def migrate(workspace: Path) -> dict:
    """Apply this migration against ``workspace`` (typically ``~/.agent-plus``).

    Returns a dict with three keys:

      status:  "ok" | "skipped" | "failed"
      message: str
      changes: list   # for the JSON envelope's audit trail
    """
```

### Idempotency

`migrate()` MUST be safe to re-run. If the workspace is already at the
destination state the function MUST return:

```python
{"status": "skipped", "message": "already applied", "changes": []}
```

The runner records every successful application to
`~/.agent-plus/migrations.json` keyed by the migration id (the filename
stem, e.g. `v0_13_5`). It will skip ids it has already recorded — but a
defensive `status: "skipped"` return is still required because users may
delete `migrations.json` to "force re-run" and a destructive migration
must not eat their workspace twice.

### Failure mode

Raise an exception OR return `{"status": "failed", ...}`. Either signals
the upgrade flow to roll back the bin replacement from the `.bak`
directory written earlier in the upgrade run. Failed migrations are
recorded in `migrations.json` so subsequent upgrade runs skip them
unless the user passes `--force`.

### Selection

The runner sorts available migrations by the natural-sort key of their
destination version. It applies every migration whose destination is in
`(LAST_SETUP_VERSION, LATEST_VERSION]` — i.e. strictly greater than the
last-recorded setup version, less-or-equal to the new framework version
being installed.

### Stdlib-only

No third-party imports. `pathlib`, `json`, `subprocess` (list-form), `os`
— same constraints as the rest of agent-plus.

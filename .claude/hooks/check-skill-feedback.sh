#!/usr/bin/env bash
#
# Stop hook: nudge the agent to log skill-feedback before stopping.
#
# DRAFT — opt-in only. Does nothing unless the marker file
#   <project-or-home>/.agent-plus/skill-feedback/.enabled
# exists. To wire it into the session-end flow, ALSO add this entry to
# .claude/settings.json under "hooks.Stop":
#
#   {
#     "hooks": [
#       {
#         "type": "command",
#         "command": "bash $CLAUDE_PROJECT_DIR/.claude/hooks/check-skill-feedback.sh"
#       }
#     ]
#   }
#
# Behaviour:
#   - Marker absent           → exit 0 silently. (Default. Shipping the
#                               file in the repo never changes session
#                               behaviour until the user opts in.)
#   - Marker present + recent → exit 0 (a log entry was written this
#     log entry under the    session; no nudge needed).
#     storage root
#   - Marker present + no     → exit 2 with a stderr nudge telling the
#     recent log entry         agent to run `skill-feedback log <skill>
#                              --rating ... --outcome ...` for whichever
#                              skill it just used.
#
# The "recent" window is 10 minutes by default. Override via the env var
# SKILL_FEEDBACK_FRESH_SECS (integer seconds).
#
# This hook does NOT enforce per-skill coverage — the Stop hook can't
# reliably tell which skills ran in the session. It's a soft prompt, not
# a gate. Skill authors who want stricter coverage should write their
# own per-skill hook in their plugin's repo.

set -u

# Resolve storage root the same way the CLI does: SKILL_FEEDBACK_DIR env
# wins; else <git-toplevel>/.agent-plus/skill-feedback/; else cwd
# fallback; else ~/.agent-plus/skill-feedback/.
storage_root() {
    if [ -n "${SKILL_FEEDBACK_DIR:-}" ]; then
        printf '%s\n' "$SKILL_FEEDBACK_DIR"
        return
    fi
    local top
    top=$(git rev-parse --show-toplevel 2>/dev/null) || top=""
    if [ -n "$top" ]; then
        printf '%s\n' "$top/.agent-plus/skill-feedback"
        return
    fi
    if [ -d "$PWD/.agent-plus" ]; then
        printf '%s\n' "$PWD/.agent-plus/skill-feedback"
        return
    fi
    printf '%s\n' "$HOME/.agent-plus/skill-feedback"
}

root=$(storage_root)
marker="$root/.enabled"

# Off by default. Shipping this file in the repo is a no-op until the
# user creates the marker.
[ -f "$marker" ] || exit 0

fresh_secs=${SKILL_FEEDBACK_FRESH_SECS:-600}

# Did any *.jsonl file under the storage root get touched in the last
# $fresh_secs seconds? `find -newermt` would be cleanest but isn't
# portable; use mtime instead.
recent_count=0
if [ -d "$root" ]; then
    # mtime in minutes, rounded down. fresh_secs / 60.
    fresh_mins=$(( fresh_secs / 60 ))
    [ "$fresh_mins" -lt 1 ] && fresh_mins=1
    recent_count=$(find "$root" -maxdepth 1 -type f -name '*.jsonl' \
        -mmin "-$fresh_mins" 2>/dev/null | wc -l | tr -d ' ')
fi

if [ "${recent_count:-0}" -gt 0 ]; then
    exit 0
fi

{
    echo "skill-feedback: no log entry written in this session."
    echo ""
    echo "If you used any skill (yours or another agent-plus plugin's),"
    echo "append a one-line self-assessment before stopping:"
    echo ""
    echo "  skill-feedback log <skill-name> \\"
    echo "    --rating 1-5 \\"
    echo "    --outcome success|partial|failure \\"
    echo "    [--friction \"<short label>\"]"
    echo ""
    echo "Be honest — over-positive ratings waste the skill author's time."
    echo "Free-text fields are scrubbed for token patterns before write."
    echo ""
    echo "(This nudge is gated by $marker — delete that file to disable.)"
} >&2

exit 2

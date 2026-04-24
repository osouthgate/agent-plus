#!/usr/bin/env bash
#
# Stop hook: fail if a plugin's bin/ or SKILL.md changed on this branch
# without a matching README.md or CHANGELOG.md update.
#
# Enforces the rule in AGENTS.md: docs and changelog must move in lockstep
# with code. Prevents the kind of drift where a shipped feature still has
# "not supported yet" in the README (see: hermes-remote chat, 2026-04).
#
# Exit 2 + stderr = Stop hook feeds the message back to Claude and asks it
# to keep working.

set -u

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repo_root" || exit 0

# Compare against the closest upstream base. If neither origin/main nor main
# exists (e.g. fresh clone, detached HEAD), bail quietly — nothing to check.
base=""
for ref in origin/main main origin/master master; do
    if git rev-parse --verify --quiet "$ref" >/dev/null 2>&1; then
        base=$(git merge-base HEAD "$ref" 2>/dev/null) || true
        [ -n "$base" ] && break
    fi
done
[ -z "$base" ] && exit 0
[ "$base" = "$(git rev-parse HEAD)" ] && exit 0  # on the base branch itself

# All committed changes on this branch since diverging from base.
changed=$(git diff --name-only "$base" HEAD 2>/dev/null | sort -u)
[ -z "$changed" ] && exit 0

# Plugin dirs = direct children that have a bin/<name> or skills/<name>/SKILL.md.
drift=()
for dir in */; do
    plugin=${dir%/}
    case "$plugin" in .claude|node_modules|.git) continue ;; esac
    [ -d "$plugin/bin" ] || [ -d "$plugin/skills" ] || continue

    # Did this plugin's code or skill change?
    code_changed=$(printf '%s\n' "$changed" | grep -E "^${plugin}/(bin/|skills/.*SKILL\.md)" || true)
    [ -z "$code_changed" ] && continue

    readme_changed=$(printf '%s\n' "$changed" | grep -E "^${plugin}/README\.md$" || true)
    changelog_changed=$(printf '%s\n' "$changed" | grep -E "^${plugin}/CHANGELOG\.md$" || true)

    missing=()
    [ -z "$readme_changed" ] && missing+=("README.md")
    [ -z "$changelog_changed" ] && missing+=("CHANGELOG.md")

    if [ ${#missing[@]} -gt 0 ]; then
        drift+=("$plugin: code/skill changed but $(IFS=, ; echo "${missing[*]}") did not")
    fi
done

if [ ${#drift[@]} -gt 0 ]; then
    {
        echo "agent-plus: doc drift detected on this branch."
        echo ""
        printf '  - %s\n' "${drift[@]}"
        echo ""
        echo "Per AGENTS.md, when you modify a plugin's bin/ or SKILL.md you MUST"
        echo "update that plugin's README.md and append a CHANGELOG.md entry before"
        echo "stopping. Update them now, commit, and try again."
    } >&2
    exit 2
fi

exit 0

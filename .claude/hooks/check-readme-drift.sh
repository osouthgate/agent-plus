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

# New plugin detection: a <plugin>/.claude-plugin/plugin.json ADDED on this
# branch means a whole new plugin landed. Root-level docs and manifest must
# move too, or the plugin ships orphaned (not in the root table, not in the
# marketplace, not discoverable).
new_plugin_manifests=$(git diff --name-only --diff-filter=A "$base" HEAD 2>/dev/null \
    | grep -E '^[^/]+/\.claude-plugin/plugin\.json$' || true)

for manifest in $new_plugin_manifests; do
    plugin=${manifest%%/*}
    missing=()

    # Root README must mention the new plugin somewhere (table row, link, etc.)
    printf '%s\n' "$changed" | grep -qE "^README\.md$" \
        && grep -q "$plugin" README.md 2>/dev/null \
        || missing+=("root README.md mention")

    # Marketplace must list the plugin
    if [ -f .claude-plugin/marketplace.json ]; then
        printf '%s\n' "$changed" | grep -qE '^\.claude-plugin/marketplace\.json$' \
            && grep -q "\"$plugin\"" .claude-plugin/marketplace.json 2>/dev/null \
            || missing+=(".claude-plugin/marketplace.json entry")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        drift+=("$plugin: new plugin added but missing $(IFS=, ; echo "${missing[*]}")")
    fi
done

if [ ${#drift[@]} -gt 0 ]; then
    {
        echo "agent-plus: doc drift detected on this branch."
        echo ""
        printf '  - %s\n' "${drift[@]}"
        echo ""
        echo "Per AGENTS.md:"
        echo "  - When you modify a plugin's bin/ or SKILL.md you MUST update its"
        echo "    README.md and append a CHANGELOG.md entry before stopping."
        echo "  - When you ADD a whole new plugin, you MUST also add it to the"
        echo "    root README.md plugin table and .claude-plugin/marketplace.json,"
        echo "    AND remind the user to run: gh repo edit --add-topic <name>."
        echo "Update them now, commit, and try again."
    } >&2
    exit 2
fi

exit 0

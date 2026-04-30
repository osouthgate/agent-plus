#!/bin/sh
# install.sh — agent-plus framework one-shot installer.
#
# Downloads the five framework primitive bin files from GitHub raw, makes
# them executable, and drops them into ~/.local/bin (or
# $AGENT_PLUS_INSTALL_DIR if set). Pure POSIX shell — no bashisms.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/osouthgate/agent-plus/main/install.sh | sh
#   AGENT_PLUS_INSTALL_DIR=$HOME/bin sh install.sh
#   sh install.sh --dry-run        # print what would happen, install nothing
#
# Verify post-install:
#   agent-plus-meta doctor --pretty

set -e

# ─── config ──────────────────────────────────────────────────────────────────

REPO_RAW="https://raw.githubusercontent.com/osouthgate/agent-plus/main"
INSTALL_DIR="${AGENT_PLUS_INSTALL_DIR:-$HOME/.local/bin}"

# Primitives shipped from the framework marketplace.
PRIMITIVES="agent-plus-meta repo-analyze diff-summary skill-feedback skill-plus"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=1
            ;;
        -h|--help)
            sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "install.sh: unknown argument: $arg" >&2
            echo "usage: sh install.sh [--dry-run]" >&2
            exit 2
            ;;
    esac
done

# ─── helpers ─────────────────────────────────────────────────────────────────

# Count primitives portably (no arrays in /bin/sh).
TOTAL=0
for _p in $PRIMITIVES; do
    TOTAL=$((TOTAL + 1))
done

print_header() {
    echo "agent-plus framework installer"
    echo "=============================="
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "(dry run — nothing will be downloaded or written)"
    fi
}

print_footer() {
    echo ""
    echo "Done. Add $INSTALL_DIR to PATH if it's not already:"
    # shellcheck disable=SC2016
    echo "  echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.bashrc"
    echo ""
    echo "Verify:"
    echo "  agent-plus-meta doctor --pretty"
}

install_one() {
    plugin="$1"
    index="$2"
    url="$REPO_RAW/$plugin/bin/$plugin"
    target="$INSTALL_DIR/$plugin"

    if [ "$DRY_RUN" -eq 1 ]; then
        printf "[%d/%d] %-18s would download %s -> %s\n" \
            "$index" "$TOTAL" "$plugin" "$url" "$target"
        return 0
    fi

    if ! curl -fsSL "$url" -o "$target.tmp"; then
        printf "[%d/%d] %-18s FAILED to download %s\n" \
            "$index" "$TOTAL" "$plugin" "$url" >&2
        rm -f "$target.tmp"
        return 1
    fi

    mv "$target.tmp" "$target"
    if ! chmod 755 "$target" 2>/dev/null; then
        printf "[%d/%d] %-18s FAILED to chmod %s\n" \
            "$index" "$TOTAL" "$plugin" "$target" >&2
        rm -f "$target"
        return 1
    fi
    printf "[%d/%d] %-18s installed at %s\n" \
        "$index" "$TOTAL" "$plugin" "$target"
}

# ─── main ────────────────────────────────────────────────────────────────────

print_header

if [ "$DRY_RUN" -eq 0 ]; then
    if [ ! -d "$INSTALL_DIR" ]; then
        mkdir -p "$INSTALL_DIR"
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo "install.sh: curl is required but not found on PATH" >&2
        exit 1
    fi
fi

i=0
failed=""
for plugin in $PRIMITIVES; do
    i=$((i + 1))
    if ! install_one "$plugin" "$i"; then
        failed="$failed $plugin"
    fi
done

if [ -n "$failed" ]; then
    echo "" >&2
    echo "install.sh: the following primitive(s) failed to install:$failed" >&2
    echo "Re-run install.sh after fixing the network issue, or install missing pieces manually." >&2
    exit 1
fi

print_footer

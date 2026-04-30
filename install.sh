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
#   sh install.sh --no-init        # skip the agent-plus-meta init chain (CI)
#   sh install.sh --unattended     # no prompts, accept defaults, exit 0 on partial install
#
# Verify post-install:
#   agent-plus-meta doctor --pretty

set -e

# ─── config ──────────────────────────────────────────────────────────────────

REPO_RAW="https://raw.githubusercontent.com/osouthgate/agent-plus/main"
INSTALL_DIR="${AGENT_PLUS_INSTALL_DIR:-$HOME/.local/bin}"

# Primitives shipped from the framework marketplace.
PRIMITIVES="agent-plus-meta repo-analyze diff-summary skill-feedback skill-plus"

# ─── verb dispatcher ─────────────────────────────────────────────────────────
#
# Generic --<verb> dispatch skeleton. v0.13.0 only implements VERB=install
# (the existing behaviour, the default verb when none is specified). The
# upgrade/uninstall branches exist as stubs that exit 2 — v0.13.5 fills in
# `--upgrade`, v0.15.0 fills in `--uninstall`. This refactor is no-op for
# every existing invocation.
#
# Today:   install.sh [--dry-run] [--no-init] [--unattended]
#          install.sh --help
#          install.sh                                      (default = install)
# Future:  install.sh --upgrade [flags]                    (v0.13.5)
#          install.sh --uninstall [flags]                  (v0.15.0)

VERB="install"
# Peek at the first arg only — verbs are positional-leading, never deep in
# the flag list. This keeps the parser unambiguous and lets the install verb
# preserve flag order exactly as before.
case "${1:-}" in
    --upgrade)
        VERB="upgrade"
        shift
        ;;
    --uninstall)
        VERB="uninstall"
        shift
        ;;
esac

dispatch_upgrade() {
    echo "install.sh: agent-plus-meta upgrade not yet implemented; ships in v0.13.5" >&2
    exit 2
}

dispatch_uninstall() {
    echo "install.sh: agent-plus-meta uninstall not yet implemented; ships in v0.15.0" >&2
    exit 2
}

case "$VERB" in
    upgrade)   dispatch_upgrade ;;
    uninstall) dispatch_uninstall ;;
    install)   : ;; # fall through to existing install parser below
esac

DRY_RUN=0
NO_INIT=0
UNATTENDED=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=1
            ;;
        --no-init)
            NO_INIT=1
            ;;
        --unattended)
            UNATTENDED=1
            ;;
        -h|--help)
            sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "install.sh: unknown argument: $arg" >&2
            echo "usage: sh install.sh [--dry-run] [--no-init] [--unattended]" >&2
            echo "       sh install.sh --upgrade    (v0.13.5+)" >&2
            echo "       sh install.sh --uninstall  (v0.15.0+)" >&2
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
    if [ "$UNATTENDED" -eq 1 ]; then
        echo "(unattended mode — no prompts, accept defaults, exit 0 on partial install)"
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
        printf "[install_sh_curl_failed] %s: failed to download %s\n" \
            "$plugin" "$url" >&2
        rm -f "$target.tmp"
        return 1
    fi

    mv "$target.tmp" "$target"
    if ! chmod 755 "$target" 2>/dev/null; then
        printf "[%d/%d] %-18s FAILED to chmod %s\n" \
            "$index" "$TOTAL" "$plugin" "$target" >&2
        printf "[install_sh_curl_failed] %s: failed to chmod %s\n" \
            "$plugin" "$target" >&2
        rm -f "$target"
        return 1
    fi
    printf "[%d/%d] %-18s installed at %s\n" \
        "$index" "$TOTAL" "$plugin" "$target"
}

# Locate agent-plus-meta after install: prefer PATH, fall back to INSTALL_DIR.
locate_agent_plus_meta() {
    if command -v agent-plus-meta >/dev/null 2>&1; then
        command -v agent-plus-meta
        return 0
    fi
    if [ -x "$INSTALL_DIR/agent-plus-meta" ]; then
        echo "$INSTALL_DIR/agent-plus-meta"
        return 0
    fi
    return 1
}

# ─── main ────────────────────────────────────────────────────────────────────

print_header

if [ "$DRY_RUN" -eq 0 ]; then
    if [ ! -d "$INSTALL_DIR" ]; then
        mkdir -p "$INSTALL_DIR"
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo "install.sh: curl is required but not found on PATH" >&2
        if [ "$UNATTENDED" -eq 1 ]; then
            echo "[install_sh_curl_failed] env: curl not on PATH" >&2
            # In unattended mode we still exit 0; the caller inspects the
            # summary line to decide what to do next.
            echo "install.sh: unattended — no primitives could be installed" >&2
            exit 0
        fi
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
    if [ "$UNATTENDED" -eq 1 ]; then
        echo "install.sh: unattended mode — exit 0 despite partial install." >&2
        echo "install.sh: caller should parse [install_sh_curl_failed] lines for failures." >&2
        # Fall through to the chain attempt; if agent-plus-meta itself failed,
        # locate_agent_plus_meta will skip the chain cleanly.
    else
        echo "Re-run install.sh after fixing the network issue, or install missing pieces manually." >&2
        exit 1
    fi
fi

# ─── chain into agent-plus-meta init ────────────────────────────────────────
#
# Default: run the interactive wizard so a fresh `curl | sh` lands the user
# straight into onboarding. Skipped on:
#   - --no-init       (CI escape hatch)
#   - --dry-run       (no side effects, ever)
#   - agent-plus-meta unreachable (partial install under --unattended)

if [ "$DRY_RUN" -eq 1 ]; then
    # In dry-run, surface what *would* happen so users (and tests) can verify
    # the chain is wired up without invoking it.
    if [ "$NO_INIT" -eq 1 ]; then
        echo ""
        echo "(dry run) would skip agent-plus-meta init (--no-init)"
    elif [ "$UNATTENDED" -eq 1 ]; then
        echo ""
        echo "(dry run) would chain: agent-plus-meta init --non-interactive --auto"
    else
        echo ""
        echo "(dry run) would chain: agent-plus-meta init"
    fi
    # Dry-run never executes; print footer and exit.
    if [ -z "$failed" ]; then
        print_footer
    fi
    exit 0
fi

if [ "$NO_INIT" -eq 1 ]; then
    echo ""
    echo "Skipping agent-plus-meta init (--no-init)."
    if [ -z "$failed" ]; then
        print_footer
    fi
    exit 0
fi

apm_bin=$(locate_agent_plus_meta 2>/dev/null || true)
if [ -z "$apm_bin" ]; then
    echo "" >&2
    echo "install.sh: agent-plus-meta not reachable on PATH or in $INSTALL_DIR — skipping init chain." >&2
    echo "Hint: add $INSTALL_DIR to PATH and run: agent-plus-meta init" >&2
    if [ -z "$failed" ]; then
        print_footer
    fi
    exit 0
fi

echo ""
if [ "$UNATTENDED" -eq 1 ]; then
    echo "Running agent-plus-meta init --non-interactive --auto..."
    # Don't let init's exit code take down the install script in unattended
    # mode — the JSON envelope on stdout is the contract.
    "$apm_bin" init --non-interactive --auto || true
else
    echo "Running agent-plus-meta init..."
    "$apm_bin" init
fi

if [ -z "$failed" ]; then
    print_footer
fi
exit 0

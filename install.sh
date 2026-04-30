#!/bin/sh
# install.sh — agent-plus framework one-shot installer.
#
# Downloads a single tarball (release tag or main branch) and installs each of
# the five framework primitives as a complete plugin tree under $PREFIX, with
# small wrapper shims dropped into $INSTALL_DIR. Pure POSIX shell — no bashisms.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/osouthgate/agent-plus/main/install.sh | sh
#   AGENT_PLUS_INSTALL_DIR=$HOME/bin sh install.sh
#   AGENT_PLUS_PREFIX=$HOME/.local/share/ap sh install.sh
#   AGENT_PLUS_VERSION=0.15.1 sh install.sh
#   sh install.sh --dry-run        # print what would happen, install nothing
#   sh install.sh --no-init        # skip the agent-plus-meta init chain (CI)
#   sh install.sh --unattended     # no prompts, accept defaults, exit 0 on partial install
#
# Verify post-install:
#   agent-plus-meta doctor --pretty

set -e

# ─── config ──────────────────────────────────────────────────────────────────

REPO_OWNER="osouthgate"
REPO_NAME="agent-plus"
INSTALL_DIR="${AGENT_PLUS_INSTALL_DIR:-$HOME/.local/bin}"
PREFIX="${AGENT_PLUS_PREFIX:-$HOME/.local/share/agent-plus}"

# Primitives shipped from the framework marketplace.
PRIMITIVES="agent-plus-meta repo-analyze diff-summary skill-feedback skill-plus"

# ─── verb dispatcher ─────────────────────────────────────────────────────────

VERB="install"
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
    if command -v agent-plus-meta >/dev/null 2>&1; then
        exec agent-plus-meta upgrade "$@"
    fi
    candidate="${AGENT_PLUS_INSTALL_DIR:-$HOME/.local/bin}/agent-plus-meta"
    if [ -x "$candidate" ]; then
        exec "$candidate" upgrade "$@"
    fi
    echo "install.sh: agent-plus-meta not on PATH or in $candidate" >&2
    echo "install.sh: re-install via 'curl -fsSL .../install.sh | sh' first" >&2
    exit 2
}

dispatch_uninstall() {
    if command -v agent-plus-meta >/dev/null 2>&1; then
        exec agent-plus-meta uninstall "$@"
    fi
    candidate="${AGENT_PLUS_INSTALL_DIR:-$HOME/.local/bin}/agent-plus-meta"
    if [ -x "$candidate" ]; then
        exec "$candidate" uninstall "$@"
    fi
    # ── self-contained fallback ────────────────────────────────────────────
    fallback_dry=0
    for arg in "$@"; do
        case "$arg" in
            --workspace|--marketplaces|--all|--purge)
                echo "install.sh: agent-plus-meta not reachable; --workspace/--marketplaces/--all/--purge unavailable in fallback mode." >&2
                echo "Hint: re-install first (sh install.sh), then run: agent-plus-meta uninstall <flags>" >&2
                exit 3
                ;;
            --dry-run)
                fallback_dry=1
                ;;
            --non-interactive|--auto|--json)
                # Accepted but a no-op in fallback (we never prompt here).
                ;;
            *)
                echo "install.sh: unknown uninstall argument: $arg" >&2
                exit 2
                ;;
        esac
    done
    fallback_dir="${AGENT_PLUS_INSTALL_DIR:-$HOME/.local/bin}"
    fallback_prefix="${AGENT_PLUS_PREFIX:-$HOME/.local/share/agent-plus}"
    echo "install.sh uninstall (fallback mode — wrappers + trees only)"
    echo "============================================================"
    for primitive in $PRIMITIVES; do
        wrapper="$fallback_dir/$primitive"
        tree="$fallback_prefix/$primitive"
        if [ "$fallback_dry" -eq 1 ]; then
            if [ -e "$wrapper" ] || [ -L "$wrapper" ]; then
                echo "would remove: $wrapper"
            else
                echo "missing:      $wrapper"
            fi
            if [ -d "$tree" ]; then
                echo "would remove: $tree"
            else
                echo "missing:      $tree"
            fi
            continue
        fi
        if [ -e "$wrapper" ] || [ -L "$wrapper" ]; then
            if rm -f "$wrapper"; then
                echo "removed: $wrapper"
            else
                echo "error:   $wrapper" >&2
            fi
        else
            echo "missing: $wrapper"
        fi
        if [ -d "$tree" ]; then
            if rm -rf "$tree"; then
                echo "removed: $tree"
            else
                echo "error:   $tree" >&2
            fi
        else
            echo "missing: $tree"
        fi
    done
    exit 0
}

case "$VERB" in
    upgrade)   dispatch_upgrade "$@" ;;
    uninstall) dispatch_uninstall "$@" ;;
    install)   : ;; # fall through to existing install parser below
esac

DRY_RUN=0
NO_INIT=0
UNATTENDED=0
SOURCE_DIR=""
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
        --source-dir=*)
            # Test-only: bypass tarball download; copy from a local tree.
            SOURCE_DIR="${arg#--source-dir=}"
            ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
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

# ─── version / tarball URL resolution ────────────────────────────────────────

resolve_tag() {
    # Resolve the tag to install. Default: AGENT_PLUS_VERSION env var, else
    # latest GitHub release. Fall back to "main" if the API call fails.
    if [ -n "${AGENT_PLUS_VERSION:-}" ]; then
        echo "$AGENT_PLUS_VERSION"
        return 0
    fi
    api="https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/releases/latest"
    json=$(curl -fsSL "$api" 2>/dev/null || true)
    if [ -z "$json" ]; then
        echo "main"
        return 0
    fi
    tag=$(echo "$json" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
        | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
    if [ -z "$tag" ]; then
        echo "main"
        return 0
    fi
    echo "$tag"
}

tarball_url_for() {
    # tag may be "main" (branch) or "v0.15.1" / "0.15.1" (tag).
    tag="$1"
    case "$tag" in
        main|master)
            echo "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/heads/$tag.tar.gz"
            ;;
        v*)
            echo "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/tags/$tag.tar.gz"
            ;;
        *)
            # Bare semver: prepend v.
            echo "https://github.com/$REPO_OWNER/$REPO_NAME/archive/refs/tags/v$tag.tar.gz"
            ;;
    esac
}

# ─── helpers ─────────────────────────────────────────────────────────────────

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
    echo "Plugin trees installed under: $PREFIX"
    echo "Wrapper shims installed under: $INSTALL_DIR"
    echo ""
    echo "Add $INSTALL_DIR to PATH if it's not already:"
    # shellcheck disable=SC2016
    echo "  echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.bashrc"
    echo ""
    echo "Verify:"
    echo "  agent-plus-meta doctor --pretty"
}

write_wrapper() {
    plugin="$1"
    target="$INSTALL_DIR/$plugin"
    cat > "$target" <<EOF
#!/bin/sh
# Auto-generated by agent-plus install.sh — do not edit.
# Wrapper for $plugin. The real bin lives at:
#   \$AGENT_PLUS_PREFIX/$plugin/bin/$plugin
PREFIX="\${AGENT_PLUS_PREFIX:-\$HOME/.local/share/agent-plus}"
exec python3 "\$PREFIX/$plugin/bin/$plugin" "\$@"
EOF
    chmod 755 "$target"
}

install_from_src() {
    src_root="$1"
    i=0
    failed_local=""
    for plugin in $PRIMITIVES; do
        i=$((i + 1))
        src="$src_root/$plugin"
        dst="$PREFIX/$plugin"
        if [ ! -d "$src" ]; then
            printf "[%d/%d] %-18s MISSING in source tree (%s)\n" \
                "$i" "$TOTAL" "$plugin" "$src" >&2
            printf "[install_sh_extract_failed] %s: missing in tarball\n" \
                "$plugin" >&2
            failed_local="$failed_local $plugin"
            continue
        fi
        rm -rf "$dst"
        # cp -r is portable; on Windows Git Bash this works fine.
        cp -r "$src" "$dst"
        write_wrapper "$plugin"
        printf "[%d/%d] %-18s installed at %s (wrapper: %s)\n" \
            "$i" "$TOTAL" "$plugin" "$dst" "$INSTALL_DIR/$plugin"
    done
    if [ -n "$failed_local" ]; then
        echo "$failed_local"
        return 1
    fi
    return 0
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

TAG=$(resolve_tag)
TARBALL=$(tarball_url_for "$TAG")

if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "tag:          $TAG"
    echo "tarball:      $TARBALL"
    echo "prefix:       $PREFIX"
    echo "install dir:  $INSTALL_DIR"
    echo ""
    i=0
    for plugin in $PRIMITIVES; do
        i=$((i + 1))
        printf "[%d/%d] %-18s would install tree at %s and wrapper at %s\n" \
            "$i" "$TOTAL" "$plugin" "$PREFIX/$plugin" "$INSTALL_DIR/$plugin"
    done
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
    exit 0
fi

# Real install path: ensure deps available.
if [ -z "$SOURCE_DIR" ]; then
    if ! command -v curl >/dev/null 2>&1; then
        echo "install.sh: curl is required but not found on PATH" >&2
        if [ "$UNATTENDED" -eq 1 ]; then
            echo "[install_sh_curl_failed] env: curl not on PATH" >&2
            echo "install.sh: unattended — no primitives could be installed" >&2
            exit 0
        fi
        exit 1
    fi
fi
if ! command -v tar >/dev/null 2>&1; then
    echo "install.sh: tar is required but not found on PATH" >&2
    if [ "$UNATTENDED" -eq 1 ]; then
        echo "[install_sh_tar_failed] env: tar not on PATH" >&2
        exit 0
    fi
    exit 1
fi

mkdir -p "$INSTALL_DIR" "$PREFIX"

if [ -n "$SOURCE_DIR" ]; then
    # Test-only path: copy directly from a pre-extracted tree.
    src_root="$SOURCE_DIR"
    if [ ! -d "$src_root" ]; then
        echo "install.sh: --source-dir does not exist: $src_root" >&2
        exit 1
    fi
    echo "Installing from local source: $src_root"
else
    TMPDIR=$(mktemp -d 2>/dev/null || mktemp -d -t agentplus)
    trap 'rm -rf "$TMPDIR"' EXIT
    echo ""
    echo "Downloading $TARBALL ..."
    if ! curl -fsSL "$TARBALL" -o "$TMPDIR/agent-plus.tar.gz"; then
        echo "install.sh: tarball download failed: $TARBALL" >&2
        echo "[install_sh_curl_failed] tarball: $TARBALL" >&2
        if [ "$UNATTENDED" -eq 1 ]; then
            exit 0
        fi
        exit 1
    fi
    if ! tar -xzf "$TMPDIR/agent-plus.tar.gz" -C "$TMPDIR"; then
        echo "install.sh: tarball extraction failed" >&2
        echo "[install_sh_extract_failed] tar -xzf failed" >&2
        exit 1
    fi
    # Find the extracted top-level directory (single dir like "agent-plus-0.15.1").
    src_root=""
    for d in "$TMPDIR"/agent-plus-*/; do
        if [ -d "$d" ]; then
            src_root="${d%/}"
            break
        fi
    done
    if [ -z "$src_root" ]; then
        echo "install.sh: could not find extracted directory under $TMPDIR" >&2
        exit 1
    fi
fi

failed=""
if ! failed_list=$(install_from_src "$src_root"); then
    failed="$failed_list"
fi

if [ -n "$failed" ]; then
    echo "" >&2
    echo "install.sh: the following primitive(s) failed to install:$failed" >&2
    if [ "$UNATTENDED" -eq 1 ]; then
        echo "install.sh: unattended mode — exit 0 despite partial install." >&2
        echo "install.sh: caller should parse [install_sh_extract_failed] lines for failures." >&2
    else
        echo "Re-run install.sh after fixing the issue, or install missing pieces manually." >&2
        exit 1
    fi
fi

# ─── chain into agent-plus-meta init ────────────────────────────────────────

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
    "$apm_bin" init --non-interactive --auto || true
else
    echo "Running agent-plus-meta init..."
    "$apm_bin" init
fi

if [ -z "$failed" ]; then
    print_footer
fi
exit 0

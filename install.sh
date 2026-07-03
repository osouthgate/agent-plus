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
#                                   # NOTE: a checksum MISMATCH is a hard integrity failure,
#                                   # not a "partial install" -- it always exits 1, even
#                                   # under --unattended, and the tarball is discarded.
#   AGENT_PLUS_NO_VERIFY=1 sh install.sh   # skip sha256 verification of the release tarball
#
# Tagged releases install from a self-built release-asset tarball and verify
# it (sha256) against a SHA256SUMS file published alongside it. Releases
# before v0.21.0 predate published checksums and fall back to the unverified
# GitHub auto-archive URL, with a stderr note. main/master installs are
# always unverified (moving target).
#
# Verify post-install:
#   agent-plus-meta doctor --pretty

set -e

# ─── config ──────────────────────────────────────────────────────────────────

REPO_OWNER="osouthgate"
REPO_NAME="agent-plus"
INSTALL_DIR="${AGENT_PLUS_INSTALL_DIR:-$HOME/.local/bin}"
PREFIX="${AGENT_PLUS_PREFIX:-$HOME/.local/share/agent-plus}"
NO_VERIFY="${AGENT_PLUS_NO_VERIFY:-0}"
# Test-only: overrides the release-asset base URL (precedent: --source-dir
# below). Lets the test suite point at a local file:// dir with a seeded
# tarball + SHA256SUMS instead of a real GitHub release. See
# test/test_install_script.py.
ASSET_BASE_URL_OVERRIDE="${AGENT_PLUS_ASSET_BASE_URL:-}"

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
            sed -n '2,29p' "$0" | sed 's/^# \{0,1\}//'
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
        echo "install.sh: could not fetch latest release tag from GitHub API -- installing from main branch (may be unstable). Set AGENT_PLUS_VERSION to pin a release." >&2
        echo "main"
        return 0
    fi
    tag=$(echo "$json" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' \
        | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
    if [ -z "$tag" ]; then
        echo "install.sh: could not parse release tag from GitHub API response -- installing from main branch (may be unstable). Set AGENT_PLUS_VERSION to pin a release." >&2
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

is_release_tag() {
    # A release tag is anything that isn't one of the two branch names we
    # treat as moving targets. Kept in sync with the case pattern above.
    case "$1" in
        main|master) return 1 ;;
        *) return 0 ;;
    esac
}

normalize_tag() {
    # Ensure a "v" prefix: "0.21.0" -> "v0.21.0", "v0.21.0" -> "v0.21.0".
    case "$1" in
        v*) echo "$1" ;;
        *) echo "v$1" ;;
    esac
}

bare_version() {
    # Strip the "v" prefix: "v0.21.0" -> "0.21.0".
    norm=$(normalize_tag "$1")
    echo "${norm#v}"
}

asset_base_url_for() {
    # Base URL for release-asset downloads (the tarball + SHA256SUMS live
    # next to the install.sh/install.ps1 assets on the same release).
    if [ -n "$ASSET_BASE_URL_OVERRIDE" ]; then
        echo "$ASSET_BASE_URL_OVERRIDE"
        return 0
    fi
    tag_norm=$(normalize_tag "$1")
    echo "https://github.com/$REPO_OWNER/$REPO_NAME/releases/download/$tag_norm"
}

asset_tarball_url_for() {
    # The self-built, byte-stable tarball uploaded by .github/workflows/release.yml
    # -- preferred over tarball_url_for()'s GitHub auto-archive for tagged
    # releases, since auto-archives are not guaranteed byte-stable long-term.
    ver=$(bare_version "$1")
    base=$(asset_base_url_for "$1")
    echo "$base/agent-plus-$ver.tar.gz"
}

asset_sums_url_for() {
    base=$(asset_base_url_for "$1")
    echo "$base/SHA256SUMS"
}

# ─── helpers ─────────────────────────────────────────────────────────────────

TOTAL=0
for _p in $PRIMITIVES; do
    TOTAL=$((TOTAL + 1))
done

print_header() {
    echo "agent-plus installer"
    echo "===================="
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
    echo "Register with Claude Code (so Claude can call the plugins directly):"
    echo "  claude plugin marketplace add osouthgate/agent-plus"
    echo "  for p in agent-plus-meta repo-analyze diff-summary skill-feedback skill-plus; do"
    echo "    claude plugin install \$p@agent-plus"
    echo "  done"
    echo ""
    echo "Then in any open Claude session run:"
    echo "  /reload-plugins"
    echo "Or open a new Claude session. First thing to try:"
    echo "  Ask Claude: 'what is this repo?' -- triggers repo-analyze"
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

# Portable sha256 tool discovery. Always exits 0 (safe under `set -e` when
# used as `tool=$(find_hash_tool)`); an empty result means "none found" and
# callers must handle that explicitly.
find_hash_tool() {
    if command -v sha256sum >/dev/null 2>&1; then
        echo "sha256sum"
    elif command -v shasum >/dev/null 2>&1; then
        echo "shasum"
    elif command -v openssl >/dev/null 2>&1; then
        echo "openssl"
    else
        echo ""
    fi
}

sha256_of() {
    # $1 = tool name from find_hash_tool, $2 = file path. Always exits 0 --
    # the final pipeline stage (cut/sed) succeeds regardless of its input.
    tool="$1"
    file="$2"
    case "$tool" in
        sha256sum) sha256sum "$file" | cut -d' ' -f1 ;;
        shasum) shasum -a 256 "$file" | cut -d' ' -f1 ;;
        openssl) openssl dgst -sha256 "$file" | sed 's/^.*= //' ;;
    esac
}

expected_hash_for() {
    # $1 = SHA256SUMS path, $2 = filename to look up within it. Empty
    # output (not a nonzero exit) if there's no matching entry -- keeps
    # this safe to use as `expected=$(expected_hash_for ...)` under `set -e`.
    sums_file="$1"
    name="$2"
    grep "$name" "$sums_file" 2>/dev/null | head -1 | cut -d' ' -f1
}

# ─── main ────────────────────────────────────────────────────────────────────

print_header

TAG=$(resolve_tag)
RELEASE_TAG=0
if is_release_tag "$TAG"; then
    RELEASE_TAG=1
fi
AUTOARCHIVE_TARBALL=$(tarball_url_for "$TAG")
ASSET_TARBALL=""
if [ "$RELEASE_TAG" -eq 1 ]; then
    ASSET_TARBALL=$(asset_tarball_url_for "$TAG")
    TARBALL="$ASSET_TARBALL"
else
    TARBALL="$AUTOARCHIVE_TARBALL"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "tag:          $TAG"
    echo "tarball:      $TARBALL"
    if [ "$RELEASE_TAG" -eq 1 ]; then
        echo "fallback:     $AUTOARCHIVE_TARBALL (used if this release predates published checksums)"
        if [ "$NO_VERIFY" = "1" ]; then
            echo "verification: skipped (AGENT_PLUS_NO_VERIFY=1)"
        else
            echo "verification: sha256, checked against SHA256SUMS from the release; a mismatch aborts the install (set AGENT_PLUS_NO_VERIFY=1 to skip)"
        fi
    else
        echo "verification: skipped ($TAG installs are unverified by design -- pin a release via AGENT_PLUS_VERSION for a verified install)"
    fi
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
if ! command -v python3 >/dev/null 2>&1; then
    echo "install.sh: Python 3 not found on PATH (need python3). Install Python 3 from https://python.org and re-run." >&2
    if [ "$UNATTENDED" -eq 1 ]; then
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
    TARBALL_PATH="$TMPDIR/agent-plus.tar.gz"
    USED_ASSET=0
    echo ""
    if [ "$RELEASE_TAG" -eq 1 ]; then
        echo "Downloading $ASSET_TARBALL ..."
        if curl -fsSL "$ASSET_TARBALL" -o "$TARBALL_PATH" 2>/dev/null; then
            USED_ASSET=1
        else
            echo "install.sh: release asset not found -- this release predates published checksums; verification skipped. Falling back to source archive." >&2
            echo "Downloading $AUTOARCHIVE_TARBALL ..."
            if ! curl -fsSL "$AUTOARCHIVE_TARBALL" -o "$TARBALL_PATH"; then
                echo "install.sh: tarball download failed: $AUTOARCHIVE_TARBALL" >&2
                echo "[install_sh_curl_failed] tarball: $AUTOARCHIVE_TARBALL" >&2
                if [ "$UNATTENDED" -eq 1 ]; then
                    exit 0
                fi
                exit 1
            fi
        fi
    else
        echo "install.sh: installing from $TAG -- unverified (moving target)." >&2
        echo "Downloading $AUTOARCHIVE_TARBALL ..."
        if ! curl -fsSL "$AUTOARCHIVE_TARBALL" -o "$TARBALL_PATH"; then
            echo "install.sh: tarball download failed: $AUTOARCHIVE_TARBALL" >&2
            echo "[install_sh_curl_failed] tarball: $AUTOARCHIVE_TARBALL" >&2
            if [ "$UNATTENDED" -eq 1 ]; then
                exit 0
            fi
            exit 1
        fi
    fi

    if [ "$USED_ASSET" -eq 1 ]; then
        if [ "$NO_VERIFY" = "1" ]; then
            echo "install.sh: AGENT_PLUS_NO_VERIFY=1 -- skipping checksum verification." >&2
        else
            hash_tool=$(find_hash_tool)
            if [ -z "$hash_tool" ]; then
                echo "install.sh: no sha256 tool found (sha256sum, shasum, or openssl) -- continuing WITHOUT verification. Set AGENT_PLUS_NO_VERIFY=1 to silence this warning." >&2
            else
                sums_path="$TMPDIR/SHA256SUMS"
                sums_url=$(asset_sums_url_for "$TAG")
                if ! curl -fsSL "$sums_url" -o "$sums_path" 2>/dev/null; then
                    echo "install.sh: could not download SHA256SUMS ($sums_url) -- continuing WITHOUT verification." >&2
                else
                    ver=$(bare_version "$TAG")
                    tarball_name="agent-plus-$ver.tar.gz"
                    expected=$(expected_hash_for "$sums_path" "$tarball_name")
                    if [ -z "$expected" ]; then
                        echo "install.sh: SHA256SUMS has no entry for $tarball_name -- continuing WITHOUT verification." >&2
                    else
                        actual=$(sha256_of "$hash_tool" "$TARBALL_PATH")
                        if [ "$expected" != "$actual" ]; then
                            echo "install.sh: CHECKSUM MISMATCH for $tarball_name" >&2
                            echo "install.sh:   expected $expected" >&2
                            echo "install.sh:   actual   $actual" >&2
                            echo "[install_sh_checksum_failed] $tarball_name expected=$expected actual=$actual" >&2
                            echo "install.sh: downloaded tarball discarded; refusing to install an unverified payload." >&2
                            rm -f "$TARBALL_PATH"
                            exit 1
                        fi
                        echo "Checksum verified (sha256 via $hash_tool): $tarball_name"
                    fi
                fi
            fi
        fi
    fi

    if ! tar -xzf "$TARBALL_PATH" -C "$TMPDIR"; then
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
# Redirect stdout to /dev/null so the machine-readable JSON envelope is silenced.
# All human-readable output goes to stderr and still appears in the terminal.
init_rc=0
if [ "$UNATTENDED" -eq 1 ]; then
    echo "Running agent-plus-meta init --non-interactive --auto..."
    "$apm_bin" init --non-interactive --auto > /dev/null || init_rc=$?
else
    echo "Running agent-plus-meta init..."
    "$apm_bin" init > /dev/null || init_rc=$?
fi
if [ "$init_rc" -ne 0 ]; then
    echo "install.sh: agent-plus-meta init exited with code $init_rc." >&2
    echo "If you see plugin registration instructions above, follow those first." >&2
    echo "Otherwise run 'agent-plus-meta doctor --pretty' to diagnose." >&2
fi

if [ -z "$failed" ]; then
    print_footer
fi
exit 0

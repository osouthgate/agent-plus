#!/bin/sh
# scripts/install-precommit.sh — one-line activation of the agent-plus
# pre-commit hook (.githooks/pre-commit).
#
# What this does:
#   git config core.hooksPath .githooks
#
# That points git at the versioned .githooks/ dir instead of the per-clone
# .git/hooks/ which isn't tracked. After running this, every `git commit`
# in this clone runs the hook unless bypassed via `git commit --no-verify`
# or `SKIP_PRECOMMIT_TESTS=1 git commit ...`.
#
# Why a hook: agent-plus's tests can pass locally but fail in CI when the
# maintainer's `~/.env` or shell env vars (e.g. LINEAR_API_KEY) trigger
# different code paths than CI's clean env. The hook re-runs the suite
# under `env -i` to mirror CI hermeticity. See .githooks/pre-commit for
# the two recent incidents this prevents (v0.12.0 + v0.15.5).
#
# Pure POSIX shell.

set -e

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

if [ ! -f .githooks/pre-commit ]; then
    echo "install-precommit.sh: .githooks/pre-commit not found at $REPO_ROOT" >&2
    echo "  (this script must run from inside an agent-plus checkout)" >&2
    exit 1
fi

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit 2>/dev/null || true

echo "✓ pre-commit hook activated."
echo
echo "Every commit now runs the test suite under \`env -i\` (clean env)."
echo "Bypass when needed: git commit --no-verify"
echo "Or set: SKIP_PRECOMMIT_TESTS=1 git commit -m '...'"
echo
echo "To deactivate: git config --unset core.hooksPath"

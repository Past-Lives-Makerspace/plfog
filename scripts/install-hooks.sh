#!/usr/bin/env bash
# Install the repo's tracked git hooks into this checkout. Run once per clone,
# and again whenever scripts/hooks/* changes. Works in the main checkout and in
# linked worktrees (uses `git rev-parse --git-path` to find the real hooks dir).
set -e
cd "$(git rev-parse --show-toplevel)"

HOOK_DEST="$(git rev-parse --git-path hooks/pre-push)"
cp scripts/hooks/pre-push "$HOOK_DEST"
chmod +x "$HOOK_DEST"
echo "Installed pre-push hook -> $HOOK_DEST"

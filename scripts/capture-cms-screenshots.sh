#!/usr/bin/env bash
# Generate full-page screenshots of every copy-bearing CMS page for review.
#
# Drives a real Chromium against a throwaway live Django server (no dev server
# or database setup needed — it seeds its own data), walks the book CMS, and
# writes PNGs + an index.html contact sheet to ./screenshots/.
#
# Usage:   scripts/capture-cms-screenshots.sh
# Output:  screenshots/index.html  (open it in a browser)
set -euo pipefail
cd "$(dirname "$0")/.."

export CAPTURE_SCREENSHOTS=1
export SHOT_DIR="${SHOT_DIR:-screenshots}"

# Prefer the project venv if present, else fall back to PATH.
PY="python"
PYTEST="pytest"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi
if [ -x ".venv/bin/pytest" ]; then PYTEST=".venv/bin/pytest"; fi

# Make sure the browser is installed (no-op if it already is).
"$PY" -m playwright install chromium >/dev/null 2>&1 || true

"$PYTEST" -m e2e -s tests/e2e/screenshots_spec.py "$@"

echo
echo "Done. Open ${SHOT_DIR}/index.html to review the copy."

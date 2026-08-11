#!/usr/bin/env bash
# Regenerate the help-center screenshots declared in membership/help_content.py.
#
# Drives a real Chromium against a throwaway live Django server (no dev server
# or database setup needed — it seeds its own data), captures every ShotSpec
# into static/help/<article-slug>/, and writes an index.html contact sheet to
# ./screenshots/help/. Run it whenever the UI changes, eyeball the sheet, then
# commit the changed PNGs with the copy change.
#
# Usage:   scripts/capture-help-screenshots.sh
# Output:  screenshots/help/index.html  (open it in a browser)
set -euo pipefail
cd "$(dirname "$0")/.."

export CAPTURE_HELP_SCREENSHOTS=1
export SHOT_DIR="${SHOT_DIR:-screenshots}"

# Prefer the project venv if present, else fall back to PATH.
PY="python"
PYTEST="pytest"
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi
if [ -x ".venv/bin/pytest" ]; then PYTEST=".venv/bin/pytest"; fi

# Make sure the browser is installed (no-op if it already is).
"$PY" -m playwright install chromium >/dev/null 2>&1 || true

"$PYTEST" -m e2e -s tests/e2e/help_screenshots_spec.py "$@"

echo
echo "Done. Open ${SHOT_DIR}/help/index.html to review the shots."

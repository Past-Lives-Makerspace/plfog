#!/usr/bin/env bash
# Build the copy-review Email Copy Gallery locally (what copy-review.yml publishes).
#
# Browserless — needs only Django + factories + pytest (no Chromium). The spec's
# transactional db fixture uses a throwaway test database and rolls back, so this
# never touches your dev data. Output: ${SHOT_DIR:-email-gallery}/index.html
set -euo pipefail
cd "$(dirname "$0")/.."

export BUILD_EMAIL_GALLERY=1
export SHOT_DIR="${SHOT_DIR:-email-gallery}"

pytest -m e2e --no-cov -s tests/e2e/email_gallery_spec.py "$@"

echo "Built. Preview it:"
echo "  python -m http.server 8000 -d ${SHOT_DIR}   # then open http://localhost:8000"

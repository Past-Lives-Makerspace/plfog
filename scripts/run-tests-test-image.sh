#!/usr/bin/env sh
# Like run-tests-ephemeral.sh but uses plfog-web-test:latest (dev image + httpx +
# respx) so the Discord-adapter respx specs can run. Mounts THIS worktree and
# points at the running compose Postgres. Temporary Phase-2 helper.
#
# Usage:
#   scripts/run-tests-test-image.sh pytest tests/core/events --no-cov -q
#   scripts/run-tests-test-image.sh ruff check core/events
#   scripts/run-tests-test-image.sh mypy core/events
set -e
docker run --rm \
  --network plfog_default \
  -v "$(pwd)":/app -w /app \
  -e DATABASE_URL="postgres://plfog:plfog@db:5432/plfog" \
  -e DJANGO_SETTINGS_MODULE=plfog.settings \
  plfog-web-test:latest \
  python -m "$@"

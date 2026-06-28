#!/usr/bin/env sh
# Run pytest/ruff/mypy in an ephemeral plfog-web-dev container that mounts THIS
# worktree and points at the running compose Postgres (`db` service on the
# plfog_default network). The long-running plfog-web-1 container serves a
# DIFFERENT worktree, so we never reuse it for this tree's tests.
#
# Usage:
#   scripts/run-tests-ephemeral.sh pytest tests/core/events --no-cov -q
#   scripts/run-tests-ephemeral.sh ruff check core/events
#   scripts/run-tests-ephemeral.sh mypy core/events
set -e
docker run --rm \
  --network plfog_default \
  -v "$(pwd)":/app -w /app \
  -e DATABASE_URL="postgres://plfog:plfog@db:5432/plfog" \
  -e DJANGO_SETTINGS_MODULE=plfog.settings \
  plfog-web-dev:latest \
  python -m "$@"

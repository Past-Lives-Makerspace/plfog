#!/usr/bin/env bash
set -euo pipefail
set -a; source .env; set +a
DATABASE_URL="$PROD_DATABASE_URL" \
DJANGO_DEBUG=false \
  .venv/bin/python manage.py create_admin_user "$@"

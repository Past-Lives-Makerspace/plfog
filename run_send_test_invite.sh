#!/usr/bin/env bash
set -euo pipefail
set -a; source .env; set +a
DATABASE_URL="$PROD_DATABASE_URL" \
DJANGO_DEBUG=false \
  .venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'plfog.settings')
django.setup()
from core.models import Invite
Invite(email='plazajosue2@gmail.com').send_invite_email()
print('Sent.')
"

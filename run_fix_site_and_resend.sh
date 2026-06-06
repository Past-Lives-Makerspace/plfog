#!/usr/bin/env bash
set -euo pipefail
set -a; source .env; set +a
DATABASE_URL="$PROD_DATABASE_URL" \
DJANGO_DEBUG=false \
  .venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'plfog.settings')
django.setup()

from django.contrib.sites.models import Site
site = Site.objects.get_current()
print(f'Current site domain: {site.domain}')
site.domain = 'members.pastlives.space'
site.name = 'Past Lives Makerspace'
site.save()
print(f'Updated to: {site.domain}')

from core.models import Invite
for email in ['karamy@xposureunlimited.com', 'amykspreadborough@gmail.com']:
    invite = Invite.objects.get(email__iexact=email)
    invite.send_invite_email()
    print(f'Resent invite to {email}')
"

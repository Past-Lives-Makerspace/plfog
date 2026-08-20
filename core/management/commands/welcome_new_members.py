"""Email new paying, active Airtable members their first sign-in link (a welcome).

Off by default — an admin switches it on from Site Settings → Automations. When on, it runs daily
(~6 AM Portland) via ``run_scheduled_tasks``. For each candidate
(:meth:`membership.models.MemberQuerySet.awaiting_welcome_email`) it calls
:meth:`membership.models.Member.send_welcome_email`, which provisions the member's passwordless
account when the Pending→Active import left them without one, sends the branded
``member.login_invite`` email, and stamps ``welcome_email_sent_at`` so nobody is emailed twice.

A one-time migration backfill marked every pre-existing member welcomed, so only members added
after this shipped are ever emailed. One member's failure (e.g. an address that already belongs to
another account) is logged and skipped without aborting the rest of the batch.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership.models import Member


class Command(BaseCommand):
    help = "Email new paying, active Airtable members their first sign-in link. Off by default."

    def handle(self, *args: Any, **options: Any) -> None:
        sent = 0
        skipped = 0
        for member in Member.objects.awaiting_welcome_email():
            try:
                member.send_welcome_email()
                sent += 1
            except Exception as exc:  # noqa: BLE001 — one bad member must not abort the batch
                skipped += 1
                self.stderr.write(self.style.ERROR(f"  ✗ welcome {member.pk} ({member.display_name}): {exc}"))
        self.stdout.write(self.style.SUCCESS(f"Welcomed {sent} new member(s); skipped {skipped}."))

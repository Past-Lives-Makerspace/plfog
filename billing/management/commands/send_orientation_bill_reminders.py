"""Remind members of an unpaid orientation bill 7, 14 and 30 days after it was sent (#1120).

Registered in ``core/scheduled_jobs.py`` as a DAILY job, so it shows on Automations and can be
switched off there. All the work is :func:`billing.orientation_bills.send_due_reminders`: one
reminder per bill per run at most, the latest step due, decided under the booking row lock, so
a rerun the same day sends nothing more. The 30 day reminder also tells the orientor.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from billing.orientation_bills import send_due_reminders


class Command(BaseCommand):
    help = "Remind members of unpaid orientation bills at 7, 14 and 30 days, and tell the orientor at 30."

    def handle(self, *args: Any, **options: Any) -> None:
        sent = send_due_reminders()
        self.stdout.write(f"Sent {sent} orientation bill reminder(s).")

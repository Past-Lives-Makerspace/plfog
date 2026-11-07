"""Remind members of an unpaid orientation bill 7, 14 and 30 days after it was sent (#1120).

Registered in ``core/scheduled_jobs.py`` as a DAILY job, so it shows on Automations and can be
switched off there. All the work is :func:`billing.orientation_bills.send_due_reminders`: one
reminder per bill per run at most, the latest step due, decided under the booking row lock, so
a rerun the same day sends nothing more. The 30 day reminder also tells the orientor. A
reminder that fails keeps the bill's old step for the next run, and fails the run so the
Webmasters hear.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from billing.orientation_bills import send_due_reminders


class Command(BaseCommand):
    help = "Remind members of unpaid orientation bills at 7, 14 and 30 days, and tell the orientor at 30."

    def handle(self, *args: Any, **options: Any) -> None:
        run = send_due_reminders()
        self.stdout.write(f"Sent {run.sent} orientation bill reminder(s).")
        if run.failed:
            # Fail the run so the Webmasters hear; each failed bill kept its old step and is retried.
            raise CommandError(f"{run.failed} orientation bill reminder(s) failed and will be tried again.")

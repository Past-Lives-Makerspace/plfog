"""Remind everyone an email poll went to who has not answered, once, before it closes (#1142 part 4).

Registered in ``core/scheduled_jobs.py`` to run every 15 minutes, so it shows on Automations and
can be switched off there. All the work is :func:`polls.models.send_due_poll_reminders`: each due
poll is claimed once, and one that fails gives its claim back for the next run and fails this one
so the Webmasters hear.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from polls.models import send_due_poll_reminders


class Command(BaseCommand):
    help = "Send each email poll's one reminder to recipients who have not answered."

    def handle(self, *args: Any, **options: Any) -> None:
        run = send_due_poll_reminders(timezone.now())
        self.stdout.write(f"Reminded {run.polls} poll(s); emailed {run.emailed} member(s).")
        if run.failed:
            raise CommandError(f"{run.failed} poll reminder(s) failed and will be tried again.")

"""Make each closed email poll's results email draft, once (#1142 AC15).

Registered in ``core/scheduled_jobs.py`` to run every 15 minutes. All the work is
:func:`polls.models.draft_due_poll_results`: each closed poll is claimed once, and one that fails
gives its claim back for the next run and fails this one so the Webmasters hear. Nothing is sent.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from polls.models import draft_due_poll_results


class Command(BaseCommand):
    help = "Make a results email draft in Email Members for each email poll that has closed."

    def handle(self, *args: Any, **options: Any) -> None:
        run = draft_due_poll_results(timezone.now())
        self.stdout.write(f"Made {run.drafted} poll results draft(s); skipped {run.skipped} with no pick question.")
        if run.failed:
            raise CommandError(f"{run.failed} poll results draft(s) failed and will be tried again.")

"""Fire the ``voting.closing_48h`` reminder 48h before the month-end vote close.

A thin driver over the generalized scheduler (design §2.6): it hands this tick's
voting occurrence (:func:`membership.voting.closing_48h_occurrences`) to
:func:`core.events.scheduler.run_sources`, which due-checks it against the 15-minute
tick window and fires it via ``emit`` — deduped on :class:`core.models.EventDelivery`
by the ``voting:YYYY-MM`` period so a re-run within the same cycle is a no-op.

Supersedes the previous month-end−3-days, in-app-only reminder (which used a
``ScheduledNotificationMarker`` + ``notifications.dispatch("voting_closing_soon")``)
and the dead ``voting_cycle_open`` trigger: there is now ONE voting-reminder path.
Wired into the 15-minute ``run_scheduled_tasks`` cron exactly as before — the command
name and its always-run placement are unchanged, only its mechanism moved onto the
spine.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.events.scheduler import run_sources
from membership.voting import closing_48h_occurrences


class Command(BaseCommand):
    help = "Fire the voting.closing_48h reminder when the cycle close is ~48h away. Safe every 15 min."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        fired = run_sources([closing_48h_occurrences], now=now)
        if fired:
            self.stdout.write(self.style.SUCCESS(f"Fired {fired} voting reminder(s)."))
        else:
            self.stdout.write("No voting reminder due this tick.")

"""Send the facility tour reminders due this tick: 24 hours before each booked tour (#948).

A thin driver over the generalized scheduler, like ``send_event_reminders``: it hands
:func:`tours.notify.tour_reminder_occurrences` to :func:`core.events.scheduler.run_sources`,
which fires the ones due in this 15 minute window. Each reminder's ``period`` names the booking
and its start, so a re-run never sends twice and a cancelled tour is never a candidate.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.events.scheduler import run_sources
from tours.notify import tour_reminder_occurrences


class Command(BaseCommand):
    help = "Send the facility tour reminders due this tick (24 hours before each booked tour)."

    def handle(self, *args: Any, **options: Any) -> None:
        fired = run_sources([tour_reminder_occurrences], now=timezone.now())
        if fired:
            self.stdout.write(self.style.SUCCESS(f"Sent {fired} tour reminder(s)."))
        else:
            self.stdout.write("No tour reminder due this tick.")

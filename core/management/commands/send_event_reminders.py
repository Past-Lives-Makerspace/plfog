"""Fire the community-event reminders + "happening now" pings due this tick.

A thin driver over the generalized scheduler (mirrors ``send_voting_reminders``): it
hands this tick's two sources — :func:`membership.events.event_reminder_occurrences`
(7/3/1-day-before nudges) and :func:`membership.events.event_happening_now_occurrences`
(the "starting now" ping) — to :func:`core.events.scheduler.run_sources`, which
due-checks each against the 15-minute tick window and fires them via ``emit``. Each
occurrence's per-(event, offset) ``period`` dedupes on :class:`core.models.EventDelivery`,
so every-tick execution is safe.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.events.scheduler import run_sources
from membership.events import event_happening_now_occurrences, event_reminder_occurrences


class Command(BaseCommand):
    help = "Fire the community-event reminders and 'happening now' pings due this tick."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        fired = run_sources([event_reminder_occurrences, event_happening_now_occurrences], now=now)
        if fired:
            self.stdout.write(self.style.SUCCESS(f"Fired {fired} event reminder(s)."))
        else:
            self.stdout.write("No event reminder due this tick.")

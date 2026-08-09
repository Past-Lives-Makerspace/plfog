"""Fire the per-member voting reminders N days before the month-end vote close.

A thin driver over the generalized scheduler (design §2.6): it hands this tick's
voting sources — :func:`membership.voting.closing_soon_occurrences` (members who
voted), :func:`membership.voting.vote_soon_occurrences` (signed-in non-voters), and
:func:`membership.voting.officers_closing_soon_occurrences` (guild leadership turnout
heads-up) — to :func:`core.events.scheduler.run_sources`, which due-checks them
against the 15-minute tick window and fires them via ``emit``. Each source self-gates
on the ``VotingSettings`` master switches and windows its own member query; the
``voting:YYYY-MM`` period dedupes each recipient to once per cycle so a re-run is safe.

Wired into the 15-minute ``run_scheduled_tasks`` cron exactly as before — the
command name and its always-run placement are unchanged.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.events.scheduler import run_sources
from membership.voting import (
    closing_soon_occurrences,
    officers_closing_soon_occurrences,
    vote_soon_occurrences,
    voting_discord_reminder_occurrences,
)


class Command(BaseCommand):
    help = "Fire the per-member voting reminders when the cycle close is the configured lead away."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        fired = run_sources(
            [
                closing_soon_occurrences,
                vote_soon_occurrences,
                officers_closing_soon_occurrences,
                voting_discord_reminder_occurrences,
            ],
            now=now,
        )
        if fired:
            self.stdout.write(self.style.SUCCESS(f"Fired {fired} voting reminder(s)."))
        else:
            self.stdout.write("No voting reminder due this tick.")

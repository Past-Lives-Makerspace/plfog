"""Warn ex-members by Discord DM and remove them from the server when the wait is over (#898).

Run weekly by ``run_scheduled_tasks`` while the job is on (it ships off). The
work and its guards live in :func:`membership.discord_removal.process_removals`.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send due Discord removal warnings and remove ex-members whose wait is over."

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.discord_removal import process_removals

        stats = process_removals()
        if not stats.ran:
            self.stdout.write("Discord removals skipped: the bot or the server id is not configured.")
            return
        self.stdout.write(self.style.SUCCESS(f"Handled {stats.handled} Discord removal step(s)."))

"""Queue Discord messages that ping @everyone or a guild role for review in Spotlight (#943).

Runs every tick of ``run_scheduled_tasks``; the work is :func:`core.discord_pings.queue_discord_pings`.
Nothing it queues is live until an admin approves it in Admin Tools > Spotlight.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from core.discord_pings import queue_discord_pings


class Command(BaseCommand):
    help = "Queue Discord @everyone and guild pings for review in Spotlight."

    def handle(self, *args: Any, **options: Any) -> None:
        queued = queue_discord_pings()
        self.stdout.write(f"Queued {queued} Discord message(s) for review.")

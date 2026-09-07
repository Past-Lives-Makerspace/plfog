"""Fold Discord Scheduled-Event Interested marks into event RSVP lists.

Thin wrapper over :func:`membership.interested_sync.sync_interested_rsvps` so the
15-minute scheduled-job dispatcher (and a manual run) can invoke it. All logic lives
in the module; this command only reports the count.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sync Discord Scheduled-Event Interested marks into event RSVPs."

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.interested_sync import sync_interested_rsvps

        changed = sync_interested_rsvps()
        self.stdout.write(f"Interested sync updated {changed} event(s).")

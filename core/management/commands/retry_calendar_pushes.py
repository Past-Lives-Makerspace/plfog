"""Re-push community events whose Google Calendar sync is pending or failed.

Wired into ``run_scheduled_tasks``' always-run tuple (every ~15 minutes). Self-gating:
a no-op when Google sync is off, so it is safe to run on every tick. Only touches
PUBLISHED rows in ``PENDING``/``FAILED`` (``needs_push()``) — pre-existing ``IDLE`` rows
are never mass-backfilled. Bounded per run so a single tick stays cheap.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

_MAX_PER_RUN = 200


class Command(BaseCommand):
    help = "Re-push community events whose Google Calendar sync is pending or failed."

    def handle(self, *args: Any, **options: Any) -> None:
        from core.integrations.google_calendar import GoogleCalendarClient
        from core.models import SiteConfiguration
        from membership.models import CommunityEvent

        if (
            not GoogleCalendarClient.from_settings().enabled
            or not SiteConfiguration.load().google_calendar_sync_enabled
        ):
            self.stdout.write("Google Calendar sync is off — nothing to retry.")
            return

        pushed = 0
        for event in CommunityEvent.objects.needs_push().select_related("guild")[:_MAX_PER_RUN]:
            event.push_to_google()  # best-effort; updates sync_state per event
            pushed += 1
        self.stdout.write(self.style.SUCCESS(f"Retried Google Calendar push for {pushed} event(s)."))

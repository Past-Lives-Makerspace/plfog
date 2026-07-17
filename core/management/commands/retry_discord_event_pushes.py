"""Re-push community events whose Discord Scheduled Events sync is pending or failed, and
roll unmappable-cadence single events forward to their next occurrence.

Wired into ``run_scheduled_tasks``' always-run set (every ~15 minutes). Self-gating: a
no-op when Discord Events sync is off, so it is safe to run on every tick. Only touches
PUBLISHED non-studio-hours rows in ``PENDING``/``FAILED`` (``needs_discord_push()``) plus
SYNCED single-occurrence rows whose pushed occurrence has passed
(``needs_discord_rollforward()``) — a passed one re-creates a fresh event for its next
occurrence (a completed Discord event can't be PATCHed forward). Bounded per run so a
single tick stays cheap.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

_MAX_PER_RUN = 200


class Command(BaseCommand):
    help = "Re-push pending/failed Discord Scheduled Events and roll single-occurrence events forward."

    def handle(self, *args: Any, **options: Any) -> None:
        from django.utils import timezone

        from core.integrations.discord_events import DiscordScheduledEventsClient
        from core.models import SiteConfiguration
        from membership.models import CommunityEvent

        if (
            not DiscordScheduledEventsClient.from_settings().enabled
            or not SiteConfiguration.load().discord_events_sync_enabled
        ):
            self.stdout.write("Discord Events sync is off — nothing to retry.")
            return

        pushed = 0
        for event in CommunityEvent.objects.needs_discord_push().select_related("guild")[:_MAX_PER_RUN]:
            event.push_to_discord()  # best-effort; updates discord_sync_state per event
            pushed += 1

        rolled = 0
        rollforward = CommunityEvent.objects.needs_discord_rollforward(timezone.now())
        for event in rollforward.select_related("guild")[:_MAX_PER_RUN]:
            event.push_to_discord()  # recomputes the next occurrence + creates a fresh event
            rolled += 1

        self.stdout.write(
            self.style.SUCCESS(f"Retried Discord push for {pushed} event(s); rolled {rolled} event(s) forward.")
        )

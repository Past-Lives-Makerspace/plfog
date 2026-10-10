"""Re-push community events whose Discord Scheduled Events sync is pending or failed, roll
unmappable-cadence single events forward to their next occurrence, and re-push a series
the recurrence map no longer expresses as a rule.

Wired into ``run_scheduled_tasks``' always-run set (every ~15 minutes). Self-gating: a
no-op when Discord Events sync is off, so it is safe to run on every tick. Only touches
PUBLISHED non-studio-hours rows in ``PENDING``/``FAILED`` (``needs_discord_push()``), SYNCED
single-occurrence rows whose pushed occurrence has passed (``needs_discord_rollforward()``;
a passed one re-creates a fresh event for its next occurrence, since a completed Discord
event can't be PATCHed forward), and SYNCED native series (``discord_native_series()``)
whose cadence :func:`~core.integrations.discord_events.pushes_as_native_series` now
rejects (the push replaces the series on Discord with its next single occurrence) or whose
series needs re-anchoring per :func:`~core.integrations.discord_events.series_needs_reanchor`
(the clocks changed, so Discord's fixed UTC time reads an hour off; the push re-anchors the
same series at the next occurrence). Those passes heal a series pushed under an older map (the First Friday Art
Walk, #755) or before a clock change without a hand-run step. Bounded per run so a single
tick stays cheap.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

_MAX_PER_RUN = 200


class Command(BaseCommand):
    help = "Re-push pending/failed Discord Scheduled Events and roll single-occurrence events forward."

    def handle(self, *args: Any, **options: Any) -> None:
        from django.utils import timezone

        from core.integrations.discord_events import (
            DiscordScheduledEventsClient,
            pushes_as_native_series,
            series_needs_reanchor,
        )
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

        remapped = 0
        reanchored = 0
        for event in CommunityEvent.objects.discord_native_series().select_related("guild")[:_MAX_PER_RUN]:
            if not pushes_as_native_series(event):
                event.push_to_discord()  # replaces the Discord series with its next single occurrence
                remapped += 1
            elif series_needs_reanchor(event):
                event.push_to_discord()  # PATCHes the same series to start at the next occurrence
                reanchored += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Retried Discord push for {pushed} event(s); rolled {rolled} event(s) forward; "
                f"remapped {remapped} series; re-anchored {reanchored} series."
            )
        )

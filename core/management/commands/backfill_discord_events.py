"""One-time backfill: push existing future published events into Discord's Scheduled Events.

Run once at go-live (after turning the toggle on and granting the bot Manage Events) to
mirror events that were created before this sync existed. Considers only PUBLISHED,
non-studio-hours, still-upcoming events (a recurring series always counts). ``--dry-run``
reports the count without any push. Idempotent: an already-synced event just PATCHes.
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Push existing future published (non-studio-hours) events into Discord's Scheduled Events."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many events would be pushed without pushing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.models import CommunityEvent

        events = (
            CommunityEvent.objects.published()
            .exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS)
            .upcoming()
            .select_related("guild")
        )

        if options["dry_run"]:
            self.stdout.write(f"[dry-run] Would push {events.count()} event(s) to Discord's Scheduled Events.")
            return

        pushed = 0
        for event in events:
            event.push_to_discord()  # best-effort; records the outcome per event
            pushed += 1
        self.stdout.write(self.style.SUCCESS(f"Pushed {pushed} event(s) to Discord's Scheduled Events."))

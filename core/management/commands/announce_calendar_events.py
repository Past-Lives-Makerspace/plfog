"""Announce newly added calendar events/classes in the #calendar Discord channel.

Wired into ``run_scheduled_tasks``' always-run set (every ~15 minutes). Self-gating: a
no-op when calendar posts are off or no channel id is configured. Idempotent — each item
is stamped ``channel_announced_at`` the moment it's handled, so nothing announces twice
and a capped backlog is silently marked instead of flooding the channel. All logic lives
in :mod:`hub.discord_calendar_posts`; this is the thin command wrapper.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Post new calendar events and classes to the #calendar Discord channel."

    def handle(self, *args: Any, **options: Any) -> None:
        from hub.discord_calendar_posts import announce_new_events

        posted = announce_new_events()
        if posted == 0:
            self.stdout.write("No new events to announce.")
            return
        self.stdout.write(self.style.SUCCESS(f"Announced {posted} new event(s) in #calendar."))

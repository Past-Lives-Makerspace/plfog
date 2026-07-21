"""Post the Monday-morning week-of-classes digest to the Discord #classes channel.

Wired into ``run_scheduled_tasks``' WEEKLY set (Mondays ~6 AM PT). Self-gating: a no-op
when class posts are off or no channel id is configured, and it never posts an empty
digest — so a stray manual run is always safe. All logic lives in
:mod:`hub.discord_class_posts`; this is the thin command wrapper.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Post the coming week's classes digest to the Discord #classes channel."

    def handle(self, *args: Any, **options: Any) -> None:
        from hub.discord_class_posts import post_weekly_classes_digest

        count = post_weekly_classes_digest()
        if count == 0:
            self.stdout.write("No digest posted (posts off, no channel id, or nothing this week).")
            return
        self.stdout.write(self.style.SUCCESS(f"Posted the weekly classes digest ({count} embed(s))."))

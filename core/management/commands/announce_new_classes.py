"""Announce newly published (publicly bookable) classes in the Discord #classes channel.

Wired into ``run_scheduled_tasks``' always-run set (every ~15 minutes). Self-gating: a
no-op when class posts are off or no channel id is configured. Idempotent — each offering
is stamped ``channel_announced_at`` the moment it's handled, so nothing announces twice
(the stamp survives unpublish/republish) and a capped backlog is silently marked instead
of flooding the channel. All logic lives in :mod:`hub.discord_class_posts`; this is the
thin command wrapper.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Post newly published classes to the Discord #classes channel."

    def handle(self, *args: Any, **options: Any) -> None:
        from hub.discord_class_posts import announce_new_classes

        posted = announce_new_classes()
        if posted == 0:
            self.stdout.write("No new classes to announce.")
            return
        self.stdout.write(self.style.SUCCESS(f"Announced {posted} new class(es) in #classes."))

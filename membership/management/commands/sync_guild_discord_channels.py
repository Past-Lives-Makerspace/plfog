"""Fetch each guild's real Discord channel name from its webhook and cache it on the row.

The announcement composer's Discord channel picker shows the guild's actual ``#channel``
instead of a generic "Our Guild Channel". The name is not something a lead types in — it is
fetched from Discord (webhook -> channel id -> channel name) and cached on
``Guild.discord_channel_name`` by this command. Run it on deploy and/or a cron so a renamed
channel stays accurate; it never runs during a web request (a live Discord call must not sit
in a page render).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership.models import Guild


class Command(BaseCommand):
    help = "Fetch and cache each guild's Discord channel name for the announcement composer picker."

    def handle(self, *args: Any, **options: Any) -> None:
        guilds = Guild.objects.filter(is_active=True).exclude(discord_webhook_url="").order_by("name")
        for guild in guilds:
            name = guild.sync_discord_channel_name()
            self.stdout.write(f"{guild.name}: {name or '(unresolved)'}")

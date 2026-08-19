"""Seed the Help Center's example guild (the Cartographers Guild).

Runs on every deploy (render.yaml buildCommand, right after ``seed_help_center``)
and is idempotent — content lives in :mod:`membership.example_guild`; correcting
it there and redeploying is the workflow, exactly like the help articles.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from membership.example_guild import EXAMPLE_GUILD_SLUG, seed_example_guild


class Command(BaseCommand):
    help = "Create or refresh the Help Center's example guild (idempotent)."

    def handle(self, *args: Any, **options: Any) -> None:
        guild = seed_example_guild()
        self.stdout.write(
            self.style.SUCCESS(
                f"Example guild ready: {guild.name} (/guilds/{EXAMPLE_GUILD_SLUG}/, is_active={guild.is_active})"
            )
        )

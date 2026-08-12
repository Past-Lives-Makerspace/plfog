"""One-off sweep: DM the join welcome to every unlinked Discord server member.

Unlike the reconcile cron's welcome step (new joiners only — ``joined_at`` inside the
last 48 hours), this sweeps EVERY human server member regardless of join date, through
the same once-only :class:`membership.models.DiscordJoinWelcome` ledger — so re-running
is always safe and never re-DMs anyone. Deliberately NOT in ``core.scheduled_jobs``:
run it by hand (Render one-off job / local shell) when a backfill is wanted. The
site-settings "DM new Discord joiners" toggle gates this sweep too — one switch kills
all joiner nudging, cron and sweep alike.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "DM the one-time join welcome to every unlinked Discord server member (ignores the 48-hour window)."

    def handle(self, *args: Any, **options: Any) -> None:
        from core.events.discord_dm import bot_token
        from core.events.discord_members import fetch_guild_members
        from core.models import SiteConfiguration
        from membership.discord_sync import _send_join_welcomes

        config = SiteConfiguration.load()
        server_id = (config.discord_server_id or "").strip()
        if not bot_token() or not server_id:
            self.stdout.write("Skipped (Discord not configured).")
            return
        if not config.discord_joiner_nudge_enabled:
            self.stdout.write("Skipped (new-joiner DMs are turned off in Site Settings).")
            return

        page = fetch_guild_members(server_id)
        if not page.complete:
            self.stdout.write(
                self.style.WARNING(
                    "Member list incomplete — a truncated fetch is caught by re-running; zero members almost "
                    "always means the bot's Server Members Intent is off in the Discord developer portal."
                )
            )
        stats = _send_join_welcomes(page.members)
        self.stdout.write(
            self.style.SUCCESS(
                f"Join-welcome sweep: {stats.welcomed} welcomed, {stats.skipped_linked} skipped (already linked), "
                f"{stats.skipped_ledgered} skipped (already welcomed), {stats.undeliverable} undeliverable "
                f"(DMs closed — marked welcomed, never retried)."
            )
        )

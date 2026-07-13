"""Reconcile Discord reaction-role memberships with the app (cron-facing).

Calls :func:`membership.discord_sync.reconcile_reactions`, which reads the reaction-role
message and keeps every ``source="discord"`` guild membership in step — adding members who
react, and (only on a complete fetch) removing those who un-react. No-ops when Discord is
unconfigured. Added to the 15-minute ``run_scheduled_tasks`` always-run tuple; the
underlying ``emit``/join logic is idempotent, so re-running every tick is safe.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reconcile Discord reaction-role memberships (add reactors, remove un-reactors)."

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.discord_sync import reconcile_reactions

        stats = reconcile_reactions()
        if not stats.ran:
            self.stdout.write("Discord guild-role sync skipped (not configured).")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Discord guild-role sync: +{stats.added} added, -{stats.removed} removed, "
                f"{stats.skipped_guilds} guild(s) skipped (incomplete fetch)."
            )
        )

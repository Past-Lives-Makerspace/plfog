"""Mirror the #choose-your-guild reactions into the app and onto Discord (cron-facing).

Calls :func:`membership.discord_sync.reconcile_reactions`: a linked reactor joins the guild
(``source="discord"``), a linked member who took their reaction off leaves it (#1319), and every
reactor gets the guild role they lack. No-ops when Discord is unconfigured.
In the 15 minute ``run_scheduled_tasks`` tick; safe to re-run.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Mirror the role message reactions into guild memberships and roles."

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.discord_sync import reconcile_reactions

        stats = reconcile_reactions()
        if not stats.ran:
            self.stdout.write("Discord guild-role sync skipped (not configured).")
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Discord guild-role sync: +{stats.added} added, {stats.left} left, "
                f"{stats.taken} roles taken, "
                f"{stats.roles.given} roles given, {stats.roles.failed} role grants failed, "
                f"{stats.nudged} unlinked reactor(s) nudged."
            )
        )

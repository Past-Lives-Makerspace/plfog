"""Audit guild-lead assignments for drift.

Guild-lead authority comes solely from the ``Guild.guild_lead`` FK. This command
surfaces the silent ways that can break: a lead with no linked User (they can
never log in to use their perks — the exact failure that hit production), an
inactive lead, or a guild with no lead at all. Run it as a Render cron with
``--strict`` to get alerted when something needs attention.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from membership.models import Guild, Member


class Command(BaseCommand):
    help = "Audit guild-lead assignments (leads with no login, inactive leads, guilds with no lead)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero if any problems are found (useful for cron/CI alerting).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        no_lead, no_user, inactive, healthy, total = self._categorize()

        self.stdout.write(f"Audited {total} guild(s): {healthy} healthy.")
        self._report(
            no_user,
            self.style.ERROR,
            "lead(s) with NO linked user account (cannot log in to use perks):",
            self._lead_line,
        )
        self._report(inactive, self.style.WARNING, "lead(s) whose member is not Active:", self._status_line)
        self._report(
            no_lead,
            self.style.NOTICE,
            "guild(s) with no lead (only admin approval applies — info only):",
            lambda g: f"  - {g.name}",
        )

        problems = len(no_user) + len(inactive)
        if problems:
            self.stdout.write(self.style.ERROR(f"\n{problems} guild(s) need attention."))
            if options["strict"]:
                raise CommandError(f"{problems} guild-lead problem(s) found.")
        else:
            self.stdout.write(self.style.SUCCESS("\nNo guild-lead problems found."))

    @staticmethod
    def _categorize() -> tuple[list[Guild], list[Guild], list[Guild], int, int]:
        no_lead: list[Guild] = []
        no_user: list[Guild] = []
        inactive: list[Guild] = []
        healthy = 0
        guilds = list(Guild.objects.select_related("guild_lead", "guild_lead__user").order_by("name"))
        for guild in guilds:
            lead = guild.guild_lead
            if lead is None:
                no_lead.append(guild)
                continue
            flagged = False
            if lead.user_id is None:
                no_user.append(guild)
                flagged = True
            if lead.status != Member.Status.ACTIVE:
                inactive.append(guild)
                flagged = True
            if not flagged:
                healthy += 1
        return no_lead, no_user, inactive, healthy, len(guilds)

    def _report(
        self,
        guilds: list[Guild],
        style: Callable[[str], str],
        header: str,
        formatter: Callable[[Guild], str],
    ) -> None:
        if not guilds:
            return
        self.stdout.write(style(f"\n{len(guilds)} {header}"))
        for guild in guilds:
            self.stdout.write(formatter(guild))

    @staticmethod
    def _lead_line(guild: Guild) -> str:
        lead = guild.guild_lead
        assert lead is not None  # only guilds with a lead reach this formatter
        return f"  - {guild.name}: {lead.display_name} <{lead.primary_email or 'no email'}>"

    @staticmethod
    def _status_line(guild: Guild) -> str:
        lead = guild.guild_lead
        assert lead is not None
        return f"  - {guild.name}: {lead.display_name} (status={lead.get_status_display()})"

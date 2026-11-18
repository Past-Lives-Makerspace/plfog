"""List, and with ``--send`` nudge, the Active members linked to Discord who are not on the server (#1239).

    python manage.py nudge_discord_reconnect
    python manage.py nudge_discord_reconnect --send

Reads the server's member list once. Without ``--send`` it writes nothing. With it, each member
not nudged before gets one :class:`~membership.models.DiscordReconnectNudge` and a bell and email
with a Reconnect Discord button (``membership.discord_reconnect``). Anyone already nudged, open,
dismissed or done, is listed as skipped. An unreadable member list, a blank server id or a blank
bot token stops it before anyone is nudged. Run by hand as a Render job; nothing schedules it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from membership import discord_reconnect

if TYPE_CHECKING:
    from membership.models import Member


class Command(BaseCommand):
    help = "List the Active members linked to Discord but not on the server; --send nudges each to reconnect."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--send", action="store_true", help="Nudge each listed member instead of only listing.")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            on_server = discord_reconnect.server_member_ids()
        except discord_reconnect.ServerListUnavailableError as exc:
            raise CommandError(f"{exc} Nobody was nudged.") from exc
        run = discord_reconnect.find_missing(on_server)
        verb = "nudged" if options["send"] else "would nudge"
        if options["send"]:
            discord_reconnect.send_nudges(run.missing)
        for member in run.missing:
            self.stdout.write(f"{self._label(member)}: {verb}")
        for member in run.already_nudged:
            status = member.discord_reconnect_nudge.get_status_display().lower()
            self.stdout.write(f"{self._label(member)}: skipped, already nudged ({status})")
        self.stdout.write(
            f"{len(run.missing)} {verb}, {len(run.already_nudged)} skipped, {len(on_server)} on the server."
        )

    @staticmethod
    def _label(member: Member) -> str:
        return f"Member {member.pk} {member.staff_label} ({member.discord_user_id})"

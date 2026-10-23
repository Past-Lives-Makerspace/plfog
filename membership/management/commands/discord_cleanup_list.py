"""One time (#898): warn the Discord usernames given and remove them three days later.

    python manage.py discord_cleanup_list alice bob carol --dry-run
    python manage.py discord_cleanup_list alice bob carol

The usernames are arguments, never committed. Each is matched exactly (case ignored) against
the server's member list; the work is :func:`membership.discord_removal.run_cleanup_list`, and
the run and every step show on the Remove ex-members from Discord history page. Refuses to run
where the bot token is blank (staging).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser


class Command(BaseCommand):
    help = "One time: DM the given Discord usernames and queue them for removal in 3 days."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("usernames", nargs="+", help="Discord usernames, with or without a leading @.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen; send and save nothing.")

    def handle(self, *args: Any, **options: Any) -> None:
        from core.events.discord_dm import bot_disabled
        from core.events.discord_members import list_member_ids_by_username
        from core.models import SiteConfiguration
        from core.scheduled_jobs import Trigger, record_run
        from membership.discord_removal import run_cleanup_list
        from membership.models import DISCORD_REMOVAL_TASK_KEY

        usernames = list(dict.fromkeys(name.strip().lstrip("@").lower() for name in options["usernames"]))
        if bot_disabled("cleanup list"):
            raise CommandError("The bot token is blank (unset, or staging), so nothing was sent.")
        server_id = SiteConfiguration.load().discord_server_id.strip()
        if not server_id:
            raise CommandError("No Discord server id in Site Settings, so nothing was sent.")
        ids_by_username = list_member_ids_by_username(server_id)
        if options["dry_run"]:
            result = run_cleanup_list(usernames, ids_by_username, dry_run=True)
        else:
            with record_run(DISCORD_REMOVAL_TASK_KEY, trigger=Trigger.COMMAND):
                result = run_cleanup_list(usernames, ids_by_username, dry_run=False)
                if result.failed:
                    self._print(result.lines)
                    raise CommandError(f"{result.failed} DM(s) failed; the job retries them while it is on.")
        self._print(result.lines)

    def _print(self, lines: list[str]) -> None:
        for line in lines:
            self.stdout.write(line)

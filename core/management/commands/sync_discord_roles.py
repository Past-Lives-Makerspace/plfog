"""The daily Discord role sync (#1270): guild roles for every roster, then the managed roles.

    python manage.py sync_discord_roles

First every linked roster member gets each role of their guild they lack
(:func:`membership.discord_sync.sync_roster_roles`). Add only: being off a roster never takes a
role away, and a membership marked "no Discord role" (they un-reacted) is skipped. Then the
managed roles (leadership, Guild Officers, Guild Leads, Tour Guides, Instructors) are brought in
step for everyone with ``sync_leadership_roles --apply``, which catches what no save hook sees,
such as a class's instructor list or a membership ending. A role change that still fails fails
the run, so the Webmasters hear about it.
"""

from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Daily: give roster members their guild roles (add only), then sync the managed Discord roles."

    def handle(self, *args: Any, **options: Any) -> None:
        from membership.discord_sync import sync_roster_roles

        grants = sync_roster_roles()
        if grants is None:
            self.stdout.write("Guild roles skipped (the bot token or the server id is blank).")
        else:
            self.stdout.write(f"Guild roles: {grants.given} given, {grants.failed} failed.")
        call_command("sync_leadership_roles", "--apply", stdout=self.stdout)
        if grants is not None and grants.failed:
            raise CommandError(f"{grants.failed} guild role grants failed; see the log.")

"""Welcome every current guild lead and staffer to Guild Staff Tools, once (#1076).

New grants are welcomed as they happen (``membership.staff_onboarding.record_grant``). This sends
the same welcome, the email and the rules popup on their next visit, to everyone who already
leads or staffs a guild. It goes through the same ``welcome`` and the same record, so a person is
welcomed once in their life: a rerun sends nothing, and a later grant never welcomes them again.

Run it by hand once after the release that ships it. ``--dry-run`` prints who would get it and
writes nothing.

Usage::

    manage.py welcome_existing_guild_staff [--dry-run]
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from membership.staff_onboarding import backfill_candidates, welcome


class Command(BaseCommand):
    help = (
        "Send the Guild Staff Tools welcome (email and rules popup) once to every current guild lead "
        "and staffer never welcomed. Use --dry-run to list who would get it."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--dry-run", action="store_true", help="List who would be welcomed; send nothing.")

    def handle(self, *args: Any, **options: Any) -> None:
        dry_run = bool(options["dry_run"])
        candidates = backfill_candidates()
        sent = 0
        for member, guild in candidates:
            self.stdout.write(f"  {member.display_name} <{member.primary_email or 'no email'}> ({guild.name})")
            if dry_run:
                continue
            # One transaction per person: the email goes on commit, so one failure stops only theirs.
            with transaction.atomic():
                if welcome(member, guild) is not None:
                    sent += 1
        verb = "Would welcome" if dry_run else "Welcomed"
        count = len(candidates) if dry_run else sent
        self.stdout.write(f"{verb} {count} guild lead{'s' if count != 1 else ''} and staff.")

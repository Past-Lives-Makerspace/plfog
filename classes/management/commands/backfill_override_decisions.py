"""Correct the guild-lead rows an admin's publish closed before the marker existed.

Until ``ClassApproval.Decision.OVERRIDDEN_BY_ADMIN`` was added, an admin publishing over a
still-open guild-lead lane closed that row as ``approved`` with a system note. The note said
what happened; the decision did not, so every surface that reads the decision — the pipeline
step, the tokenized reviewer page, the guild page's "the guild approved these" list — showed
a yes the guild lead never gave.

This is the one-row repair, deliberately split out of the migration that added the choice.
Render runs migrations inside its build while the OLD code is still serving, so a data
migration writing the new value would hand the running code a decision it cannot parse for
the length of a build. Run this as a one-off job AFTER the new code is live:

    python manage.py backfill_override_decisions --dry-run
    python manage.py backfill_override_decisions

It matches the exact historical note literal, so a row an admin genuinely approved by hand is
never touched, and running it twice changes nothing the second time.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db import connection

from classes.models import ADMIN_OVERRIDE_NOTE, ClassApproval


class Command(BaseCommand):
    help = "Re-mark guild-lead rows an admin's publish auto-closed as overridden, not approved."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the rows that would change and write nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # First line, before anything else: a job that cannot prove which database it reached
        # is not evidence that it ran against production.
        self.stdout.write(f"Database host: {connection.settings_dict['HOST'] or '(local sqlite)'}")
        rows = list(
            ClassApproval.objects.filter(
                role=ClassApproval.Role.GUILD_LEAD,
                decision=ClassApproval.Decision.APPROVED,
                notes=ADMIN_OVERRIDE_NOTE,
            ).select_related("class_offering")
        )
        if not rows:
            self.stdout.write("No auto-closed guild-lead rows to correct.")
            return
        for row in rows:
            self.stdout.write(f"  #{row.pk} — {row.class_offering.title} (decided {row.decided_at})")
        if options["dry_run"]:
            self.stdout.write(f"[dry-run] would correct {len(rows)} row(s); nothing written")
            return
        changed = ClassApproval.objects.filter(pk__in=[row.pk for row in rows]).update(
            decision=ClassApproval.Decision.OVERRIDDEN_BY_ADMIN
        )
        self.stdout.write(self.style.SUCCESS(f"Corrected {changed} row(s)."))

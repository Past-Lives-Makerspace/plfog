"""Remove every seeded demo class and what hangs off it from this database (#983).

Covers every class whose slug starts with ``demo-`` (including ones no seeder's ``--remove``
knows about), their sessions, registrations, refunds and payouts, and the ``[DEMO]``
categories. Demo persona members stay. Always read ``--dry-run`` first: it lists every row by
model and primary key, names demo registrations that had a real Stripe payment on their own,
and says why it would refuse. Idempotent: a second run removes nothing.

    python manage.py scrub_demo_data --dry-run
    python manage.py scrub_demo_data
"""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from classes.demo_scrub import DemoScrubPlan, DemoScrubRefusedError, plan_demo_scrub, scrub_demo_data


class Command(BaseCommand):
    help = "Delete every demo- class with its sessions, registrations, refunds and payouts, and the [DEMO] categories."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List every row that would be removed, and why it would refuse, without deleting anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.db import connection

        self.stdout.write(f"Database host: {connection.settings_dict['HOST'] or '(local sqlite)'}")
        if options["dry_run"]:
            plan = plan_demo_scrub()
            self._write_plan(plan)
            self.stdout.write("[dry-run] nothing deleted.")
            return
        try:
            plan = scrub_demo_data()
        except DemoScrubRefusedError as exc:
            raise CommandError(f"Refused, nothing deleted: {exc}") from exc
        self._write_plan(plan)
        if not plan.is_empty:
            self.stdout.write(self.style.SUCCESS(f"Removed {plan.total_rows} demo rows."))

    def _write_plan(self, plan: DemoScrubPlan) -> None:
        if plan.is_empty:
            self.stdout.write("Nothing to remove: no demo classes or [DEMO] categories left.")
        else:
            self.stdout.write(f"Rows to remove: {plan.total_rows} across {len(plan.deletes)} tables")
            for label, pks in plan.deletes.items():
                self.stdout.write(f"  {label} ({len(pks)}): {', '.join(str(pk) for pk in pks)}")
        if plan.cleared_links:
            self.stdout.write("Links cleared on rows that stay:")
            for label, count in plan.cleared_links.items():
                self.stdout.write(f"  {label}: {count}")
        if plan.stripe_backed:
            self.stdout.write(f"Demo registrations with a real Stripe payment ({len(plan.stripe_backed)}):")
            for reg in plan.stripe_backed:
                self.stdout.write(
                    f"  registration {reg.pk} on {reg.class_slug}: paid ${reg.paid_cents / 100:,.2f},"
                    f" refunded ${reg.refunded_cents / 100:,.2f}"
                )
        for warning in plan.warnings:
            self.stdout.write(self.style.WARNING(f"Warning: {warning}"))
        for blocker in plan.blockers:
            self.stdout.write(self.style.ERROR(f"Refuses: {blocker}"))

"""Create the building's Locations and fill blank Location fields from existing data (#616).

A dry run by default: it prints the locations it would create or fill in, the shares space link,
every proposed assignment with the rule that matched, counts per location, and every record left
blank with the reason. The dry run issues only SELECTs, so it can run locally against production
through a read only connection. ``--apply`` writes the same plan in one transaction; it is safe to
re-run and never replaces a Location that is already set. The rules live in
``membership.services.location_backfill``.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from membership.services.location_backfill import BackfillPlan, apply_backfill, plan_backfill


def _cell(text: str, width: int) -> str:
    """``text`` padded or cut to ``width`` characters."""
    if len(text) > width:
        return text[: width - 3] + "..."
    return text.ljust(width)


class Command(BaseCommand):
    help = "Create the building's Locations and fill blank Location fields. Dry run unless --apply."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true", help="Write the plan. Without it nothing is written.")

    def handle(self, *args: Any, **options: Any) -> None:
        plan = plan_backfill()
        self._print_plan(plan)
        if not options["apply"]:
            self.stdout.write("Dry run: nothing written. Run with --apply to write this plan.")
            return
        result = apply_backfill(plan)
        self.stdout.write(
            self.style.SUCCESS(
                f"Applied: {result.created} locations created, {result.updated} filled in, "
                f"{result.linked} links added, {result.assigned} records assigned."
            )
        )

    def _print_plan(self, plan: BackfillPlan) -> None:
        self.stdout.write("Locations")
        for change in plan.locations:
            guild = change.guild_name or "no guild"
            detail = f" (fill {', '.join(change.fills)})" if change.fills else ""
            note = f', note "{change.note}"' if change.note else ""
            self.stdout.write(f"  {change.action:<6} {change.name} [{guild}{note}]{detail}")
        for first, second in plan.links:
            self.stdout.write(f"  link   {first} shares space with {second}")

        self.stdout.write("")
        self.stdout.write(f"Proposed assignments ({len(plan.proposed)})")
        header = f"  {_cell('Kind', 16)} {_cell('ID', 6)} {_cell('Title', 48)} {_cell('Location', 22)} Rule"
        self.stdout.write(header)
        for a in plan.proposed:
            self.stdout.write(
                f"  {_cell(a.kind, 16)} {_cell(str(a.pk), 6)} {_cell(a.title, 48)} {_cell(str(a.location), 22)} {a.rule}"
            )

        self.stdout.write("")
        self.stdout.write("Counts per location")
        for name, count in plan.counts_by_location():
            self.stdout.write(f"  {_cell(name, 24)} {count}")

        self.stdout.write("")
        self.stdout.write(f"Left blank ({len(plan.blank)})")
        for a in plan.blank:
            self.stdout.write(f"  {_cell(a.kind, 16)} {_cell(str(a.pk), 6)} {_cell(a.title, 48)} {a.rule}")

        for warning in plan.warnings:
            self.stdout.write(self.style.WARNING(f"Warning: {warning}"))
        self.stdout.write("")

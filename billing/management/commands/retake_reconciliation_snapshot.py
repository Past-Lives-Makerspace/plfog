"""Retake a month's reconciliation snapshot from today's data, keeping its guild voting cents (#983).

For a month whose frozen snapshot holds rows that should never have counted (demo classes),
after those rows are gone. The voting figures are copied from the snapshot being replaced,
never recalculated. ``--dry-run`` prints the old and new totals and the voting per guild,
and writes nothing.

    python manage.py retake_reconciliation_snapshot --month 2026-08 --dry-run
    python manage.py retake_reconciliation_snapshot --month 2026-08
"""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from billing.management.commands.take_reconciliation_snapshot import month_bounds


def _voting_by_guild(results: dict[str, Any]) -> tuple[dict[str, int], set[str]]:
    """Guild label to frozen voting cents from a snapshot's results JSON, and the guilds with no figure.

    A row from before voting was frozen has no ``voting_cents``; it reads 0, exactly as
    ``resolve_voting_allocation`` reads it, and its label is returned so the printout says so.
    """
    from billing.reconciliation import RecipientKind

    rows = [row for group in results["groups"] if group["kind"] == RecipientKind.GUILD.value for row in group["rows"]]
    missing = {row["label"] for row in rows if "voting_cents" not in row}
    cents = {row["label"]: 0 if row["label"] in missing else int(row["voting_cents"]) for row in rows}
    return cents, missing


class Command(BaseCommand):
    help = "Replace one month's reconciliation snapshot with a fresh one that keeps its guild voting cents."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--month", type=str, required=True, help="Month to retake, YYYY-MM.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the old and new totals and the voting per guild without writing anything.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from billing.models import ReconciliationSnapshot
        from billing.payments_panel import PanelWindow
        from billing.reconciliation import build_reconciliation

        try:
            parsed = datetime.strptime(options["month"].strip(), "%Y-%m")
        except ValueError as exc:
            raise CommandError(f"--month must be YYYY-MM, got '{options['month']}'.") from exc
        start, end = month_bounds(parsed.year, parsed.month)
        try:
            old = ReconciliationSnapshot.latest_for(period_start=start, period_end=end)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"Replacing snapshot {old.pk} for {start:%B %Y}: ${old.grand_total_cents / 100:,.2f}")
        if options["dry_run"]:
            fresh = build_reconciliation(window=PanelWindow(start=start, end=end)).to_snapshot_dict()
            self.stdout.write(f"Retaken now it would total ${fresh['grand_total_cents'] / 100:,.2f}")
            self._write_voting(old.results, fresh)
            self.stdout.write("[dry-run] nothing written.")
            return
        new = old.retake()
        self.stdout.write(
            self.style.SUCCESS(f"Took snapshot {new.pk} for {start:%B %Y}: ${new.grand_total_cents / 100:,.2f}")
        )
        self._write_voting(old.results, new.results)

    def _write_voting(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        old_voting, old_missing = _voting_by_guild(before)
        new_voting, _new_missing = _voting_by_guild(after)
        self.stdout.write("Guild voting (old -> new):")
        for label in sorted(old_voting.keys() | new_voting.keys()):
            was = old_voting.get(label, 0)
            now = new_voting.get(label, 0)
            flag = "" if was == now else "  CHANGED"
            note = "  (old snapshot has no voting figure; read as $0.00)" if label in old_missing else ""
            self.stdout.write(f"  {label}: ${was / 100:,.2f} -> ${now / 100:,.2f}{flag}{note}")

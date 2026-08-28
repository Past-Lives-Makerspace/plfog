"""Freeze a month's reconciliation allocation into a ReconciliationSnapshot.

Wired into ``run_scheduled_tasks``' always-run set (every ~15 minutes). Self-gating:
with no ``--month``, it targets the just-ended month and no-ops once a snapshot for
that period already exists, so running it every tick takes exactly one auto snapshot
at month rollover. Pass ``--month YYYY-MM`` to freeze a specific month by hand.
"""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import date, datetime, timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


class Command(BaseCommand):
    help = "Freeze the prior month's reconciliation into a snapshot. Safe to run every 15 minutes."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--month",
            type=str,
            default="",
            help="Month to freeze, YYYY-MM. Defaults to the just-ended month (an automatic run).",
        )
        parser.add_argument(
            "--title",
            type=str,
            default="",
            help="Optional label. Defaults to the month/year.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from billing.models import ReconciliationSnapshot

        month_arg: str = options["month"]
        title: str = options["title"]
        is_auto = not month_arg

        if month_arg:
            try:
                parsed = datetime.strptime(month_arg.strip(), "%Y-%m")
            except ValueError as exc:
                raise CommandError(f"--month must be YYYY-MM, got '{month_arg}'.") from exc
            year, month = parsed.year, parsed.month
        else:
            first_of_this_month = timezone.localdate().replace(day=1)
            just_ended = first_of_this_month - timedelta(days=1)
            year, month = just_ended.year, just_ended.month

        start, end = _month_bounds(year, month)
        if ReconciliationSnapshot.objects.filter(period_start=start, period_end=end).exists():
            self.stdout.write(f"A snapshot already exists for {start:%B %Y} — skipping.")
            return

        snapshot = ReconciliationSnapshot.take(
            period_start=start,
            period_end=end,
            title=title.strip(),
            is_auto=is_auto,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Took reconciliation snapshot for {start:%B %Y} (${snapshot.grand_total_cents / 100:,.2f})."
            )
        )

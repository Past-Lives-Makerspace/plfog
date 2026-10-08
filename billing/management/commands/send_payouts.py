"""Record instructor and orientor shares as they fall due and send them through Stripe (#662)."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from billing.payouts import run_payouts


class Command(BaseCommand):
    help = "Record due payout shares and send them through Stripe. Safe every 15 minutes; idle while payouts are off."

    def handle(self, *args: Any, **options: Any) -> None:
        run = run_payouts()
        self.stdout.write(
            f"Payouts: {run.created} recorded, {run.sent} sent, {run.failed} failed, {run.gave_up} owed by hand."
        )

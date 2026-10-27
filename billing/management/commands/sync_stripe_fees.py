"""Record each Stripe payment's fee, net and payout (#984). Runs daily; ``--backfill`` covers all history.

Safe to rerun: a recorded payment is never read again unless a refund sends it back to
pending, and a payout already matched stays matched. The run fails (so the Webmasters hear)
when Stripe could not answer a read, or when a payment has failed to record for a week.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from billing import stripe_fees
from billing.models import StripeFeeRecord


class Command(BaseCommand):
    """Fill fee records from Stripe and match them to payouts."""

    help = "Record each Stripe payment's fee, net and payout. --backfill covers every past payment."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--backfill",
            action="store_true",
            help="Match payouts over all history, not the last 35 days, and list every payment Stripe does not have.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        backfill: bool = options["backfill"]
        report = stripe_fees.sync(backfill=backfill)
        self.stdout.write(
            f"Added {report.added}, recorded {report.recorded}, waiting on Stripe {report.waiting}, "
            f"not found {report.not_found}, Stripe errors {report.stripe_errors}, "
            f"matched to a payout {report.payouts_stamped}."
        )
        if backfill:
            if report.not_found_sources:
                self.stdout.write(f"{len(report.not_found_sources)} paid payment(s) are on neither Stripe account:")
                for kind, pk in report.not_found_sources:
                    self.stdout.write(f"  {kind} {pk}")
            else:
                self.stdout.write("Every paid payment was found on Stripe.")
        stuck = StripeFeeRecord.objects.stuck().count()
        if report.stripe_errors or stuck:
            raise CommandError(
                f"{report.stripe_errors} Stripe read(s) failed this run and {stuck} payment(s) have failed "
                "to record for a week; they stay pending and the next run retries them."
            )

"""Management command to create a funding snapshot from current vote preferences."""

from __future__ import annotations

from argparse import ArgumentParser
from decimal import Decimal

from django.core.management.base import BaseCommand

from membership.models import FundingSnapshot


class Command(BaseCommand):
    """Create a FundingSnapshot from the current VotePreferences."""

    help = "Take a funding snapshot from current vote preferences."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--minimum-pool",
            type=Decimal,
            default=None,
            help="Dollar floor applied to the funding pool (default: the VotingSettings floor).",
        )
        parser.add_argument(
            "--title",
            type=str,
            default="",
            help="Custom cycle label. Defaults to current month/year.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Execute the snapshot command."""
        minimum_pool: Decimal | None = options["minimum_pool"]  # type: ignore[assignment]
        title: str = options["title"]  # type: ignore[assignment]

        snapshot = FundingSnapshot.take(title=title, minimum_pool=minimum_pool)

        if snapshot is None:
            self.stdout.write("No vote preferences found. Skipping snapshot.")
            return

        self.stdout.write(
            f"Snapshot created: {snapshot.cycle_label} — "
            f"{snapshot.contributor_count} contributor(s) — pool ${snapshot.funding_pool}"
        )

"""Management command to create a funding snapshot from current vote preferences."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from membership.models import FundingSnapshot, VotePreference
from membership.vote_calculator import calculate_results


class Command(BaseCommand):
    """Create a FundingSnapshot from the current VotePreferences."""

    help = "Take a funding snapshot from current vote preferences."

    def handle(self, *args: object, **options: object) -> None:
        """Execute the snapshot command."""
        preferences = VotePreference.objects.select_related(
            "member__membership_plan",
            "guild_1st",
            "guild_2nd",
            "guild_3rd",
        ).all()

        if not preferences.exists():
            self.stdout.write("No vote preferences found. Skipping snapshot.")
            return

        paying_prefs = preferences.filter(member__membership_plan__monthly_price__gt=0)
        paying_count = paying_prefs.count()

        votes = [
            {
                "guild_1st": pref.guild_1st.name,
                "guild_2nd": pref.guild_2nd.name,
                "guild_3rd": pref.guild_3rd.name,
            }
            for pref in preferences
        ]

        calc = calculate_results(votes, paying_voter_count=paying_count)
        pool = calc["total_pool"]

        cycle_label = timezone.now().strftime("%B %Y")

        snapshot = FundingSnapshot.objects.create(
            cycle_label=cycle_label,
            contributor_count=paying_count,
            funding_pool=pool,
            results=calc,
        )

        self.stdout.write(
            f"Snapshot created: {cycle_label} — {paying_count} contributor(s) — pool ${snapshot.funding_pool}"
        )

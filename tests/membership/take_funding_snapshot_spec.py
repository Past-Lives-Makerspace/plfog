"""BDD specs for the take_funding_snapshot management command."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command

from membership.models import FundingSnapshot
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    VotePreferenceFactory,
)


@pytest.mark.django_db
def describe_take_funding_snapshot_command():
    def it_creates_snapshot_from_existing_votes():
        plan = MembershipPlanFactory(monthly_price=Decimal("100.00"))
        g1 = GuildFactory(name="Wood")
        g2 = GuildFactory(name="Metal")
        g3 = GuildFactory(name="Clay")
        member = MemberFactory(membership_plan=plan)
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        out = StringIO()
        call_command("take_funding_snapshot", stdout=out)

        assert FundingSnapshot.objects.count() == 1
        snap = FundingSnapshot.objects.first()
        assert snap is not None
        assert snap.contributor_count == 1
        assert "results" in snap.results
        assert "Snapshot created" in out.getvalue()

    def it_handles_no_votes_gracefully():
        out = StringIO()
        call_command("take_funding_snapshot", stdout=out)

        assert FundingSnapshot.objects.count() == 0
        assert "No vote preferences found" in out.getvalue()

    def it_only_counts_paying_members_in_pool():
        paying_plan = MembershipPlanFactory(monthly_price=Decimal("150.00"))
        free_plan = MembershipPlanFactory(monthly_price=Decimal("0.00"))
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        paying = MemberFactory(membership_plan=paying_plan)
        free_member = MemberFactory(membership_plan=free_plan)
        VotePreferenceFactory(member=paying, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=free_member, guild_1st=g2, guild_2nd=g1, guild_3rd=g3)

        call_command("take_funding_snapshot", stdout=StringIO())

        snap = FundingSnapshot.objects.first()
        assert snap is not None
        assert snap.contributor_count == 1
        assert snap.funding_pool == Decimal("10.00")

    def it_uses_current_month_as_cycle_label():
        from django.utils import timezone

        plan = MembershipPlanFactory(monthly_price=Decimal("50.00"))
        g1 = GuildFactory(name="Alpha")
        g2 = GuildFactory(name="Beta")
        g3 = GuildFactory(name="Gamma")
        member = MemberFactory(membership_plan=plan)
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        call_command("take_funding_snapshot", stdout=StringIO())

        snap = FundingSnapshot.objects.first()
        assert snap is not None
        expected_label = timezone.now().strftime("%B %Y")
        assert snap.cycle_label == expected_label

"""Specs that lock in the new snapshot semantics and role-intersection behavior.

See docs/superpowers/plans/2026-04-09-funding-snapshot-overhaul.md.

The schema already makes ``Member.member_type`` and ``Member.fog_role`` fully
orthogonal fields — an admin can be work-trade or an
employee, etc. These specs prove that:

1. Non-paying admin votes (work-trade, employee) count toward guild allocation but don't contribute
   to the funding pool.
2. Paying admin votes count toward both.
3. The $1,000 minimum pool floor kicks in correctly at the boundaries.
4. ``raw_votes`` is populated with denormalized role info so the analyzer can
   re-slice historical snapshots.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from membership.models import FundingSnapshot, Member
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    VotePreferenceFactory,
)


@pytest.fixture()
def three_guilds(db):
    return GuildFactory(name="Ceramics"), GuildFactory(name="Textiles"), GuildFactory(name="Wood")


@pytest.mark.django_db
def describe_snapshot_with_mixed_roles():
    def it_counts_a_work_trade_admins_vote_for_allocation_with_the_pool_at_the_floor(three_guilds):
        g1, g2, g3 = three_guilds
        admin = MemberFactory(
            member_type=Member.MemberType.WORK_TRADE,
            fog_role=Member.FogRole.ADMIN,
            full_legal_name="Oscar Admin",
        )
        VotePreferenceFactory(member=admin, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))

        assert snap is not None
        # Admin is non-paying → contributor_count=0, contributed pool=0
        assert snap.contributor_count == 0
        # But their vote drives the allocation
        assert snap.results["votes_cast"] == 1
        # Pool is at the floor
        assert snap.funding_pool == Decimal("1000.00")
        assert snap.minimum_pool == Decimal("1000.00")

    def it_counts_paying_admin_contribution_to_pool(three_guilds):
        g1, g2, g3 = three_guilds
        admin = MemberFactory(
            member_type=Member.MemberType.STANDARD,
            fog_role=Member.FogRole.ADMIN,
        )
        VotePreferenceFactory(member=admin, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        snap = FundingSnapshot.take(minimum_pool=Decimal("0"))

        assert snap is not None
        assert snap.contributor_count == 1
        assert snap.funding_pool == Decimal("10.00")

    def it_counts_non_paying_admin_votes_for_allocation_only(three_guilds):
        g1, g2, g3 = three_guilds
        admin = MemberFactory(
            member_type=Member.MemberType.EMPLOYEE,
            fog_role=Member.FogRole.ADMIN,
        )
        VotePreferenceFactory(member=admin, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        snap = FundingSnapshot.take(minimum_pool=Decimal("0"))

        assert snap is not None
        assert snap.contributor_count == 0
        assert snap.funding_pool == Decimal("0.00")
        # Allocation still ran: each guild has its points
        assert snap.results["votes_cast"] == 1

    def it_applies_minimum_pool_when_no_paying_voters(three_guilds):
        g1, g2, g3 = three_guilds
        for member_type in [
            Member.MemberType.WORK_TRADE,
            Member.MemberType.EMPLOYEE,
            Member.MemberType.VOLUNTEER,
        ]:
            m = MemberFactory(member_type=member_type)
            VotePreferenceFactory(member=m, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))

        assert snap is not None
        assert snap.contributor_count == 0
        assert snap.funding_pool == Decimal("1000.00")

    def it_applies_minimum_pool_when_contribution_is_below_floor(three_guilds):
        g1, g2, g3 = three_guilds
        for _ in range(3):
            m = MemberFactory(member_type=Member.MemberType.STANDARD)
            VotePreferenceFactory(member=m, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))

        assert snap is not None
        # 3 paying × $10 = $30, but floor lifts it to $1000
        assert snap.contributor_count == 3
        assert snap.funding_pool == Decimal("1000.00")

    def it_skips_floor_when_contribution_exceeds_it(three_guilds):
        g1, g2, g3 = three_guilds
        for _ in range(150):
            m = MemberFactory(member_type=Member.MemberType.STANDARD)
            VotePreferenceFactory(member=m, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))

        assert snap is not None
        assert snap.contributor_count == 150
        # 150 × $10 = $1500 > $1000 floor → pool = $1500
        assert snap.funding_pool == Decimal("1500.00")

    def it_stores_raw_votes_with_denormalized_role_info(three_guilds):
        g1, g2, g3 = three_guilds
        officer = MemberFactory(
            member_type=Member.MemberType.WORK_TRADE,
            fog_role=Member.FogRole.ADMIN,
            full_legal_name="Alice Officer",
        )
        paying = MemberFactory(
            member_type=Member.MemberType.STANDARD,
            fog_role=Member.FogRole.MEMBER,
            full_legal_name="Bob Standard",
        )
        VotePreferenceFactory(member=officer, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=paying, guild_1st=g2, guild_2nd=g3, guild_3rd=g1)

        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))

        assert snap is not None
        assert len(snap.raw_votes) == 2
        by_name = {v["member_name"]: v for v in snap.raw_votes}

        alice = by_name["Alice Officer"]
        assert alice["member_type"] == Member.MemberType.WORK_TRADE
        assert alice["fog_role"] == Member.FogRole.ADMIN
        assert alice["is_paying"] is False
        assert alice["guild_1st_name"] == "Ceramics"

        bob = by_name["Bob Standard"]
        assert bob["member_type"] == Member.MemberType.STANDARD
        assert bob["fog_role"] == Member.FogRole.MEMBER
        assert bob["is_paying"] is True
        assert bob["guild_1st_name"] == "Textiles"

    def it_returns_none_when_no_votes():
        snap = FundingSnapshot.take(minimum_pool=Decimal("1000"))
        assert snap is None

"""BDD specs for the take_funding_snapshot management command."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from membership.models import FundingSnapshot, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
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

    def it_only_counts_paying_members_in_contribution():
        """contributor_count is the paying-only count; pool is max(contrib, floor)."""
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        paying = MemberFactory(member_type=Member.MemberType.STANDARD)
        non_paying = MemberFactory(member_type=Member.MemberType.WORK_TRADE)
        VotePreferenceFactory(member=paying, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=non_paying, guild_1st=g2, guild_2nd=g1, guild_3rd=g3)

        # Run with no floor so we can observe the raw contribution.
        call_command("take_funding_snapshot", "--minimum-pool", "0", stdout=StringIO())

        snap = FundingSnapshot.objects.first()
        assert snap is not None
        assert snap.contributor_count == 1
        assert snap.funding_pool == Decimal("10.00")

    def it_applies_default_1000_minimum_pool_floor():
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        member = MemberFactory(member_type=Member.MemberType.STANDARD)
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        call_command("take_funding_snapshot", stdout=StringIO())

        snap = FundingSnapshot.objects.first()
        assert snap is not None
        assert snap.contributor_count == 1
        assert snap.minimum_pool == Decimal("1000.00")
        assert snap.funding_pool == Decimal("1000.00")

    def it_honors_custom_minimum_pool_flag():
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        member = MemberFactory(member_type=Member.MemberType.STANDARD)
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        call_command("take_funding_snapshot", "--minimum-pool", "500", stdout=StringIO())

        snap = FundingSnapshot.objects.first()
        assert snap is not None
        assert snap.minimum_pool == Decimal("500.00")
        assert snap.funding_pool == Decimal("500.00")

    def it_stores_raw_votes_with_denormalized_role_info():
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        member = MemberFactory(
            member_type=Member.MemberType.WORK_TRADE,
            fog_role=Member.FogRole.GUILD_OFFICER,
            full_legal_name="Alice Officer",
        )
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        call_command("take_funding_snapshot", stdout=StringIO())

        snap = FundingSnapshot.objects.first()
        assert snap is not None
        assert len(snap.raw_votes) == 1
        row = snap.raw_votes[0]
        assert row["member_id"] == member.pk
        assert row["member_name"] == "Alice Officer"
        assert row["member_type"] == Member.MemberType.WORK_TRADE
        assert row["fog_role"] == Member.FogRole.GUILD_OFFICER
        assert row["is_paying"] is False
        assert row["guild_1st_name"] == "G1"
        assert row["guild_1st_id"] == g1.pk

    def it_excludes_votes_from_members_without_linked_user():
        """Backfilled votes for Airtable-imported members who never signed up must be excluded."""
        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        paying_signed_up = MemberFactory(member_type=Member.MemberType.STANDARD)
        unlinked = MemberFactory(user=None, member_type=Member.MemberType.STANDARD)
        VotePreferenceFactory(member=paying_signed_up, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=unlinked, guild_1st=g1, guild_2nd=g2, guild_3rd=g3, signed_up=False)

        call_command("take_funding_snapshot", "--minimum-pool", "0", stdout=StringIO())

        snap = FundingSnapshot.objects.first()
        assert snap is not None
        # Only the signed-up paying member contributes; unlinked is excluded
        assert snap.contributor_count == 1
        assert snap.funding_pool == Decimal("10.00")
        assert snap.results["votes_cast"] == 1
        assert len(snap.raw_votes) == 1
        assert snap.raw_votes[0]["member_id"] == paying_signed_up.pk

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

    def it_logs_funding_snapshot_taken_activity_when_votes_exist():
        from core.models import SiteActivity

        g1 = GuildFactory(name="G1")
        g2 = GuildFactory(name="G2")
        g3 = GuildFactory(name="G3")
        member = MemberFactory()
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)

        snap = FundingSnapshot.take()

        assert snap is not None
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN).exists()
        activity = SiteActivity.objects.get(kind=SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN)
        assert activity.target_id == snap.pk
        assert activity.actor is None

    def it_does_not_log_funding_snapshot_taken_activity_when_no_votes():
        from core.models import SiteActivity

        result = FundingSnapshot.take()

        assert result is None
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.FUNDING_SNAPSHOT_TAKEN).exists()

    def it_freezes_guild_lead_and_staff_flags_in_raw_votes():
        g1, g2, g3 = GuildFactory(name="G1"), GuildFactory(name="G2"), GuildFactory(name="G3")
        lead = MemberFactory(full_legal_name="Lena Lead")
        staff = MemberFactory(full_legal_name="Sam Staff")
        plain = MemberFactory(full_legal_name="Pat Plain")
        VotePreferenceFactory(member=lead, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=staff, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        VotePreferenceFactory(member=plain, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        led_guild = GuildFactory(name="Led", guild_lead=lead)
        GuildStaffMembershipFactory(member=staff, guild=led_guild)

        snap = FundingSnapshot.take(minimum_pool=Decimal("0"))

        assert snap is not None
        by_name = {v["member_name"]: v for v in snap.raw_votes}
        assert by_name["Lena Lead"]["is_guild_lead"] is True
        assert by_name["Lena Lead"]["is_guild_staff"] is False
        assert by_name["Sam Staff"]["is_guild_staff"] is True
        assert by_name["Sam Staff"]["is_guild_lead"] is False
        assert by_name["Pat Plain"]["is_guild_lead"] is False
        assert by_name["Pat Plain"]["is_guild_staff"] is False


@pytest.mark.django_db
def describe_source_label():
    def it_is_manual():
        snap = FundingSnapshot(cycle_label="June 2026", contributor_count=0, funding_pool=Decimal("0"))
        assert snap.source_label == "Manual"


@pytest.mark.django_db
def describe_delete():
    def _snapshot_with_votes() -> FundingSnapshot:
        g1, g2, g3 = GuildFactory(name="G1"), GuildFactory(name="G2"), GuildFactory(name="G3")
        member = MemberFactory(member_type=Member.MemberType.STANDARD)
        VotePreferenceFactory(member=member, guild_1st=g1, guild_2nd=g2, guild_3rd=g3)
        snap = FundingSnapshot.take(minimum_pool=Decimal("0"))
        assert snap is not None
        return snap

    def it_hard_deletes_the_row():
        snap = _snapshot_with_votes()
        pk = snap.pk
        snap.delete()
        assert not FundingSnapshot.objects.filter(pk=pk).exists()

    def it_deletes_the_airtable_record_when_record_id_present():
        snap = _snapshot_with_votes()
        snap.airtable_record_id = "recSNAP123"
        with patch("airtable_sync.service.delete_snapshot_from_airtable") as mock_delete:
            snap.delete()
        mock_delete.assert_called_once_with("recSNAP123")

    def it_skips_airtable_when_no_record_id():
        snap = _snapshot_with_votes()
        assert snap.airtable_record_id is None
        with patch("airtable_sync.service.delete_snapshot_from_airtable") as mock_delete:
            snap.delete()
        mock_delete.assert_not_called()

    def it_skips_airtable_when_sync_disabled():
        snap = _snapshot_with_votes()
        snap.airtable_record_id = "recSNAP999"
        snap._skip_airtable_sync = True
        with patch("airtable_sync.service.delete_snapshot_from_airtable") as mock_delete:
            snap.delete()
        mock_delete.assert_not_called()

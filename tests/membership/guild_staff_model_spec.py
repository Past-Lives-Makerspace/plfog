"""BDD specs for the GuildStaffMembership model and its Guild / email helpers."""

from __future__ import annotations

import pytest

from classes.emails import _guild_leadership_recipients
from membership.models import GuildStaffMembership
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

Role = GuildStaffMembership.Role


def describe_GuildStaffMembership():
    def describe___str__():
        def it_names_the_member_role_and_guild():
            guild = GuildFactory(name="Ceramics")
            member = MemberFactory(full_legal_name="Ada Lovelace")
            sm = GuildStaffMembershipFactory(guild=guild, member=member, role=Role.TREASURER)
            assert str(sm) == f"{member.display_name} — Treasurer of Ceramics"


def describe_Guild_staff_helpers():
    def describe_is_staffed_by():
        def it_is_true_only_for_staff_members():
            guild = GuildFactory()
            staff = MemberFactory()
            stranger = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=staff, role=Role.SECRETARY)
            assert guild.is_staffed_by(staff) is True
            assert guild.is_staffed_by(stranger) is False

    def describe_staff_by_role():
        def it_groups_members_in_role_declaration_order():
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=MemberFactory(), role=Role.SECRETARY)
            GuildStaffMembershipFactory(guild=guild, member=MemberFactory(), role=Role.ORIENTER)
            GuildStaffMembershipFactory(guild=guild, member=MemberFactory(), role=Role.CO_LEAD)
            labels = [label for label, _ in guild.staff_by_role()]
            assert labels == ["Co-Guild Lead", "Secretary", "Orienter"]

        def it_sorts_members_within_a_role_by_name():
            guild = GuildFactory()
            zoe = MemberFactory(full_legal_name="Zoe")
            amy = MemberFactory(full_legal_name="Amy")
            GuildStaffMembershipFactory(guild=guild, member=zoe, role=Role.CO_LEAD)
            GuildStaffMembershipFactory(guild=guild, member=amy, role=Role.CO_LEAD)
            _label, rows = guild.staff_by_role()[0]
            assert [r.member_id for r in rows] == [amy.pk, zoe.pk]

        def it_is_empty_with_no_staff():
            assert GuildFactory().staff_by_role() == []

    def describe_leadership_members():
        def it_returns_the_lead_plus_staff_without_duplicates():
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            staff = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=staff, role=Role.SECRETARY)
            # The lead also holds a staff role — they must not appear twice.
            GuildStaffMembershipFactory(guild=guild, member=lead, role=Role.TREASURER)
            ids = [m.pk for m in guild.leadership_members()]
            assert ids.count(lead.pk) == 1
            assert staff.pk in ids

        def it_is_empty_for_a_leadless_guild_with_no_staff():
            assert GuildFactory(guild_lead=None).leadership_members() == []


def describe_guild_leadership_recipients():
    def it_returns_empty_for_no_guild():
        assert _guild_leadership_recipients(None) == []

    def it_dedupes_and_skips_members_without_an_email():
        lead = MemberFactory(_pre_signup_email="lead@example.com")
        guild = GuildFactory(guild_lead=lead)
        with_email = MemberFactory(_pre_signup_email="staff@example.com")
        without_email = MemberFactory(_pre_signup_email="")
        GuildStaffMembershipFactory(guild=guild, member=with_email, role=Role.CO_LEAD)
        GuildStaffMembershipFactory(guild=guild, member=without_email, role=Role.ORIENTER)
        assert _guild_leadership_recipients(guild) == ["lead@example.com", "staff@example.com"]

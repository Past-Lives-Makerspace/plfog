"""BDD specs for the GuildStaffMembership model and its Guild / email helpers."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

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

        def it_uses_the_custom_title_when_set():
            guild = GuildFactory(name="Ceramics")
            member = MemberFactory(full_legal_name="Ada Lovelace")
            sm = GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Glaze Technician")
            assert str(sm) == f"{member.display_name} — Glaze Technician of Ceramics"

    def describe_display_title():
        def it_returns_the_custom_title_when_set():
            sm = GuildStaffMembershipFactory(custom=True, custom_title="Studio Technician")
            assert sm.display_title == "Studio Technician"

        def it_falls_back_to_the_preset_role_label():
            sm = GuildStaffMembershipFactory(role=Role.ORIENTER)
            assert sm.display_title == "Orientator"

    def describe_constraints():
        def it_rejects_a_row_with_neither_role_nor_custom_title():
            with pytest.raises(IntegrityError), transaction.atomic():
                GuildStaffMembershipFactory(role="", custom_title="")

        def it_rejects_a_row_with_both_a_role_and_a_custom_title():
            with pytest.raises(IntegrityError), transaction.atomic():
                GuildStaffMembershipFactory(role=Role.CO_LEAD, custom_title="Studio Technician")

        def it_rejects_a_duplicate_preset_role_for_the_same_member():
            guild = GuildFactory()
            member = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=member, role=Role.SECRETARY)
            with pytest.raises(IntegrityError), transaction.atomic():
                GuildStaffMembershipFactory(guild=guild, member=member, role=Role.SECRETARY)

        def it_rejects_a_duplicate_custom_title_for_the_same_member():
            guild = GuildFactory()
            member = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Studio Technician")
            with pytest.raises(IntegrityError), transaction.atomic():
                GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Studio Technician")

        def it_allows_one_member_to_hold_a_preset_role_and_a_custom_title():
            guild = GuildFactory()
            member = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=member, role=Role.ORIENTER)
            GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Studio Technician")
            assert guild.staff_memberships.filter(member=member).count() == 2


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
            assert labels == ["Guild Lead", "Secretary", "Orientator"]

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

        def it_lists_presets_first_then_custom_titles_alphabetically():
            guild = GuildFactory()
            GuildStaffMembershipFactory(guild=guild, member=MemberFactory(), role=Role.SECRETARY)
            GuildStaffMembershipFactory(
                guild=guild, member=MemberFactory(), custom=True, custom_title="Studio Technician"
            )
            GuildStaffMembershipFactory(
                guild=guild, member=MemberFactory(), custom=True, custom_title="Glaze Technician"
            )
            labels = [label for label, _ in guild.staff_by_role()]
            assert labels == ["Secretary", "Glaze Technician", "Studio Technician"]

        def it_groups_a_custom_title_as_its_own_heading():
            guild = GuildFactory()
            member = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Glaze Technician")
            grouped = dict(guild.staff_by_role())
            assert [s.member_id for s in grouped["Glaze Technician"]] == [member.pk]

    def describe_staff_by_member():
        def it_lists_each_member_once_with_all_their_titles():
            guild = GuildFactory()
            member = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=member, role=Role.ORIENTER)
            GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Glaze Technician")
            grouped = guild.staff_by_member()
            assert [m.pk for m, _rows in grouped] == [member.pk]
            titles = [sm.display_title for sm in grouped[0][1]]
            assert titles == ["Orientator", "Glaze Technician"]

        def it_orders_a_members_titles_presets_first_then_custom_alphabetically():
            guild = GuildFactory()
            member = MemberFactory()
            GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Studio Technician")
            GuildStaffMembershipFactory(guild=guild, member=member, role=Role.TREASURER)
            GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Glaze Technician")
            GuildStaffMembershipFactory(guild=guild, member=member, role=Role.CO_LEAD)
            titles = [sm.display_title for sm in guild.staff_by_member()[0][1]]
            assert titles == ["Guild Lead", "Treasurer", "Glaze Technician", "Studio Technician"]

        def it_sorts_members_by_name_case_insensitively():
            guild = GuildFactory()
            zoe = MemberFactory(full_legal_name="zoe")
            amy = MemberFactory(full_legal_name="Amy")
            GuildStaffMembershipFactory(guild=guild, member=zoe, role=Role.CO_LEAD)
            GuildStaffMembershipFactory(guild=guild, member=amy, role=Role.SECRETARY)
            assert [m.pk for m, _rows in guild.staff_by_member()] == [amy.pk, zoe.pk]

        def it_is_empty_with_no_staff():
            assert GuildFactory().staff_by_member() == []

        def it_takes_a_single_query_with_no_n_plus_one(django_assert_num_queries):
            guild = GuildFactory()
            for member in (MemberFactory(), MemberFactory()):
                GuildStaffMembershipFactory(guild=guild, member=member, role=Role.ORIENTER)
                GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Glaze Technician")
            with django_assert_num_queries(1):
                for member, rows in guild.staff_by_member():
                    _ = member.display_name
                    for sm in rows:
                        _ = sm.display_title

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

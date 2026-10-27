"""BDD specs for the shared guild staff methods (#968).

The guild Staff tab and the member edit Permissions tab change a person's guild roles through
the same model methods: ``Guild.add_staff``, ``Guild.remove_staff``, ``Guild.clear_lead``,
``GuildStaffMembership.retitle`` and ``GuildStaffMembership.title_problem``. ``Member.guild_roles``
is what the Permissions tab lists.
"""

from __future__ import annotations

import pytest

from membership.models import Guild, GuildRoles, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

Role = GuildStaffMembership.Role


def _flags(guild: Guild, member: Member) -> list[bool]:
    return list(guild.staff_memberships.filter(member=member).values_list("can_review_classes", flat=True))


def describe_title_problem():
    def it_accepts_one_preset_role():
        guild, member = GuildFactory(), MemberFactory()
        assert GuildStaffMembership.title_problem(guild, member, Role.TREASURER, "") == ""

    def it_refuses_a_role_and_a_title_together():
        guild, member = GuildFactory(), MemberFactory()
        assert "not both" in GuildStaffMembership.title_problem(guild, member, Role.TREASURER, "Tech")

    def it_refuses_neither_unless_only_class_reviewer_is_asked():
        guild, member = GuildFactory(), MemberFactory()
        assert GuildStaffMembership.title_problem(guild, member, "", "") == "Pick a role or type a custom title."
        assert GuildStaffMembership.title_problem(guild, member, "", "", class_reviewer=True) == ""

    def it_refuses_a_custom_title_that_is_a_preset_label():
        guild, member = GuildFactory(), MemberFactory()
        assert "already a preset role" in GuildStaffMembership.title_problem(guild, member, "", "treasurer")

    def it_refuses_a_role_or_title_the_member_already_holds():
        guild, member = GuildFactory(), MemberFactory(full_legal_name="Ana Held")
        GuildStaffMembershipFactory(guild=guild, member=member, role=Role.ORIENTER)
        GuildStaffMembershipFactory(guild=guild, member=member, role="", custom_title="Kiln Tech")
        assert "is already Orientor" in GuildStaffMembership.title_problem(guild, member, Role.ORIENTER, "")
        assert "already holds the title" in GuildStaffMembership.title_problem(guild, member, "", "kiln tech")

    def it_skips_the_row_being_renamed():
        guild, member = GuildFactory(), MemberFactory()
        row = GuildStaffMembershipFactory(guild=guild, member=member, role="", custom_title="Kiln Tech")
        assert GuildStaffMembership.title_problem(guild, member, "", "Kiln Tech", exclude_pk=row.pk) == ""


def describe_add_staff():
    def it_adds_a_preset_role_without_class_reviewer():
        guild, member = GuildFactory(), MemberFactory()
        staff = guild.add_staff(member, role=Role.SECRETARY)
        assert staff is not None
        assert (staff.role, staff.custom_title, staff.can_review_classes) == (Role.SECRETARY, "", False)

    def it_adds_a_custom_title_with_class_reviewer_on_every_row():
        guild, member = GuildFactory(), MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=member, role=Role.ORIENTER)
        staff = guild.add_staff(member, custom_title="Glaze Tech", class_reviewer=True)
        assert staff is not None and staff.custom_title == "Glaze Tech"
        assert _flags(guild, member) == [True, True]

    def it_adds_a_class_reviewer_row_when_only_class_reviewer_is_asked():
        guild, member = GuildFactory(), MemberFactory()
        staff = guild.add_staff(member, class_reviewer=True)
        assert staff is not None
        assert (staff.role, staff.custom_title, staff.can_review_classes) == ("", "Class Reviewer", True)

    def it_only_switches_on_a_member_already_on_the_staff():
        guild, member = GuildFactory(), MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=member, role=Role.TREASURER)
        assert guild.add_staff(member, class_reviewer=True) is None
        assert guild.staff_memberships.filter(member=member).count() == 1
        assert _flags(guild, member) == [True]

    def it_gives_a_new_row_the_review_state_the_member_already_has():
        guild, member = GuildFactory(), MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=member, role=Role.TREASURER, can_review_classes=True)
        staff = guild.add_staff(member, role=Role.ORIENTER)
        assert staff is not None and staff.can_review_classes is True

    def it_refuses_the_guild_lead():
        member = MemberFactory()
        guild = GuildFactory(guild_lead=member)
        with pytest.raises(ValueError, match="already has every staff power"):
            guild.add_staff(member, role=Role.TREASURER)

    def it_refuses_a_bad_title():
        guild, member = GuildFactory(), MemberFactory()
        with pytest.raises(ValueError, match="Pick a role"):
            guild.add_staff(member)


def describe_remove_staff():
    def it_deletes_the_row_and_keeps_hours_while_another_role_remains():
        guild, member = GuildFactory(), MemberFactory()
        keep = GuildStaffMembershipFactory(guild=guild, member=member, role=Role.ORIENTER)
        drop = GuildStaffMembershipFactory(guild=guild, member=member, role=Role.TREASURER)
        assert guild.remove_staff(drop) == 0
        assert list(guild.staff_memberships.filter(member=member)) == [keep]

    def it_retires_orienter_hours_on_the_last_row(monkeypatch):
        from membership import orientations

        guild, member = GuildFactory(), MemberFactory()
        row = GuildStaffMembershipFactory(guild=guild, member=member, role=Role.ORIENTER)
        seen: list[tuple[Guild, Member]] = []

        def fake_retire(g: Guild, m: Member) -> tuple[int, int]:
            seen.append((g, m))
            return (0, 2)

        monkeypatch.setattr(orientations, "retire_orienter", fake_retire)
        assert guild.remove_staff(row) == 2
        assert seen == [(guild, member)]
        assert not guild.staff_memberships.exists()

    def it_refuses_a_row_from_another_guild():
        row = GuildStaffMembershipFactory(role=Role.TREASURER)
        with pytest.raises(ValueError, match="not on"):
            GuildFactory().remove_staff(row)
        assert GuildStaffMembership.objects.filter(pk=row.pk).exists()


def describe_clear_lead():
    def it_removes_the_lead_and_returns_them_keeping_staff_rows():
        lead = MemberFactory()
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=lead, role=Role.TREASURER)
        assert guild.clear_lead() == lead
        guild.refresh_from_db()
        assert guild.guild_lead is None
        assert guild.staff_memberships.filter(member=lead).exists()


def describe_retitle():
    def it_changes_the_title_and_keeps_class_reviewer():
        row = GuildStaffMembershipFactory(role=Role.TREASURER, can_review_classes=True)
        row.retitle("", "Studio Tech")
        row.refresh_from_db()
        assert (row.role, row.custom_title, row.can_review_classes) == ("", "Studio Tech", True)

    def it_refuses_a_title_the_member_already_holds():
        row = GuildStaffMembershipFactory(role=Role.TREASURER)
        GuildStaffMembershipFactory(guild=row.guild, member=row.member, role=Role.ORIENTER)
        with pytest.raises(ValueError, match="is already Orientor"):
            row.retitle(Role.ORIENTER, "")


def describe_guild_roles():
    def it_lists_led_and_staffed_guilds_by_name_with_review_state():
        member = MemberFactory()
        led = GuildFactory(name="Zinc Guild", guild_lead=member)
        staffed = GuildFactory(name="Alder Guild")
        reviewing = GuildFactory(name="Maple Guild")
        GuildFactory(name="Unrelated Guild")
        GuildStaffMembershipFactory(guild=staffed, member=member, role="", custom_title="Wood Tech")
        GuildStaffMembershipFactory(guild=staffed, member=member, role=Role.ORIENTER)
        GuildStaffMembershipFactory(guild=reviewing, member=member, role=Role.SECRETARY, can_review_classes=True)

        roles = member.guild_roles()

        assert [r.guild.name for r in roles] == ["Alder Guild", "Maple Guild", "Zinc Guild"]
        alder, maple, zinc = roles
        assert alder == GuildRoles(guild=staffed, is_lead=False, staff=alder.staff, reviews_classes=False)
        assert [row.display_title for row in alder.staff] == ["Orientor", "Wood Tech"]
        assert (maple.is_lead, maple.reviews_classes) == (False, True)
        assert (zinc.guild, zinc.is_lead, zinc.staff, zinc.reviews_classes) == (led, True, [], True)

    def it_is_empty_for_someone_with_no_guild_role():
        assert MemberFactory().guild_roles() == []

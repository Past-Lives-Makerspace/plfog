"""BDD specs for the guild Staff tab — co-leads, secretaries, treasurers, orienters.

Every staff role carries the same authority as the guild lead: staff can edit the
guild page and its classes, run orientations, and receive the same emails. Staff are
managed on the guild edit page's Staff tab. See ``GuildStaffMembership`` and
``membership.permissions``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import GuildStaffMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

Role = GuildStaffMembership.Role


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    if not member.full_legal_name:
        member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def describe_staff_tab_on_guild_edit():
    def it_shows_staff_context_and_add_form_to_a_lead(client: Client):
        lead = _member_user("s_lead")
        guild = GuildFactory(guild_lead=lead.member)
        co = _member_user("s_co")
        GuildStaffMembershipFactory(guild=guild, member=co.member, role=Role.CO_LEAD)
        client.login(username="s_lead", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert response.status_code == 200
        groups = response.context["staff_by_member"]
        assert groups[0][0].pk == co.member.pk
        assert groups[0][1][0].member_id == co.member.pk
        assert reverse("hub_guild_staff_add", args=[guild.pk]).encode() in response.content

    def it_excludes_the_lead_from_the_candidate_list(client: Client):
        lead = _member_user("s_cand_lead")
        guild = GuildFactory(guild_lead=lead.member)
        fresh = _member_user("s_cand_fresh")
        client.login(username="s_cand_lead", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        ids = set(response.context["staff_add_form"].fields["member"].queryset.values_list("pk", flat=True))
        assert fresh.member.pk in ids
        assert lead.member.pk not in ids


def describe_GuildStaffAddForm():
    def _form(guild: object, data: dict[str, object]) -> object:
        from hub.forms import GuildStaffAddForm

        return GuildStaffAddForm(data, member_queryset=Member.objects.all(), guild=guild)

    def it_accepts_a_preset_role():
        member = MemberFactory()
        form = _form(GuildFactory(), {"member": member.pk, "role": Role.TREASURER.value})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["role"] == Role.TREASURER.value
        assert form.cleaned_data["custom_title"] == ""

    def it_accepts_and_trims_a_custom_title():
        member = MemberFactory()
        form = _form(GuildFactory(), {"member": member.pk, "custom_title": "  Studio Technician  "})
        assert form.is_valid(), form.errors
        assert form.cleaned_data["custom_title"] == "Studio Technician"
        assert form.cleaned_data["role"] == ""

    def it_rejects_both_a_role_and_a_custom_title():
        member = MemberFactory()
        form = _form(
            GuildFactory(), {"member": member.pk, "role": Role.CO_LEAD.value, "custom_title": "Studio Technician"}
        )
        assert not form.is_valid()
        assert "not both" in " ".join(form.non_field_errors())

    def it_rejects_neither_a_role_nor_a_custom_title():
        member = MemberFactory()
        form = _form(GuildFactory(), {"member": member.pk})
        assert not form.is_valid()
        assert "Pick a role or type a custom title." in form.non_field_errors()

    def it_rejects_a_duplicate_preset_role_the_member_already_holds():
        guild = GuildFactory()
        member = MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=member, role=Role.SECRETARY)
        form = _form(guild, {"member": member.pk, "role": Role.SECRETARY.value})
        assert not form.is_valid()
        assert "is already" in " ".join(form.non_field_errors())

    def it_rejects_a_duplicate_custom_title_case_insensitively():
        guild = GuildFactory()
        member = MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=member, custom=True, custom_title="Studio Technician")
        form = _form(guild, {"member": member.pk, "custom_title": "studio technician"})
        assert not form.is_valid()
        assert "already holds the title" in " ".join(form.non_field_errors())

    def it_rejects_a_custom_title_that_matches_a_preset_role_label():
        member = MemberFactory()
        form = _form(GuildFactory(), {"member": member.pk, "custom_title": "treasurer"})
        assert not form.is_valid()
        assert "already a preset role" in " ".join(form.non_field_errors())


def describe_guild_staff_add():
    def it_lets_a_lead_add_a_staff_member(client: Client):
        lead = _member_user("a_lead")
        guild = GuildFactory(guild_lead=lead.member)
        target = _member_user("a_target")
        client.login(username="a_lead", password="pass")
        response = client.post(
            reverse("hub_guild_staff_add", args=[guild.pk]),
            {"member": target.member.pk, "role": Role.TREASURER.value},
        )
        assert response.status_code == 302
        assert guild.staff_memberships.filter(member=target.member, role=Role.TREASURER).exists()

    def it_lets_a_lead_add_a_staff_member_with_a_custom_title(client: Client):
        lead = _member_user("a_custom_lead")
        guild = GuildFactory(guild_lead=lead.member)
        target = _member_user("a_custom_target")
        client.login(username="a_custom_lead", password="pass")
        response = client.post(
            reverse("hub_guild_staff_add", args=[guild.pk]),
            {"member": target.member.pk, "custom_title": "Studio Technician"},
        )
        assert response.status_code == 302
        sm = guild.staff_memberships.get(member=target.member)
        assert sm.role == ""
        assert sm.custom_title == "Studio Technician"
        page = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert b"Studio Technician" in page.content

    def it_lets_a_staff_member_add_another_staff_member(client: Client):
        guild = GuildFactory()
        me = _member_user("a_staff")
        GuildStaffMembershipFactory(guild=guild, member=me.member, role=Role.CO_LEAD)
        target = _member_user("a_staff_target")
        client.login(username="a_staff", password="pass")
        response = client.post(
            reverse("hub_guild_staff_add", args=[guild.pk]),
            {"member": target.member.pk, "role": Role.SECRETARY.value},
        )
        assert response.status_code == 302
        assert guild.staff_memberships.filter(member=target.member).exists()

    def it_lets_an_admin_add_staff_to_a_leadless_guild(client: Client):
        _member_user("a_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(guild_lead=None)
        target = _member_user("a_leadless_target")
        client.login(username="a_admin", password="pass")
        response = client.post(
            reverse("hub_guild_staff_add", args=[guild.pk]),
            {"member": target.member.pk, "role": Role.ORIENTER.value},
        )
        assert response.status_code == 302
        assert guild.staff_memberships.filter(member=target.member).exists()

    def it_forbids_an_unrelated_member(client: Client):
        guild = GuildFactory()
        _member_user("a_stranger")
        target = _member_user("a_stranger_target")
        client.login(username="a_stranger", password="pass")
        response = client.post(
            reverse("hub_guild_staff_add", args=[guild.pk]),
            {"member": target.member.pk, "role": Role.CO_LEAD.value},
        )
        assert response.status_code == 403
        assert not guild.staff_memberships.exists()

    def it_is_idempotent_for_a_duplicate_role(client: Client):
        lead = _member_user("a_dup_lead")
        guild = GuildFactory(guild_lead=lead.member)
        target = _member_user("a_dup_target")
        GuildStaffMembershipFactory(guild=guild, member=target.member, role=Role.SECRETARY)
        client.login(username="a_dup_lead", password="pass")
        response = client.post(
            reverse("hub_guild_staff_add", args=[guild.pk]),
            {"member": target.member.pk, "role": Role.SECRETARY.value},
        )
        assert response.status_code == 302
        assert guild.staff_memberships.filter(member=target.member, role=Role.SECRETARY).count() == 1

    def it_rejects_an_invalid_submission_without_adding(client: Client):
        lead = _member_user("a_inv")
        guild = GuildFactory(guild_lead=lead.member)
        client.login(username="a_inv", password="pass")
        response = client.post(
            reverse("hub_guild_staff_add", args=[guild.pk]),
            {"member": "999999", "role": Role.CO_LEAD.value},
        )
        assert response.status_code == 302
        assert guild.staff_memberships.count() == 0

    def it_rejects_get(client: Client):
        lead = _member_user("a_get")
        guild = GuildFactory(guild_lead=lead.member)
        client.login(username="a_get", password="pass")
        assert client.get(reverse("hub_guild_staff_add", args=[guild.pk])).status_code == 405


def describe_guild_staff_remove():
    def it_lets_a_lead_remove_a_staff_member(client: Client):
        lead = _member_user("rm_lead")
        guild = GuildFactory(guild_lead=lead.member)
        target = _member_user("rm_target")
        sm = GuildStaffMembershipFactory(guild=guild, member=target.member, role=Role.ORIENTER)
        client.login(username="rm_lead", password="pass")
        response = client.post(reverse("hub_guild_staff_remove", args=[guild.pk, sm.pk]))
        assert response.status_code == 302
        assert not guild.staff_memberships.filter(pk=sm.pk).exists()

    def it_forbids_an_unrelated_member(client: Client):
        guild = GuildFactory()
        target = _member_user("rm_t2")
        sm = GuildStaffMembershipFactory(guild=guild, member=target.member, role=Role.CO_LEAD)
        _member_user("rm_stranger")
        client.login(username="rm_stranger", password="pass")
        response = client.post(reverse("hub_guild_staff_remove", args=[guild.pk, sm.pk]))
        assert response.status_code == 403
        assert guild.staff_memberships.filter(pk=sm.pk).exists()

    def it_404s_for_a_membership_on_another_guild(client: Client):
        lead = _member_user("rm_404")
        guild = GuildFactory(guild_lead=lead.member)
        other = GuildFactory()
        sm = GuildStaffMembershipFactory(guild=other, role=Role.CO_LEAD)
        client.login(username="rm_404", password="pass")
        assert client.post(reverse("hub_guild_staff_remove", args=[guild.pk, sm.pk])).status_code == 404

    def it_rejects_get(client: Client):
        lead = _member_user("rm_get")
        guild = GuildFactory(guild_lead=lead.member)
        client.login(username="rm_get", password="pass")
        assert client.get(reverse("hub_guild_staff_remove", args=[guild.pk, 1])).status_code == 405


def describe_staff_gain_full_guild_lead_access():
    def it_lets_a_staff_member_open_the_guild_editor(client: Client):
        guild = GuildFactory()
        staff = _member_user("e_staff")
        GuildStaffMembershipFactory(guild=guild, member=staff.member, role=Role.SECRETARY)
        client.login(username="e_staff", password="pass")
        assert client.get(reverse("hub_guild_edit", args=[guild.pk])).status_code == 200

    def it_shows_staff_under_the_lead_section_on_the_public_page(client: Client):
        guild = GuildFactory()
        staff = _member_user("p_staff")
        GuildStaffMembershipFactory(guild=guild, member=staff.member, role=Role.TREASURER)
        client.login(username="p_staff", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        assert b"Treasurer" in response.content
        assert staff.member.display_name.encode() in response.content


def describe_staff_grouped_by_member_with_title_badges():
    def it_shows_a_member_once_with_every_title_on_the_public_page(client: Client):
        lead = _member_user("pp_lead")
        guild = GuildFactory(guild_lead=lead.member)
        staff = _member_user("pp_staff")
        GuildStaffMembershipFactory(guild=guild, member=staff.member, role=Role.ORIENTER)
        GuildStaffMembershipFactory(guild=guild, member=staff.member, custom=True, custom_title="Glaze Technician")
        client.login(username="pp_lead", password="pass")
        response = client.get(reverse("hub_guild_detail", args=[guild.slug]))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Orientator" in content
        assert "Glaze Technician" in content
        # The fix: one row per person, so the staff member's name shows exactly once.
        assert content.count(staff.member.display_name) == 1

    def it_shows_a_member_once_with_per_title_remove_on_the_staff_tab(client: Client):
        lead = _member_user("bb_lead")
        guild = GuildFactory(guild_lead=lead.member)
        staff = _member_user("bb_staff")
        sm_role = GuildStaffMembershipFactory(guild=guild, member=staff.member, role=Role.ORIENTER)
        sm_custom = GuildStaffMembershipFactory(
            guild=guild, member=staff.member, custom=True, custom_title="Glaze Technician"
        )
        client.login(username="bb_lead", password="pass")
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        grouped = response.context["staff_by_member"]
        assert [m.pk for m, _rows in grouped] == [staff.member.pk]
        content = response.content.decode()
        assert "Orientator" in content
        assert "Glaze Technician" in content
        # Each title has its own Remove control wired to that membership's confirm modal.
        assert f"del-staff-{sm_role.pk}" in content
        assert f"del-staff-{sm_custom.pk}" in content

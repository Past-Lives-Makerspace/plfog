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
    def _form(guild: object, data: dict[str, object], *, allow_co_lead: bool = False) -> object:
        from hub.forms import GuildStaffAddForm

        return GuildStaffAddForm(data, member_queryset=Member.objects.all(), guild=guild, allow_co_lead=allow_co_lead)

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
            GuildFactory(), {"member": member.pk, "role": Role.TREASURER.value, "custom_title": "Studio Technician"}
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

    def it_omits_co_lead_from_the_role_choices_for_non_admins():
        form = _form(GuildFactory(), {})
        values = [value for value, _ in form.fields["role"].choices]
        assert Role.CO_LEAD.value not in values
        assert Role.SECRETARY.value in values

    def it_offers_co_lead_when_allowed():
        form = _form(GuildFactory(), {}, allow_co_lead=True)
        values = [value for value, _ in form.fields["role"].choices]
        assert Role.CO_LEAD.value in values

    def it_rejects_a_co_lead_add_when_not_allowed():
        member = MemberFactory()
        form = _form(GuildFactory(), {"member": member.pk, "role": Role.CO_LEAD.value})
        assert not form.is_valid()
        assert "Only an admin can add a Co-Lead." in form.non_field_errors()

    def it_accepts_a_co_lead_add_when_allowed():
        member = MemberFactory()
        form = _form(GuildFactory(), {"member": member.pk, "role": Role.CO_LEAD.value}, allow_co_lead=True)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["role"] == Role.CO_LEAD.value


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

    def describe_co_lead_is_admin_only():
        def it_forbids_an_orienter_adding_a_co_lead(client: Client):
            guild = GuildFactory()
            orienter = _member_user("cl_orienter")
            GuildStaffMembershipFactory(guild=guild, member=orienter.member, role=Role.ORIENTER)
            target = _member_user("cl_orienter_target")
            client.login(username="cl_orienter", password="pass")
            response = client.post(
                reverse("hub_guild_staff_add", args=[guild.pk]),
                {"member": target.member.pk, "role": Role.CO_LEAD.value},
                follow=True,
            )
            assert not guild.staff_memberships.filter(role=Role.CO_LEAD).exists()
            assert any("Only an admin can add a Co-Lead." in str(m) for m in response.context["messages"])

        def it_forbids_the_guild_lead_adding_a_co_lead(client: Client):
            lead = _member_user("cl_lead")
            guild = GuildFactory(guild_lead=lead.member)
            target = _member_user("cl_lead_target")
            client.login(username="cl_lead", password="pass")
            response = client.post(
                reverse("hub_guild_staff_add", args=[guild.pk]),
                {"member": target.member.pk, "role": Role.CO_LEAD.value},
            )
            assert response.status_code == 302
            assert not guild.staff_memberships.exists()

        def it_lets_an_admin_add_a_co_lead(client: Client):
            _member_user("cl_admin", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory()
            target = _member_user("cl_admin_target")
            client.login(username="cl_admin", password="pass")
            response = client.post(
                reverse("hub_guild_staff_add", args=[guild.pk]),
                {"member": target.member.pk, "role": Role.CO_LEAD.value},
            )
            assert response.status_code == 302
            assert guild.staff_memberships.filter(member=target.member, role=Role.CO_LEAD).exists()

        def it_hides_the_co_lead_option_from_a_lead(client: Client):
            lead = _member_user("cl_ui_lead")
            guild = GuildFactory(guild_lead=lead.member)
            client.login(username="cl_ui_lead", password="pass")
            response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
            assert response.status_code == 200
            assert b'value="co_lead"' not in response.content

        def it_offers_the_co_lead_option_to_an_admin(client: Client):
            _member_user("cl_ui_admin", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory()
            client.login(username="cl_ui_admin", password="pass")
            response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
            assert response.status_code == 200
            assert b'value="co_lead"' in response.content

        def it_still_lets_a_lead_add_a_secretary(client: Client):
            lead = _member_user("cl_sec_lead")
            guild = GuildFactory(guild_lead=lead.member)
            target = _member_user("cl_sec_target")
            client.login(username="cl_sec_lead", password="pass")
            response = client.post(
                reverse("hub_guild_staff_add", args=[guild.pk]),
                {"member": target.member.pk, "role": Role.SECRETARY.value},
            )
            assert response.status_code == 302
            assert guild.staff_memberships.filter(member=target.member, role=Role.SECRETARY).exists()


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


def describe_guild_lead_set():
    def it_lets_an_admin_set_a_lead(client: Client):
        _member_user("gl_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(guild_lead=None)
        target = _member_user("gl_target")
        client.login(username="gl_admin", password="pass")
        response = client.post(reverse("hub_guild_lead_set", args=[guild.pk]), {"member": target.member.pk})
        assert response.status_code == 302
        guild.refresh_from_db()
        assert guild.guild_lead_id == target.member.pk

    def it_lets_an_admin_replace_the_lead(client: Client):
        _member_user("gl_admin2", fog_role=Member.FogRole.ADMIN)
        old = _member_user("gl_old_lead")
        guild = GuildFactory(guild_lead=old.member)
        new = _member_user("gl_new_lead")
        client.login(username="gl_admin2", password="pass")
        response = client.post(reverse("hub_guild_lead_set", args=[guild.pk]), {"member": new.member.pk})
        assert response.status_code == 302
        guild.refresh_from_db()
        assert guild.guild_lead_id == new.member.pk

    def it_surfaces_the_command_style_warnings(client: Client):
        _member_user("gl_warn_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(guild_lead=None)
        unlinked = MemberFactory()  # no linked user account
        client.login(username="gl_warn_admin", password="pass")
        response = client.post(reverse("hub_guild_lead_set", args=[guild.pk]), {"member": unlinked.pk}, follow=True)
        guild.refresh_from_db()
        assert guild.guild_lead_id == unlinked.pk  # warned, not refused — mirrors set_guild_lead
        assert any("no linked user account" in str(m) for m in response.context["messages"])

    def it_forbids_the_guild_lead(client: Client):
        lead = _member_user("gl_lead")
        guild = GuildFactory(guild_lead=lead.member)
        target = _member_user("gl_lead_target")
        client.login(username="gl_lead", password="pass")
        response = client.post(reverse("hub_guild_lead_set", args=[guild.pk]), {"member": target.member.pk})
        assert response.status_code == 403
        guild.refresh_from_db()
        assert guild.guild_lead_id == lead.member.pk

    def it_forbids_an_orienter(client: Client):
        guild = GuildFactory()
        orienter = _member_user("gl_orienter")
        GuildStaffMembershipFactory(guild=guild, member=orienter.member, role=Role.ORIENTER)
        target = _member_user("gl_orienter_target")
        client.login(username="gl_orienter", password="pass")
        response = client.post(reverse("hub_guild_lead_set", args=[guild.pk]), {"member": target.member.pk})
        assert response.status_code == 403
        guild.refresh_from_db()
        assert guild.guild_lead_id is None

    def it_rejects_an_invalid_member_without_changing_the_lead(client: Client):
        _member_user("gl_inv_admin", fog_role=Member.FogRole.ADMIN)
        old = _member_user("gl_inv_old")
        guild = GuildFactory(guild_lead=old.member)
        client.login(username="gl_inv_admin", password="pass")
        response = client.post(reverse("hub_guild_lead_set", args=[guild.pk]), {"member": "999999"})
        assert response.status_code == 302
        guild.refresh_from_db()
        assert guild.guild_lead_id == old.member.pk

    def it_rejects_get(client: Client):
        _member_user("gl_get_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        client.login(username="gl_get_admin", password="pass")
        assert client.get(reverse("hub_guild_lead_set", args=[guild.pk])).status_code == 405

    def describe_the_staff_tab_control():
        def it_renders_for_an_admin(client: Client):
            _member_user("gl_ui_admin", fog_role=Member.FogRole.ADMIN)
            guild = GuildFactory()
            client.login(username="gl_ui_admin", password="pass")
            response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
            assert response.status_code == 200
            assert reverse("hub_guild_lead_set", args=[guild.pk]).encode() in response.content
            assert response.context["lead_form"] is not None

        def it_does_not_render_for_the_guild_lead(client: Client):
            lead = _member_user("gl_ui_lead")
            guild = GuildFactory(guild_lead=lead.member)
            client.login(username="gl_ui_lead", password="pass")
            response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
            assert response.status_code == 200
            assert reverse("hub_guild_lead_set", args=[guild.pk]).encode() not in response.content
            assert response.context["lead_form"] is None

        def it_does_not_render_for_an_orienter(client: Client):
            guild = GuildFactory()
            orienter = _member_user("gl_ui_orienter")
            GuildStaffMembershipFactory(guild=guild, member=orienter.member, role=Role.ORIENTER)
            client.login(username="gl_ui_orienter", password="pass")
            response = client.get(reverse("hub_guild_edit", args=[guild.pk]))
            assert response.status_code == 200
            assert reverse("hub_guild_lead_set", args=[guild.pk]).encode() not in response.content


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
        assert "Orienter" in content
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
        assert "Orienter" in content
        assert "Glaze Technician" in content
        # Each title has its own Remove control wired to that membership's confirm modal.
        assert f"del-staff-{sm_role.pk}" in content
        assert f"del-staff-{sm_custom.pk}" in content

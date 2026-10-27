"""BDD specs for Guild Roles on the member edit Permissions tab, and the Staff tab's add switch (#968).

An admin manages every guild role a person holds from that person's page: each guild they lead
or staff with its Class Reviewer switch (the Staff tab's own endpoint), Add to a Guild (staff
row, optional Class Reviewer, or Class Reviewer alone), Change and Remove on each title, and
Make Guild Lead / Remove as lead. Admins only; nothing is emailed or notified. The guild Staff
tab's add form gains the Class Reviewer switch for the lead and admins.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.models import Notification
from membership.models import Guild, GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

Role = GuildStaffMembership.Role
PASSWORD = "pw12345!"


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password=PASSWORD)
    member = user.member
    member.fog_role = fog_role
    member.full_legal_name = username.title()
    member.save(update_fields=["fog_role", "full_legal_name"])
    member.sync_user_permissions()
    client.login(username=username, password=PASSWORD)
    return member


@pytest.fixture
def admin(client: Client) -> Member:
    return _login(client, "roles-admin", fog_role=Member.FogRole.ADMIN)


@pytest.fixture
def erin() -> Member:
    return MemberFactory(full_legal_name="Erin Example")


def _edit_url(member: Member) -> str:
    return reverse("hub_admin_member_edit", args=[member.pk])


def _messages(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def _flags(guild: Guild, member: Member) -> set[bool]:
    return set(guild.staff_memberships.filter(member=member).values_list("can_review_classes", flat=True))


def describe_the_permissions_tab():
    def it_lists_each_guild_with_a_switch_on_the_staff_tab_endpoint(client: Client, admin: Member, erin: Member):
        guild = GuildFactory(name="Printmaking Guild")
        GuildStaffMembershipFactory(guild=guild, member=erin, role=Role.ORIENTER, can_review_classes=True)
        html = client.get(_edit_url(erin)).content.decode()
        assert 'id="guild-roles"' in html
        switch_url = reverse("hub_guild_staff_class_reviewer", args=[guild.pk, erin.pk])
        row = re.search(r'data-class-reviewer-guild="%d".*?</label>' % guild.pk, html, re.S)
        assert row is not None
        assert f'hx-post="{switch_url}"' in row.group(0)
        assert "checked" in row.group(0)
        assert reverse("hub_admin_member_guild_staff_remove", args=[erin.pk, guild.staff_memberships.get().pk]) in html

    def it_shows_a_lead_as_read_only_text_with_remove_as_lead(client: Client, admin: Member, erin: Member):
        guild = GuildFactory(name="Ceramics Guild", guild_lead=erin)
        html = client.get(_edit_url(erin)).content.decode()
        assert f'data-class-reviewer-guild="{guild.pk}"' not in html
        assert "Class Reviewer: always, as guild lead" in html
        assert reverse("hub_admin_member_guild_lead_clear", args=[erin.pk, guild.pk]) in html

    def it_says_so_when_they_hold_no_guild_role(client: Client, admin: Member, erin: Member):
        assert b"pl-guild-roles__empty" in client.get(_edit_url(erin)).content

    def it_keeps_the_capabilities_save_last(client: Client, admin: Member, erin: Member):
        html = client.get(_edit_url(erin)).content.decode()
        assert html.index('id="guild-roles"') < html.index('value="capabilities"')


def describe_the_switch_from_the_member_page():
    def it_is_the_same_state_the_guild_staff_tab_reads(client: Client, admin: Member, erin: Member):
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=erin, role=Role.TREASURER)
        response = client.post(reverse("hub_guild_staff_class_reviewer", args=[guild.pk, erin.pk]), {"on": "on"})
        assert response.status_code == 204
        staff_tab = client.get(reverse("hub_guild_edit", args=[guild.pk]) + "?tab=staff")
        assert erin.pk in staff_tab.context["class_reviewer_ids"]
        client.post(reverse("hub_guild_staff_class_reviewer", args=[guild.pk, erin.pk]))
        assert _flags(guild, erin) == {False}  # off keeps the row
        assert guild.staff_memberships.filter(member=erin).exists()


def describe_add_to_a_guild():
    def _post(client: Client, member: Member, **data):
        return client.post(reverse("hub_admin_member_guild_staff_add", args=[member.pk]), data)

    def it_adds_a_class_reviewer_in_one_step(client: Client, admin: Member, erin: Member):
        guild = GuildFactory(name="Printmaking Guild")
        response = _post(client, erin, guild=guild.pk, class_reviewer="on")
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=permissions#guild-roles")
        row = guild.staff_memberships.get(member=erin)
        assert (row.role, row.custom_title, row.can_review_classes) == ("", "Class Reviewer", True)
        assert _messages(response) == ["Erin Example is now Class Reviewer of Printmaking Guild."]

    def it_switches_on_someone_already_on_the_staff(client: Client, admin: Member, erin: Member):
        guild = GuildFactory(name="Wood Guild")
        GuildStaffMembershipFactory(guild=guild, member=erin, role=Role.ORIENTER)
        response = _post(client, erin, guild=guild.pk, class_reviewer="on")
        assert guild.staff_memberships.filter(member=erin).count() == 1
        assert _flags(guild, erin) == {True}
        assert _messages(response) == ["Erin Example now reviews Wood Guild classes."]

    def it_adds_a_role_with_class_reviewer(client: Client, admin: Member, erin: Member):
        guild = GuildFactory(name="Wood Guild")
        response = _post(client, erin, guild=guild.pk, role=Role.CO_LEAD, class_reviewer="on")
        row = guild.staff_memberships.get(member=erin)
        assert (row.role, row.can_review_classes) == (Role.CO_LEAD, True)
        assert _messages(response) == ["Erin Example is now Co-Lead of Wood Guild. Class Reviewer is on."]

    def it_flashes_why_it_refused(client: Client, admin: Member, erin: Member):
        guild = GuildFactory()
        response = _post(client, erin, guild=guild.pk)
        assert not guild.staff_memberships.exists()
        assert _messages(response) == ["Pick a role or type a custom title."]

    def it_offers_only_active_guilds_they_do_not_lead(client: Client, admin: Member, erin: Member):
        hidden = GuildFactory(name="Hidden Guild", is_active=False)
        led = GuildFactory(name="Led Guild", guild_lead=erin)
        for guild in (hidden, led):
            _post(client, erin, guild=guild.pk, role=Role.TREASURER)
            assert not guild.staff_memberships.exists()

    def it_sends_nothing(client: Client, admin: Member, erin: Member):
        notifications = Notification.objects.count()
        _post(client, erin, guild=GuildFactory().pk, class_reviewer="on")
        assert mail.outbox == []
        assert Notification.objects.count() == notifications

    def it_refuses_a_non_admin(client: Client, erin: Member):
        _login(client, "plain-member")
        guild = GuildFactory()
        assert _post(client, erin, guild=guild.pk, class_reviewer="on").status_code == 403
        assert not guild.staff_memberships.exists()


def describe_change_and_remove_a_title():
    def it_renames_a_title_and_keeps_class_reviewer(client: Client, admin: Member, erin: Member):
        row = GuildStaffMembershipFactory(member=erin, role=Role.TREASURER, can_review_classes=True)
        url = reverse("hub_admin_member_guild_staff_edit", args=[erin.pk, row.pk])
        response = client.post(url, {f"staff-{row.pk}-role": "", f"staff-{row.pk}-custom_title": " Glaze Tech "})
        row.refresh_from_db()
        assert (row.role, row.custom_title, row.can_review_classes) == ("", "Glaze Tech", True)
        assert _messages(response) == [f"Erin Example is now Glaze Tech of {row.guild.name} (was Treasurer)."]

    def it_flashes_a_refused_rename(client: Client, admin: Member, erin: Member):
        row = GuildStaffMembershipFactory(member=erin, role=Role.TREASURER)
        url = reverse("hub_admin_member_guild_staff_edit", args=[erin.pk, row.pk])
        response = client.post(url, {f"staff-{row.pk}-role": Role.ORIENTER, f"staff-{row.pk}-custom_title": "X"})
        row.refresh_from_db()
        assert row.role == Role.TREASURER
        assert _messages(response) == ["Pick a preset role or type a custom title — not both."]

    def it_removes_a_title_like_the_staff_tab(client: Client, admin: Member, erin: Member):
        row = GuildStaffMembershipFactory(member=erin, role=Role.TREASURER)
        response = client.post(reverse("hub_admin_member_guild_staff_remove", args=[erin.pk, row.pk]))
        assert not GuildStaffMembership.objects.filter(pk=row.pk).exists()
        assert _messages(response) == [f"Erin Example is no longer Treasurer of {row.guild.name}."]

    def it_only_touches_this_members_rows(client: Client, admin: Member, erin: Member):
        other = GuildStaffMembershipFactory(role=Role.TREASURER)
        assert client.post(reverse("hub_admin_member_guild_staff_remove", args=[erin.pk, other.pk])).status_code == 404
        edit = reverse("hub_admin_member_guild_staff_edit", args=[erin.pk, other.pk])
        assert client.post(edit, {}).status_code == 404


def describe_guild_lead():
    def it_makes_them_lead_and_names_who_it_replaced(client: Client, admin: Member, erin: Member):
        old = MemberFactory(full_legal_name="Old Lead")
        guild = GuildFactory(name="Glass Guild", guild_lead=old)
        response = client.post(reverse("hub_admin_member_guild_lead_set", args=[erin.pk]), {"guild": guild.pk})
        guild.refresh_from_db()
        assert guild.guild_lead == erin
        assert _messages(response)[0] == "Erin Example is now the lead of Glass Guild. Old Lead is no longer its lead."

    def it_names_the_current_lead_in_the_picker(client: Client, admin: Member, erin: Member):
        GuildFactory(name="Glass Guild", guild_lead=MemberFactory(full_legal_name="Old Lead"))
        GuildFactory(name="Bare Guild")
        html = client.get(_edit_url(erin)).content.decode()
        assert "Glass Guild (lead now: Old Lead)" in html
        assert "Bare Guild (no lead)" in html

    def it_flashes_assign_warnings_and_form_errors(client: Client, admin: Member, erin: Member):
        guild = GuildFactory(name="Bare Guild")
        response = client.post(reverse("hub_admin_member_guild_lead_set", args=[erin.pk]), {"guild": guild.pk})
        assert _messages(response)[0] == "Erin Example is now the lead of Bare Guild."
        assert any("no linked user account" in m for m in _messages(response))
        refused = client.post(reverse("hub_admin_member_guild_lead_set", args=[erin.pk]), {"guild": ""})
        assert _messages(refused)[-1] == "This field is required."

    def it_removes_them_as_lead_only_where_they_lead(client: Client, admin: Member, erin: Member):
        guild = GuildFactory(name="Glass Guild", guild_lead=erin)
        other = GuildFactory(guild_lead=MemberFactory())
        assert client.post(reverse("hub_admin_member_guild_lead_clear", args=[erin.pk, other.pk])).status_code == 404
        response = client.post(reverse("hub_admin_member_guild_lead_clear", args=[erin.pk, guild.pk]))
        guild.refresh_from_db()
        assert guild.guild_lead is None
        assert _messages(response) == ["Erin Example is no longer the lead of Glass Guild."]


def describe_the_staff_tab_add_form():
    def _add(client: Client, guild: Guild, member: Member, **data):
        return client.post(reverse("hub_guild_staff_add", args=[guild.pk]), {"member": member.pk, **data})

    def it_shows_the_switch_to_the_lead_and_turns_it_on(client: Client, erin: Member):
        lead = _login(client, "staff-lead")
        guild = GuildFactory(guild_lead=lead)
        page = client.get(reverse("hub_guild_edit", args=[guild.pk]) + "?tab=staff").content.decode()
        assert "data-staff-add-reviewer" in page
        _add(client, guild, erin, role=Role.ORIENTER, class_reviewer="on")
        assert _flags(guild, erin) == {True}

    def it_adds_a_reviewer_with_no_title(client: Client, erin: Member):
        guild = GuildFactory(guild_lead=_login(client, "staff-lead"))
        response = _add(client, guild, erin, class_reviewer="on")
        assert guild.staff_memberships.get(member=erin).custom_title == "Class Reviewer"
        assert _messages(response) == [f"Erin Example is now Class Reviewer of {guild.name}."]

    def it_hides_and_ignores_the_switch_for_other_staff(client: Client, erin: Member):
        staffer = _login(client, "staff-orienter")
        guild = GuildFactory()
        GuildStaffMembershipFactory(guild=guild, member=staffer, role=Role.ORIENTER)
        page = client.get(reverse("hub_guild_edit", args=[guild.pk]) + "?tab=staff").content.decode()
        assert "data-staff-add-reviewer" not in page
        _add(client, guild, erin, role=Role.TREASURER, class_reviewer="on")
        assert _flags(guild, erin) == {False}

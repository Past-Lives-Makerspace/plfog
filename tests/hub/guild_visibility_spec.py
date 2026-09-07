"""BDD specs for the admin-only guild visibility (show/hide) toggle."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member  # auto-linked via signal
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_guild_visibility_save():
    def it_lets_an_admin_hide_and_show_a_guild(client: Client):
        _user("vis_admin", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(is_active=True)
        client.login(username="vis_admin", password="pass")
        url = reverse("hub_guild_visibility_save", args=[guild.pk])

        # Hide — an unchecked toggle is simply absent from the POST.
        resp = client.post(url, data={})
        assert resp.status_code == 302
        assert resp["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=basic"
        guild.refresh_from_db()
        assert guild.is_active is False

        # Show again.
        resp = client.post(url, data={"is_active": "on"})
        assert resp.status_code == 302
        guild.refresh_from_db()
        assert guild.is_active is True

    def it_hides_the_guild_from_the_directory_when_off(client: Client):
        _user("vis_dir", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(is_active=True)
        assert guild in Guild.objects.directory()
        client.login(username="vis_dir", password="pass")

        client.post(reverse("hub_guild_visibility_save", args=[guild.pk]), data={})
        guild.refresh_from_db()
        assert guild not in Guild.objects.directory()

    def it_shows_a_success_message(client: Client):
        _user("vis_msg", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(is_active=True)
        client.login(username="vis_msg", password="pass")
        url = reverse("hub_guild_visibility_save", args=[guild.pk])
        hide = client.post(url, data={}, follow=True)
        assert "This guild is now hidden from members." in [m.message for m in hide.context["messages"]]
        show = client.post(url, data={"is_active": "on"}, follow=True)
        assert "This guild is now visible to members." in [m.message for m in show.context["messages"]]

    def it_forbids_a_guild_lead(client: Client):
        lead_user = _user("vis_lead")
        guild = GuildFactory(guild_lead=lead_user.member, is_active=True)
        client.login(username="vis_lead", password="pass")
        resp = client.post(reverse("hub_guild_visibility_save", args=[guild.pk]), data={})
        assert resp.status_code == 403
        guild.refresh_from_db()
        assert guild.is_active is True  # unchanged

    def it_requires_login(client: Client):
        guild = GuildFactory()
        resp = client.post(reverse("hub_guild_visibility_save", args=[guild.pk]), data={})
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]


def describe_guild_visibility_control_on_the_edit_page():
    def it_shows_the_toggle_to_an_admin(client: Client):
        _user("vis_show", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(is_active=True)
        client.login(username="vis_show", password="pass")
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200
        assert b"Visible to members" in resp.content

    def it_hides_the_toggle_from_a_guild_lead(client: Client):
        lead_user = _user("vis_hide")
        guild = GuildFactory(guild_lead=lead_user.member, is_active=True)
        client.login(username="vis_hide", password="pass")
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200  # a lead can still edit the guild
        assert b"Visible to members" not in resp.content  # but never sees the visibility control

    def it_keeps_the_edit_page_reachable_for_an_inactive_guild(client: Client):
        _user("vis_inactive", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(is_active=False)
        client.login(username="vis_inactive", password="pass")
        resp = client.get(reverse("hub_guild_edit", args=[guild.pk]))
        assert resp.status_code == 200  # so an admin can flip it back on

"""BDD specs for the guild Member Suggestions toggle save (Announcements tab)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _member(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


def describe_guild_announcement_settings_save():
    def it_lets_the_lead_toggle_suggestions_off_and_on(client: Client):
        user = _member("as_lead")
        guild = GuildFactory(guild_lead=user.member)  # default: suggestions on
        client.login(username="as_lead", password="pass")
        url = reverse("hub_guild_announcement_settings_save", args=[guild.pk])
        # Turn OFF — an unchecked toggle is simply absent from the POST.
        resp = client.post(url, data={})
        assert resp.status_code == 302
        assert resp["Location"] == f"{reverse('hub_guild_edit', args=[guild.pk])}?tab=announcements"
        guild.refresh_from_db()
        assert guild.allow_member_announcement_suggestions is False
        # Turn ON.
        resp = client.post(url, data={"allow_member_announcement_suggestions": "on"})
        assert resp.status_code == 302
        guild.refresh_from_db()
        assert guild.allow_member_announcement_suggestions is True

    def it_shows_a_success_message(client: Client):
        user = _member("as_msg")
        guild = GuildFactory(guild_lead=user.member)
        client.login(username="as_msg", password="pass")
        resp = client.post(reverse("hub_guild_announcement_settings_save", args=[guild.pk]), data={}, follow=True)
        messages = [m.message for m in resp.context["messages"]]
        assert "Announcement settings saved." in messages

    def it_forbids_a_non_editor(client: Client):
        _member("as_reg")
        guild = GuildFactory()
        client.login(username="as_reg", password="pass")
        resp = client.post(reverse("hub_guild_announcement_settings_save", args=[guild.pk]), data={})
        assert resp.status_code == 403

    def it_requires_login(client: Client):
        guild = GuildFactory()
        resp = client.post(reverse("hub_guild_announcement_settings_save", args=[guild.pk]), data={})
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

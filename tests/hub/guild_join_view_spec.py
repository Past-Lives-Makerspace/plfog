"""BDD specs for the hero Join / Leave guild views (HTMX front door).

Join is the one deliberate web join path: it records the subscription and, only when the
member left the modal's welcome box checked, sends the guild's welcome email once. Leave
removes the subscription. Both return the flipped CTA partial plus a toast.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.models import Notification
from membership.models import GuildMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _linked_user(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pw", email=f"{username}@example.com")


def _toast(response: object) -> dict:
    return json.loads(response["HX-Trigger"])["showToast"]


def describe_guild_join():
    def it_joins_and_sends_the_welcome_when_the_box_is_checked(client: Client):
        user = _linked_user("joiner")
        guild = GuildFactory(name="Wood Guild")
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="joiner", password="pw")

        response = client.post(reverse("hub_guild_join", args=[guild.pk]), {"send_welcome": "on"})

        assert response.status_code == 200
        assert GuildMembership.objects.filter(guild=guild, member=user.member).exists()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.member.primary_email]
        assert b"Member" in response.content
        assert _toast(response)["type"] == "success"

    def it_joins_without_a_welcome_when_the_box_is_unchecked(client: Client):
        user = _linked_user("joiner2")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="joiner2", password="pw")

        client.post(reverse("hub_guild_join", args=[guild.pk]), {})

        assert GuildMembership.objects.filter(guild=guild, member=user.member).exists()
        assert mail.outbox == []

    def it_sends_no_welcome_when_the_guild_disabled_it(client: Client):
        _linked_user("joiner3")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, welcome_email_enabled=False)
        client.login(username="joiner3", password="pw")

        client.post(reverse("hub_guild_join", args=[guild.pk]), {"send_welcome": "on"})

        assert mail.outbox == []

    def it_fires_the_lead_new_follower_notice_on_join(client: Client):
        lead = _linked_user("lead_notice").member
        guild = GuildFactory(guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild)
        _linked_user("joiner4")
        client.login(username="joiner4", password="pw")

        client.post(reverse("hub_guild_join", args=[guild.pk]), {"send_welcome": "on"})

        assert Notification.objects.filter(user=lead.user, trigger="guild_joined").exists()

    def it_does_not_re_send_on_a_repeat_join(client: Client):
        _linked_user("joiner5")
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="joiner5", password="pw")

        client.post(reverse("hub_guild_join", args=[guild.pk]), {"send_welcome": "on"})
        client.post(reverse("hub_guild_join", args=[guild.pk]), {"send_welcome": "on"})

        assert len(mail.outbox) == 1

    def it_guards_an_account_with_no_member(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="nomem", password="pw")
        Member.objects.filter(user=user).delete()
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="nomem", password="pw")

        response = client.post(reverse("hub_guild_join", args=[guild.pk]), {"send_welcome": "on"})

        assert response.status_code == 204
        assert _toast(response)["type"] == "error"
        assert not GuildMembership.objects.filter(guild=guild).exists()
        assert mail.outbox == []

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.post(reverse("hub_guild_join", args=[guild.pk]))
        assert response.status_code == 302


def describe_guild_leave():
    def it_leaves_and_returns_the_join_state(client: Client):
        user = _linked_user("leaver")
        guild = GuildFactory()
        GuildMembership.objects.create(guild=guild, member=user.member)
        client.login(username="leaver", password="pw")

        response = client.post(reverse("hub_guild_leave", args=[guild.pk]))

        assert response.status_code == 200
        assert not GuildMembership.objects.filter(guild=guild, member=user.member).exists()
        assert b"Join This Guild" in response.content
        assert _toast(response)["type"] == "info"

    def it_guards_an_account_with_no_member(client: Client):
        MembershipPlanFactory()
        user = User.objects.create_user(username="nomem2", password="pw")
        Member.objects.filter(user=user).delete()
        guild = GuildFactory()
        client.login(username="nomem2", password="pw")

        response = client.post(reverse("hub_guild_leave", args=[guild.pk]))

        assert response.status_code == 204
        assert _toast(response)["type"] == "error"

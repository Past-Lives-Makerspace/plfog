"""BDD specs for the hero Join / Leave guild views (HTMX front door).

Join is the one deliberate web join path: it records the subscription and, only when the
member left the modal's welcome box checked, sends the guild's welcome email once. Leave
removes the subscription. Both return the flipped CTA partial plus a toast.
"""

from __future__ import annotations

import json
from unittest import mock

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

_WEBHOOK = "https://discord.com/api/webhooks/1/token"


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


def describe_guild_join_discord_announce():
    """The opt-in 'Announce on the guild's Discord channel?' box on the web join.

    Reuses the ``/join-guild`` welcome plumbing (``guild_webhook`` + ``post_embed``), so the
    same per-guild gate (posting enabled AND a non-blank webhook) decides whether anything
    posts. ``post_embed`` is spied; ``guild_webhook`` stays real so the gate runs for real.
    """

    @pytest.fixture
    def post_spy(monkeypatch):
        from core.events import discord as discord_module

        spy = mock.MagicMock(name="post_embed", return_value=True)
        monkeypatch.setattr(discord_module, "post_embed", spy)
        return spy

    def it_posts_one_channel_message_when_the_box_is_checked(client: Client, post_spy):
        user = _linked_user("dj_on")
        guild = GuildFactory(name="Forge", discord_webhook_url=_WEBHOOK)
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="dj_on", password="pw")

        response = client.post(reverse("hub_guild_join", args=[guild.pk]), {"announce_discord": "on"})

        assert response.status_code == 200
        post_spy.assert_called_once()
        hook, message = post_spy.call_args.args
        assert hook == _WEBHOOK
        assert message.title == f"{user.member.display_name} just joined Forge!"

    def it_posts_nothing_when_the_box_is_unchecked(client: Client, post_spy):
        _linked_user("dj_off")
        guild = GuildFactory(discord_webhook_url=_WEBHOOK)
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="dj_off", password="pw")

        client.post(reverse("hub_guild_join", args=[guild.pk]), {})

        post_spy.assert_not_called()

    def it_posts_nothing_when_the_guild_has_no_channel(client: Client, post_spy):
        _linked_user("dj_nochan")
        guild = GuildFactory(discord_webhook_url="")  # no channel wired for posting
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="dj_nochan", password="pw")

        client.post(reverse("hub_guild_join", args=[guild.pk]), {"announce_discord": "on"})

        post_spy.assert_not_called()

    def it_does_not_announce_on_a_repeat_join(client: Client, post_spy):
        # subscribe_to_guild returns False on a repeat join, so the announce never fires again.
        _linked_user("dj_repeat")
        guild = GuildFactory(discord_webhook_url=_WEBHOOK)
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="dj_repeat", password="pw")

        client.post(reverse("hub_guild_join", args=[guild.pk]), {"announce_discord": "on"})
        client.post(reverse("hub_guild_join", args=[guild.pk]), {"announce_discord": "on"})

        post_spy.assert_called_once()

    def it_still_joins_when_the_discord_post_raises(client: Client, monkeypatch):
        from core.events import discord as discord_module

        boom = mock.MagicMock(name="post_embed", side_effect=RuntimeError("discord down"))
        monkeypatch.setattr(discord_module, "post_embed", boom)
        user = _linked_user("dj_boom")
        guild = GuildFactory(discord_webhook_url=_WEBHOOK)
        GuildOrientationSettingsFactory(guild=guild)
        client.login(username="dj_boom", password="pw")

        response = client.post(reverse("hub_guild_join", args=[guild.pk]), {"announce_discord": "on"})

        assert response.status_code == 200
        assert GuildMembership.objects.filter(guild=guild, member=user.member).exists()


def describe_guild_join_modal_discord_toggle():
    """The announce toggle only renders for guilds that actually post to a channel."""

    def it_shows_the_toggle_when_the_guild_posts_to_a_channel(client: Client):
        _linked_user("tg_on")
        guild = GuildFactory(discord_webhook_url=_WEBHOOK)
        client.login(username="tg_on", password="pw")

        response = client.get(f"/guilds/{guild.slug}/")

        assert b"announce_discord" in response.content

    def it_hides_the_toggle_when_the_guild_has_no_channel(client: Client):
        _linked_user("tg_off")
        guild = GuildFactory(discord_webhook_url="")
        client.login(username="tg_off", password="pw")

        response = client.get(f"/guilds/{guild.slug}/")

        assert b"announce_discord" not in response.content

    def it_hides_the_toggle_when_posting_is_disabled(client: Client):
        _linked_user("tg_disabled")
        guild = GuildFactory(discord_webhook_url=_WEBHOOK, discord_post_enabled=False)
        client.login(username="tg_disabled", password="pw")

        response = client.get(f"/guilds/{guild.slug}/")

        assert b"announce_discord" not in response.content

"""BDD specs for the anon-allowed low-friction Discord link (/discord/link/).

The OAuth + reactions HTTP is mocked with ``respx``; the ``state`` handshake is driven
through the test client's session. Every outcome renders a full-page landing (no dead
ends), never a redirect to a 500.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from factory.django import mute_signals

from tests.membership.factories import DiscordGuildEmojiFactory, GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
_IDENTITY_URL = "https://discord.com/api/v10/users/@me"
_LINK_STATE_KEY = "discord_link_state"


def _configure(settings):
    from core.models import SiteConfiguration

    settings.DISCORD_CLIENT_ID = "cid"
    settings.DISCORD_CLIENT_SECRET = "secret"
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.discord_role_message_channel_id = "chan"
    config.discord_role_message_id = "msg"
    config.save()


def _verified_member(email: str, *, discord_user_id: str = ""):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"u_{email}", email=email)
    member = MemberFactory(discord_user_id=discord_user_id)
    member.user = user
    member.save(update_fields=["user", "discord_user_id"])
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return member


def _mock_oauth(email: str, *, verified: bool = True, discord_user_id: str = "42"):
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.get(_IDENTITY_URL).mock(
        return_value=httpx.Response(
            200, json={"id": discord_user_id, "username": "jo", "email": email, "verified": verified}
        )
    )


def _client_with_state(state="s"):
    client = Client()
    session = client.session
    session[_LINK_STATE_KEY] = state
    session.save()
    return client


def describe_discord_link_start():
    def it_redirects_to_discord_and_stores_the_link_state(settings):
        _configure(settings)
        client = Client()
        response = client.get(reverse("hub_discord_link_start"))
        assert response.status_code == 302
        assert response.url.startswith("https://discord.com/oauth2/authorize?")
        assert client.session[_LINK_STATE_KEY]

    def it_lands_on_a_friendly_retry_when_unconfigured(settings):
        settings.DISCORD_CLIENT_ID = ""
        settings.DISCORD_CLIENT_SECRET = ""
        response = Client().get(reverse("hub_discord_link_start"))
        assert response.status_code == 200
        assert b"Try again" in response.content


def describe_discord_link_callback():
    @respx.mock
    def it_links_and_lands_with_guilds_on_a_verified_match(settings):
        _configure(settings)
        member = _verified_member("jo@example.com")
        guild = GuildFactory(name="Glassworks")
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        _mock_oauth("jo@example.com", discord_user_id="42")
        respx.get(url__regex=r".+/reactions/.+").mock(return_value=httpx.Response(200, json=[{"id": "42"}]))
        respx.put(url__regex=r".+/roles/.+").mock(return_value=httpx.Response(204))
        client = _client_with_state()
        response = client.get(reverse("hub_discord_link_callback"), {"code": "c", "state": "s"})
        assert response.status_code == 200
        assert b"Discord connected" in response.content
        assert b"Glassworks" in response.content
        member.refresh_from_db()
        assert member.discord_user_id == "42"

    @respx.mock
    def it_lands_on_needs_login_for_an_unverified_email(settings):
        _configure(settings)
        _verified_member("jo@example.com")
        _mock_oauth("jo@example.com", verified=False)
        response = _client_with_state().get(reverse("hub_discord_link_callback"), {"code": "c", "state": "s"})
        assert response.status_code == 200
        assert b"Almost there" in response.content

    @respx.mock
    def it_lands_on_already_linked_elsewhere(settings):
        _configure(settings)
        MemberFactory(discord_user_id="42")
        _verified_member("jo@example.com")
        _mock_oauth("jo@example.com", discord_user_id="42")
        response = _client_with_state().get(reverse("hub_discord_link_callback"), {"code": "c", "state": "s"})
        assert response.status_code == 200
        assert b"already connected to a different" in response.content

    @respx.mock
    def it_lands_on_account_has_other_discord(settings):
        _configure(settings)
        member = _verified_member("jo@example.com", discord_user_id="99")
        # Log the member in so link_and_import receives the member and hits guard 2.
        client = Client()
        client.force_login(member.user)
        session = client.session
        session[_LINK_STATE_KEY] = "s"
        session.save()
        _mock_oauth("jo@example.com", discord_user_id="42")
        response = client.get(reverse("hub_discord_link_callback"), {"code": "c", "state": "s"})
        assert response.status_code == 200
        assert b"already has a different Discord" in response.content

    @respx.mock
    def it_lands_on_retry_on_an_oauth_failure(settings):
        _configure(settings)
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400))
        response = _client_with_state().get(reverse("hub_discord_link_callback"), {"code": "c", "state": "s"})
        assert response.status_code == 200
        assert b"Something went wrong" in response.content

    def it_lands_on_cancelled_when_the_user_denies(settings):
        _configure(settings)
        response = _client_with_state().get(reverse("hub_discord_link_callback"), {"error": "access_denied"})
        assert response.status_code == 200
        assert b"Connection cancelled" in response.content

    def it_lands_on_retry_on_a_bad_state(settings):
        _configure(settings)
        response = _client_with_state("expected").get(
            reverse("hub_discord_link_callback"), {"code": "c", "state": "forged"}
        )
        assert response.status_code == 200
        assert b"Something went wrong" in response.content

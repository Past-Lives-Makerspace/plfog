"""Discord account-linking views — connect (state + redirect), callback, disconnect.

The callback's Discord HTTP is mocked with ``respx``; the OAuth ``state`` handshake is
driven through the test client's session.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from factory.django import mute_signals

from tests.membership.factories import MembershipPlanFactory

pytestmark = pytest.mark.django_db

_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
_IDENTITY_URL = "https://discord.com/api/v10/users/@me"
_STATE_KEY = "discord_oauth_state"


def _linked_user(username: str = "dl_member"):
    """A logged-in-able User whose Member was auto-provisioned (needs a plan first)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    return user


def _userless_user(username: str = "dl_nomember"):
    """A User with no Member (signals muted so none is auto-provisioned)."""
    with mute_signals(post_save):
        return User.objects.create_user(username=username, password="pass")


def _notifications_url() -> str:
    return f"{reverse('hub_user_settings')}?tab=notifications"


def describe_discord_connect():
    def it_redirects_to_discord_and_stores_state(settings):
        settings.DISCORD_CLIENT_ID = "cid"
        settings.DISCORD_CLIENT_SECRET = "secret"
        client = Client()
        client.force_login(_linked_user())
        response = client.get(reverse("hub_discord_connect"))
        assert response.status_code == 302
        assert response.url.startswith("https://discord.com/oauth2/authorize?")
        assert client.session[_STATE_KEY]

    def it_errors_when_discord_is_not_configured(settings):
        settings.DISCORD_CLIENT_ID = ""
        settings.DISCORD_CLIENT_SECRET = ""
        client = Client()
        client.force_login(_linked_user())
        response = client.get(reverse("hub_discord_connect"))
        assert response.status_code == 302
        assert response.url == _notifications_url()
        assert _STATE_KEY not in client.session

    def it_errors_for_a_user_without_a_member():
        client = Client()
        client.force_login(_userless_user())
        response = client.get(reverse("hub_discord_connect"))
        assert response.status_code == 302
        assert response.url == reverse("hub_user_settings")


def describe_discord_callback():
    @respx.mock
    def it_links_the_member_on_a_valid_callback(settings):
        settings.DISCORD_CLIENT_ID = "cid"
        settings.DISCORD_CLIENT_SECRET = "secret"
        user = _linked_user()
        client = Client()
        client.force_login(user)
        session = client.session
        session[_STATE_KEY] = "state-token"
        session.save()
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "424242"}))
        response = client.get(reverse("hub_discord_callback"), {"code": "the-code", "state": "state-token"})
        assert response.status_code == 302
        assert response.url == _notifications_url()
        user.member.refresh_from_db()
        assert user.member.discord_user_id == "424242"

    def it_rejects_a_mismatched_state():
        user = _linked_user()
        client = Client()
        client.force_login(user)
        session = client.session
        session[_STATE_KEY] = "expected"
        session.save()
        response = client.get(reverse("hub_discord_callback"), {"code": "c", "state": "forged"})
        assert response.status_code == 302
        assert response.url == _notifications_url()
        user.member.refresh_from_db()
        assert user.member.discord_is_linked is False

    def it_handles_a_denied_authorization():
        user = _linked_user()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("hub_discord_callback"), {"error": "access_denied"})
        assert response.status_code == 302
        assert response.url == _notifications_url()
        user.member.refresh_from_db()
        assert user.member.discord_is_linked is False

    def it_rejects_a_callback_with_no_code():
        user = _linked_user()
        client = Client()
        client.force_login(user)
        session = client.session
        session[_STATE_KEY] = "state-token"
        session.save()
        response = client.get(reverse("hub_discord_callback"), {"state": "state-token"})
        assert response.status_code == 302
        assert response.url == _notifications_url()
        user.member.refresh_from_db()
        assert user.member.discord_is_linked is False


def _configure_sync(settings):
    from core.models import SiteConfiguration

    settings.DISCORD_CLIENT_ID = "cid"
    settings.DISCORD_CLIENT_SECRET = "secret"
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.discord_role_message_channel_id = "chan"
    config.discord_role_message_id = "msg"
    config.save()


def _logged_in_with_state(user, state="state-token"):
    client = Client()
    client.force_login(user)
    session = client.session
    session[_STATE_KEY] = state
    session.save()
    return client


def describe_discord_callback_outcomes():
    @respx.mock
    def it_imports_guilds_and_links_on_a_valid_callback(settings):
        from tests.membership.factories import DiscordGuildEmojiFactory, GuildFactory

        _configure_sync(settings)
        user = _linked_user("dl_import")
        guild = GuildFactory()
        DiscordGuildEmojiFactory(emoji="🔥", guild=guild)
        client = _logged_in_with_state(user)
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "424242"}))
        respx.get(url__regex=r".+/reactions/.+").mock(return_value=httpx.Response(200, json=[{"id": "424242"}]))
        response = client.get(reverse("hub_discord_callback"), {"code": "c", "state": "state-token"})
        assert response.status_code == 302
        from membership.models import GuildMembership

        assert GuildMembership.objects.filter(guild=guild, member=user.member).exists()

    @respx.mock
    def it_refuses_when_the_discord_is_linked_elsewhere(settings):
        from tests.membership.factories import MemberFactory

        _configure_sync(settings)
        MemberFactory(discord_user_id="424242")  # already owns it
        user = _linked_user("dl_elsewhere")
        client = _logged_in_with_state(user)
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "424242"}))
        response = client.get(reverse("hub_discord_callback"), {"code": "c", "state": "state-token"})
        assert response.status_code == 302
        user.member.refresh_from_db()
        assert user.member.discord_is_linked is False

    @respx.mock
    def it_refuses_to_swap_a_different_connected_discord(settings):
        _configure_sync(settings)
        user = _linked_user("dl_other")
        user.member.link_discord("999")
        client = _logged_in_with_state(user)
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "424242"}))
        response = client.get(reverse("hub_discord_callback"), {"code": "c", "state": "state-token"})
        assert response.status_code == 302
        user.member.refresh_from_db()
        assert user.member.discord_user_id == "999"  # unchanged

    @respx.mock
    def it_shows_an_error_on_an_oauth_failure(settings):
        _configure_sync(settings)
        user = _linked_user("dl_fail")
        client = _logged_in_with_state(user)
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400))
        response = client.get(reverse("hub_discord_callback"), {"code": "c", "state": "state-token"})
        assert response.status_code == 302
        user.member.refresh_from_db()
        assert user.member.discord_is_linked is False


def describe_discord_disconnect():
    def it_clears_the_link_on_post():
        user = _linked_user()
        user.member.link_discord("424242")
        client = Client()
        client.force_login(user)
        response = client.post(reverse("hub_discord_disconnect"))
        assert response.status_code == 302
        assert response.url == _notifications_url()
        user.member.refresh_from_db()
        assert user.member.discord_is_linked is False

    def it_rejects_a_get():
        user = _linked_user()
        client = Client()
        client.force_login(user)
        response = client.get(reverse("hub_discord_disconnect"))
        assert response.status_code == 405

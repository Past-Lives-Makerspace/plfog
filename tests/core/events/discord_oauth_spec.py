"""Discord OAuth2 account-linking service — authorize URL, token + identity, linking.

All Discord HTTP is mocked with ``respx``. Covers configuration detection, the
authorize-URL shape, the code→token and token→identity exchanges (success + every
failure raising :class:`DiscordOAuthError`), and the end-to-end link onto a member.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from core.events import discord_oauth
from core.events.discord_oauth import DiscordOAuthError

_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
_IDENTITY_URL = "https://discord.com/api/v10/users/@me"
_REDIRECT = "http://pastlives.test:8000/settings/discord/callback/"


def describe_is_configured():
    def it_is_true_when_id_and_secret_are_set(settings):
        settings.DISCORD_CLIENT_ID = "cid"
        settings.DISCORD_CLIENT_SECRET = "secret"
        assert discord_oauth.is_configured() is True

    def it_is_false_when_the_secret_is_blank(settings):
        settings.DISCORD_CLIENT_ID = "cid"
        settings.DISCORD_CLIENT_SECRET = ""
        assert discord_oauth.is_configured() is False

    def it_is_false_when_the_id_is_blank(settings):
        settings.DISCORD_CLIENT_ID = ""
        settings.DISCORD_CLIENT_SECRET = "secret"
        assert discord_oauth.is_configured() is False


def describe_authorize_url():
    def it_builds_the_identify_authorize_url(settings):
        settings.DISCORD_CLIENT_ID = "cid"
        url = discord_oauth.authorize_url(_REDIRECT, "state-token")
        assert url.startswith("https://discord.com/oauth2/authorize?")
        params = parse_qs(urlparse(url).query)
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["cid"]
        assert params["redirect_uri"] == [_REDIRECT]
        assert params["scope"] == ["identify"]
        assert params["state"] == ["state-token"]


def describe_exchange_code():
    @respx.mock
    def it_returns_the_access_token_on_success(settings):
        settings.DISCORD_CLIENT_ID = "cid"
        settings.DISCORD_CLIENT_SECRET = "secret"
        route = respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc123"}))
        assert discord_oauth.exchange_code("code", _REDIRECT) == "abc123"
        sent = route.calls.last.request.read().decode()
        assert "grant_type=authorization_code" in sent
        assert "code=code" in sent

    @respx.mock
    def it_raises_on_a_non_success_status():
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400, text="bad request"))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.exchange_code("code", _REDIRECT)

    @respx.mock
    def it_raises_on_a_network_error():
        respx.post(_TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.exchange_code("code", _REDIRECT)

    @respx.mock
    def it_raises_when_no_access_token_is_returned():
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.exchange_code("code", _REDIRECT)


def describe_fetch_user_id():
    @respx.mock
    def it_returns_the_user_id_on_success():
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "777"}))
        assert discord_oauth.fetch_user_id("token") == "777"

    @respx.mock
    def it_raises_on_a_non_success_status():
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.fetch_user_id("token")

    @respx.mock
    def it_raises_on_a_network_error():
        respx.get(_IDENTITY_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.fetch_user_id("token")

    @respx.mock
    def it_raises_when_no_id_is_returned():
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.fetch_user_id("token")


def describe_link_member_from_code():
    @respx.mock
    @pytest.mark.django_db
    def it_links_the_member_with_the_fetched_discord_id(settings, linked_member):
        settings.DISCORD_CLIENT_ID = "cid"
        settings.DISCORD_CLIENT_SECRET = "secret"
        member = linked_member()
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "888999"}))
        discord_oauth.link_member_from_code(member, "code", _REDIRECT)
        member.refresh_from_db()
        assert member.discord_user_id == "888999"
        assert member.discord_is_linked is True

    @respx.mock
    @pytest.mark.django_db
    def it_leaves_the_member_unlinked_when_the_token_exchange_fails(linked_member):
        member = linked_member()
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.link_member_from_code(member, "code", _REDIRECT)
        member.refresh_from_db()
        assert member.discord_is_linked is False

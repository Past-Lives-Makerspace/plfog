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
        assert params["scope"] == ["identify email"]
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


def describe_fetch_identity():
    @respx.mock
    def it_returns_the_id_and_username_on_success():
        respx.get(_IDENTITY_URL).mock(
            return_value=httpx.Response(200, json={"id": "777", "username": "makerjo", "global_name": "Jo"})
        )
        identity = discord_oauth.fetch_identity("token")
        assert identity.user_id == "777"
        assert identity.handle == "makerjo"

    @respx.mock
    def it_falls_back_to_global_name_when_username_is_blank():
        respx.get(_IDENTITY_URL).mock(
            return_value=httpx.Response(200, json={"id": "777", "username": "", "global_name": "Jo"})
        )
        assert discord_oauth.fetch_identity("token").handle == "Jo"

    @respx.mock
    def it_returns_a_blank_handle_when_neither_name_is_present():
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "777"}))
        assert discord_oauth.fetch_identity("token").handle == ""

    @respx.mock
    def it_raises_on_a_non_success_status():
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.fetch_identity("token")

    @respx.mock
    def it_raises_on_a_network_error():
        respx.get(_IDENTITY_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.fetch_identity("token")

    @respx.mock
    def it_raises_when_no_id_is_returned():
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"username": "nope"}))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.fetch_identity("token")


def describe_fetch_identity_email():
    @respx.mock
    def it_reads_the_email_and_verified_flag():
        respx.get(_IDENTITY_URL).mock(
            return_value=httpx.Response(
                200, json={"id": "7", "username": "jo", "email": "jo@example.com", "verified": True}
            )
        )
        identity = discord_oauth.fetch_identity("token")
        assert identity.email == "jo@example.com"
        assert identity.email_verified is True

    @respx.mock
    def it_defaults_email_blank_and_unverified_when_absent():
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "7"}))
        identity = discord_oauth.fetch_identity("token")
        assert identity.email == ""
        assert identity.email_verified is False


def _verified_member(email: str):
    from allauth.account.models import EmailAddress
    from django.contrib.auth.models import User
    from django.db.models.signals import post_save
    from factory.django import mute_signals

    from tests.membership.factories import MemberFactory

    with mute_signals(post_save):
        user = User.objects.create_user(username=f"vm_{email}", email=email)
    member = MemberFactory()
    member.user = user
    member.save(update_fields=["user"])
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return member


def describe_resolve_member_from_code():
    def _mock_identity(email: str, verified: bool):
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        respx.get(_IDENTITY_URL).mock(
            return_value=httpx.Response(200, json={"id": "42", "username": "jo", "email": email, "verified": verified})
        )

    @respx.mock
    @pytest.mark.django_db
    def it_returns_the_member_on_a_verified_email_match():
        member = _verified_member("jo@example.com")
        _mock_identity("jo@example.com", verified=True)
        resolved, identity = discord_oauth.resolve_member_from_code("code", _REDIRECT)
        assert resolved == member
        assert identity.user_id == "42"

    @respx.mock
    @pytest.mark.django_db
    def it_returns_none_when_the_email_is_unverified():
        _verified_member("jo@example.com")
        _mock_identity("jo@example.com", verified=False)
        resolved, identity = discord_oauth.resolve_member_from_code("code", _REDIRECT)
        assert resolved is None
        assert identity.email == "jo@example.com"

    @respx.mock
    @pytest.mark.django_db
    def it_returns_none_when_no_verified_account_matches():
        _mock_identity("stranger@example.com", verified=True)
        resolved, _identity = discord_oauth.resolve_member_from_code("code", _REDIRECT)
        assert resolved is None

    @respx.mock
    @pytest.mark.django_db
    def it_raises_on_an_oauth_failure():
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.resolve_member_from_code("code", _REDIRECT)


def describe_link_member_from_code():
    @respx.mock
    @pytest.mark.django_db
    def it_links_the_member_and_fills_a_blank_handle(settings, linked_member):
        settings.DISCORD_CLIENT_ID = "cid"
        settings.DISCORD_CLIENT_SECRET = "secret"
        member = linked_member()
        assert member.discord_handle == ""
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "888999", "username": "makerjo"}))
        discord_oauth.link_member_from_code(member, "code", _REDIRECT)
        member.refresh_from_db()
        assert member.discord_user_id == "888999"
        assert member.discord_is_linked is True
        assert member.discord_handle == "makerjo"

    @respx.mock
    @pytest.mark.django_db
    def it_leaves_the_member_unlinked_when_the_token_exchange_fails(linked_member):
        member = linked_member()
        respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400))
        with pytest.raises(DiscordOAuthError):
            discord_oauth.link_member_from_code(member, "code", _REDIRECT)
        member.refresh_from_db()
        assert member.discord_is_linked is False

"""Feedback request 14: connecting Discord from the Android app.

The app starts Connect in its WebView, Discord hands the callback to Chrome (another cookie
jar, same signed-in member), and a member who was never on the server is put on it.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import signing
from django.test import Client
from django.urls import reverse

from hub.discord_views import _signed_state
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
_IDENTITY_URL = "https://discord.com/api/v10/users/@me"
_JOIN_URL = "https://discord.com/api/v10/guilds/srv/members/424242"
_ROLE_RE = r"https://discord\.com/api/v10/guilds/srv/members/424242/roles/.+"
_UNVERIFIED = "We couldn't verify that Discord sign-in. Please try connecting again."
_CONNECTED = "Your Discord account is connected"


def _configure(settings) -> None:
    from core.models import SiteConfiguration

    settings.DISCORD_CLIENT_ID = "cid"
    settings.DISCORD_CLIENT_SECRET = "secret"
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.save()


def _user(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, password="pass")


def _browser(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _start_in_app(user: User) -> str:
    """Press Connect in one browser and return the ``state`` Discord will hand back."""
    response = _browser(user).get(reverse("hub_discord_connect"))
    return parse_qs(urlparse(response.url).query)["state"][0]


def _mock_discord(join: httpx.Response | None = None) -> tuple[respx.Route, respx.Route]:
    """Mock the whole connect: code exchange, identity, the server join and role pushes."""
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
    respx.get(_IDENTITY_URL).mock(return_value=httpx.Response(200, json={"id": "424242"}))
    join_route = respx.put(_JOIN_URL).mock(return_value=join or httpx.Response(201, json={}))
    roles = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
    respx.route(host="discord.com").mock(return_value=httpx.Response(200, json=[]))
    return join_route, roles


def _connect(user: User) -> list[str]:
    """Run the callback with a state signed for ``user``; returns the flash messages."""
    response = _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": _signed_state(user.pk)})
    assert response.url == f"{reverse('hub_user_settings')}?tab=notifications"
    return _messages(response)


def _messages(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def describe_android_app_connect():
    @respx.mock
    def it_links_when_the_callback_lands_in_another_browser(settings):
        _configure(settings)
        user = _user("fb14")
        state = _start_in_app(user)
        _mock_discord()
        _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": state})
        user.member.refresh_from_db()
        assert user.member.discord_user_id == "424242"

    @respx.mock
    def it_puts_a_member_who_was_never_on_the_server_onto_it(settings):
        _configure(settings)
        user = _user("fb14b")
        client = _browser(user)
        state = parse_qs(urlparse(client.get(reverse("hub_discord_connect")).url).query)["state"][0]
        join, _roles = _mock_discord()
        client.get(reverse("hub_discord_callback"), {"code": "c", "state": state})
        assert join.called
        assert join.calls.last.request.read() == b'{"access_token":"abc"}'


def describe_connect_puts_the_person_on_the_server():
    @pytest.mark.parametrize("answer", [201, 204])
    @respx.mock
    def it_joins_then_pushes_a_role_for_each_guild(settings, answer):
        # 201 = added now, 204 = already on the server; both are a success.
        _configure(settings)
        user = _user(f"fb14r{answer}")
        GuildMembershipFactory(member=user.member, guild=GuildFactory(discord_role_ids=["r1"]))
        GuildMembershipFactory(member=user.member, guild=GuildFactory(discord_role_ids=["r2"]))
        join, roles = _mock_discord(join=httpx.Response(answer))
        messages = _connect(user)
        assert any(_CONNECTED in m for m in messages)
        assert join.called
        assert {call.request.url.path.rsplit("/", 1)[-1] for call in roles.calls} == {"r1", "r2"}
        # On the server first, so the roles land on someone who is there.
        paths = [call.request.url.path for call in respx.calls]
        assert paths.index("/api/v10/guilds/srv/members/424242") < min(
            i for i, path in enumerate(paths) if "/roles/" in path
        )

    @pytest.mark.parametrize("answer", [403, 500])
    @respx.mock
    def it_still_links_when_discord_refuses_or_errors(settings, answer):
        _configure(settings)
        user = _user(f"fb14e{answer}")
        _mock_discord(join=httpx.Response(answer, text="no"))
        messages = _connect(user)
        user.member.refresh_from_db()
        assert user.member.discord_user_id == "424242"
        assert any(_CONNECTED in m for m in messages)

    @respx.mock
    def it_still_links_and_sends_nothing_when_the_bot_is_off(settings):
        # Staging: the bot token answers blank, so the join is never sent.
        _configure(settings)
        settings.DISCORD_BOT_TOKEN = ""
        user = _user("fb14off")
        join, _roles = _mock_discord()
        messages = _connect(user)
        user.member.refresh_from_db()
        assert user.member.discord_user_id == "424242"
        assert any(_CONNECTED in m for m in messages)
        assert not join.called


def describe_signed_state():
    @respx.mock
    def it_rejects_a_state_signed_for_another_user(settings):
        _configure(settings)
        user = _user("fb14d")
        other = User.objects.create_user(username="fb14e", password="pass")
        token = respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        response = _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": _signed_state(other.pk)})
        assert _UNVERIFIED in _messages(response)
        assert not token.called
        user.member.refresh_from_db()
        assert user.member.discord_is_linked is False

    @respx.mock
    def it_rejects_an_expired_state(settings, monkeypatch):
        _configure(settings)
        user = _user("fb14f")
        eleven_minutes_ago = time.time() - 660
        with monkeypatch.context() as patch:
            patch.setattr(signing.time, "time", lambda: eleven_minutes_ago)
            state = _signed_state(user.pk)
        token = respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        response = _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": state})
        assert _UNVERIFIED in _messages(response)
        assert not token.called

    @respx.mock
    def it_rejects_a_state_with_one_character_changed(settings):
        _configure(settings)
        user = _user("fb14h")
        state = _signed_state(user.pk)
        tampered = state[:-1] + ("A" if state[-1] != "A" else "B")
        token = respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        response = _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": tampered})
        assert _UNVERIFIED in _messages(response)
        assert not token.called

    @respx.mock
    def it_rejects_a_missing_state(settings):
        _configure(settings)
        user = _user("fb14i")
        token = respx.post(_TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "abc"}))
        response = _browser(user).get(reverse("hub_discord_callback"), {"code": "c"})
        assert _UNVERIFIED in _messages(response)
        assert not token.called

    @respx.mock
    def it_refuses_a_state_used_a_second_time(settings):
        _configure(settings)
        user = _user("fb14j")
        state = _signed_state(user.pk)
        _mock_discord()
        first = _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": state})
        assert _UNVERIFIED not in _messages(first)
        token_calls = respx.calls.call_count
        replay = _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": state})
        assert _UNVERIFIED in _messages(replay)
        assert respx.calls.call_count == token_calls  # the replay never reaches Discord

    def it_rejects_a_value_signed_with_another_salt(settings):
        _configure(settings)
        user = _user("fb14g")
        state = signing.dumps({"pk": user.pk, "nonce": "n"}, salt="something.else")
        response = _browser(user).get(reverse("hub_discord_callback"), {"code": "c", "state": state})
        assert _UNVERIFIED in _messages(response)

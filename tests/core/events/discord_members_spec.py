"""Removing a person from the Discord server through the bot (#898). HTTP is mocked with ``respx``."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events.discord_members import JoinOutcome, KickOutcome, add_member, kick_member, list_member_ids_by_username

KICK = "https://discord.com/api/v10/guilds/srv/members/42"
LIST = "https://discord.com/api/v10/guilds/srv/members"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"


def describe_kick_member():
    @respx.mock
    def it_removes_and_names_the_reason_in_the_audit_log():
        route = respx.delete(KICK).mock(return_value=httpx.Response(204))
        assert kick_member("srv", "42", "Membership ended") is KickOutcome.REMOVED
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bot bot-tok"
        assert request.headers["X-Audit-Log-Reason"] == "Membership ended"

    @respx.mock
    def it_reports_someone_not_on_the_server():
        respx.delete(KICK).mock(return_value=httpx.Response(404))
        assert kick_member("srv", "42", "r") is KickOutcome.NOT_ON_SERVER

    @respx.mock
    def it_reports_a_refusal():
        respx.delete(KICK).mock(return_value=httpx.Response(403, text="Missing Permissions"))
        assert kick_member("srv", "42", "r") is KickOutcome.REFUSED

    @respx.mock
    def it_raises_on_anything_else():
        respx.delete(KICK).mock(return_value=httpx.Response(429, text="slow down"))
        with pytest.raises(httpx.HTTPStatusError, match="429 slow down"):
            kick_member("srv", "42", "r")

    @respx.mock
    def it_lets_a_network_error_through():
        respx.delete(KICK).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(httpx.ConnectError):
            kick_member("srv", "42", "r")

    @respx.mock
    def it_sends_nothing_without_a_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert kick_member("srv", "42", "r") is KickOutcome.DISABLED
        assert not respx.calls


def describe_add_member():
    @respx.mock
    def it_adds_with_the_members_own_token():
        route = respx.put(KICK).mock(return_value=httpx.Response(201, json={}))
        assert add_member("srv", "42", "user-tok") is JoinOutcome.ADDED
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bot bot-tok"
        assert request.read() == b'{"access_token":"user-tok"}'

    @respx.mock
    def it_reports_someone_already_there():
        respx.put(KICK).mock(return_value=httpx.Response(204))
        assert add_member("srv", "42", "t") is JoinOutcome.ALREADY_THERE

    @respx.mock
    def it_reports_a_refusal():
        respx.put(KICK).mock(return_value=httpx.Response(403, text="no"))
        assert add_member("srv", "42", "t") is JoinOutcome.REFUSED

    @respx.mock
    def it_raises_on_anything_else():
        respx.put(KICK).mock(return_value=httpx.Response(400, text="bad token"))
        with pytest.raises(httpx.HTTPStatusError, match="400 bad token"):
            add_member("srv", "42", "t")

    @respx.mock
    def it_sends_nothing_without_a_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert add_member("srv", "42", "t") is JoinOutcome.DISABLED
        assert not respx.calls


def _page(start: int, count: int) -> list[dict[str, object]]:
    return [{"user": {"id": str(start + i), "username": f"User{start + i}"}} for i in range(count)]


def describe_list_member_ids_by_username():
    @respx.mock
    def it_pages_until_a_short_page():
        route = respx.get(LIST).mock(
            side_effect=[httpx.Response(200, json=_page(1, 1000)), httpx.Response(200, json=_page(1001, 2))]
        )
        found = list_member_ids_by_username("srv")
        assert len(found) == 1002
        assert found["user1002"] == "1002"
        first, second = (call.request.url.params for call in route.calls)
        assert (first["limit"], first["after"]) == ("1000", "0")
        assert second["after"] == "1000"

    @respx.mock
    def it_raises_when_the_members_intent_is_off():
        respx.get(LIST).mock(return_value=httpx.Response(403, json={"code": 50001}))
        with pytest.raises(httpx.HTTPStatusError):
            list_member_ids_by_username("srv")

    @respx.mock
    def it_is_empty_without_a_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert list_member_ids_by_username("srv") == {}
        assert not respx.calls

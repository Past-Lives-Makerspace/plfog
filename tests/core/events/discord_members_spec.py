"""Removing a person from the Discord server through the bot (#898). HTTP is mocked with ``respx``."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events.discord_members import KickOutcome, kick_member

KICK = "https://discord.com/api/v10/guilds/srv/members/42"


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

"""BDD specs: the channel-message helpers are logged no-ops on staging, before any request.

A copied production row names the channels, and the settings-level token is blank on
staging; the helpers must not even build the request (a blank ``Authorization: Bot``
header used to be the only thing stopping it).
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from core.integrations import discord_channel

pytestmark = pytest.mark.django_db

_CHANNEL = "946149249178021949"
_MESSAGE = "1000000000000000001"
_MESSAGES_URL = f"https://discord.com/api/v10/channels/{_CHANNEL}/messages"
_WEBHOOK = "https://discord.com/api/webhooks/123/abc"


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.DISCORD_BOT_TOKEN = "bot-token"
    return settings


def describe_on_staging():
    @respx.mock
    def it_does_not_post_and_returns_an_empty_message(staging, caplog):
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "9"}))
        with caplog.at_level(logging.WARNING, logger="core.events.discord_dm"):
            result = discord_channel.post_channel_message(_CHANNEL, [{"title": "Digest"}])
        assert result == {}
        assert route.called is False
        assert "channel post skipped" in caplog.text

    @respx.mock
    def it_does_not_edit_and_returns_none(staging):
        route = respx.patch(f"{_MESSAGES_URL}/{_MESSAGE}").mock(return_value=httpx.Response(200, json={}))
        assert discord_channel.edit_channel_message(_CHANNEL, _MESSAGE, [{"title": "Info"}]) is None
        assert route.called is False

    @respx.mock
    def it_does_not_fetch_a_channel_name(staging):
        hook = respx.get(_WEBHOOK).mock(return_value=httpx.Response(200, json={"channel_id": _CHANNEL}))
        assert discord_channel.fetch_channel_name_from_webhook(_WEBHOOK) == ""
        assert hook.called is False


def describe_off_staging():
    @respx.mock
    def it_still_posts_with_the_bot_token(settings):
        settings.IS_STAGING = False
        settings.DISCORD_BOT_TOKEN = "bot-token"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "9"}))
        assert discord_channel.post_channel_message(_CHANNEL, [{"title": "Digest"}]) == {"id": "9"}
        assert route.called is True

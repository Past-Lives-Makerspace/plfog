"""BDD specs for the Discord channel-message client (bot-authed embed posts).

All HTTP mocked with respx — these NEVER hit Discord.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from core.integrations import discord_channel as dc

CHANNEL_ID = "chan123"
MESSAGE_ID = "msg456"
_MESSAGES_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
_EDIT_URL = f"{_MESSAGES_URL}/{MESSAGE_ID}"


def describe_post_channel_message():
    @respx.mock
    def it_posts_the_embeds_with_bot_auth(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "m1"}))
        dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])
        assert route.called
        request = route.calls[0].request
        assert request.headers["Authorization"] == "Bot tok"
        assert b'"embeds"' in request.content

    @respx.mock
    def it_raises_on_a_non_2xx(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(403, text="missing access"))
        with pytest.raises(dc.DiscordChannelError):
            dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])

    @respx.mock
    def it_wraps_a_network_error(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_MESSAGES_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(dc.DiscordChannelError):
            dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])

    @respx.mock
    def it_retries_once_after_a_rate_limits_advertised_wait(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(
            side_effect=[
                httpx.Response(429, json={"retry_after": 1.5}, headers={"Retry-After": "1.5"}),
                httpx.Response(200, json={"id": "m1"}),
            ]
        )
        with patch("core.integrations.discord_channel.time.sleep") as fake_sleep:
            dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])
        fake_sleep.assert_called_once_with(1.5)
        assert route.call_count == 2

    @respx.mock
    def it_raises_when_the_retry_is_rate_limited_again(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.post(_MESSAGES_URL).mock(
            return_value=httpx.Response(429, json={"retry_after": 1.0}, headers={"Retry-After": "1.0"})
        )
        with patch("core.integrations.discord_channel.time.sleep"), pytest.raises(dc.DiscordChannelError):
            dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])

    @respx.mock
    def it_fails_fast_on_a_429_with_an_unusable_retry_after(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(
            return_value=httpx.Response(429, text="slow down", headers={"Retry-After": "nonsense"})
        )
        with pytest.raises(dc.DiscordChannelError):
            dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])
        assert route.call_count == 1

    @respx.mock
    def it_fails_fast_on_a_429_with_a_too_long_wait(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(
            return_value=httpx.Response(429, text="slow down", headers={"Retry-After": "120"})
        )
        with pytest.raises(dc.DiscordChannelError):
            dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])
        assert route.call_count == 1

    @respx.mock
    def it_fails_fast_on_a_429_without_a_retry_after_header(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(429, text="slow down"))
        with pytest.raises(dc.DiscordChannelError):
            dc.post_channel_message(CHANNEL_ID, [{"title": "Hi"}])
        assert route.call_count == 1

    @respx.mock
    def it_refuses_more_than_ten_embeds_without_calling_discord(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(dc.DiscordChannelError):
            dc.post_channel_message(CHANNEL_ID, [{"title": f"e{i}"} for i in range(11)])
        assert not route.called


def describe_edit_channel_message():
    @respx.mock
    def it_patches_the_message_in_place_with_bot_auth(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.patch(_EDIT_URL).mock(return_value=httpx.Response(200, json={"id": MESSAGE_ID}))
        dc.edit_channel_message(CHANNEL_ID, MESSAGE_ID, [{"title": "Hi"}])
        assert route.called
        request = route.calls[0].request
        assert request.headers["Authorization"] == "Bot tok"
        assert b'"embeds"' in request.content

    @respx.mock
    def it_raises_on_a_non_2xx(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.patch(_EDIT_URL).mock(return_value=httpx.Response(404, text="unknown message"))
        with pytest.raises(dc.DiscordChannelError):
            dc.edit_channel_message(CHANNEL_ID, MESSAGE_ID, [{"title": "Hi"}])

    @respx.mock
    def it_wraps_a_network_error(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.patch(_EDIT_URL).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(dc.DiscordChannelError):
            dc.edit_channel_message(CHANNEL_ID, MESSAGE_ID, [{"title": "Hi"}])

    @respx.mock
    def it_retries_once_after_a_rate_limits_advertised_wait(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.patch(_EDIT_URL).mock(
            side_effect=[
                httpx.Response(429, json={"retry_after": 1.5}, headers={"Retry-After": "1.5"}),
                httpx.Response(200, json={"id": MESSAGE_ID}),
            ]
        )
        with patch("core.integrations.discord_channel.time.sleep") as fake_sleep:
            dc.edit_channel_message(CHANNEL_ID, MESSAGE_ID, [{"title": "Hi"}])
        fake_sleep.assert_called_once_with(1.5)
        assert route.call_count == 2

    @respx.mock
    def it_refuses_more_than_ten_embeds_without_calling_discord(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.patch(_EDIT_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(dc.DiscordChannelError):
            dc.edit_channel_message(CHANNEL_ID, MESSAGE_ID, [{"title": f"e{i}"} for i in range(11)])
        assert not route.called

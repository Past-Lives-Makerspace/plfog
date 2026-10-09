"""A sender's Discord name and picture for a webhook post (#730): the lookup, the name rules, the cache.

HTTP is a real ``httpx`` call mocked with ``respx``, as in ``discord_spec.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx
import pytest
import respx
from django.core.cache import cache

from core.events import discord
from core.events.channels import Message
from core.events.discord_identity import (
    DiscordIdentity,
    cached_identity,
    fetch_identity,
    identity_from_user_json,
    webhook_username_allowed,
)

_USER_ID = "4242"
_USER_URL = f"https://discord.com/api/v10/users/{_USER_ID}"


@pytest.fixture(autouse=True)
def _bot_token_and_clean_cache(settings) -> Iterator[None]:
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    settings.IS_STAGING = False
    cache.clear()
    yield
    cache.clear()


def describe_webhook_username_allowed():
    @pytest.mark.parametrize("name", ["Felix", "x", "a" * 80, "Covo the Maker", "Jay H."])
    def it_allows_an_ordinary_name(name: str):
        assert webhook_username_allowed(name) is True

    @pytest.mark.parametrize(
        "name",
        ["", "a" * 81, "discord dan", "My DISCORD", "Clyde", "theclydeshow", "@felix", "a#1", "a:b", "x```y"],
    )
    def it_refuses_a_name_discord_would_reject(name: str):
        assert webhook_username_allowed(name) is False

    @pytest.mark.parametrize("name", ["everyone", "Here", "HERE"])
    def it_refuses_the_two_reserved_names(name: str):
        assert webhook_username_allowed(name) is False

    def it_allows_a_name_that_only_contains_a_reserved_word():
        assert webhook_username_allowed("Everyone's Pal") is True


def describe_identity_from_user_json():
    def it_prefers_the_display_name_and_builds_the_cdn_avatar_url():
        identity = identity_from_user_json(_USER_ID, {"global_name": "Felix", "username": "covo", "avatar": "abc"})
        assert identity == DiscordIdentity(
            username="Felix", avatar_url=f"https://cdn.discordapp.com/avatars/{_USER_ID}/abc.png"
        )

    def it_keeps_png_for_an_animated_avatar():
        identity = identity_from_user_json(_USER_ID, {"global_name": "Felix", "avatar": "a_abc"})
        assert identity is not None
        assert identity.avatar_url == f"https://cdn.discordapp.com/avatars/{_USER_ID}/a_abc.png"

    def it_falls_back_to_the_account_name_without_a_display_name():
        identity = identity_from_user_json(_USER_ID, {"global_name": None, "username": "covo", "avatar": None})
        assert identity == DiscordIdentity(username="covo", avatar_url="")

    def it_strips_the_name():
        identity = identity_from_user_json(_USER_ID, {"global_name": "  Felix  "})
        assert identity is not None
        assert identity.username == "Felix"

    def it_gives_none_for_a_name_discord_refuses():
        assert identity_from_user_json(_USER_ID, {"global_name": "Discord Dan", "avatar": "abc"}) is None

    def it_gives_none_with_no_name_at_all():
        assert identity_from_user_json(_USER_ID, {}) is None


def describe_fetch_identity():
    @respx.mock
    def it_reads_the_user_as_the_bot():
        route = respx.get(_USER_URL).mock(
            return_value=httpx.Response(200, json={"id": _USER_ID, "global_name": "Felix", "avatar": "abc"})
        )
        identity = fetch_identity(_USER_ID)
        assert identity is not None
        assert identity.username == "Felix"
        assert route.calls.last.request.headers["Authorization"] == "Bot bot-tok"

    @respx.mock
    def it_logs_and_gives_none_on_an_error_status(caplog):
        respx.get(_USER_URL).mock(return_value=httpx.Response(404, text="Unknown User"))
        with caplog.at_level(logging.WARNING, logger="core.events.discord_identity"):
            assert fetch_identity(_USER_ID) is None
        assert "404" in caplog.text
        assert _USER_ID in caplog.text

    @respx.mock
    def it_logs_and_gives_none_on_a_network_error(caplog):
        respx.get(_USER_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
        with caplog.at_level(logging.WARNING, logger="core.events.discord_identity"):
            assert fetch_identity(_USER_ID) is None
        assert "network error" in caplog.text

    @respx.mock
    def it_logs_and_gives_none_on_a_body_that_is_not_json(caplog):
        respx.get(_USER_URL).mock(return_value=httpx.Response(200, text="<html>"))
        with caplog.at_level(logging.WARNING, logger="core.events.discord_identity"):
            assert fetch_identity(_USER_ID) is None
        assert "not JSON" in caplog.text

    @respx.mock
    def it_logs_and_gives_none_on_json_that_is_not_a_user(caplog):
        respx.get(_USER_URL).mock(return_value=httpx.Response(200, json=["Felix"]))
        with caplog.at_level(logging.WARNING, logger="core.events.discord_identity"):
            assert fetch_identity(_USER_ID) is None
        assert "not a user" in caplog.text

    @respx.mock
    def it_logs_and_gives_none_for_a_name_discord_refuses(caplog):
        respx.get(_USER_URL).mock(return_value=httpx.Response(200, json={"global_name": "Clyde", "avatar": "abc"}))
        with caplog.at_level(logging.WARNING, logger="core.events.discord_identity"):
            assert fetch_identity(_USER_ID) is None
        assert "cannot be a webhook name" in caplog.text

    def it_asks_nothing_without_a_bot_token(settings):
        with respx.mock(assert_all_called=False) as router:
            settings.DISCORD_BOT_TOKEN = ""
            route = router.get(_USER_URL)
            assert fetch_identity(_USER_ID) is None
            assert not route.called

    def it_asks_nothing_without_a_user_id():
        with respx.mock(assert_all_called=False) as router:
            route = router.get(_USER_URL)
            assert fetch_identity("") is None
            assert not route.called


def describe_cached_identity():
    @respx.mock
    def it_asks_discord_once_for_repeated_previews():
        route = respx.get(_USER_URL).mock(return_value=httpx.Response(200, json={"global_name": "Felix"}))
        first = cached_identity(_USER_ID)
        second = cached_identity(_USER_ID)
        assert first == second == DiscordIdentity(username="Felix")
        assert route.call_count == 1

    @respx.mock
    def it_remembers_a_failure_too():
        route = respx.get(_USER_URL).mock(return_value=httpx.Response(500))
        assert cached_identity(_USER_ID) is None
        assert cached_identity(_USER_ID) is None
        assert route.call_count == 1

    def it_gives_none_without_a_user_id():
        assert cached_identity("") is None


def describe_build_embed_payload_identity():
    def it_carries_the_name_and_picture_when_set():
        payload = discord.build_embed_payload(
            Message(title="T", body="B", discord_username="Felix", discord_avatar_url="https://cdn/x.png")
        )
        assert payload["username"] == "Felix"
        assert payload["avatar_url"] == "https://cdn/x.png"

    def it_leaves_the_picture_out_for_a_default_avatar():
        payload = discord.build_embed_payload(Message(title="T", body="B", discord_username="Felix"))
        assert payload["username"] == "Felix"
        assert "avatar_url" not in payload

    def it_leaves_both_out_by_default():
        payload = discord.build_embed_payload(Message(title="T", body="B"))
        assert "username" not in payload
        assert "avatar_url" not in payload

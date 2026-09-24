"""BDD specs: every bot-authenticated sender is a logged no-op on staging, before any request.

Roles, reactions, DMs and the interaction callbacks all take their credential from
``core.events.discord_dm.bot_token``; each entry point asks ``bot_disabled`` first and
returns its "nothing sent" value without touching ``httpx``.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from core.events import discord_dm, discord_interactions, discord_reactions, discord_roles

_API = "https://discord.com/api/v10"


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.DISCORD_BOT_TOKEN = "bot-token"
    settings.DISCORD_CLIENT_ID = "app-id"
    return settings


def describe_bot_disabled():
    def it_is_true_and_logs_once_on_staging(staging, caplog):
        with caplog.at_level(logging.WARNING, logger="core.events.discord_dm"):
            assert discord_dm.bot_disabled("probe") is True
        assert "Discord probe skipped: the bot token is blank" in caplog.text

    def it_is_false_and_silent_with_a_token(settings, caplog):
        settings.IS_STAGING = False
        settings.DISCORD_BOT_TOKEN = "bot-token"
        with caplog.at_level(logging.WARNING, logger="core.events.discord_dm"):
            assert discord_dm.bot_disabled("probe") is False
        assert caplog.text == ""


def describe_roles_on_staging():
    @respx.mock
    def it_does_not_assign_or_remove_a_role(staging):
        route = respx.route(url__regex=rf"{_API}/guilds/.*").mock(return_value=httpx.Response(204))
        assert discord_roles.assign_role("999", "42", "7") is False
        assert discord_roles.remove_role("999", "42", "7") is False
        assert route.called is False


def describe_reactions_on_staging():
    @respx.mock
    def it_reports_an_incomplete_empty_read_without_a_request(staging):
        route = respx.get(url__regex=rf"{_API}/channels/.*/reactions/.*").mock(
            return_value=httpx.Response(200, json=[{"id": "1"}])
        )
        page = discord_reactions.fetch_reactors("1", "2", "🔥")
        assert page.user_ids == set()
        assert page.complete is False
        assert route.called is False


def describe_dms_on_staging():
    @respx.mock
    def it_does_not_open_a_channel_or_post(staging):
        route = respx.post(url__regex=rf"{_API}/(users/@me/channels|channels/.*/messages)").mock(
            return_value=httpx.Response(200, json={"id": "c1"})
        )
        assert discord_dm.open_dm_channel("42") == ""
        assert discord_dm.send_dm_text("42", "hello") is False
        assert route.called is False


def describe_interaction_callbacks_on_staging():
    @respx.mock
    def it_sends_no_callback_ack_followup_or_poll_expiry(staging):
        route = respx.route(url__regex=rf"{_API}/.*").mock(return_value=httpx.Response(200, json={}))
        assert discord_interactions.send_modal_via_callback("i1", "tok", {"type": 9}) is None
        assert discord_interactions.ack_deferred("i1", "tok") is False
        assert discord_interactions.ack_component_deferred("i1", "tok") is False
        assert discord_interactions.send_followup("tok", content="done") is False
        assert discord_interactions.expire_poll("1", "2") is False
        assert route.called is False

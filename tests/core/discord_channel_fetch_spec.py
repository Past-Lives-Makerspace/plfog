"""fetch_channel_name_from_webhook — the best-effort '#channel' name behind a guild webhook.

Two hops (webhook -> channel id -> channel name via the bot); NEVER raises, since it backs a
display label. Guild.sync_discord_channel_name caches the result on the row, and the
sync_guild_discord_channels command runs it across every guild with a webhook.
"""

from __future__ import annotations

from io import StringIO

import httpx
import pytest
import respx
from django.core.management import call_command

from core.events.discord_dm import API_BASE
from core.integrations.discord_channel import fetch_channel_name_from_webhook
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_HOOK = "https://discord.com/api/webhooks/1/abc"


def describe_fetch_channel_name_from_webhook():
    @respx.mock
    def it_returns_the_hash_channel_name(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.get(_HOOK).mock(return_value=httpx.Response(200, json={"channel_id": "123"}))
        respx.get(f"{API_BASE}/channels/123").mock(return_value=httpx.Response(200, json={"name": "glass-guild"}))
        assert fetch_channel_name_from_webhook(_HOOK) == "#glass-guild"

    def it_is_blank_without_a_bot_token(settings):
        settings.DISCORD_BOT_TOKEN = ""
        assert fetch_channel_name_from_webhook(_HOOK) == ""

    def it_is_blank_without_a_webhook(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        assert fetch_channel_name_from_webhook("") == ""

    @respx.mock
    def it_is_blank_when_the_webhook_has_no_channel_id(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.get(_HOOK).mock(return_value=httpx.Response(200, json={}))
        assert fetch_channel_name_from_webhook(_HOOK) == ""

    @respx.mock
    def it_is_blank_on_a_discord_error(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        respx.get(_HOOK).mock(return_value=httpx.Response(500))
        assert fetch_channel_name_from_webhook(_HOOK) == ""


def describe_sync_discord_channel_name():
    @respx.mock
    def it_caches_the_name_on_the_guild(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        guild = GuildFactory(discord_webhook_url=_HOOK)
        respx.get(_HOOK).mock(return_value=httpx.Response(200, json={"channel_id": "9"}))
        respx.get(f"{API_BASE}/channels/9").mock(return_value=httpx.Response(200, json={"name": "ceramics"}))
        assert guild.sync_discord_channel_name() == "#ceramics"
        guild.refresh_from_db()
        assert guild.discord_channel_name == "#ceramics"

    def it_falls_back_to_a_generic_label_when_unfetched():
        guild = GuildFactory(discord_channel_name="")
        assert guild.announcement_channel_label == "your guild's channel"


def describe_sync_guild_discord_channels_command():
    @respx.mock
    def it_syncs_every_guild_with_a_webhook(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        guild = GuildFactory(discord_webhook_url=_HOOK, name="Glass Guild")
        GuildFactory(discord_webhook_url="", name="No Webhook Guild")  # skipped (no webhook)
        respx.get(_HOOK).mock(return_value=httpx.Response(200, json={"channel_id": "7"}))
        respx.get(f"{API_BASE}/channels/7").mock(return_value=httpx.Response(200, json={"name": "glass"}))
        out = StringIO()
        call_command("sync_guild_discord_channels", stdout=out)
        guild.refresh_from_db()
        assert guild.discord_channel_name == "#glass"
        assert "Glass Guild: #glass" in out.getvalue()

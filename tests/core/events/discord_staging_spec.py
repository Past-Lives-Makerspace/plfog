"""BDD specs for the Discord kill switch on staging (settings.IS_STAGING).

Staging carries a copy of production's webhooks, routes and channel ids, so every resolver
answers blank there, the webhook post refuses a URL it is handed, and the bot token (the one
credential every bot call takes) is blank.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import pytest
import respx

from core.events import discord, discord_dm
from core.events.channels import Message
from core.integrations import discord_events
from core.models import DiscordWebhookRoute, SiteConfiguration
from membership.models import GuildAnnouncement, resolve_channel_webhook
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.com/api/webhooks/123/abc"


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.DISCORD_NOTIFY_WEBHOOK_URL = _WEBHOOK
    settings.DISCORD_BOT_TOKEN = "bot-token"
    return settings


def _message() -> Message:
    return Message(title="Class published", body="Come make things", url="/classes/", trigger_kind="class_published")


def describe_resolvers_on_staging():
    def it_blanks_the_global_webhook(staging):
        assert discord.global_webhook() == ""

    def it_blanks_an_opted_in_guild_webhook(staging):
        guild = SimpleNamespace(discord_post_enabled=True, discord_webhook_url=_WEBHOOK)
        assert discord.guild_webhook(guild) == ""

    def it_blanks_the_event_route_even_with_an_enabled_db_route(staging):
        DiscordWebhookRoute.objects.create(event_key="class_published", webhook_url=_WEBHOOK, is_enabled=True)
        assert discord.webhook_for_event("class_published") == ""

    def it_blanks_the_bot_token(staging):
        assert discord_dm.bot_token() == ""

    def it_blanks_the_announcement_picker_for_site_settings_channels(staging):
        config = SiteConfiguration.load()
        config.discord_general_webhook_url = _WEBHOOK
        config.save(update_fields=["discord_general_webhook_url"])
        assert resolve_channel_webhook(GuildAnnouncement.DiscordChannel.GENERAL) == ""

    def it_blanks_the_announcement_picker_for_a_guild_channel(staging):
        guild = GuildFactory(discord_webhook_url=_WEBHOOK, discord_post_enabled=True)
        assert resolve_channel_webhook(GuildAnnouncement.DiscordChannel.GUILD, guild) == ""


def describe_resolvers_off_staging():
    def it_keeps_answering_from_settings_and_the_database(settings):
        settings.IS_STAGING = False
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _WEBHOOK
        settings.DISCORD_BOT_TOKEN = "bot-token"
        guild = SimpleNamespace(discord_post_enabled=True, discord_webhook_url=_WEBHOOK)
        assert discord.global_webhook() == _WEBHOOK
        assert discord.guild_webhook(guild) == _WEBHOOK
        assert discord.webhook_for_event("class_published") == _WEBHOOK
        assert discord_dm.bot_token() == "bot-token"


def describe_post_embed_on_staging():
    @respx.mock
    def it_refuses_a_url_it_is_handed_without_calling_discord(staging, caplog):
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        with caplog.at_level(logging.WARNING, logger="core.events.discord"):
            assert discord.post_embed(_WEBHOOK, _message()) is False
        assert route.called is False
        assert "ENVIRONMENT=staging" in caplog.text

    def it_stays_quiet_about_a_blank_webhook(staging, caplog):
        with caplog.at_level(logging.WARNING, logger="core.events.discord"):
            assert discord.post_embed("", _message()) is False
        assert caplog.text == ""


def describe_scheduled_events_client_on_staging():
    def it_comes_back_disabled_even_when_fully_configured(staging):
        config = SiteConfiguration.load()
        config.discord_server_id = "999"
        config.discord_events_sync_enabled = True
        config.save(update_fields=["discord_server_id", "discord_events_sync_enabled"])
        assert discord_events.DiscordScheduledEventsClient.from_settings().enabled is False

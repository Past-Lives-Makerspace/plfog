"""BDD specs: the Scheduled Events client never sends on staging.

``from_settings()`` comes back disabled however the copied Site Settings row is set, and
the raw call seam refuses before building a request even for a client constructed by hand.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.integrations import discord_events as de
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

_SERVER = "999"
_EVENTS_URL = f"https://discord.com/api/v10/guilds/{_SERVER}/scheduled-events"


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.DISCORD_BOT_TOKEN = "bot-token"
    config = SiteConfiguration.load()
    config.discord_server_id = _SERVER
    config.discord_events_sync_enabled = True
    config.save(update_fields=["discord_server_id", "discord_events_sync_enabled"])
    return settings


def describe_on_staging():
    def it_builds_a_disabled_client_from_a_fully_configured_row(staging):
        client = de.DiscordScheduledEventsClient.from_settings()
        assert client.enabled is False
        assert client.server_id == _SERVER

    @respx.mock
    def it_refuses_a_hand_built_client_before_any_request(staging):
        route = respx.post(_EVENTS_URL).mock(return_value=httpx.Response(200, json={"id": "e1"}))
        client = de.DiscordScheduledEventsClient(enabled=True, server_id=_SERVER)
        with pytest.raises(de.DiscordEventsError, match="bot token is blank"):
            client.insert_event(_SERVER, {"name": "Open studio"})
        assert route.called is False

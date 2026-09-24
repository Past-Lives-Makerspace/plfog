"""BDD specs: the reaction reconcile and the Interested sync do nothing on staging.

Both plant the ids a copied production Site Settings row carries and check that neither
sweep reaches Discord nor changes anything.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.models import SiteConfiguration
from membership import discord_sync, interested_sync

pytestmark = pytest.mark.django_db

_API = "https://discord.com/api/v10"


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.DISCORD_BOT_TOKEN = "bot-token"
    config = SiteConfiguration.load()
    config.discord_server_id = "999"
    config.discord_role_message_channel_id = "1"
    config.discord_role_message_id = "2"
    config.discord_events_sync_enabled = True
    config.save()
    return settings


def describe_on_staging():
    @respx.mock
    def it_does_not_reconcile_reactions(staging):
        route = respx.route(url__regex=rf"{_API}/.*").mock(return_value=httpx.Response(200, json=[]))
        stats = discord_sync.reconcile_reactions()
        assert stats.ran is False
        assert route.called is False

    @respx.mock
    def it_does_not_sweep_interested_marks(staging):
        route = respx.route(url__regex=rf"{_API}/.*").mock(return_value=httpx.Response(200, json=[]))
        assert interested_sync.sync_interested_rsvps() == 0
        assert route.called is False

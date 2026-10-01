"""BDD specs: the hub's channel posters reach no Discord endpoint on staging.

Each spec plants the channel ids and toggles a copied production Site Settings row would
carry, so the callers' own gates all pass, and checks that the module boundary
(``core.integrations.discord_channel``) still sends nothing.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from core.models import SiteConfiguration
from hub import discord_calendar_posts, discord_class_posts, discord_info_post

pytestmark = pytest.mark.django_db

_CHANNEL = "946149249178021949"
_MESSAGE = "1000000000000000001"
_MESSAGES_URL = f"https://discord.com/api/v10/channels/{_CHANNEL}/messages"
_EMBED = [{"title": "This week", "description": "Something is on"}]


@pytest.fixture
def staging(settings):
    settings.IS_STAGING = True
    settings.DISCORD_BOT_TOKEN = "bot-token"
    config = SiteConfiguration.load()
    config.discord_classes_posts_enabled = True
    config.discord_classes_channel_id = _CHANNEL
    config.discord_calendar_posts_enabled = True
    config.discord_calendar_channel_id = _CHANNEL
    config.discord_info_channel_id = _CHANNEL
    config.discord_info_message_id = _MESSAGE
    config.save()
    return settings


def describe_on_staging():
    @respx.mock
    def it_posts_no_weekly_classes_digest(staging):
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "9"}))
        with patch.object(discord_class_posts, "build_weekly_classes_digest_embeds", return_value=_EMBED):
            discord_class_posts.post_weekly_classes_digest()
        assert route.called is False

    @respx.mock
    def it_posts_no_weekly_calendar_digest(staging):
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "9"}))
        with patch.object(discord_calendar_posts, "build_weekly_digest_embeds", return_value=_EMBED):
            discord_calendar_posts.post_weekly_digest()
        assert route.called is False

    @respx.mock
    def it_does_not_edit_the_info_post(staging):
        route = respx.patch(f"{_MESSAGES_URL}/{_MESSAGE}").mock(return_value=httpx.Response(200, json={}))
        discord_info_post.sync_info_post()
        assert route.called is False

"""BDD specs for the FOG-managed #important-info pinned post.

The builder is pure (config content + the live slash-command registry — the same one
``register_discord_commands`` serializes); the sync PATCHes the existing pinned message in
place. All HTTP mocked with respx — these NEVER hit Discord.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from core.events.discord_commands import all_commands
from core.models import DISCORD_INFO_LINKS_DEFAULT, SiteConfiguration
from hub import discord_info_post as dip

pytestmark = pytest.mark.django_db

_CHANNEL_ID = "1122351596400025661"
_MESSAGE_ID = "1529134555494355147"
_EDIT_URL = f"https://discord.com/api/v10/channels/{_CHANNEL_ID}/messages/{_MESSAGE_ID}"


def _configure(**overrides: str) -> SiteConfiguration:
    config = SiteConfiguration.load()
    for name, value in overrides.items():
        setattr(config, name, value)
    config.save()
    return config


def describe_build_info_embeds():
    def it_builds_the_links_embed_from_the_config_content():
        _configure(discord_info_links_content="**Wiki**\nhttps://wiki.pastlives.space")
        links, _commands = dip.build_info_embeds()
        assert links["title"] == dip.LINKS_TITLE
        assert links["description"] == "**Wiki**\nhttps://wiki.pastlives.space"

    def it_falls_back_to_the_default_content_when_the_field_is_blanked():
        _configure(discord_info_links_content="   ")
        links, _commands = dip.build_info_embeds()
        assert links["description"] == DISCORD_INFO_LINKS_DEFAULT
        assert "https://pastlives.app" in links["description"]

    def it_lists_every_registered_command_with_its_description():
        _links, commands = dip.build_info_embeds()
        assert commands["title"] == dip.COMMANDS_TITLE
        registry = all_commands()
        assert registry  # the registry is populated at app startup — the guide is never empty
        for cmd in registry:
            assert f"`/{cmd.name}`" in commands["description"]
            assert cmd.description in commands["description"]

    def it_keeps_every_embed_description_within_discords_limit():
        # Bypass the form's cap (model save) to prove the builder clips defensively.
        _configure(discord_info_links_content="x" * 10 + "\n" + "y" * 5000)
        for embed in dip.build_info_embeds():
            assert len(embed["description"]) <= dip.EMBED_DESCRIPTION_MAX
        links, _commands = dip.build_info_embeds()
        assert links["description"].endswith("…")  # clipped on a line boundary, flagged


def describe_sync_info_post():
    @respx.mock
    def it_does_nothing_without_both_ids(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        _configure(discord_info_channel_id=_CHANNEL_ID, discord_info_message_id="")
        dip.sync_info_post()  # no respx route registered — any HTTP call would error loudly

    @respx.mock
    def it_patches_the_pinned_message_with_both_embeds(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        _configure(discord_info_channel_id=_CHANNEL_ID, discord_info_message_id=_MESSAGE_ID)
        route = respx.patch(_EDIT_URL).mock(return_value=httpx.Response(200, json={"id": _MESSAGE_ID}))
        dip.sync_info_post()
        assert route.called
        request = route.calls[0].request
        assert request.headers["Authorization"] == "Bot tok"
        payload = json.loads(request.content.decode())
        assert [embed["title"] for embed in payload["embeds"]] == [dip.LINKS_TITLE, dip.COMMANDS_TITLE]

    @respx.mock
    def it_propagates_a_discord_failure(settings):
        from core.integrations.discord_channel import DiscordChannelError

        settings.DISCORD_BOT_TOKEN = "tok"
        _configure(discord_info_channel_id=_CHANNEL_ID, discord_info_message_id=_MESSAGE_ID)
        respx.patch(_EDIT_URL).mock(return_value=httpx.Response(403, text="missing access"))
        with pytest.raises(DiscordChannelError):
            dip.sync_info_post()

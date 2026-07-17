"""Specs for the ``register_discord_commands`` management command.

The command PUTs the declarative registry to Discord's bulk-overwrite endpoint — one call
per scope. All Discord REST is mocked with ``respx``; blank-credential guards must fail
loudly (``CommandError``) before any call is made.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from django.core.management import call_command
from django.core.management.base import CommandError

from core.events import discord_commands
from core.events.discord_commands import SlashCommand, all_commands, register
from core.events.discord_interactions import reply
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db

_GUILD_URL = "https://discord.com/api/v10/applications/app1/guilds/srv1/commands"
_GLOBAL_URL = "https://discord.com/api/v10/applications/app1/commands"


@pytest.fixture(autouse=True)
def _restore_registry():
    snapshot = dict(discord_commands._REGISTRY)
    yield
    discord_commands._REGISTRY.clear()
    discord_commands._REGISTRY.update(snapshot)


@pytest.fixture
def discord_creds(settings):
    settings.DISCORD_CLIENT_ID = "app1"
    settings.DISCORD_BOT_TOKEN = "bot-token"


def _set_server_id(server_id: str) -> None:
    config = SiteConfiguration.load()
    config.discord_server_id = server_id
    config.save()


def describe_register_discord_commands():
    @respx.mock
    def it_puts_guild_commands_to_the_server_scoped_endpoint(discord_creds):
        _set_server_id("srv1")
        route = respx.put(_GUILD_URL).mock(return_value=httpx.Response(200, json=[]))

        call_command("register_discord_commands", "--guild-only")

        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bot bot-token"
        assert json.loads(request.content) == [c.to_api_dict() for c in all_commands() if c.scope == "guild"]

    @respx.mock
    def it_puts_global_commands_to_the_application_endpoint(discord_creds):
        register(SlashCommand(name="global-cmd", description="d", handler=lambda i, m: reply("ok"), scope="global"))
        route = respx.put(_GLOBAL_URL).mock(return_value=httpx.Response(200, json=[]))

        call_command("register_discord_commands", "--global-only")

        assert route.called
        assert json.loads(route.calls.last.request.content) == [
            c.to_api_dict() for c in all_commands() if c.scope == "global"
        ]

    @respx.mock
    def it_does_not_call_discord_on_a_dry_run(discord_creds):
        _set_server_id("srv1")
        route = respx.put(_GUILD_URL).mock(return_value=httpx.Response(200, json=[]))

        call_command("register_discord_commands", "--guild-only", "--dry-run")

        assert not route.called

    def it_fails_loudly_when_the_client_id_is_blank(settings):
        settings.DISCORD_CLIENT_ID = ""
        settings.DISCORD_BOT_TOKEN = "bot-token"
        with pytest.raises(CommandError):
            call_command("register_discord_commands")

    def it_fails_loudly_when_the_bot_token_is_blank(settings):
        settings.DISCORD_CLIENT_ID = "app1"
        settings.DISCORD_BOT_TOKEN = ""
        with pytest.raises(CommandError):
            call_command("register_discord_commands")

    def it_fails_loudly_when_the_server_id_is_blank_for_guild_scope(discord_creds):
        _set_server_id("")
        with pytest.raises(CommandError):
            call_command("register_discord_commands", "--guild-only")

"""Specs for the slash-command registry, dispatcher, resolution helpers, and /fog-ping.

Registry mutations are snapshotted and restored around every test so throwaway commands
never leak into other specs. All Discord REST (the deferred flow) is mocked with ``respx``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events import discord_commands
from core.events.discord_commands import (
    GUIDE,
    ComponentHandler,
    SlashCommand,
    _fog_ping,
    _guide,
    all_commands,
    autodiscover,
    dispatch,
    dispatch_component,
    guild_disambiguation_reply,
    register,
    register_component,
    resolve_guild,
    resolve_member,
)
from core.events.discord_interactions import error_reply, reply
from membership.models import GuildMembership
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot and restore both global registries so per-test commands don't leak."""
    snapshot = dict(discord_commands._REGISTRY)
    component_snapshot = dict(discord_commands._COMPONENT_REGISTRY)
    yield
    discord_commands._REGISTRY.clear()
    discord_commands._REGISTRY.update(snapshot)
    discord_commands._COMPONENT_REGISTRY.clear()
    discord_commands._COMPONENT_REGISTRY.update(component_snapshot)


def _cmd(name: str, **kw) -> SlashCommand:
    kw.setdefault("description", "A test command.")
    kw.setdefault("handler", lambda interaction, member: reply("ok"))
    return SlashCommand(name=name, **kw)


def describe_register():
    def it_adds_a_command_to_the_registry():
        cmd = _cmd("temp-cmd")
        register(cmd)
        assert cmd in all_commands()

    def it_raises_on_a_duplicate_name():
        register(_cmd("dup-cmd"))
        with pytest.raises(ValueError, match="Duplicate slash command"):
            register(_cmd("dup-cmd"))


def describe_all_commands():
    def it_includes_the_builtin_fog_ping():
        assert "fog-ping" in [c.name for c in all_commands()]


def describe_to_api_dict():
    def it_serializes_to_discord_application_command_json():
        cmd = _cmd("serialized", options=[{"name": "guild", "type": 3}])
        assert cmd.to_api_dict() == {
            "name": "serialized",
            "description": "A test command.",
            "options": [{"name": "guild", "type": 3}],
            "type": 1,
        }

    def it_uses_the_options_builder_when_present():
        built = [{"name": "guild", "type": 3, "choices": [{"name": "A", "value": "a"}]}]
        # A builder wins even when a static ``options`` list is also set.
        cmd = _cmd("built", options=[{"name": "ignored", "type": 3}], options_builder=lambda: built)
        assert cmd.to_api_dict()["options"] == built

    def it_falls_back_to_static_options_without_a_builder():
        cmd = _cmd("no-builder", options=[{"name": "guild", "type": 3}])
        assert cmd.options_builder is None
        assert cmd.to_api_dict()["options"] == [{"name": "guild", "type": 3}]


def describe_autodiscover():
    def it_tolerates_apps_without_a_discord_commands_module():
        # No installed app ships a top-level discord_commands module yet, so this is a
        # clean no-op — the point is that a missing module never raises.
        autodiscover()

    def it_reraises_an_import_error_from_inside_a_present_module(monkeypatch):
        def _boom(name):
            raise ModuleNotFoundError("No module named 'inner_dep'", name="inner_dep")

        monkeypatch.setattr(discord_commands, "import_module", _boom)
        with pytest.raises(ModuleNotFoundError):
            autodiscover()


def describe_resolve_member():
    def it_resolves_from_the_guild_member_user_shape(linked_member):
        member = linked_member(discord_user_id="555")
        assert resolve_member({"member": {"user": {"id": "555"}}}) == member

    def it_resolves_from_the_dm_user_shape(linked_member):
        member = linked_member(discord_user_id="777")
        assert resolve_member({"user": {"id": "777"}}) == member

    def it_returns_none_for_an_unknown_discord_id():
        assert resolve_member({"user": {"id": "000"}}) is None

    def it_returns_none_when_no_user_id_is_present():
        assert resolve_member({}) is None


def describe_resolve_guild():
    def it_prefers_an_explicit_guild_option_over_channel_inference():
        GuildFactory(name="Channel Guild", discord_channel_id="chan1")
        option_guild = GuildFactory(name="Option Guild")
        interaction = {"channel_id": "chan1", "data": {"options": [{"name": "guild", "value": option_guild.pk}]}}
        assert resolve_guild(interaction) == option_guild

    def it_falls_back_to_channel_mapping_without_an_explicit_option():
        guild = GuildFactory(name="Chan Only", discord_channel_id="chan2")
        # A decoy non-"guild" option is present but ignored, so channel inference is used.
        interaction = {"channel_id": "chan2", "data": {"options": [{"name": "note", "value": "hi"}]}}
        assert resolve_guild(interaction) == guild

    def it_returns_none_when_neither_option_nor_channel_maps():
        assert resolve_guild({"channel_id": "unknown", "data": {}}) is None


def describe_guild_disambiguation_reply():
    def it_lists_the_members_guilds_ephemerally(linked_member):
        member = linked_member()
        GuildMembership.objects.record_app_join(GuildFactory(name="Alpha"), member)
        GuildMembership.objects.record_app_join(GuildFactory(name="Beta"), member)
        result = guild_disambiguation_reply(member)
        assert result["data"]["flags"] == 64
        assert "Alpha" in result["data"]["content"]
        assert "Beta" in result["data"]["content"]

    def it_handles_a_member_with_no_guilds():
        result = guild_disambiguation_reply(None)
        assert result["data"]["flags"] == 64
        assert "couldn't tell which guild" in result["data"]["content"]


def describe_dispatch():
    def it_returns_error_reply_for_an_unknown_command(rf):
        result = dispatch({"type": 2, "data": {"name": "does-not-exist"}}, rf.post("/"))
        assert result == error_reply()

    def it_returns_unlinked_reply_when_a_link_is_required_and_no_member(rf):
        interaction = {"type": 2, "data": {"name": "fog-ping"}, "member": {"user": {"id": "000"}}}
        result = dispatch(interaction, rf.post("/"))
        assert result["data"]["flags"] == 64
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].startswith("http")
        assert button["url"].endswith("/discord/link/")

    def it_calls_the_handler_for_a_linked_member(rf, linked_member):
        member = linked_member(discord_user_id="555")
        interaction = {"type": 2, "data": {"name": "fog-ping"}, "member": {"user": {"id": "555"}}}
        result = dispatch(interaction, rf.post("/"))
        assert result["type"] == 4
        assert member.display_name in result["data"]["content"]

    def it_converts_a_handler_exception_into_error_reply(rf):
        def _boom(interaction, member):
            raise RuntimeError("kaboom")

        register(_cmd("boom", handler=_boom, requires_link=False))
        interaction = {"type": 2, "data": {"name": "boom"}, "user": {"id": "1"}}
        assert dispatch(interaction, rf.post("/")) == error_reply()


def describe_fog_ping():
    def it_greets_a_linked_member_with_a_hub_link_button(linked_member, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        member = linked_member(discord_user_id="555")
        result = _fog_ping({}, member)
        assert result["data"]["flags"] == 64
        assert member.display_name in result["data"]["content"]
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].startswith("https://members.example")


def describe_guide():
    def _description() -> str:
        result = _guide({}, None)
        assert result["type"] == 4
        assert result["data"]["flags"] == 64  # ephemeral
        embed = result["data"]["embeds"][0]
        assert embed["title"] == "Past Lives commands"
        return embed["description"]

    def it_lists_every_registered_command_with_its_name_and_description():
        description = _description()
        for cmd in all_commands():
            assert f"**/{cmd.name}** — {cmd.description}" in description

    def it_includes_join_guild_and_itself():
        description = _description()
        assert "**/join-guild**" in description
        assert "**/guide**" in description

    def it_points_unlinked_members_at_the_link_command():
        assert "Some commands need your account connected — run `/link` first." in _description()

    def it_derives_the_list_from_the_registry():
        # A freshly registered command appears without touching the guide — the list is the
        # registry, not a hand-kept copy. ``_restore_registry`` de-registers it afterwards.
        register(_cmd("brand-new-cmd", description="A shiny new thing.", requires_link=False))
        assert "**/brand-new-cmd** — A shiny new thing." in _description()

    def it_is_readable_without_a_link_and_ephemeral():
        assert GUIDE.requires_link is False
        assert GUIDE.ephemeral is True


def _component(prefix: str, **kw) -> ComponentHandler:
    kw.setdefault("handler", lambda interaction, member: {"type": 7, "data": {"content": "updated"}})
    return ComponentHandler(prefix=prefix, **kw)


def describe_register_component():
    def it_adds_a_handler_to_the_component_registry(rf, linked_member):
        linked_member(discord_user_id="555")
        register_component(_component("temp-prefix"))
        interaction = {"type": 3, "data": {"custom_id": "temp-prefix:1"}, "member": {"user": {"id": "555"}}}
        assert dispatch_component(interaction, rf.post("/"))["type"] == 7

    def it_raises_on_a_duplicate_prefix():
        register_component(_component("dup-prefix"))
        with pytest.raises(ValueError, match="Duplicate component prefix"):
            register_component(_component("dup-prefix"))


def describe_dispatch_component():
    def it_returns_error_reply_for_an_unknown_prefix(rf):
        interaction = {"type": 3, "data": {"custom_id": "ghost:1:-:"}, "user": {"id": "1"}}
        assert dispatch_component(interaction, rf.post("/")) == error_reply()

    def it_returns_unlinked_reply_when_a_link_is_required_and_no_member(rf):
        register_component(_component("linked-only"))
        interaction = {"type": 3, "data": {"custom_id": "linked-only:2"}, "member": {"user": {"id": "000"}}}
        result = dispatch_component(interaction, rf.post("/"))
        assert result["type"] == 4  # a fresh ephemeral prompt, not an in-place update
        assert result["data"]["flags"] == 64
        button = result["data"]["components"][0]["components"][0]
        assert button["url"].startswith("http")
        assert button["url"].endswith("/discord/link/")

    def it_calls_the_handler_for_a_linked_member(rf, linked_member):
        member = linked_member(discord_user_id="555")
        seen = {}

        def _handler(interaction, handler_member):
            seen["member"] = handler_member
            return {"type": 7, "data": {"content": "page 2"}}

        register_component(_component("seen", handler=_handler))
        interaction = {"type": 3, "data": {"custom_id": "seen:2:-:"}, "member": {"user": {"id": "555"}}}
        assert dispatch_component(interaction, rf.post("/")) == {"type": 7, "data": {"content": "page 2"}}
        assert seen["member"] == member

    def it_runs_a_link_free_handler_with_member_none(rf):
        register_component(_component("open", requires_link=False))
        interaction = {"type": 3, "data": {"custom_id": "open:1"}, "user": {"id": "000"}}
        assert dispatch_component(interaction, rf.post("/"))["type"] == 7

    def it_converts_a_handler_exception_into_error_reply(rf, linked_member):
        linked_member(discord_user_id="555")

        def _boom(interaction, member):
            raise RuntimeError("kaboom")

        register_component(_component("boom-prefix", handler=_boom))
        interaction = {"type": 3, "data": {"custom_id": "boom-prefix:1"}, "member": {"user": {"id": "555"}}}
        assert dispatch_component(interaction, rf.post("/")) == error_reply()


_CALLBACK_URL = "https://discord.com/api/v10/interactions/intA/tokB/callback"
_FOLLOWUP_URL = "https://discord.com/api/v10/webhooks/appX/tokB/messages/@original"


def describe_deferred_dispatch():
    @respx.mock
    def it_acks_deferred_runs_the_handler_and_patches_the_followup(rf, settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.DISCORD_CLIENT_ID = "appX"
        callback = respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        followup = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))
        register(_cmd("slow", handler=lambda i, m: reply("slow done", ephemeral=True), requires_link=False, defer=True))

        interaction = {"type": 2, "id": "intA", "token": "tokB", "data": {"name": "slow"}, "user": {"id": "1"}}
        result = dispatch(interaction, rf.post("/"))

        import json

        assert result == {}
        assert callback.called and followup.called
        # Ephemeral flag carried on the deferred ack; the followup replaces @original.
        assert json.loads(callback.calls.last.request.content) == {"type": 5, "data": {"flags": 64}}
        assert json.loads(followup.calls.last.request.content)["content"] == "slow done"

    @respx.mock
    def it_logs_but_does_not_raise_when_the_followup_network_call_fails(rf, settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.DISCORD_CLIENT_ID = "appX"
        respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        respx.patch(_FOLLOWUP_URL).mock(side_effect=httpx.ConnectError("down"))
        register(_cmd("slow2", handler=lambda i, m: reply("done"), requires_link=False, defer=True))

        interaction = {"type": 2, "id": "intA", "token": "tokB", "data": {"name": "slow2"}, "user": {"id": "1"}}
        assert dispatch(interaction, rf.post("/")) == {}  # best-effort followup, no raise

    @respx.mock
    def it_sends_an_error_followup_when_the_deferred_handler_raises(rf, settings):
        settings.DISCORD_BOT_TOKEN = "bot"
        settings.DISCORD_CLIENT_ID = "appX"
        respx.post(_CALLBACK_URL).mock(return_value=httpx.Response(204))
        followup = respx.patch(_FOLLOWUP_URL).mock(return_value=httpx.Response(200, json={"id": "m"}))

        def _boom(interaction, member):
            raise RuntimeError("kaboom")

        register(_cmd("slow3", handler=_boom, requires_link=False, defer=True))
        interaction = {"type": 2, "id": "intA", "token": "tokB", "data": {"name": "slow3"}, "user": {"id": "1"}}

        import json

        assert dispatch(interaction, rf.post("/")) == {}  # no raise; already acked
        assert "went wrong" in json.loads(followup.calls.last.request.content)["content"]

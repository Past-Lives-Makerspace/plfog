"""Specs for the shared Discord reply/format/resolution helpers used by the member commands."""

from __future__ import annotations

from datetime import datetime

import pytest
from django.utils import timezone

from core.events.discord_replies import (
    format_local,
    guild_not_specified_reply,
    hub_url,
    option_value,
    resolve_command_guild,
    truncate,
)
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _with_options(*options: dict) -> dict:
    return {"data": {"options": list(options)}}


def describe_option_value():
    def it_returns_the_named_option_as_a_string():
        interaction = _with_options({"name": "slot", "value": 42})
        assert option_value(interaction, "slot") == "42"

    def it_returns_none_when_the_option_is_absent():
        assert option_value(_with_options(), "slot") is None

    def it_returns_none_when_the_value_is_null():
        assert option_value(_with_options({"name": "note", "value": None}), "note") is None


def describe_hub_url():
    def it_builds_an_absolute_member_hub_url(settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        assert hub_url("hub_tab_detail") == "https://members.example/tab/"


def describe_format_local():
    def it_renders_a_human_date_and_time_in_the_site_timezone():
        moment = timezone.make_aware(datetime(2099, 7, 19, 14, 0))
        rendered = format_local(moment)
        assert "Jul 19" in rendered
        assert "2:00 PM" in rendered


def describe_truncate():
    def it_leaves_short_text_untouched():
        assert truncate("hello", 20) == "hello"

    def it_trims_and_appends_the_suffix_when_over_the_limit():
        result = truncate("abcdefghij", 5, suffix="…")
        assert result.endswith("…")
        assert len(result) <= 5


def describe_resolve_command_guild():
    def it_matches_an_explicit_name_case_insensitively():
        guild = GuildFactory(name="Blacksmithing")
        interaction = _with_options({"name": "guild", "value": "blacksmith"})
        assert resolve_command_guild(interaction) == guild

    def it_returns_none_when_the_explicit_name_is_ambiguous():
        GuildFactory(name="Glass Casting")
        GuildFactory(name="Glass Blowing")
        interaction = _with_options({"name": "guild", "value": "Glass"})
        assert resolve_command_guild(interaction) is None

    def it_breaks_a_substring_tie_with_an_exact_match():
        exact = GuildFactory(name="Glass")
        GuildFactory(name="Glassblowing")
        interaction = _with_options({"name": "guild", "value": "Glass"})
        assert resolve_command_guild(interaction) == exact

    def it_returns_none_when_the_explicit_name_matches_nothing():
        GuildFactory(name="Woodworking")
        interaction = _with_options({"name": "guild", "value": "ceramics"})
        assert resolve_command_guild(interaction) is None

    def it_falls_back_to_the_channel_mapping_without_an_explicit_option():
        guild = GuildFactory(name="Fibers", discord_channel_id="chan-xyz")
        interaction = {"channel_id": "chan-xyz", "data": {"options": []}}
        assert resolve_command_guild(interaction) == guild

    def it_returns_none_when_neither_option_nor_channel_maps():
        assert resolve_command_guild({"channel_id": "nope", "data": {}}) is None


def describe_guild_not_specified_reply():
    def it_lists_active_guilds_ephemerally():
        GuildFactory(name="Ceramics")
        GuildFactory(name="Printmaking")
        result = guild_not_specified_reply()
        assert result["data"]["flags"] == 64
        assert "Ceramics" in result["data"]["content"]
        assert "Printmaking" in result["data"]["content"]

"""Specs for ``Guild.discord_channel_id`` and ``GuildManager.for_discord_channel``.

The channel → guild map powers slash-command auto-detection: a guild command run *in*
a guild's Discord channel infers the guild without an explicit option.
"""

from __future__ import annotations

import pytest

from membership.models import Guild
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def describe_Guild_discord_channel_id():
    def it_defaults_to_blank():
        guild = GuildFactory(name="Blank Channel Guild")
        assert guild.discord_channel_id == ""


def describe_for_discord_channel():
    def it_returns_the_guild_mapped_to_the_channel():
        guild = GuildFactory(name="Glass", discord_channel_id="111222333")
        assert Guild.objects.for_discord_channel("111222333") == guild

    def it_returns_none_for_an_unmapped_channel():
        GuildFactory(name="Wood", discord_channel_id="111222333")
        assert Guild.objects.for_discord_channel("999888777") is None

    def it_returns_none_for_a_blank_channel_id():
        GuildFactory(name="Metal", discord_channel_id="")
        assert Guild.objects.for_discord_channel("") is None

    def it_ignores_inactive_guilds():
        GuildFactory(name="Retired", discord_channel_id="444555666", is_active=False)
        assert Guild.objects.for_discord_channel("444555666") is None

    def it_returns_a_result_without_raising_on_a_duplicate_mapping():
        # An accidental duplicate mapping must degrade to the disambiguation fallback
        # (.first()), never raise MultipleObjectsReturned.
        first = GuildFactory(name="Dup One", discord_channel_id="707070")
        GuildFactory(name="Dup Two", discord_channel_id="707070")
        assert Guild.objects.for_discord_channel("707070") in {first, Guild.objects.get(name="Dup Two")}

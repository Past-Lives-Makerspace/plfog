"""GuildAnnouncement.resolve_discord_webhook — the channel → webhook map.

The persisted ``discord_channel`` picks where the single Discord echo posts: the guild's own
channel, the makerspace-wide #general-chat / #leadership webhooks (on SiteConfiguration), or
nowhere. A blank source resolves to "" (the emit path reads "" as "no Discord post").
"""

from __future__ import annotations

import pytest

from core.models import SiteConfiguration
from membership.models import GuildAnnouncement
from tests.membership.factories import GuildAnnouncementFactory, GuildFactory

pytestmark = pytest.mark.django_db

_GUILD_HOOK = "https://discord.com/api/webhooks/1/guild"
_GENERAL_HOOK = "https://discord.com/api/webhooks/2/general"
_LEADERSHIP_HOOK = "https://discord.com/api/webhooks/3/leadership"


def _set_shared_webhooks(*, general: str = "", leadership: str = "") -> None:
    config = SiteConfiguration.load()
    config.discord_general_webhook_url = general
    config.discord_leadership_webhook_url = leadership
    config.save()


def describe_resolve_discord_webhook():
    def describe_guild_channel():
        def it_returns_the_guilds_own_webhook_stripped():
            guild = GuildFactory(discord_webhook_url=f"  {_GUILD_HOOK}  ")
            ann = GuildAnnouncementFactory(guild=guild, discord_channel=GuildAnnouncement.DiscordChannel.GUILD)
            assert ann.resolve_discord_webhook() == _GUILD_HOOK

        def it_returns_blank_when_the_guild_has_no_webhook():
            guild = GuildFactory(discord_webhook_url="")
            ann = GuildAnnouncementFactory(guild=guild, discord_channel=GuildAnnouncement.DiscordChannel.GUILD)
            assert ann.resolve_discord_webhook() == ""

    def describe_general_channel():
        def it_returns_the_shared_general_webhook():
            _set_shared_webhooks(general=_GENERAL_HOOK)
            ann = GuildAnnouncementFactory(discord_channel=GuildAnnouncement.DiscordChannel.GENERAL)
            assert ann.resolve_discord_webhook() == _GENERAL_HOOK

        def it_returns_blank_when_general_is_unset():
            _set_shared_webhooks(general="")
            ann = GuildAnnouncementFactory(discord_channel=GuildAnnouncement.DiscordChannel.GENERAL)
            assert ann.resolve_discord_webhook() == ""

    def describe_leadership_channel():
        def it_returns_the_shared_leadership_webhook():
            _set_shared_webhooks(leadership=_LEADERSHIP_HOOK)
            ann = GuildAnnouncementFactory(discord_channel=GuildAnnouncement.DiscordChannel.LEADERSHIP)
            assert ann.resolve_discord_webhook() == _LEADERSHIP_HOOK

        def it_returns_blank_when_leadership_is_unset():
            _set_shared_webhooks(leadership="")
            ann = GuildAnnouncementFactory(discord_channel=GuildAnnouncement.DiscordChannel.LEADERSHIP)
            assert ann.resolve_discord_webhook() == ""

    def describe_none_channel():
        def it_returns_blank_for_dont_post():
            guild = GuildFactory(discord_webhook_url=_GUILD_HOOK)
            ann = GuildAnnouncementFactory(guild=guild, discord_channel=GuildAnnouncement.DiscordChannel.NONE)
            assert ann.resolve_discord_webhook() == ""

    def describe_unknown_channel():
        def it_raises_value_error_on_an_unknown_persisted_value():
            ann = GuildAnnouncementFactory()
            ann.discord_channel = "bogus"
            with pytest.raises(ValueError, match="Unknown Discord channel"):
                ann.resolve_discord_webhook()


def describe_default_channel():
    def it_defaults_a_new_row_to_the_guild_channel():
        ann = GuildAnnouncementFactory()
        assert ann.discord_channel == GuildAnnouncement.DiscordChannel.GUILD

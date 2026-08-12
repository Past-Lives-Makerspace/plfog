"""BDD specs for the on-demand nudge_unlinked_discord_members sweep command.

The sweep welcomes EVERY unlinked, unledgered human server member — no 48-hour join
window — through the same once-only ledger as the cron step. Never scheduled; the
site-settings toggle gates it just like the cron.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import httpx
import pytest
import respx
from django.core.management import call_command
from django.utils import timezone

from core.events.discord_members import GuildMemberPage
from membership.discord_sync import JoinWelcomeStats
from membership.models import DiscordJoinWelcome

pytestmark = pytest.mark.django_db

_MEMBERS_RE = r"https://discord\.com/api/v10/guilds/.+/members\?.*"
_DM_CHANNELS_URL = "https://discord.com/api/v10/users/@me/channels"
_DM_MESSAGES_URL = "https://discord.com/api/v10/channels/dm99/messages"


def _configure(settings, *, server_id: str = "srv", enabled: bool = True) -> None:
    from core.models import SiteConfiguration

    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = server_id
    config.discord_joiner_nudge_enabled = enabled
    config.save()


def describe_nudge_unlinked_discord_members():
    def it_skips_when_the_bot_token_is_blank(settings):
        settings.DISCORD_BOT_TOKEN = ""
        out = StringIO()
        call_command("nudge_unlinked_discord_members", stdout=out)
        assert "Skipped (Discord not configured)." in out.getvalue()

    def it_skips_when_the_server_id_is_blank(settings):
        _configure(settings, server_id="")
        out = StringIO()
        call_command("nudge_unlinked_discord_members", stdout=out)
        assert "Skipped (Discord not configured)." in out.getvalue()

    def it_skips_when_the_toggle_is_off(settings):
        _configure(settings, enabled=False)
        out = StringIO()
        call_command("nudge_unlinked_discord_members", stdout=out)
        assert "Skipped (new-joiner DMs are turned off in Site Settings)." in out.getvalue()

    @respx.mock
    def it_welcomes_a_member_who_joined_months_ago(settings):
        # The sweep has NO join-date window — every unlinked human is a candidate.
        _configure(settings)
        joined_at = (timezone.now() - timedelta(days=90)).isoformat()
        respx.get(url__regex=_MEMBERS_RE).mock(
            return_value=httpx.Response(200, json=[{"user": {"id": "veteran"}, "joined_at": joined_at}])
        )
        respx.post(_DM_CHANNELS_URL).mock(return_value=httpx.Response(200, json={"id": "dm99"}))
        msg = respx.post(_DM_MESSAGES_URL).mock(return_value=httpx.Response(200, json={"id": "m1"}))
        out = StringIO()
        call_command("nudge_unlinked_discord_members", stdout=out)
        assert msg.called
        assert "1 welcomed" in out.getvalue()
        assert DiscordJoinWelcome.objects.filter(discord_user_id="veteran").exists()

    def it_prints_all_four_counts(settings):
        _configure(settings)
        out = StringIO()
        with (
            patch(
                "core.events.discord_members.fetch_guild_members",
                return_value=GuildMemberPage(members=[], complete=True),
            ),
            patch(
                "membership.discord_sync._send_join_welcomes",
                return_value=JoinWelcomeStats(welcomed=2, undeliverable=1, skipped_linked=3, skipped_ledgered=4),
            ),
        ):
            call_command("nudge_unlinked_discord_members", stdout=out)
        output = out.getvalue()
        assert "2 welcomed" in output
        assert "3 skipped (already linked)" in output
        assert "4 skipped (already welcomed)" in output
        assert "1 undeliverable" in output
        assert "Member list incomplete" not in output

    def it_warns_on_a_truncated_fetch_and_sweeps_who_was_seen(settings):
        _configure(settings)
        out = StringIO()
        with (
            patch(
                "core.events.discord_members.fetch_guild_members",
                return_value=GuildMemberPage(members=[], complete=False),
            ),
            patch(
                "membership.discord_sync._send_join_welcomes",
                return_value=JoinWelcomeStats(),
            ) as send,
        ):
            call_command("nudge_unlinked_discord_members", stdout=out)
        assert "Member list incomplete" in out.getvalue()
        assert "Server Members Intent" in out.getvalue()
        assert send.called  # still sweeps whoever was seen
        assert "0 welcomed" in out.getvalue()

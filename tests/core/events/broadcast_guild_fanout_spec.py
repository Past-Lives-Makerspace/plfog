"""Multi-webhook broadcast fan-out — the central post PLUS the guild's own webhook.

A guild-scoped Discord broadcast (``ctx["guild"]`` set) posts to the central webhook
(unchanged) AND additionally to the guild's own webhook when the guild has opted in and
set a webhook. The two posts dedup INDEPENDENTLY (distinct ``EventDelivery`` target
refs), the guild post is best-effort (a guild failure never blocks central), and a
blank/disabled guild webhook is a safe no-op.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from core.events import discord as discord_module
from core.events.emit import emit
from core.models import EventDelivery
from tests.membership.factories import GuildFactory

_CENTRAL = "https://discord.com/api/webhooks/100/central"
_GUILD = "https://discord.com/api/webhooks/200/guild"


class _FakeGuild:
    """A duck-typed stand-in for the context's ``guild`` (no DB needed)."""

    def __init__(self, *, url: str, enabled: bool) -> None:
        self.discord_webhook_url = url
        self.discord_post_enabled = enabled


def describe_guild_webhook():
    def it_returns_the_stripped_url_when_enabled_and_set():
        guild = _FakeGuild(url=f"  {_GUILD}  ", enabled=True)
        assert discord_module.guild_webhook(guild) == _GUILD

    def it_returns_blank_when_disabled_even_with_a_url():
        guild = _FakeGuild(url=_GUILD, enabled=False)
        assert discord_module.guild_webhook(guild) == ""

    def it_returns_blank_when_enabled_but_url_blank():
        guild = _FakeGuild(url="", enabled=True)
        assert discord_module.guild_webhook(guild) == ""

    def it_returns_blank_for_a_non_guild_object_without_raising():
        assert discord_module.guild_webhook(object()) == ""


@pytest.mark.django_db
def describe_dual_route_broadcast():
    @respx.mock
    def it_posts_to_both_the_central_and_the_guild_webhook(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        guild = GuildFactory(discord_webhook_url=_GUILD, discord_post_enabled=True)
        central = respx.post(_CENTRAL).mock(return_value=httpx.Response(204))
        guild_route = respx.post(_GUILD).mock(return_value=httpx.Response(204))
        emit("guild_announcement", context={"guild": guild}, title="T", body="B", period="ann:both")
        assert central.called
        assert guild_route.called

    def it_is_central_only_when_the_toggle_is_off():
        guild = GuildFactory(discord_webhook_url=_GUILD, discord_post_enabled=False)
        with patch_post() as mock_post:
            emit("guild_announcement", context={"guild": guild}, title="T", body="B", period="ann:off")
        assert mock_post.call_count == 1

    @respx.mock
    def it_is_a_safe_noop_for_the_guild_when_the_webhook_is_blank(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        guild = GuildFactory(discord_webhook_url="", discord_post_enabled=True)
        central = respx.post(_CENTRAL).mock(return_value=httpx.Response(204))
        emit("guild_announcement", context={"guild": guild}, title="T", body="B", period="ann:blank")
        assert central.called
        # No guild ledger slot is ever claimed when the webhook is blank.
        assert not EventDelivery.objects.filter(target_ref__startswith="broadcast:guild:").exists()

    def it_posts_central_only_when_no_guild_is_in_context():
        # class_published carries no ``guild`` — the guild branch never fires.
        with patch_post() as mock_post:
            emit("class_published", context={}, title="T", body="B", period="cp:nocontext")
        assert mock_post.call_count == 1


@pytest.mark.django_db
def describe_independent_dedup():
    def it_dedups_central_and_guild_on_separate_ledger_slots():
        guild = GuildFactory(discord_webhook_url=_GUILD, discord_post_enabled=True)
        with patch_post() as mock_post:
            emit("guild_announcement", context={"guild": guild}, title="T", body="B", period="ann:dedup")
            assert mock_post.call_count == 2  # central + guild on first emit
            emit("guild_announcement", context={"guild": guild}, title="T", body="B", period="ann:dedup")
            assert mock_post.call_count == 2  # re-emit posts neither again
        assert EventDelivery.objects.filter(
            event_key="guild_announcement", target_ref="broadcast", channel="discord", period="ann:dedup"
        ).exists()
        assert EventDelivery.objects.filter(
            event_key="guild_announcement",
            target_ref=f"broadcast:guild:{guild.pk}",
            channel="discord",
            period="ann:dedup",
        ).exists()


@pytest.mark.django_db
def describe_best_effort_guild_failure():
    @respx.mock
    def it_does_not_let_a_guild_failure_block_the_central_post(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        guild = GuildFactory(discord_webhook_url=_GUILD, discord_post_enabled=True)
        central = respx.post(_CENTRAL).mock(return_value=httpx.Response(204))
        respx.post(_GUILD).mock(return_value=httpx.Response(500, text="boom"))
        # The guild 500 is swallowed by post_embed — emit must not raise.
        emit("guild_announcement", context={"guild": guild}, title="T", body="B", period="ann:fail")
        assert central.called


def patch_post():
    """Patch the spine's outbound Discord POST so call counts can be asserted."""
    from unittest.mock import patch

    return patch("core.events.discord.post_embed", return_value=True)

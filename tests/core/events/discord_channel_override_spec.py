"""The single emit-spine seam: an explicit ``ctx["discord_broadcast_webhook"]`` override.

When a guild announcement carries a chosen webhook, the picker owns the destination: the
central ``for spec`` DISCORD iteration (→ ``webhook_for_event`` → the global/route webhook) is
skipped, and ``_guild_broadcast`` posts the single embed to the chosen URL. A blank override
posts nowhere (but the per-recipient in-app + email fan-out still runs). Every OTHER event —
one that never sets the key — keeps its byte-for-byte central-post behavior.

HTTP is mocked at ``core.events.discord.post_embed`` so we can assert exactly which webhook(s)
were posted to.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.events.emit import emit
from core.events.registry import Channel
from core.models import EventDelivery, TransactionalEmailLog
from tests.membership.factories import GuildFactory, GuildMembershipFactory

pytestmark = pytest.mark.django_db

_CENTRAL = "https://discord.com/api/webhooks/100/central"
_CHOSEN = "https://discord.com/api/webhooks/900/chosen"


def _posted_urls(mock_post) -> list[str]:
    return [call.args[0] for call in mock_post.call_args_list]


def describe_discord_broadcast_webhook_override():
    def it_posts_once_to_the_chosen_webhook_and_skips_the_central_post(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        guild = GuildFactory(discord_webhook_url=_CENTRAL, discord_post_enabled=True)
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit(
                "guild_announcement",
                context={"guild": guild, "discord_broadcast_webhook": _CHOSEN},
                title="T",
                body="B",
                period="ann:override",
            )
        assert _posted_urls(mock_post) == [_CHOSEN]
        # The global/central webhook is never hit — the picker replaced it.
        assert _CENTRAL not in _posted_urls(mock_post)

    def it_records_a_single_guild_ledger_slot_not_the_central_one(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        guild = GuildFactory(discord_webhook_url=_CENTRAL)
        with patch("core.events.discord.post_embed", return_value=True):
            emit(
                "guild_announcement",
                context={"guild": guild, "discord_broadcast_webhook": _CHOSEN},
                title="T",
                body="B",
                period="ann:ledger",
            )
        assert EventDelivery.objects.filter(
            event_key="guild_announcement", target_ref=f"broadcast:guild:{guild.pk}", channel="discord"
        ).exists()
        # No central "broadcast" slot is claimed for a picker-driven announcement.
        assert not EventDelivery.objects.filter(
            event_key="guild_announcement", target_ref="broadcast", channel="discord"
        ).exists()

    def it_dedups_the_chosen_post_across_re_emits():
        guild = GuildFactory()
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit(
                "guild_announcement",
                context={"guild": guild, "discord_broadcast_webhook": _CHOSEN},
                title="T",
                body="B",
                period="ann:dedup",
            )
            emit(
                "guild_announcement",
                context={"guild": guild, "discord_broadcast_webhook": _CHOSEN},
                title="T",
                body="B",
                period="ann:dedup",
            )
        assert mock_post.call_count == 1

    def describe_when_the_override_is_blank():
        def it_posts_no_discord_but_still_delivers_in_app_and_email(linked_member):
            member = linked_member()
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=member)
            with patch("core.events.discord.post_embed", return_value=True) as mock_post:
                result = emit(
                    "guild_announcement",
                    context={"guild": guild, "discord_broadcast_webhook": ""},
                    title="T",
                    body="B",
                    period="ann:blank",
                    suppress_guild_broadcast=True,
                )
            assert mock_post.call_count == 0
            # The member still hears it in the app and by email.
            assert (member.user_id, Channel.IN_APP) in result.delivered
            assert TransactionalEmailLog.objects.filter(trigger_kind="guild_announcement").count() == 1


def describe_events_without_the_override():
    def it_leaves_the_central_post_untouched_for_other_events(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit("class_published", context={}, title="T", body="B", period="cp:central")
        assert _posted_urls(mock_post) == [_CENTRAL]

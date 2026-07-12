"""The @mention plumbing + the site-wide chosen-webhook fix (announcement compose wizard).

Three seams: a ``discord_mention`` field on the frozen ``Message``, one ``emit()`` param that
stamps it onto ONLY the DISCORD message, and ``build_embed_payload`` emitting top-level
``content`` (the ping — embeds don't ping) + ``allowed_mentions`` (the single flag gating BOTH
@here and @everyone). Blank mention → byte-identical payload.

Plus the site-wide fix: a chosen ``discord_broadcast_webhook`` now posts even WITHOUT a guild in
context (the old ``_guild_broadcast`` early-returned when no guild), so a site-wide announcement
routed to #general/#leadership actually posts.

HTTP is mocked at ``core.events.discord.post_embed`` so we can assert which webhook(s) were hit
and what mention rode the Message.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.events.channels import Message
from core.events.discord import build_embed_payload
from core.events.emit import emit
from core.models import EventDelivery
from tests.membership.factories import GuildFactory

_CENTRAL = "https://discord.com/api/webhooks/100/central"
_CHOSEN = "https://discord.com/api/webhooks/900/chosen"


def _posted_urls(mock_post) -> list[str]:
    return [call.args[0] for call in mock_post.call_args_list]


def _posted_messages(mock_post) -> list[Message]:
    return [call.args[1] for call in mock_post.call_args_list]


def describe_build_embed_payload():
    def it_omits_the_mention_keys_when_blank():
        payload = build_embed_payload(Message(title="T", body="B"))
        assert "content" not in payload
        assert "allowed_mentions" not in payload
        # Byte-identical to the pre-mention payload shape.
        assert set(payload) == {"embeds"}

    def it_adds_the_here_ping_and_the_allowed_mentions_gate():
        payload = build_embed_payload(Message(title="T", body="B", discord_mention="@here"))
        assert payload["content"] == "@here"
        assert payload["allowed_mentions"] == {"parse": ["everyone"]}

    def it_adds_the_everyone_ping_and_the_allowed_mentions_gate():
        payload = build_embed_payload(Message(title="T", body="B", discord_mention="@everyone"))
        assert payload["content"] == "@everyone"
        assert payload["allowed_mentions"] == {"parse": ["everyone"]}


@pytest.mark.django_db
def describe_emit_discord_mention():
    def it_stamps_the_mention_on_the_central_broadcast(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit("site_announcement", context={}, title="T", body="B", period="m:central", discord_mention="@everyone")
        assert _posted_messages(mock_post)[0].discord_mention == "@everyone"

    def it_stamps_the_mention_on_the_chosen_webhook_post():
        guild = GuildFactory()
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit(
                "guild_announcement",
                context={"guild": guild, "discord_broadcast_webhook": _CHOSEN},
                title="T",
                body="B",
                period="m:chosen",
                discord_mention="@here",
            )
        assert _posted_urls(mock_post) == [_CHOSEN]
        assert _posted_messages(mock_post)[0].discord_mention == "@here"

    def it_leaves_the_message_unstamped_when_no_mention_is_passed(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit("site_announcement", context={}, title="T", body="B", period="m:plain")
        assert _posted_messages(mock_post)[0].discord_mention == ""


@pytest.mark.django_db
def describe_site_wide_chosen_webhook_without_a_guild():
    def it_posts_to_the_chosen_webhook_even_without_a_guild(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit(
                "site_announcement",
                context={"discord_broadcast_webhook": _CHOSEN},
                title="T",
                body="B",
                period="site:noguild",
            )
        assert _posted_urls(mock_post) == [_CHOSEN]
        # The global/central webhook is never hit — the picker replaced it, guild or not.
        assert _CENTRAL not in _posted_urls(mock_post)

    def it_records_a_broadcast_chosen_ledger_slot(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        with patch("core.events.discord.post_embed", return_value=True):
            emit(
                "site_announcement",
                context={"discord_broadcast_webhook": _CHOSEN},
                title="T",
                body="B",
                period="site:ledger",
            )
        assert EventDelivery.objects.filter(
            event_key="site_announcement", target_ref="broadcast:chosen", channel="discord"
        ).exists()

    def it_posts_nothing_when_the_chosen_webhook_is_blank(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _CENTRAL
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            emit(
                "site_announcement",
                context={"discord_broadcast_webhook": ""},
                title="T",
                body="B",
                period="site:blank",
            )
        assert mock_post.call_count == 0

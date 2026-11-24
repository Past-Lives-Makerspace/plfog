"""Discord broadcast channel — webhook posting + event→webhook routing (§2.4).

HTTP is exercised against a real ``httpx`` call, mocked with ``respx`` (the event
spine's outbound HTTP test stack). Covers: a successful post, the blank-webhook
no-op, a swallowed HTTP failure, the embed payload shape, and event→webhook
routing (override vs. global default).
"""

from __future__ import annotations

import json

import httpx
import respx

from core.events import discord
from core.events.channels import Message

_WEBHOOK = "https://discord.com/api/webhooks/123/abc"
_OVERRIDE = "https://discord.com/api/webhooks/999/guildstaff"


def _message(**kw) -> Message:
    base = dict(title="Release 0.19", body="What's new", url="/changelog/", trigger_kind="release_published")
    base.update(kw)
    return Message(**base)


def describe_build_embed_payload():
    def it_wraps_one_embed_with_title_body_color():
        payload = discord.build_embed_payload(_message())
        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert embed["title"] == "Release 0.19"
        assert embed["description"] == "What's new"
        assert embed["color"] == discord._EMBED_COLOR

    def it_includes_url_when_present():
        embed = discord.build_embed_payload(_message(url="/c/"))["embeds"][0]
        assert embed["url"] == "/c/"

    def it_omits_url_when_blank():
        embed = discord.build_embed_payload(_message(url=""))["embeds"][0]
        assert "url" not in embed

    def it_keeps_a_plain_message_payload_byte_identical():
        # Every event but the guild welcome sets none of the #1326 fields: its payload is exactly
        # what it was before them, key for key.
        expected = {
            "embeds": [
                {
                    "title": "Release 0.19",
                    "description": "What's new",
                    "color": discord._EMBED_COLOR,
                    "url": "/changelog/",
                }
            ]
        }
        assert json.dumps(discord.build_embed_payload(_message())) == json.dumps(expected)

    def describe_guild_welcome_dressing():
        def it_uses_the_message_color_instead_of_blurple():
            embed = discord.build_embed_payload(_message(discord_color=0xE4572E))["embeds"][0]
            assert embed["color"] == 0xE4572E

        def it_keeps_a_zero_color_as_black_not_blurple():
            embed = discord.build_embed_payload(_message(discord_color=0))["embeds"][0]
            assert embed["color"] == 0

        def it_adds_the_thumbnail():
            embed = discord.build_embed_payload(_message(discord_thumbnail_url="https://x.test/logo.png"))["embeds"][0]
            assert embed["thumbnail"] == {"url": "https://x.test/logo.png"}

        def it_adds_the_author_with_its_icon():
            embed = discord.build_embed_payload(
                _message(discord_author_name="Jay Gifford", discord_author_icon_url="https://x.test/jay.png")
            )["embeds"][0]
            assert embed["author"] == {"name": "Jay Gifford", "icon_url": "https://x.test/jay.png"}

        def it_adds_the_author_alone_without_an_icon():
            embed = discord.build_embed_payload(_message(discord_author_name="Jay Gifford"))["embeds"][0]
            assert embed["author"] == {"name": "Jay Gifford"}

        def it_drops_an_author_icon_with_no_name():
            embed = discord.build_embed_payload(_message(discord_author_icon_url="https://x.test/jay.png"))["embeds"][0]
            assert "author" not in embed

        def it_adds_the_footer_with_its_icon():
            embed = discord.build_embed_payload(
                _message(
                    discord_footer_text="Metalworkers Guild · New member",
                    discord_footer_icon_url="https://x.test/l.png",
                )
            )["embeds"][0]
            assert embed["footer"] == {"text": "Metalworkers Guild · New member", "icon_url": "https://x.test/l.png"}

        def it_adds_the_footer_alone_without_an_icon():
            embed = discord.build_embed_payload(_message(discord_footer_text="Writers Guild · New member"))["embeds"][0]
            assert embed["footer"] == {"text": "Writers Guild · New member"}

        def it_drops_a_footer_icon_with_no_text():
            embed = discord.build_embed_payload(_message(discord_footer_icon_url="https://x.test/l.png"))["embeds"][0]
            assert "footer" not in embed


def describe_embed_color_from_hex():
    def it_reads_a_guild_hex_color():
        assert discord.embed_color_from_hex("#E4572E") == 0xE4572E

    def it_reads_lowercase_and_surrounding_space():
        assert discord.embed_color_from_hex("  #aa0000 ") == 0xAA0000

    def it_gives_none_for_a_blank_color():
        assert discord.embed_color_from_hex("") is None

    def it_gives_none_for_a_malformed_color():
        for bad in ("E4572E", "#E457", "#GGGGGG", "#E4572E0", "red"):
            assert discord.embed_color_from_hex(bad) is None


def describe_webhook_for_event():
    def it_returns_the_global_webhook_by_default(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _WEBHOOK
        discord.EVENT_WEBHOOK_OVERRIDES.clear()
        assert discord.webhook_for_event("release_published") == _WEBHOOK

    def it_returns_a_per_event_override_when_configured(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = _WEBHOOK
        discord.EVENT_WEBHOOK_OVERRIDES["class_review_requested"] = _OVERRIDE
        try:
            assert discord.webhook_for_event("class_review_requested") == _OVERRIDE
        finally:
            discord.EVENT_WEBHOOK_OVERRIDES.clear()

    def it_returns_blank_when_no_webhook_configured(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = ""
        discord.EVENT_WEBHOOK_OVERRIDES.clear()
        assert discord.webhook_for_event("release_published") == ""


def describe_post_embed():
    @respx.mock
    def it_posts_the_embed_and_returns_true_on_2xx():
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        result = discord.post_embed(_WEBHOOK, _message())
        assert result is True
        assert route.called
        sent = route.calls.last.request
        body = sent.read().decode()
        assert "Release 0.19" in body
        assert "What's new" in body

    def it_is_a_noop_when_the_webhook_is_blank():
        # No respx route registered — a real post would error; blank short-circuits.
        assert discord.post_embed("", _message()) is False

    @respx.mock
    def it_swallows_a_non_2xx_response_and_returns_false():
        respx.post(_WEBHOOK).mock(return_value=httpx.Response(500, text="boom"))
        assert discord.post_embed(_WEBHOOK, _message()) is False

    @respx.mock
    def it_swallows_a_network_error_and_returns_false():
        respx.post(_WEBHOOK).mock(side_effect=httpx.ConnectError("no route"))
        assert discord.post_embed(_WEBHOOK, _message()) is False


def describe_global_webhook():
    def it_strips_whitespace(settings):
        settings.DISCORD_NOTIFY_WEBHOOK_URL = f"  {_WEBHOOK}  "
        assert discord.global_webhook() == _WEBHOOK

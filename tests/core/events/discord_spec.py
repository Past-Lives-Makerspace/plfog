"""Discord broadcast channel — webhook posting + event→webhook routing (§2.4).

HTTP is exercised against a real ``httpx`` call, mocked with ``respx`` (the event
spine's outbound HTTP test stack). Covers: a successful post, the blank-webhook
no-op, a swallowed HTTP failure, the embed payload shape, and event→webhook
routing (override vs. global default).
"""

from __future__ import annotations

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

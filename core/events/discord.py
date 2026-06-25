"""Discord webhook posting + event→webhook routing (design §2.4, Decision 9).

Discord is the spine's one **per-event broadcast** channel: an event posts a
single embed to a configured webhook, not one message per recipient. This module
owns two concerns kept deliberately separate:

1. **Routing** — :func:`webhook_for_event` maps an event key to the webhook it
   should post to. For now every event routes to the **global** webhook
   (``settings.DISCORD_NOTIFY_WEBHOOK_URL``); :data:`EVENT_WEBHOOK_OVERRIDES` is the
   override structure a Phase-3 admin surface will populate (e.g.
   ``"class_review_requested" → a guild's staff channel``). Keeping it as data now
   means per-event routing is configurable later with zero adapter changes.
2. **Posting** — :func:`post_embed` builds the Discord embed payload from a rendered
   message and POSTs it to a webhook. Best-effort: it logs and returns ``False`` on
   any failure and never raises (the spine must keep fanning out).

Per the project's "disabled when blank" idiom (see ``MailchimpClient`` /
``SimplybookClient``), a blank global webhook makes the whole channel a no-op.

HTTP uses ``httpx`` (mocked with ``respx`` in tests) — the event spine's outbound
HTTP layer, distinct from the legacy ``requests``-based integration clients.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from django.conf import settings

if TYPE_CHECKING:
    from core.events.channels import Message

logger = logging.getLogger(__name__)

# Discord's blurple, the conventional accent for bot embeds.
_EMBED_COLOR = 0x5865F2
_DEFAULT_TIMEOUT_SECONDS = 5.0

# Per-event routing overrides: event_key → webhook URL. EMPTY by default — every
# event falls back to the global webhook. A Phase-3 admin surface populates this
# (today it's a code-level structure so the routing seam exists and is tested).
# Kept as a plain dict (not a setting) so it can become DB-backed later without a
# call-site change: callers only ever ask :func:`webhook_for_event`.
EVENT_WEBHOOK_OVERRIDES: dict[str, str] = {}


def global_webhook() -> str:
    """The site-wide default Discord webhook URL (blank = channel disabled)."""
    return (getattr(settings, "DISCORD_NOTIFY_WEBHOOK_URL", "") or "").strip()


def webhook_for_event(event_key: str) -> str:
    """Resolve the webhook ``event_key`` should broadcast to.

    Returns the per-event override when one is configured, else the global
    webhook. A blank result means Discord is disabled for this event (the adapter
    treats it as a no-op).

    Args:
        event_key: The :class:`core.events.registry.EventType` key being emitted.

    Returns:
        The webhook URL, or ``""`` when none is configured (disabled).
    """
    override = EVENT_WEBHOOK_OVERRIDES.get(event_key, "").strip()
    if override:
        return override
    return global_webhook()


def build_embed_payload(message: Message) -> dict[str, object]:
    """Build the Discord webhook JSON payload from a rendered message.

    Discord's webhook API takes ``{"embeds": [<embed>]}``. The embed carries the
    event's ``title`` and ``body`` (as the embed description); ``url`` makes the
    title a clickable link when present.

    Args:
        message: The rendered :class:`core.events.channels.Message`.

    Returns:
        A JSON-serialisable dict ready to POST to a Discord webhook.
    """
    embed: dict[str, object] = {
        "title": message.title,
        "description": message.body,
        "color": _EMBED_COLOR,
    }
    if message.url:
        embed["url"] = message.url
    return {"embeds": [embed]}


def post_embed(webhook_url: str, message: Message) -> bool:
    """POST one embed to a Discord webhook. Best-effort — never raises.

    Args:
        webhook_url: The target webhook (already resolved via
            :func:`webhook_for_event`). A blank value is a no-op returning
            ``False`` (disabled).
        message: The rendered message to broadcast.

    Returns:
        ``True`` on a 2xx response, ``False`` on a blank webhook, a network error,
        or any non-2xx status. Failures are logged (never the webhook value).
    """
    if not webhook_url:
        return False
    payload = build_embed_payload(message)
    try:
        response = httpx.post(webhook_url, json=payload, timeout=_DEFAULT_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("Discord webhook post failed (network error): %s", exc)
        return False
    if response.is_success:
        return True
    logger.warning(
        "Discord webhook post failed: %s %s",
        response.status_code,
        response.text[:300],
    )
    return False

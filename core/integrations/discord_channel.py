"""Post (and edit) channel messages (embeds) as the FOG bot.

The thin sibling of :mod:`core.integrations.discord_events`: the same bot REST auth
(``bot_token`` / ``_auth_headers`` / ``API_BASE`` from :mod:`core.events.discord_dm`), the
same fail-loudly error wrapping, and the same bounded single retry on a 429 — always on
here, because the only callers are background jobs (the weekly calendar digest and the
15-minute new-event announcer), never an interactive request.

Callers gate themselves on ``SiteConfiguration.discord_calendar_posts_enabled`` +
``discord_calendar_channel_id`` before calling; this module just posts and raises
:class:`DiscordChannelError` on any failure so a scheduled run records FAILED loudly.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from core.events.discord_dm import API_BASE, _auth_headers, bot_token

_TIMEOUT_SECONDS = 5.0
_RATE_LIMIT_MAX_WAIT_SECONDS = 15.0
# Discord allows at most 10 embeds per message; callers chunk above that.
MAX_EMBEDS_PER_MESSAGE = 10


class DiscordChannelError(Exception):
    """A Discord channel-message API call failed (transport error or non-2xx response)."""


def post_channel_message(channel_id: str, embeds: list[dict[str, Any]]) -> None:
    """POST one message carrying ``embeds`` to the channel. Raises :class:`DiscordChannelError`
    on any failure — these run inside scheduled jobs, which must fail loudly, not silently
    drop a post.

    A 429 is retried once after Discord's advertised ``Retry-After`` (bounded); a second
    429, or an unusable/too-long ``Retry-After``, raises.
    """
    _send_embeds("POST", f"{API_BASE}/channels/{channel_id}/messages", embeds)


def edit_channel_message(channel_id: str, message_id: str, embeds: list[dict[str, Any]]) -> None:
    """PATCH an existing bot message's embeds in place — the message id (and its pin) survive.

    Used for the FOG-managed #important-info pinned post. Same fail-loudly contract and
    bounded single 429 retry as :func:`post_channel_message`; raises
    :class:`DiscordChannelError` on any failure so the caller can surface it to the admin.
    """
    _send_embeds("PATCH", f"{API_BASE}/channels/{channel_id}/messages/{message_id}", embeds)


def fetch_channel_name_from_webhook(webhook_url: str) -> str:
    """Best-effort ``#channel-name`` of the channel a webhook posts to (``""`` on any failure).

    Two hops: GET the webhook URL (its own token authorizes it, no bot auth needed) to learn the
    ``channel_id``, then GET that channel as the bot to read its ``name``. Unlike the posting
    helpers above this NEVER raises — it backs a *display* label (the announcement composer's
    Discord channel picker shows the real ``#channel`` instead of "Our Guild Channel"), so a
    transient Discord hiccup must degrade to a generic label, not 500 the compose page. Requires
    the bot token; returns ``""`` when it is unset.
    """
    if not webhook_url or not bot_token():
        return ""
    try:
        hook = httpx.get(webhook_url, timeout=_TIMEOUT_SECONDS)
        hook.raise_for_status()
        channel_id = (hook.json().get("channel_id") or "").strip()
        if not channel_id:
            return ""
        channel = httpx.get(f"{API_BASE}/channels/{channel_id}", headers=_auth_headers(), timeout=_TIMEOUT_SECONDS)
        channel.raise_for_status()
        name = (channel.json().get("name") or "").strip()
    except (httpx.HTTPError, ValueError, AttributeError):
        return ""
    return f"#{name}" if name else ""


def _send_embeds(method: str, url: str, embeds: list[dict[str, Any]]) -> None:
    """Deliver ``embeds`` via one bot REST call, with the shared cap check + single 429 retry."""
    if len(embeds) > MAX_EMBEDS_PER_MESSAGE:
        raise DiscordChannelError(f"Discord allows {MAX_EMBEDS_PER_MESSAGE} embeds per message, got {len(embeds)}.")
    response = _send(method, url, embeds)
    if response.status_code == 429:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None and retry_after <= _RATE_LIMIT_MAX_WAIT_SECONDS:
            time.sleep(retry_after)
            response = _send(method, url, embeds)
    if not response.is_success:
        raise DiscordChannelError(f"Discord API {response.status_code}: {response.text[:300]}")


def _send(method: str, url: str, embeds: list[dict[str, Any]]) -> httpx.Response:
    """One raw REST call; only transport failures raise (as :class:`DiscordChannelError`)."""
    try:
        return httpx.request(
            method,
            url,
            json={"embeds": embeds},
            headers=_auth_headers(),
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise DiscordChannelError(str(exc)) from exc


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Seconds Discord asks us to wait on a 429, or ``None`` when absent/unparseable."""
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None

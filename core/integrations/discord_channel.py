"""Post messages (embeds) to a Discord channel as the FOG bot.

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

from core.events.discord_dm import API_BASE, _auth_headers

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
    if len(embeds) > MAX_EMBEDS_PER_MESSAGE:
        raise DiscordChannelError(f"Discord allows {MAX_EMBEDS_PER_MESSAGE} embeds per message, got {len(embeds)}.")
    response = _send(channel_id, embeds)
    if response.status_code == 429:
        retry_after = _retry_after_seconds(response)
        if retry_after is not None and retry_after <= _RATE_LIMIT_MAX_WAIT_SECONDS:
            time.sleep(retry_after)
            response = _send(channel_id, embeds)
    if not response.is_success:
        raise DiscordChannelError(f"Discord API {response.status_code}: {response.text[:300]}")


def _send(channel_id: str, embeds: list[dict[str, Any]]) -> httpx.Response:
    """One raw REST call; only transport failures raise (as :class:`DiscordChannelError`)."""
    try:
        return httpx.post(
            f"{API_BASE}/channels/{channel_id}/messages",
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

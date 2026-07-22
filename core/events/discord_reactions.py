"""Inbound Discord reader — who reacted with a given emoji on the role message.

The *inbound* half of the two-way guild sync reads **reactions** (never roles), so it is
structurally disjoint from the outbound role writer (:mod:`core.events.discord_roles`).

The removal guardrail lives here. :func:`fetch_reactors` pages Discord's reactions
endpoint and reports whether it read the reaction list **completely** (every page a 2xx,
pagination run to the end). Adds are always safe from a partial read — everyone we saw
genuinely reacted — but **removals are gated on ``complete=True``**: a missing id on an
incomplete fetch could mean "un-reacted" OR "we never fetched that page", so the reconcile
never deletes from a truncated read. Best-effort throughout: any non-2xx, network error,
or exhausted rate-limit stops paging and returns ``complete=False`` with whatever was
gathered. HTTP goes through ``httpx`` (mocked with ``respx`` in tests).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from core.events.discord_dm import bot_token

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_PAGE_LIMIT = 100
_MAX_RATE_LIMIT_RETRIES = 2
_MAX_RETRY_SLEEP_SECONDS = 5.0


@dataclass(frozen=True)
class ReactorPage:
    """The set of Discord user ids that reacted with an emoji, plus a completeness flag.

    Attributes:
        user_ids: Discord snowflakes (as strings) that reacted with this emoji.
        complete: ``True`` ONLY when every page fetched with a 2xx and pagination ran to
            the end. ``False`` on any non-2xx / network error / exhausted rate-limit —
            the reconcile then skips removals for the affected guild.
    """

    user_ids: set[str]
    complete: bool


def fetch_reactors(channel_id: str, message_id: str, emoji: str) -> ReactorPage:
    """Return every Discord user id that reacted with ``emoji`` on the role message.

    Pages ``GET /channels/{ch}/messages/{msg}/reactions/{emoji}?limit=100&after=…`` until
    a page returns fewer than 100 users. On any failure mid-paging, stops and returns
    ``complete=False`` with what was gathered so far (safe to *add*, never to *remove*).
    429s honor ``Retry-After`` up to a small cap and a couple of retries, then bail as
    incomplete — the next 15-minute tick retries.

    ``emoji`` is a unicode character (e.g. ``🔥``) or a custom emoji as ``name:id``; it is
    URL-encoded here.
    """
    user_ids: set[str] = set()
    if not bot_token() or not channel_id or not message_id or not emoji:
        return ReactorPage(user_ids=user_ids, complete=False)

    encoded_emoji = quote(emoji, safe="")
    base_url = f"{API_BASE}/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}"
    headers = {"Authorization": f"Bot {bot_token()}"}
    after: str | None = None

    while True:
        page = _fetch_reaction_page(base_url, headers, after)
        if page is None:  # network error / non-2xx / rate-limit exhausted → incomplete.
            return ReactorPage(user_ids=user_ids, complete=False)
        for user in page:
            user_id = str(user.get("id", "")).strip()
            # Bot accounts (e.g. a bot seeding the reaction emojis) can never be
            # members — skip them so downstream consumers only ever see humans.
            if user_id and not user.get("bot", False):
                user_ids.add(user_id)
        if len(page) < _PAGE_LIMIT:
            return ReactorPage(user_ids=user_ids, complete=True)
        after = str(page[-1].get("id", ""))
        if not after:
            # A full page with no usable last-id can't be paged further — stop safely.
            return ReactorPage(user_ids=user_ids, complete=False)


def _fetch_reaction_page(base_url: str, headers: dict[str, str], after: str | None) -> list | None:
    """Fetch one page of reactors (honoring rate-limit retries), or ``None`` on any failure.

    Returns the raw list of Discord user objects on a 2xx, or ``None`` on a network error,
    a non-2xx, or an exhausted 429 retry budget — the caller treats ``None`` as "stop
    paging, mark incomplete".
    """
    params: dict[str, str | int] = {"limit": _PAGE_LIMIT}
    if after is not None:
        params["after"] = after
    retries = 0
    while True:
        try:
            response = httpx.get(base_url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            logger.warning("Discord reactions fetch failed (network error): %s", exc)
            return None
        if response.status_code == 429:
            if retries >= _MAX_RATE_LIMIT_RETRIES:
                logger.warning("Discord reactions fetch rate-limited (giving up this tick).")
                return None
            retries += 1
            time.sleep(min(_retry_after_seconds(response), _MAX_RETRY_SLEEP_SECONDS))
            continue
        if not response.is_success:
            logger.warning("Discord reactions fetch failed: %s %s", response.status_code, response.text[:300])
            return None
        return response.json()


def _retry_after_seconds(response: httpx.Response) -> float:
    """Parse the ``Retry-After`` header (seconds) into a float, defaulting to a small wait."""
    raw = response.headers.get("Retry-After", "")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0

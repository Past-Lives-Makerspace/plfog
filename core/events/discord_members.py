"""Inbound Discord reader — the server's member list, for the new-joiner welcome DM.

Modeled on :mod:`core.events.discord_reactions`: a best-effort, paginated reader that
reports whether it read the list **completely**. An incomplete fetch is SAFE to act on —
everyone seen genuinely is a server member, and the once-only welcome ledger dedupes —
so the caller welcomes whoever was seen and the next cron tick (or the manual sweep)
catches the rest. Any non-2xx, network error, or exhausted rate-limit stops paging and
returns ``complete=False`` with whatever was gathered. HTTP goes through ``httpx``
(mocked with ``respx`` in tests).

Payload gotcha: Discord **omits** ``user.bot`` for humans (it is not ``false``, it is
absent) — a missing flag means "not a bot". Requires the bot's Server Members Intent
(Discord developer portal → Bot → Privileged Gateway Intents); if revoked, Discord
returns 403 and the reader reports ``complete=False`` with zero members.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from core.events.discord_dm import bot_token
from core.events.discord_reactions import _retry_after_seconds

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
_DEFAULT_TIMEOUT_SECONDS = 5.0
_PAGE_LIMIT = 1000
_MAX_RATE_LIMIT_RETRIES = 2
_MAX_RETRY_SLEEP_SECONDS = 5.0


@dataclass(frozen=True)
class GuildMemberInfo:
    """One server member as seen by ``GET /guilds/{id}/members``.

    Attributes:
        user_id: The member's Discord snowflake (as a string).
        bot: ``True`` for bot accounts. Discord omits the flag for humans, so an
            absent flag parses to ``False``.
        joined_at: When they joined the server (tz-aware), or ``None`` when the
            timestamp is absent or unparseable — the caller skips it (no window
            match, no crash).
    """

    user_id: str
    bot: bool
    joined_at: datetime | None


@dataclass(frozen=True)
class GuildMemberPage:
    """The server members gathered, plus a completeness flag.

    Attributes:
        members: Every member parsed from the pages that were read.
        complete: ``True`` ONLY when every page fetched with a 2xx and pagination ran
            to the end. ``False`` on any non-2xx / network error / exhausted
            rate-limit — safe to act on (the ledger dedupes), but a warning-worthy
            signal: a persistent 403 usually means the Server Members Intent is off.
    """

    members: list[GuildMemberInfo]
    complete: bool


def fetch_guild_members(server_id: str) -> GuildMemberPage:
    """Return every member of the Discord server, paging until the list ends.

    Pages ``GET /guilds/{server_id}/members?limit=1000&after=…`` until a page returns
    fewer than 1000 entries. On any failure mid-paging, stops and returns
    ``complete=False`` with what was gathered so far. 429s honor ``Retry-After`` up to
    a small cap and a couple of retries, then bail as incomplete — the next
    15-minute tick retries.
    """
    members: list[GuildMemberInfo] = []
    if not bot_token() or not server_id:
        return GuildMemberPage(members=members, complete=False)

    base_url = f"{API_BASE}/guilds/{server_id}/members"
    headers = {"Authorization": f"Bot {bot_token()}"}
    after: str | None = None

    while True:
        page = _fetch_member_page(base_url, headers, after)
        if page is None:  # network error / non-2xx / rate-limit exhausted → incomplete.
            return GuildMemberPage(members=members, complete=False)
        for entry in page:
            info = _parse_member(entry)
            if info is not None:
                members.append(info)
        if len(page) < _PAGE_LIMIT:
            return GuildMemberPage(members=members, complete=True)
        last = _parse_member(page[-1])
        if last is None:
            # A full page with no usable last user id can't be paged further — stop safely.
            return GuildMemberPage(members=members, complete=False)
        after = last.user_id


def _parse_member(entry: dict) -> GuildMemberInfo | None:
    """Parse one raw member object, or ``None`` when it carries no user id.

    Discord omits ``user.bot`` for humans — an absent flag means "not a bot".
    """
    user = entry.get("user") or {}
    user_id = str(user.get("id", "")).strip()
    if not user_id:
        return None
    return GuildMemberInfo(
        user_id=user_id,
        bot=bool(user.get("bot", False)),
        joined_at=_parse_joined_at(entry.get("joined_at")),
    )


def _parse_joined_at(raw: object) -> datetime | None:
    """Parse Discord's ISO8601 ``joined_at``, or ``None`` when absent/garbled."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _fetch_member_page(base_url: str, headers: dict[str, str], after: str | None) -> list | None:
    """Fetch one page of members (honoring rate-limit retries), or ``None`` on any failure.

    Returns the raw list of Discord guild-member objects on a 2xx, or ``None`` on a
    network error, a non-2xx, or an exhausted 429 retry budget — the caller treats
    ``None`` as "stop paging, mark incomplete".
    """
    params: dict[str, str | int] = {"limit": _PAGE_LIMIT}
    if after is not None:
        params["after"] = after
    retries = 0
    while True:
        try:
            response = httpx.get(base_url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            logger.warning("Discord guild-members fetch failed (network error): %s", exc)
            return None
        if response.status_code == 429:
            if retries >= _MAX_RATE_LIMIT_RETRIES:
                logger.warning("Discord guild-members fetch rate-limited (giving up this tick).")
                return None
            retries += 1
            time.sleep(min(_retry_after_seconds(response), _MAX_RETRY_SLEEP_SECONDS))
            continue
        if not response.is_success:
            logger.warning("Discord guild-members fetch failed: %s %s", response.status_code, response.text[:300])
            return None
        return response.json()

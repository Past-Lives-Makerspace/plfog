"""A member's Discord name and picture, for an announcement posted "as me" (#730).

A channel webhook may post under any name and picture it is handed (the payload's
``username`` and ``avatar_url``); Discord still marks the post APP, so it is never posted from
the member's own account. The name and picture come from the member's verified Discord account
(``Member.discord_user_id``, set by the OAuth link) through ``GET /users/{id}`` as the bot. No
avatar is stored here: the picture is Discord's CDN URL for the avatar hash the lookup returns.

:func:`fetch_identity` is the live lookup the send makes. :func:`cached_identity` is the one the
composer's Discord Preview uses, kept for a few minutes so a preview refresh is not a Discord
call. Both are best-effort and return ``None`` on any failure, so the post goes out under the
webhook's own name instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

import httpx
from django.core.cache import cache

from core.events.discord_dm import API_BASE, _auth_headers, bot_disabled

logger = logging.getLogger(__name__)

# A send waits on this lookup, so it is short: a slow Discord costs the post its name, not its delivery.
_LOOKUP_TIMEOUT_SECONDS = 3.0
_CDN_AVATAR_URL = "https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"
# Discord refuses a webhook name outside 1 to 80 characters, one containing either of these words
# (in any case), or one breaking its username rules; the post itself would then fail.
WEBHOOK_USERNAME_MAX_LENGTH = 80
_FORBIDDEN_NAME_WORDS = ("discord", "clyde")
_FORBIDDEN_NAME_MARKS = ("@", "#", ":", "```")
_FORBIDDEN_NAMES = ("everyone", "here")
_CACHE_KEY = "discord-identity:{user_id}"
_CACHE_SECONDS = 600
# A failed lookup is remembered briefly too, so a preview refresh while Discord is down is not a call.
_CACHE_FAILURE_SECONDS = 60
_MISSING = object()


@dataclass(frozen=True)
class DiscordIdentity:
    """The name and picture a webhook post shows: ``avatar_url`` is ``""`` for a default avatar."""

    username: str
    avatar_url: str = ""


def webhook_username_allowed(name: str) -> bool:
    """Whether Discord accepts ``name`` as a webhook post's ``username``.

    Args:
        name: The candidate name, already stripped.

    Returns:
        ``False`` when it is empty, longer than 80 characters, contains "discord" or "clyde" in any
        case, carries ``@``, ``#``, ``:`` or a code fence, or is exactly "everyone" or "here".
    """
    if not 1 <= len(name) <= WEBHOOK_USERNAME_MAX_LENGTH:
        return False
    lowered = name.lower()
    if any(word in lowered for word in _FORBIDDEN_NAME_WORDS):
        return False
    if any(mark in name for mark in _FORBIDDEN_NAME_MARKS):
        return False
    return lowered not in _FORBIDDEN_NAMES


def identity_from_user_json(user_id: str, data: dict[str, object]) -> DiscordIdentity | None:
    """The identity a ``GET /users/{id}`` answer gives, or ``None`` when its name cannot be used.

    The display name (``global_name``) wins over the account name (``username``). A user with no
    avatar hash gets no ``avatar_url``, so Discord shows the webhook's own picture.

    Args:
        user_id: The Discord user id the answer is for (part of the avatar URL).
        data: The decoded user object.

    Returns:
        The identity, or ``None`` when neither name passes :func:`webhook_username_allowed`.
    """
    name = str(data.get("global_name") or data.get("username") or "").strip()
    if not webhook_username_allowed(name):
        return None
    avatar_hash = str(data.get("avatar") or "").strip()
    avatar_url = _CDN_AVATAR_URL.format(user_id=user_id, avatar_hash=avatar_hash) if avatar_hash else ""
    return DiscordIdentity(username=name, avatar_url=avatar_url)


def fetch_identity(discord_user_id: str) -> DiscordIdentity | None:
    """Look up a member's Discord name and picture as the bot, now.

    Best-effort: logs a warning and returns ``None`` on a blank bot token (unset, or staging), a
    network error, a non-2xx answer, an unreadable body, or a name Discord would refuse on a webhook.

    Args:
        discord_user_id: The member's verified Discord user id.

    Returns:
        The identity to post under, or ``None`` to post under the webhook's own name.
    """
    if not discord_user_id or bot_disabled("sender name lookup"):
        return None
    try:
        response = httpx.get(
            f"{API_BASE}/users/{discord_user_id}", headers=_auth_headers(), timeout=_LOOKUP_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord sender name lookup failed (network error) for %s: %s", discord_user_id, exc)
        return None
    if not response.is_success:
        logger.warning(
            "Discord sender name lookup failed for %s: %s %s",
            discord_user_id,
            response.status_code,
            response.text[:300],
        )
        return None
    try:
        data = response.json()
    except ValueError:
        logger.warning("Discord sender name lookup for %s answered something that is not JSON.", discord_user_id)
        return None
    if not isinstance(data, dict):
        logger.warning("Discord sender name lookup for %s answered something that is not a user.", discord_user_id)
        return None
    identity = identity_from_user_json(discord_user_id, data)
    if identity is None:
        logger.warning(
            "Discord sender name for %s cannot be a webhook name; posting under the default.", discord_user_id
        )
    return identity


def cached_identity(discord_user_id: str) -> DiscordIdentity | None:
    """:func:`fetch_identity`, remembered for ten minutes (a failure for one), for the preview.

    Args:
        discord_user_id: The member's verified Discord user id.

    Returns:
        The identity the preview shows, or ``None`` for the default name and icon.
    """
    if not discord_user_id:
        return None
    key = _CACHE_KEY.format(user_id=discord_user_id)
    hit = cache.get(key, _MISSING)
    if hit is not _MISSING:
        return cast("DiscordIdentity | None", hit)
    identity = fetch_identity(discord_user_id)
    cache.set(key, identity, _CACHE_SECONDS if identity is not None else _CACHE_FAILURE_SECONDS)
    return identity

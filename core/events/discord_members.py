"""Server membership calls through the FOG bot: removing, re-adding and listing people (#898).

Same bot-token REST idiom as :mod:`core.events.discord_roles`: the credential comes from
:func:`core.events.discord_dm.bot_token`, so a blank token (unset, or ``ENVIRONMENT=staging``)
makes every call a logged no-op. Unlike the role helpers this one reports what happened
instead of swallowing it, because the caller records each removal on the Automations page and
decides whether to retry: a 403 is a standing refusal (the bot's role sits below the person's),
a 404 means the person is already gone, and anything else (429, 5xx, a network error) raises.
"""

from __future__ import annotations

import logging
from enum import StrEnum

import httpx

from core.events.discord_dm import API_BASE, bot_disabled, bot_token

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 10.0


#: The most members ``GET /guilds/{id}/members`` returns per page.
_MEMBERS_PAGE_SIZE = 1000


class KickOutcome(StrEnum):
    """What Discord said to a removal."""

    REMOVED = "removed"
    NOT_ON_SERVER = "not_on_server"
    REFUSED = "refused"
    DISABLED = "disabled"


def kick_member(server_id: str, user_id: str, reason: str) -> KickOutcome:
    """Remove ``user_id`` from ``server_id`` (``DELETE /guilds/{id}/members/{user}``).

    Args:
        server_id: The Discord server (guild) id.
        user_id: The person's Discord id (snowflake).
        reason: Shown in the server's audit log.

    Returns:
        ``REMOVED`` on a 2xx, ``NOT_ON_SERVER`` on a 404, ``REFUSED`` on a 403, and ``DISABLED``
        when the bot token is blank (nothing is sent).

    Raises:
        httpx.HTTPStatusError: On any other non-2xx (429, 5xx), which is worth a retry.
        httpx.HTTPError: On a network error.
    """
    if bot_disabled("member removal"):
        return KickOutcome.DISABLED
    response = httpx.delete(
        f"{API_BASE}/guilds/{server_id}/members/{user_id}",
        headers={"Authorization": f"Bot {bot_token()}", "X-Audit-Log-Reason": reason},
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    )
    if response.is_success:
        return KickOutcome.REMOVED
    if response.status_code == 404:
        return KickOutcome.NOT_ON_SERVER
    if response.status_code == 403:
        logger.warning("Discord refused to remove %s: %s", user_id, response.text[:300])
        return KickOutcome.REFUSED
    raise httpx.HTTPStatusError(
        f"Discord member removal failed: {response.status_code} {response.text[:300]}",
        request=response.request,
        response=response,
    )


class JoinOutcome(StrEnum):
    """What Discord said to putting a person back on the server."""

    ADDED = "added"
    ALREADY_THERE = "already_there"
    REFUSED = "refused"
    DISABLED = "disabled"


def add_member(server_id: str, user_id: str, access_token: str) -> JoinOutcome:
    """Put ``user_id`` back on ``server_id`` with their own OAuth token (``PUT /guilds/{id}/members/{user}``).

    Discord accepts this only with a token carrying the ``guilds.join`` scope, granted when the
    person connects their Discord, so it is called from the connect flow and nowhere else.

    Returns:
        ``ADDED`` on 201, ``ALREADY_THERE`` on 204, ``REFUSED`` on a 403, ``DISABLED`` when the
        bot token is blank (nothing is sent).

    Raises:
        httpx.HTTPStatusError: On any other non-2xx.
        httpx.HTTPError: On a network error.
    """
    if bot_disabled("member re-add"):
        return JoinOutcome.DISABLED
    response = httpx.put(
        f"{API_BASE}/guilds/{server_id}/members/{user_id}",
        json={"access_token": access_token},
        headers={"Authorization": f"Bot {bot_token()}"},
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    )
    if response.status_code == 201:
        return JoinOutcome.ADDED
    if response.status_code == 204:
        return JoinOutcome.ALREADY_THERE
    if response.status_code == 403:
        logger.warning("Discord refused to re-add %s: %s", user_id, response.text[:300])
        return JoinOutcome.REFUSED
    raise httpx.HTTPStatusError(
        f"Discord member re-add failed: {response.status_code} {response.text[:300]}",
        request=response.request,
        response=response,
    )


def list_member_ids_by_username(server_id: str) -> dict[str, str]:
    """Every member of ``server_id`` as ``{lowercased username: Discord id}``, paging 1000 at a time.

    Usernames are unique on Discord and compared without case. Empty when the bot token is blank.

    Raises:
        httpx.HTTPStatusError: On a non-2xx page (a 403 means the Server Members intent is off).
        httpx.HTTPError: On a network error.
    """
    if bot_disabled("member list"):
        return {}
    found: dict[str, str] = {}
    after = "0"
    while True:
        response = httpx.get(
            f"{API_BASE}/guilds/{server_id}/members",
            params={"limit": _MEMBERS_PAGE_SIZE, "after": after},
            headers={"Authorization": f"Bot {bot_token()}"},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        page = response.json()
        for entry in page:
            user = entry["user"]
            found[str(user["username"]).lower()] = str(user["id"])
        if len(page) < _MEMBERS_PAGE_SIZE:
            return found
        after = str(page[-1]["user"]["id"])

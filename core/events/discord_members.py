"""Server membership calls through the FOG bot: removing a person from the Discord server (#898).

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

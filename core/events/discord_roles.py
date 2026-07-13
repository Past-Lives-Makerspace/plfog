"""Outbound Discord role sync — assign/remove guild roles on in-app join/leave.

Mirrors the best-effort bot-token REST idiom of :mod:`core.events.discord_dm`: a blank
token or blank id makes every call a no-op, every call logs and returns falsy on failure
and NEVER raises, and HTTP goes through ``httpx`` (mocked with ``respx`` in tests).

This is the *outbound* half of the two-way guild sync. It reads **in-app join/leave
events** and writes Discord **roles** — it never reads reactions, so it is structurally
disjoint from the inbound reconcile (:mod:`membership.discord_sync`) and the two can
never fight (spec §5.6). :func:`on_membership_changed` is the single entry point the
join/leave views call, and it is best-effort so a Discord outage can never fail the
in-app join/leave the member just performed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from core.events.discord_dm import bot_token

if TYPE_CHECKING:
    from membership.models import Guild, Member

logger = logging.getLogger(__name__)

_API_BASE = "https://discord.com/api/v10"
_DEFAULT_TIMEOUT_SECONDS = 5.0


def _auth_headers() -> dict[str, str]:
    """The ``Authorization: Bot <token>`` header Discord's REST API expects."""
    return {"Authorization": f"Bot {bot_token()}"}


def _role_request(method: str, server_id: str, user_id: str, role_id: str) -> bool:
    """Issue one PUT/DELETE against a member's role. Best-effort — never raises.

    Returns ``True`` only on a 2xx. Blank token / blank id → no-op ``False``. A 404
    (member left the server) and a 403 (bot missing Manage-Roles, or its role sits below
    the target role) are treated as benign — logged, not raised.
    """
    if not bot_token() or not server_id or not user_id or not role_id:
        return False
    url = f"{_API_BASE}/guilds/{server_id}/members/{user_id}/roles/{role_id}"
    try:
        response = httpx.request(method, url, headers=_auth_headers(), timeout=_DEFAULT_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("Discord role %s failed (network error): %s", method, exc)
        return False
    if response.is_success:
        return True
    if response.status_code in (403, 404):
        logger.warning(
            "Discord role %s benign %s (member left, or bot role too low): role=%s",
            method,
            response.status_code,
            role_id,
        )
        return False
    logger.warning("Discord role %s failed: %s %s", method, response.status_code, response.text[:300])
    return False


def assign_role(server_id: str, user_id: str, role_id: str) -> bool:
    """Give ``user_id`` the ``role_id`` on ``server_id`` (``PUT``). Best-effort."""
    return _role_request("PUT", server_id, user_id, role_id)


def remove_role(server_id: str, user_id: str, role_id: str) -> bool:
    """Take the ``role_id`` off ``user_id`` on ``server_id`` (``DELETE``). Best-effort."""
    return _role_request("DELETE", server_id, user_id, role_id)


def on_membership_changed(guild: Guild, member: Member, *, joined: bool) -> None:
    """Mirror an in-app guild join/leave onto Discord as a role change. Best-effort.

    No-ops unless the member has a linked Discord account, the guild has one or more
    ``discord_role_ids``, and the server id is configured. Assigns (join) or removes
    (leave) **every** role id in ``guild.discord_role_ids`` — the two Glass roles are
    kept in lockstep — each best-effort and independent.

    NEVER raises: it runs *after* the DB write in the join/leave view, so a Discord
    outage, a 403/404, or even a config-load hiccup can never fail the in-app action.
    Only ever called from the in-app join/leave views (never inbound), keeping outbound
    disjoint from inbound.
    """
    try:
        from core.models import SiteConfiguration

        if not member.discord_is_linked:
            return
        role_ids = list(guild.discord_role_ids or [])
        if not role_ids:
            return
        server_id = (SiteConfiguration.load().discord_server_id or "").strip()
        if not server_id:
            return
        user_id = (member.discord_user_id or "").strip()
        for role_id in role_ids:
            if joined:
                assign_role(server_id, user_id, str(role_id))
            else:
                remove_role(server_id, user_id, str(role_id))
    except Exception as exc:  # noqa: BLE001 — outbound sync must never break the in-app join/leave.
        logger.warning("Discord role sync skipped (unexpected error): %s", exc)

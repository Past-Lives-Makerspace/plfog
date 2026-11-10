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
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import httpx

from core.events.discord_dm import bot_disabled, bot_token

if TYPE_CHECKING:
    from membership.models import Guild, Member

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
_DEFAULT_TIMEOUT_SECONDS = 5.0


def _auth_headers() -> dict[str, str]:
    """The ``Authorization: Bot <token>`` header Discord's REST API expects."""
    return {"Authorization": f"Bot {bot_token()}"}


#: The longest a 429's ``Retry-After`` is waited out before the one retry.
_MAX_RETRY_AFTER_SECONDS = 10.0


def _sleep(seconds: float) -> None:
    """Wait before retrying a rate limited call; a seam so specs need not wait."""
    time.sleep(seconds)


def _retry_after(response: httpx.Response) -> float:
    """Seconds Discord asked us to wait (header, else JSON ``retry_after``), capped, at least 0."""
    raw: object = response.headers.get("Retry-After")
    if raw is None:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raw = body.get("retry_after", 1) if isinstance(body, dict) else 1
    try:
        seconds = float(str(raw))
    except ValueError:
        seconds = 1.0
    return max(0.0, min(seconds, _MAX_RETRY_AFTER_SECONDS))


def _request(method: str, url: str, params: dict[str, str | int] | None) -> httpx.Response | None:
    """One bot request; None on a network error (logged)."""
    try:
        return httpx.request(method, url, params=params, headers=_auth_headers(), timeout=_DEFAULT_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("Discord %s failed (network error): %s", method, exc)
        return None


def _send(
    method: str, url: str, params: dict[str, str | int] | None = None, *, retry: bool = False
) -> httpx.Response | None:
    """One bot request. None on a network error. Never raises.

    With ``retry`` a 429 is waited out and tried once more. Off by default, because the web
    requests that call these helpers (guild join and leave, ``/join-guild``) must answer at
    once; only background work (the leadership role tasks and command) opts in.
    """
    response = _request(method, url, params)
    if retry and response is not None and response.status_code == 429:
        _sleep(_retry_after(response))
        response = _request(method, url, params)
    return response


class RoleOutcome(StrEnum):
    """What a role PUT or DELETE came to."""

    DONE = "done"
    #: Nothing to do: a blank token or id, or a 404 (they left the server).
    BENIGN = "benign"
    #: A 403: the bot lacks Manage Roles or its role sits below the target role. Benign for
    #: the join and leave sync, a setup mistake worth reporting for the leadership roles.
    REFUSED = "refused"
    #: Worth reporting: a network error, a 429 after the retry, a 5xx or another error.
    FAILED = "failed"


def role_outcome(method: str, server_id: str, user_id: str, role_id: str, *, retry: bool = False) -> RoleOutcome:
    """Issue one PUT/DELETE against a member's role and say how it went. Never raises.

    With ``retry`` a 429 is waited out (``Retry-After``, at most 10 seconds) and tried once
    more. A 404 (the member left the server) is benign; a 403 (bot missing Manage Roles, or
    its role sits below the target role) is ``REFUSED``. Both are logged.
    """
    if bot_disabled(f"role {method}") or not server_id or not user_id or not role_id:
        return RoleOutcome.BENIGN
    response = _send(method, f"{API_BASE}/guilds/{server_id}/members/{user_id}/roles/{role_id}", retry=retry)
    if response is None:
        return RoleOutcome.FAILED
    if response.is_success:
        return RoleOutcome.DONE
    if response.status_code in (403, 404):
        logger.warning(
            "Discord role %s benign %s (member left, or bot role too low): role=%s",
            method,
            response.status_code,
            role_id,
        )
        return RoleOutcome.REFUSED if response.status_code == 403 else RoleOutcome.BENIGN
    logger.warning("Discord role %s failed: %s %s", method, response.status_code, response.text[:300])
    return RoleOutcome.FAILED


def _role_request(method: str, server_id: str, user_id: str, role_id: str) -> bool:
    """Issue one PUT/DELETE against a member's role. Best-effort: ``True`` only on a 2xx, never raises or waits."""
    return role_outcome(method, server_id, user_id, role_id) is RoleOutcome.DONE


def assign_role(server_id: str, user_id: str, role_id: str) -> bool:
    """Give ``user_id`` the ``role_id`` on ``server_id`` (``PUT``). Best-effort."""
    return _role_request("PUT", server_id, user_id, role_id)


def remove_role(server_id: str, user_id: str, role_id: str) -> bool:
    """Take the ``role_id`` off ``user_id`` on ``server_id`` (``DELETE``). Best-effort."""
    return _role_request("DELETE", server_id, user_id, role_id)


#: The most members ``GET /guilds/{id}/members`` returns per page.
_MEMBERS_PAGE_SIZE = 1000


@dataclass(frozen=True)
class RolesRead:
    """A read of one member's roles: ``roles`` is None when nothing was read, ``failed`` when that is worth reporting."""

    roles: set[str] | None
    failed: bool = False


def read_member_roles(server_id: str, user_id: str, *, retry: bool = False) -> RolesRead:
    """The role ids ``user_id`` holds on ``server_id`` (``GET /guilds/{id}/members/{user}``). Never raises.

    ``roles`` is None when nothing can be read. That is quiet (``failed`` False) for a blank
    token or id and a 404 (they are not on the server), and a failure for a network error, a
    429 after the retry, any other non-2xx or an answer without a ``roles`` list. A caller
    never writes roles from a None, so an unreadable member is left exactly as they are.
    """
    if bot_disabled("member roles read") or not server_id or not user_id:
        return RolesRead(None)
    response = _send("GET", f"{API_BASE}/guilds/{server_id}/members/{user_id}", retry=retry)
    if response is not None and response.status_code == 404:
        return RolesRead(None)
    body = _json_body(response)
    if not isinstance(body, dict) or not isinstance(body.get("roles"), list):
        return RolesRead(None, failed=True)
    return RolesRead({str(role_id) for role_id in body["roles"]})


def member_role_ids(server_id: str, user_id: str) -> set[str] | None:
    """The role ids ``user_id`` holds, or None when they cannot be read (:func:`read_member_roles`)."""
    return read_member_roles(server_id, user_id).roles


def _json_body(response: httpx.Response | None) -> object | None:
    """The decoded JSON of a 2xx, else None (logged)."""
    if response is None:
        return None
    if not response.is_success:
        logger.warning("Discord GET %s failed: %s %s", response.request.url, response.status_code, response.text[:300])
        return None
    try:
        return response.json()
    except ValueError:
        logger.warning("Discord GET %s answered with something that is not JSON", response.request.url)
        return None


@dataclass(frozen=True)
class ServerMember:
    """One member of the Discord server as the member list gives it."""

    username: str
    roles: set[str]


def server_members(server_id: str, *, retry: bool = False) -> dict[str, ServerMember] | None:
    """Every member of ``server_id`` by Discord user id, paging 1000 at a time. Never raises.

    One request per thousand members instead of one per member. Empty when the bot token
    or the server id is blank (nothing to do). None when any page cannot be read (a network
    error, a 429, a non-2xx such as a 403 when the Server Members intent is off), so a caller
    never mistakes a partial list for the server and can report the failure.
    """
    if bot_disabled("member list") or not server_id:
        return {}
    found: dict[str, ServerMember] = {}
    after = "0"
    while True:
        page = _json_body(
            _send(
                "GET",
                f"{API_BASE}/guilds/{server_id}/members",
                {"limit": _MEMBERS_PAGE_SIZE, "after": after},
                retry=retry,
            )
        )
        if not isinstance(page, list):
            return None
        for entry in page:
            user = entry["user"]
            found[str(user["id"])] = ServerMember(
                username=str(user.get("username", "")),
                roles={str(role_id) for role_id in entry.get("roles", [])},
            )
        if len(page) < _MEMBERS_PAGE_SIZE:
            return found
        after = str(page[-1]["user"]["id"])


def server_member_role_ids(server_id: str, *, retry: bool = False) -> dict[str, set[str]] | None:
    """Every member of ``server_id`` as ``{Discord user id: role ids}`` (:func:`server_members`)."""
    members = server_members(server_id, retry=retry)
    return None if members is None else {user_id: member.roles for user_id, member in members.items()}


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

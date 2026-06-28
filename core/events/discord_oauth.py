"""Discord OAuth2 account-linking service (``identify`` scope).

Orchestrates the member ↔ Discord link so the per-member Discord DM channel
(:mod:`core.events.discord_dm`) can reach them: builds the authorize URL, exchanges
the returned code for an access token, reads the user's Discord id from the identity
endpoint, and records it on the member via :meth:`membership.models.Member.link_discord`.

This is the cross-model orchestration layer (CLAUDE.md: logic out of views). The thin
linking views in :mod:`hub.discord_views` own only the HTTP request/response + the CSRF
``state`` handshake; every failure here raises :class:`DiscordOAuthError` so the view
can show one friendly message. Disabled-when-unconfigured: :func:`is_configured` is
False when the client credentials are blank, so the connect view never starts a flow
that can't complete.

HTTP uses ``httpx`` (mocked with ``respx`` in tests) — the event spine's outbound HTTP
stack, the same one :mod:`core.events.discord` / :mod:`core.events.discord_dm` use.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
from django.conf import settings

if TYPE_CHECKING:
    from membership.models import Member

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
IDENTITY_URL = "https://discord.com/api/v10/users/@me"

# Only the identity is needed to learn the member's Discord id; no guild/message scope.
_SCOPE = "identify"
_DEFAULT_TIMEOUT_SECONDS = 10.0


class DiscordOAuthError(Exception):
    """Raised when any step of the Discord OAuth account-linking flow fails."""


def client_id() -> str:
    """The Discord application's client id (blank = linking disabled)."""
    return (getattr(settings, "DISCORD_CLIENT_ID", "") or "").strip()


def client_secret() -> str:
    """The Discord application's client secret (blank = linking disabled)."""
    return (getattr(settings, "DISCORD_CLIENT_SECRET", "") or "").strip()


def is_configured() -> bool:
    """Whether Discord account-linking is available (client id + secret both present)."""
    return bool(client_id() and client_secret())


def authorize_url(redirect_uri: str, state: str) -> str:
    """Build the Discord authorize URL the member is redirected to (``identify`` scope).

    Args:
        redirect_uri: The exact callback URL registered on the Discord application
            (``<scheme>://<host>/settings/discord/callback/``).
        state: A per-session CSRF token verified on the callback.
    """
    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": redirect_uri,
        "scope": _SCOPE,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> str:
    """Exchange an OAuth ``code`` for an access token.

    Args:
        code: The authorization code Discord returned on the callback.
        redirect_uri: The same callback URL used to obtain the code.

    Returns:
        The access token string.

    Raises:
        DiscordOAuthError: On a network error, a non-success status, or a response
            with no ``access_token``.
    """
    data = {
        "client_id": client_id(),
        "client_secret": client_secret(),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    try:
        response = httpx.post(TOKEN_URL, data=data, timeout=_DEFAULT_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("Discord token exchange failed (network error): %s", exc)
        raise DiscordOAuthError("Token exchange failed (network error).") from exc
    if not response.is_success:
        logger.warning("Discord token exchange failed: %s %s", response.status_code, response.text[:300])
        raise DiscordOAuthError("Token exchange returned a non-success status.")
    access_token = response.json().get("access_token", "")
    if not access_token:
        raise DiscordOAuthError("Token exchange response carried no access_token.")
    return str(access_token)


def fetch_user_id(access_token: str) -> str:
    """Read the linked user's Discord id via ``GET /users/@me``.

    Args:
        access_token: The bearer token from :func:`exchange_code`.

    Returns:
        The user's numeric Discord id (snowflake).

    Raises:
        DiscordOAuthError: On a network error, a non-success status, or a response
            with no ``id``.
    """
    try:
        response = httpx.get(
            IDENTITY_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord identity fetch failed (network error): %s", exc)
        raise DiscordOAuthError("Identity fetch failed (network error).") from exc
    if not response.is_success:
        logger.warning("Discord identity fetch failed: %s %s", response.status_code, response.text[:300])
        raise DiscordOAuthError("Identity fetch returned a non-success status.")
    user_id = response.json().get("id", "")
    if not user_id:
        raise DiscordOAuthError("Identity response carried no user id.")
    return str(user_id)


def link_member_from_code(member: Member, code: str, redirect_uri: str) -> None:
    """Complete the link: exchange the code, read the Discord id, store it on the member.

    Args:
        member: The member linking their Discord account.
        code: The authorization code from the OAuth callback.
        redirect_uri: The callback URL used throughout the flow.

    Raises:
        DiscordOAuthError: If any step of the OAuth flow fails (the member is left
            unlinked).
    """
    access_token = exchange_code(code, redirect_uri)
    discord_user_id = fetch_user_id(access_token)
    member.link_discord(discord_user_id)

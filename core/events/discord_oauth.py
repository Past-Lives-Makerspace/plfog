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
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urlencode

import httpx
from django.conf import settings

if TYPE_CHECKING:
    from membership.models import Member


class DiscordIdentity(NamedTuple):
    """The pieces of a linked Discord account we care about.

    Attributes:
        user_id: The user's numeric Discord id (snowflake) — the join key for DMs.
        handle: The user's Discord username, falling back to their global (display)
            name. Used to pre-fill a member's blank ``discord_handle``. May be blank
            if Discord returns neither.
        email: The email on the Discord account (blank if Discord returns none). Used
            by the no-login link flow to match an existing verified Past Lives account.
        email_verified: Whether Discord itself verified that email (``verified`` on
            ``/users/@me``). The no-login auto-link fires ONLY when this is True — an
            unverified email proves nothing.
    """

    user_id: str
    handle: str
    email: str = ""
    email_verified: bool = False


logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
IDENTITY_URL = "https://discord.com/api/v10/users/@me"

# ``identify`` learns the member's Discord id; ``email`` lets the no-login link flow
# match the account by its verified email (spec §5.1).
_SCOPE = "identify email"
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


def fetch_identity(access_token: str) -> DiscordIdentity:
    """Read the linked user's Discord id and handle via ``GET /users/@me``.

    Args:
        access_token: The bearer token from :func:`exchange_code`.

    Returns:
        A :class:`DiscordIdentity` with the numeric id and the user's handle
        (``username``, falling back to ``global_name``).

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
    payload = response.json()
    user_id = payload.get("id", "")
    if not user_id:
        raise DiscordOAuthError("Identity response carried no user id.")
    handle = (payload.get("username") or payload.get("global_name") or "").strip()
    return DiscordIdentity(
        user_id=str(user_id),
        handle=handle,
        email=(payload.get("email") or "").strip(),
        email_verified=bool(payload.get("verified", False)),
    )


def resolve_member_from_code(code: str, redirect_uri: str) -> tuple[Member | None, DiscordIdentity]:
    """Exchange an OAuth ``code``, fetch the identity, and match it to a verified account.

    The no-login link flow (spec §5.1): if Discord verified the email AND it matches
    exactly one verified Past Lives account, return that member; otherwise return
    ``(None, identity)`` so the caller can fall back to a friendly "log in to confirm".
    A pre-signup member with a verified email but no linked User is (correctly) not
    found by :func:`membership.selectors.member_for_verified_email`, so they also land
    on the log-in fallback.

    Args:
        code: The authorization code from the OAuth callback.
        redirect_uri: The callback URL used throughout the flow.

    Returns:
        ``(member_or_none, identity)`` — the identity is always returned so the caller
        can apply the downstream link guards (already-linked-elsewhere, etc.).

    Raises:
        DiscordOAuthError: If any step of the OAuth flow fails.
    """
    from membership.selectors import member_for_verified_email

    access_token = exchange_code(code, redirect_uri)
    identity = fetch_identity(access_token)
    if not (identity.email_verified and identity.email):
        return None, identity
    return member_for_verified_email(identity.email), identity


def link_member_from_code(member: Member, code: str, redirect_uri: str) -> None:
    """Complete the link: exchange the code, read the Discord id + handle, store on the member.

    The handle pre-fills the member's ``discord_handle`` only when it is blank — a value
    the member typed is never overwritten (see :meth:`membership.models.Member.link_discord`).

    Args:
        member: The member linking their Discord account.
        code: The authorization code from the OAuth callback.
        redirect_uri: The callback URL used throughout the flow.

    Raises:
        DiscordOAuthError: If any step of the OAuth flow fails (the member is left
            unlinked).
    """
    access_token = exchange_code(code, redirect_uri)
    identity = fetch_identity(access_token)
    member.link_discord(identity.user_id, handle=identity.handle)

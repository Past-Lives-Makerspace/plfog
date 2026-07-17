"""Discord Interactions endpoint infrastructure — signature verification, reply
builders, and the deferred-response REST helpers.

Discord calls a single POST view (:func:`core.views.discord_interactions`) for every
slash-command interaction. This module is the machinery behind that view:

* :func:`verify_signature` — the security gate. Every request is ed25519-signed by
  Discord; we verify it against the application's public key *before* parsing the
  body or touching a member. Fails **closed** (a blank public key rejects everything).
* the **reply builders** (:func:`pong`, :func:`reply`, :func:`deferred_ack`,
  :func:`unlinked_reply`, :func:`error_reply`) — plain dicts the view serializes back
  to Discord as the interaction response.
* the **REST helpers** (:func:`ack_deferred`, :func:`send_followup`) — for handlers
  that can't guarantee a reply within Discord's 3-second deadline (§5.4). Best-effort,
  exactly like :func:`core.events.discord_dm.post_dm`: they log and return falsy on any
  failure, never raising.

HTTP uses ``httpx`` (mocked with ``respx`` in tests) and reuses the bot-auth header +
REST base from :mod:`core.events.discord_dm`, the same outbound stack the DM channel and
the event spine use.
"""

from __future__ import annotations

import logging

import httpx

from core.events.discord_dm import API_BASE, _auth_headers, bot_token

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 5.0

# Discord's message flag for an ephemeral (only-the-invoker-sees-it) reply.
_EPHEMERAL_FLAG = 64


def is_configured() -> bool:
    """Whether the interactions endpoint can verify signatures (public key present).

    Blank public key → :func:`verify_signature` rejects everything (fail closed), so the
    endpoint answers 401 until the key is set — an unconfigured endpoint never trusts an
    unsigned body.
    """
    from django.conf import settings

    return bool((getattr(settings, "DISCORD_INTERACTIONS_PUBLIC_KEY", "") or "").strip())


def verify_signature(public_key_hex: str, signature_hex: str, timestamp: str, body: bytes) -> bool:
    """Verify Discord's ed25519 signature over ``timestamp + body``.

    The signed message is the raw timestamp header bytes concatenated with the raw
    request body (timestamp first). Returns ``True`` only on a valid signature; any
    missing input, malformed hex, or bad signature returns ``False`` (never raises).
    A blank ``public_key_hex`` returns ``False`` — the endpoint fails closed.

    PyNaCl is imported lazily so the app still boots on images without it (the import
    only runs when a real, signed request arrives — in production PyNaCl is a hard
    dependency; in tests it is installed into the container).
    """
    if not public_key_hex or not signature_hex or not timestamp:
        return False
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        VerifyKey(bytes.fromhex(public_key_hex)).verify(timestamp.encode() + body, bytes.fromhex(signature_hex))
    except (BadSignatureError, ValueError):  # ValueError = malformed hex
        return False
    return True


# --- Reply builders (plain dicts serialized back to Discord) ------------------


def _flags(ephemeral: bool) -> int:
    """Discord's ephemeral flag (64) when ``ephemeral`` else 0."""
    return _EPHEMERAL_FLAG if ephemeral else 0


def pong() -> dict:
    """The PONG response to Discord's PING liveness probe."""
    return {"type": 1}


def reply(
    content: str,
    *,
    ephemeral: bool = True,
    embeds: list[dict] | None = None,
    components: list[dict] | None = None,
) -> dict:
    """A type-4 (CHANNEL_MESSAGE_WITH_SOURCE) reply the view returns as its HTTP body.

    Ephemeral by default (only the invoking member sees it) — commands surface personal
    data. ``embeds`` / ``components`` are included only when provided.
    """
    data: dict = {"content": content, "flags": _flags(ephemeral)}
    if embeds is not None:
        data["embeds"] = embeds
    if components is not None:
        data["components"] = components
    return {"type": 4, "data": data}


def deferred_ack(*, ephemeral: bool = True) -> dict:
    """A type-5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE) ack — Discord's "thinking…" state.

    Sent via :func:`ack_deferred` (POST to the callback endpoint), not returned as the
    HTTP body. The ephemeral flag here must match the eventual followup's visibility.
    """
    return {"type": 5, "data": {"flags": _flags(ephemeral)}}


def unlinked_reply(link_url: str) -> dict:
    """The ephemeral prompt an unlinked member sees — connect Discord to Past Lives.

    Carries a link-style button (``style: 5``) to the absolute one-click link flow and a
    markdown-link fallback in the content (so it works even where the button doesn't
    render). Ephemeral so an unlinked member is never publicly called out.
    """
    content = (
        "You need to connect your Discord to your Past Lives account first. "
        "It's one click — if your Discord email matches your membership, you'll be linked instantly."
        f"\n\n[Connect my Past Lives account]({link_url})"
    )
    button_row = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "Connect my Past Lives account", "url": link_url},
        ],
    }
    return reply(content, ephemeral=True, components=[button_row])


def error_reply() -> dict:
    """The ephemeral "something went wrong" reply — never a raw 500 back to Discord."""
    return reply("Something went wrong on our end — please try again in a minute.", ephemeral=True)


# --- Deferred-response REST helpers (best-effort — log + falsy, never raise) ---


def ack_deferred(interaction_id: str, token: str, *, ephemeral: bool = True) -> bool:
    """POST a type-5 deferred ack so Discord's 3-second clock is satisfied (§5.4).

    Hits ``POST /interactions/{id}/{token}/callback`` — the fast call that shows the
    native "thinking…" indicator while the handler runs. Best-effort: returns ``True`` on
    a 2xx, ``False`` on a network error or any non-2xx (logged, never raised).
    """
    try:
        response = httpx.post(
            f"{API_BASE}/interactions/{interaction_id}/{token}/callback",
            json=deferred_ack(ephemeral=ephemeral),
            headers=_auth_headers(),
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord deferred ack failed (network error): %s", exc)
        return False
    if response.is_success:
        return True
    logger.warning("Discord deferred ack failed: %s %s", response.status_code, response.text[:300])
    return False


def send_followup(token: str, *, content: str, embeds: list[dict] | None = None) -> bool:
    """PATCH the deferred interaction's ``@original`` message with the real reply (§5.4).

    Hits ``PATCH /webhooks/{DISCORD_CLIENT_ID}/{token}/messages/@original``, well inside
    Discord's 15-minute followup window. The message inherits the ephemeral state of the
    :func:`ack_deferred` that preceded it. Best-effort: returns ``True`` on a 2xx, ``False``
    on a network error or any non-2xx (logged, never raised) — a failed followup leaves
    Discord's own "interaction failed" rather than a misleading success.
    """
    from core.events.discord_oauth import client_id

    payload: dict = {"content": content}
    if embeds is not None:
        payload["embeds"] = embeds
    try:
        response = httpx.patch(
            f"{API_BASE}/webhooks/{client_id()}/{token}/messages/@original",
            json=payload,
            headers=_auth_headers(),
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord interaction followup failed (network error): %s", exc)
        return False
    if response.is_success:
        return True
    logger.warning("Discord interaction followup failed: %s %s", response.status_code, response.text[:300])
    return False


__all__ = [
    "ack_deferred",
    "bot_token",
    "deferred_ack",
    "error_reply",
    "is_configured",
    "pong",
    "reply",
    "send_followup",
    "unlinked_reply",
    "verify_signature",
]

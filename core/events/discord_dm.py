"""Per-member Discord DM delivery via the FOG bot (the per-recipient Discord channel).

Unlike the webhook broadcast in :mod:`core.events.discord` (one embed to a server
channel), this delivers a **direct message** to one linked member through the bot
user. A webhook cannot DM, so this path uses the bot token against Discord's REST API:

1. open (or reuse) the DM channel with ``POST /users/@me/channels`` →
2. post the message with ``POST /channels/<id>/messages``.

Per the project's "disabled when blank" idiom (see ``MailchimpClient`` /
:func:`core.events.discord.global_webhook`), a blank ``DISCORD_BOT_TOKEN`` makes the
whole DM channel a no-op. Best-effort: every emit-spine call logs and returns falsy on
failure, never raising — the fan-out must keep going to other recipients/channels.
(:func:`send_dm_text` is the one deliberate exception: a loud sender for callers that
persist state based on the outcome.)

HTTP uses ``httpx`` (mocked with ``respx`` in tests), the event spine's outbound HTTP
layer — the same stack :mod:`core.events.discord` uses.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from django.conf import settings

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from core.events.channels import Message

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
_DEFAULT_TIMEOUT_SECONDS = 5.0


def bot_token() -> str:
    """The FOG bot token (blank = the per-member DM channel is disabled)."""
    return (getattr(settings, "DISCORD_BOT_TOKEN", "") or "").strip()


def _auth_headers() -> dict[str, str]:
    """The ``Authorization: Bot <token>`` header Discord's REST API expects."""
    return {"Authorization": f"Bot {bot_token()}"}


def discord_user_id_for(user: User) -> str:
    """The verified Discord snowflake for ``user``'s member, or ``""`` if none/unlinked.

    A single query against the member's ``discord_user_id``. The lookup is here (not
    on the adapter) so the adapter stays thin; ``Member`` is imported lazily because
    the channel layer is imported early and must not pull the model layer at import.
    """
    from membership.models import Member

    member = Member.objects.filter(user=user).only("discord_user_id").first()
    return (member.discord_user_id or "").strip() if member else ""


def open_dm_channel(discord_user_id: str) -> str:
    """Open (or fetch) the bot's DM channel id with ``discord_user_id``.

    Returns the channel id, or ``""`` when disabled (blank token / blank id) or on any
    failure. Best-effort: logs and never raises.
    """
    if not bot_token() or not discord_user_id:
        return ""
    try:
        response = httpx.post(
            f"{API_BASE}/users/@me/channels",
            json={"recipient_id": discord_user_id},
            headers=_auth_headers(),
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord DM channel open failed (network error): %s", exc)
        return ""
    if not response.is_success:
        logger.warning("Discord DM channel open failed: %s %s", response.status_code, response.text[:300])
        return ""
    channel_id = response.json().get("id", "")
    return str(channel_id) if channel_id else ""


def format_dm_content(message: Message) -> str:
    """Build the DM text from the message title, body, and url.

    Discord renders markdown in DMs, so the title is bolded and the url (when present)
    is appended on its own line as a clickable link.
    """
    parts: list[str] = []
    if message.title:
        parts.append(f"**{message.title}**")
    if message.body:
        parts.append(message.body)
    if message.url:
        parts.append(message.url)
    return "\n\n".join(parts)


def post_dm(discord_user_id: str, message: Message) -> bool:
    """DM ``message`` to ``discord_user_id`` via the bot. Best-effort — never raises.

    Returns ``True`` on a 2xx send, ``False`` when disabled (blank token / blank id),
    when the DM channel can't be opened, on a network error, or on any non-2xx status.
    Failures are logged (never the token).
    """
    if not bot_token() or not discord_user_id:
        return False
    channel_id = open_dm_channel(discord_user_id)
    if not channel_id:
        return False
    try:
        response = httpx.post(
            f"{API_BASE}/channels/{channel_id}/messages",
            json={"content": format_dm_content(message)},
            headers=_auth_headers(),
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord DM post failed (network error): %s", exc)
        return False
    if response.is_success:
        return True
    logger.warning("Discord DM post failed: %s %s", response.status_code, response.text[:300])
    return False


_CLOSED_DM_ERROR_CODE = 50007  # Discord's "Cannot send messages to this user".


def _dms_closed(response: httpx.Response) -> bool:
    """``True`` when Discord refused with 403 + JSON error code 50007.

    That combination means the recipient's privacy settings block DMs from the bot —
    permanently undeliverable, never worth a retry. Anything else (other statuses,
    non-JSON bodies, other error codes) is NOT "closed DMs" and stays loud.
    """
    if response.status_code != 403:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("code") == _CLOSED_DM_ERROR_CODE


def send_dm_text(discord_user_id: str, content: str) -> bool:
    """Send a plain-text DM through the bot, failing LOUDLY (unlike :func:`post_dm`).

    For callers that must know whether a send genuinely went out (e.g. the one-time
    guild-link nudge, which persists "already nudged" state on the result). Returns
    ``True`` on a 2xx send, and ``False`` ONLY when the recipient's DMs are closed
    (403 + Discord error code 50007 on either call — logged, permanently
    undeliverable, never retried). Any other non-2xx raises
    :class:`httpx.HTTPStatusError`, and network errors propagate as
    :class:`httpx.HTTPError` — the caller decides what an undelivered send means.
    The caller ensures the bot is configured (a blank token would just 401 loudly).
    """
    response = httpx.post(
        f"{API_BASE}/users/@me/channels",
        json={"recipient_id": discord_user_id},
        headers=_auth_headers(),
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    )
    if _dms_closed(response):
        logger.info("Discord DM to %s undeliverable (DMs closed).", discord_user_id)
        return False
    response.raise_for_status()
    channel_id = str(response.json()["id"])
    response = httpx.post(
        f"{API_BASE}/channels/{channel_id}/messages",
        json={"content": content},
        headers=_auth_headers(),
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    )
    if _dms_closed(response):
        logger.info("Discord DM to %s undeliverable (DMs closed).", discord_user_id)
        return False
    response.raise_for_status()
    return True

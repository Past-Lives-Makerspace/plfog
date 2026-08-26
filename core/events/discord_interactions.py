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
    poll: dict | None = None,
) -> dict:
    """A type-4 (CHANNEL_MESSAGE_WITH_SOURCE) reply the view returns as its HTTP body.

    Ephemeral by default (only the invoking member sees it) — commands surface personal
    data. ``embeds`` / ``components`` / ``poll`` are included only when provided.

    ``poll`` (a native Discord poll object) is valid **only on this non-deferred path**:
    :func:`send_followup` (the deferred PATCH) cannot carry a poll, so a deferred command
    that returned one would silently drop it. A ``/poll`` handler must therefore stay
    ``defer=False`` and post its poll straight from the interaction response. A poll reply
    is public and credits a member-controlled display name in its content, so this path
    also pins ``allowed_mentions`` to ``{"parse": []}`` — a name like ``@everyone`` can
    never ping the channel. Plain (non-poll) replies are untouched, so no other command's
    mention behavior changes.
    """
    data: dict = {"content": content, "flags": _flags(ephemeral)}
    if embeds is not None:
        data["embeds"] = embeds
    if components is not None:
        data["components"] = components
    if poll is not None:
        data["poll"] = poll
        data["allowed_mentions"] = {"parse": []}
    return {"type": 4, "data": data}


def update_message(
    content: str,
    *,
    embeds: list[dict] | None = None,
    components: list[dict] | None = None,
    allowed_mentions: dict | None = None,
) -> dict:
    """A type-7 (UPDATE_MESSAGE) response — edits the message the clicked component sits on.

    The component-click counterpart of :func:`reply`: instead of posting a fresh message it
    replaces the content/embeds/components of the message the button lives on, in place.
    Deliberately carries no ``flags`` — a message's ephemeral state is immutable, so an
    ephemeral browse stays ephemeral across updates. ``embeds`` / ``components`` /
    ``allowed_mentions`` are included only when provided (an omitted key leaves Discord's
    existing value untouched); pass ``allowed_mentions={"parse": []}`` when member-controlled
    names enter the content so a name like ``@everyone`` can never ping the channel.
    """
    data: dict = {"content": content}
    if embeds is not None:
        data["embeds"] = embeds
    if components is not None:
        data["components"] = components
    if allowed_mentions is not None:
        data["allowed_mentions"] = allowed_mentions
    return {"type": 7, "data": data}


# --- Modal builders (Components v2: Label-wrapped inputs + Text Display) -------
#
# A MODAL (type 9) response is valid for an APPLICATION_COMMAND or a MESSAGE_COMPONENT
# interaction, but NEVER for a MODAL_SUBMIT (no chaining). A MODAL_SUBMIT is answered with
# a plain type-4 message (:func:`reply`), public or ephemeral. Top-level modal components are
# Label (type 18, wrapping ONE input) and Text Display (type 10); inputs are Text Input
# (type 4), String Select (type 3), and Checkbox (type 23). Max 5 top-level components; the
# title is capped at 45 characters by Discord.

_LABEL = 18
_TEXT_DISPLAY = 10
_TEXT_INPUT = 4
_STRING_SELECT = 3
_CHECKBOX = 23


def modal(custom_id: str, title: str, components: list[dict]) -> dict:
    """A type-9 (MODAL) response — pops a form. Its submit routes by ``custom_id`` prefix.

    ``components`` are up to five top-level Label / Text Display components (built with the
    helpers below). Valid only as the response to a slash command or a component click.
    """
    return {"type": 9, "data": {"custom_id": custom_id, "title": title, "components": components}}


def modal_label(label: str, child: dict, *, description: str = "") -> dict:
    """A top-level Label (type 18) wrapping ONE input child, with an optional description.

    ``label`` ≤100 chars, ``description`` ≤200. The wrapped ``child`` is a Text Input, String
    Select, or Checkbox. On submit the row echoes back as ``{"type": 18, "component": {…}}``.
    """
    row: dict = {"type": _LABEL, "label": label, "component": child}
    if description:
        row["description"] = description
    return row


def text_display(content: str) -> dict:
    """A Text Display (type 10) block — static guidance inside a modal (no input, no value)."""
    return {"type": _TEXT_DISPLAY, "content": content}


def text_input(
    custom_id: str,
    *,
    style: int = 1,
    placeholder: str = "",
    value: str = "",
    required: bool = True,
    min_length: int | None = None,
    max_length: int | None = None,
) -> dict:
    """A Text Input (type 4). ``style`` 1 = short, 2 = paragraph. ``value`` prefills it.

    Optional keys (``placeholder`` ≤100, ``value``, ``min_length``/``max_length``) are
    included only when provided so a blank field ships a minimal component.
    """
    comp: dict = {"type": _TEXT_INPUT, "custom_id": custom_id, "style": style, "required": required}
    if placeholder:
        comp["placeholder"] = placeholder
    if value:
        comp["value"] = value
    if min_length is not None:
        comp["min_length"] = min_length
    if max_length is not None:
        comp["max_length"] = max_length
    return comp


def string_select(custom_id: str, options: list[dict], *, required: bool = True) -> dict:
    """A String Select (type 3) for a modal — ≤25 options, each ``{"label","value","default"?}``.

    An option's ``"default": true`` preselects it (valid in modals). Wrap in :func:`modal_label`.
    """
    return {"type": _STRING_SELECT, "custom_id": custom_id, "options": options, "required": required}


def checkbox(custom_id: str, *, default: bool = False, required: bool = False) -> dict:
    """A single Checkbox (type 23). ``default`` sets its initial checked state.

    On submit a checked box echoes a truthy ``values`` list; an unchecked one an empty/absent
    one — read via :func:`parse_modal_values` and coerce with ``bool``.
    """
    comp: dict = {"type": _CHECKBOX, "custom_id": custom_id, "required": required}
    if default:
        comp["value"] = True
    return comp


def parse_modal_values(interaction: dict) -> dict[str, object]:
    """Flatten a MODAL_SUBMIT payload to ``{custom_id: value | values}``.

    Tolerant to both submit shapes: the Components-v2 Label row
    (``{"type": 18, "component": {…}}``, one child) and the legacy action row
    (``{"type": 1, "components": [...]}``). A Text Input yields its ``"value"`` string; a
    String Select or Checkbox yields its ``"values"`` list.
    """
    result: dict[str, object] = {}
    for row in interaction.get("data", {}).get("components", []):
        children = [row["component"]] if row.get("type") == _LABEL else row.get("components", [])
        for child in children:
            custom_id = child.get("custom_id")
            if custom_id is None:
                continue
            if "value" in child:
                result[custom_id] = child["value"]
            elif "values" in child:
                result[custom_id] = child["values"]
    return result


def deferred_ack(*, ephemeral: bool = True) -> dict:
    """A type-5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE) ack — Discord's "thinking…" state.

    Sent via :func:`ack_deferred` (POST to the callback endpoint), not returned as the
    HTTP body. The ephemeral flag here must match the eventual followup's visibility.
    """
    return {"type": 5, "data": {"flags": _flags(ephemeral)}}


def deferred_update_ack() -> dict:
    """A type-6 (DEFERRED_UPDATE_MESSAGE) ack — for a component click whose work is slow.

    The component-click counterpart of :func:`deferred_ack`: Discord stops the 3-second
    clock without posting anything, and the handler later PATCHes the ``@original``
    message (the one the clicked button sits on) via :func:`send_followup`. Sent via
    :func:`ack_component_deferred`, not returned as the HTTP body.
    """
    return {"type": 6}


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


def ack_component_deferred(interaction_id: str, token: str) -> bool:
    """POST a type-6 deferred-update ack for a slow component click (§5.4's sibling).

    Same callback endpoint as :func:`ack_deferred`, but the type-6 body tells Discord
    "I'll edit the clicked message shortly" instead of "I'll post a reply" — the
    follow-up PATCH of ``@original`` then replaces the message the button lives on.
    Best-effort: returns ``True`` on a 2xx, ``False`` otherwise (logged, never raised).
    """
    try:
        response = httpx.post(
            f"{API_BASE}/interactions/{interaction_id}/{token}/callback",
            json=deferred_update_ack(),
            headers=_auth_headers(),
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord deferred component ack failed (network error): %s", exc)
        return False
    if response.is_success:
        return True
    logger.warning("Discord deferred component ack failed: %s %s", response.status_code, response.text[:300])
    return False


def send_followup(
    token: str,
    *,
    content: str,
    embeds: list[dict] | None = None,
    components: list[dict] | None = None,
    allowed_mentions: dict | None = None,
) -> bool:
    """PATCH the deferred interaction's ``@original`` message with the real reply (§5.4).

    Hits ``PATCH /webhooks/{DISCORD_CLIENT_ID}/{token}/messages/@original``, well inside
    Discord's 15-minute followup window. The message inherits the ephemeral state of the
    :func:`ack_deferred` that preceded it. ``embeds`` / ``components`` / ``allowed_mentions``
    are included only when provided (mirroring :func:`reply`), so a deferred command's link
    buttons survive the followup path and a member-name-bearing followup can pin
    ``allowed_mentions={"parse": []}``. Best-effort: returns ``True`` on a 2xx, ``False`` on a
    network error or any non-2xx (logged, never raised) — a failed followup leaves Discord's
    own "interaction failed" rather than a misleading success.
    """
    from core.events.discord_oauth import client_id

    payload: dict = {"content": content}
    if embeds is not None:
        payload["embeds"] = embeds
    if components is not None:
        payload["components"] = components
    if allowed_mentions is not None:
        payload["allowed_mentions"] = allowed_mentions
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


def expire_poll(channel_id: str, message_id: str) -> bool:
    """End (expire) a bot-authored native poll early (§5.6). Best-effort: ``True`` on a 2xx.

    Hits ``POST /channels/{channel_id}/polls/{message_id}/expire`` with the bot auth headers.
    Legal because the poll message is authored by our application — Discord only lets you end
    your own polls. A poll already expired, deleted, or otherwise un-endable returns a
    non-2xx, which the caller surfaces as the friendly "already ended" reply. Never raises —
    logs and returns ``False`` on a network error or any non-2xx.
    """
    try:
        response = httpx.post(
            f"{API_BASE}/channels/{channel_id}/polls/{message_id}/expire",
            headers=_auth_headers(),
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Discord poll expire failed (network error): %s", exc)
        return False
    if response.is_success:
        return True
    logger.warning("Discord poll expire failed: %s %s", response.status_code, response.text[:300])
    return False


__all__ = [
    "ack_component_deferred",
    "ack_deferred",
    "bot_token",
    "checkbox",
    "deferred_ack",
    "deferred_update_ack",
    "error_reply",
    "expire_poll",
    "is_configured",
    "modal",
    "modal_label",
    "parse_modal_values",
    "pong",
    "reply",
    "send_followup",
    "string_select",
    "text_display",
    "text_input",
    "unlinked_reply",
    "update_message",
    "verify_signature",
]

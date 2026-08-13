"""``emit()`` — the single emission point (design §2.2).

``emit()`` replaces every hand-sequenced ``activity.log()`` + ``dispatch()`` pair:

1. writes the :class:`core.models.SiteActivity` row (when the event declares an
   ``activity_kind``);
2. resolves recipients via the event's named resolver (§3, role × scope);
3. for each recipient, fans out to the channels they have enabled
   (:mod:`core.events.preferences`, backward-compatible);
4. dedupes each (event, target, channel) delivery via
   :class:`core.models.EventDelivery` (§2.5) so re-runs from schedulers are safe.

Phase-1 invariant: ``emit()`` is defined and unit-tested but **called from no
existing send site** — the senders migrate onto it in a later phase. Calling it
here does not change any current behavior because nothing else invokes it yet.

Forced channels ignore preferences. Best-effort: a single channel/recipient
failure must not abort the rest of the fan-out (the channel adapters already
swallow ordinary delivery errors; this is the structural guarantee).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError

from core.events import channels as channel_module
from core.events import preferences, resolvers, templates
from core.events.channels import Message
from core.events.registry import Channel, get_event
from core.models import EventDelivery, SiteActivity

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.contrib.auth.models import User
    from django.db.models import Model

    from core.email import Attachment


def emit(
    event_key: str,
    *,
    actor: Model | None = None,
    target: Model | None = None,
    context: dict[str, Any] | None = None,
    title: str = "",
    body: str = "",
    url: str = "",
    html_body: str | None = None,
    period: str = "",
    messages: dict[Channel, Message] | None = None,
    attachments: dict[Channel, list[Attachment]] | None = None,
    email_to: str | list[str] | None = None,
    extra_emails: list[str] | None = None,
    email_only_user_ids: set[int] | None = None,
    suppress_broadcast: bool = False,
    suppress_email: bool = False,
    suppress_guild_broadcast: bool = False,
    discord_mention: str = "",
    override_preferences: bool = False,
) -> EmitResult:
    """Emit one event: log activity, resolve recipients, fan out to channels.

    Args:
        event_key: A registered :class:`core.events.registry.EventType` key.
        actor: The ``User`` who triggered the event (for the activity row). ``None``
            for system events.
        target: The related object (class, booking, …) for the activity row.
        context: The resolver context — supplies whatever the event's resolver
            needs (``guild``, ``booking``, ``member``, ``user``, …).
        title / body / url / html_body: The rendered message. (DB-backed copy is a
            later phase; Phase 1 takes the rendered strings directly so senders can
            migrate incrementally.)
        period: Idempotency window bucket (``""`` = one-shot, else e.g. ``"2026-06"``).
        messages: Per-channel **pre-rendered** message overrides (Phase 4). A sender
            whose email body is a rich *structural* template (session lists, a
            multi-line body) renders that shell itself and passes the result keyed by
            :class:`~core.events.registry.Channel` — that exact Message goes to that
            channel, bypassing copy-mode rendering for it. Channels NOT in this dict
            still render from the DB-editable copy (so the in-app row + Discord embed
            stay copy-driven while the structural email is preserved byte-for-byte).
        attachments: Per-channel files to ride along with the message (an orientation
            ``.ics`` on :data:`Channel.EMAIL`). Channels that cannot carry files
            ignore their entry.
        email_to: Explicit EMAIL-channel recipient address(es), decoupled from the
            resolver (Phase 4). Some dedicated emails address a *raw email* — the
            form-entered ``registration.email`` (which may be a guest with no linked
            account, or a verified alias that differs from the account's primary
            email), an invitee, a found-account address. When given, the EMAIL channel
            delivers to exactly these addresses (deduped + audited), and the resolver's
            per-user fan-out covers only the OTHER channels (in-app / push). This keeps
            the email's recipient byte-identical to today while the in-app row still
            goes to the linked user the resolver finds. Forced/opt-in preference checks
            do not gate an ``email_to`` send (it mirrors a dedicated transactional send,
            which never consulted preferences).
        extra_emails: ADDITIVE explicit EMAIL-channel addresses — sent *in addition to* the
            resolver's per-recipient member fan-out, NOT instead of it. Unlike ``email_to``
            (which suppresses the member email so a dedicated transactional email owns the
            send), ``extra_emails`` leaves the member per-recipient loop running: members still
            get their own email while these extra addresses ride alongside. Used by a guild
            announcement to reach the guild's custom mailing-list addresses (boosters, partner
            orgs) without dropping members. Deduped against the resolved member recipients on
            the lower-cased ``user.email`` key inside emit (the ledger can't catch it — the
            member loop claims ``user:{pk}`` while the explicit loop claims ``email:{addr}``).
            Default ``None`` → no behavior change for any existing caller.
        email_only_user_ids: A per-announcement EMAIL **subset** — the resolved-member ``pk``
            values allowed to receive the email this time. When ``None`` (every existing caller) the
            EMAIL channel fans out to every resolved user as before. When a set, the EMAIL
            channel is delivered only to resolved users whose ``pk`` is in it; **all other
            channels (in-app bell, push) still reach every resolved user**, and the per-user
            ``preferences.enabled_channels`` opt-out check still runs (an opted-out member is
            skipped even when selected). This is deliberately NOT ``email_to`` — the resolver's
            per-recipient path and its preference gate are preserved; only the email side is
            narrowed. Coexists with ``extra_emails`` (custom addresses ride their own additive
            path, filtered by the caller before it reaches here).
        suppress_broadcast: When ``True``, skip every broadcast channel (Discord) for
            this emit — the per-recipient in-app + email fan-out still runs. Used by
            the admin "Sitewide Announcement" composer when sending the release notes:
            the GitHub Action already posts the release to Discord on merge to ``main``,
            so the email blast must not double-post it.
        suppress_email: When ``True``, skip the per-recipient EMAIL channel for this
            emit (the in-app + push fan-out still runs). Used by a guild announcement
            whose author turned "Also send email" off: members still get the in-app
            bell, just no email.
        suppress_guild_broadcast: When ``True``, skip ONLY the in-context guild's own
            Discord webhook (the dual-route post in :func:`_guild_broadcast`); the
            central/makerspace-wide broadcast still posts. Used by a guild announcement
            whose author turned "Also post to Discord" off — that switch governs the
            guild's own channel, not the site-wide post.
        discord_mention: An opt-in Discord ping literal (``"@here"`` / ``"@everyone"``,
            or ``""`` for none). Stamped onto ONLY the DISCORD-channel :class:`Message`
            (via :func:`dataclasses.replace` in ``message_for``), so the embed post carries
            the ping ``content`` + ``allowed_mentions`` gate. Blank leaves every payload
            byte-identical. Only the announcement composer sets it.

    Returns:
        An :class:`EmitResult` describing what was logged and delivered.

    Raises:
        KeyError: If ``event_key`` is not registered (fails loudly).
    """
    event = get_event(event_key)
    ctx = context or {}
    channel_messages = messages or {}
    channel_attachments = attachments or {}
    explicit_emails = [email_to] if isinstance(email_to, str) else list(email_to or [])

    activity: SiteActivity | None = None
    if event.activity_kind is not None:
        activity = SiteActivity.log(event.activity_kind, actor=actor, target=target)

    recipients = resolvers.resolve(event.recipient, ctx)

    # ADDITIVE extra_emails (belt-and-suspenders dedup): drop any that collide with a resolved
    # member's (lower-cased) email so a custom address equal to a member's address sends once —
    # the ledger can't catch it (member loop claims ``user:{pk}``, explicit loop ``email:{addr}``).
    # These do NOT contribute to ``suppress_user_email`` — the member per-recipient loop still runs.
    additive_emails: list[str] = []
    if extra_emails:
        member_emails_lower = {(user.email or "").strip().lower() for user, _reason in recipients}
        additive_emails = [addr for addr in extra_emails if (addr or "").strip().lower() not in member_emails_lower]

    # Copy mode (Phase 3): when the caller passes no explicit ``title``/``body``,
    # render each channel's message from the DB-backed (or seeded-default) copy
    # against ``ctx``. When the caller DOES pass explicit strings, the Phase-1
    # incremental path is preserved exactly — the same Message goes to every
    # channel and nothing reads the copy catalogue. A per-channel ``messages``
    # override (Phase 4) wins over both for that channel.
    use_copy = not (title or body)
    fixed_message = (
        None if use_copy else Message(title=title, body=body, url=url, html_body=html_body, trigger_kind=event_key)
    )

    def message_for(channel: Channel) -> Message:
        if channel in channel_messages:
            base = channel_messages[channel]
        elif fixed_message is not None:
            base = fixed_message
        else:
            base = templates.rendered_message(event_key, channel, ctx, url=url)
        # Stamp the opt-in ping onto ONLY the Discord message — one funnel covers both the
        # central broadcast and the chosen-webhook/guild post (both route through here).
        if channel is Channel.DISCORD and discord_mention:
            return dataclasses.replace(base, discord_mention=discord_mention)
        return base

    delivered: list[tuple[int, Channel]] = []
    skipped_duplicates: list[tuple[int, Channel]] = []
    suppress_user_email = bool(explicit_emails) or suppress_email
    for user, _reason in recipients:
        # Per-announcement EMAIL subset: drop ONLY this user's email when a selection is given
        # and they're not in it. The in-app bell + push still fan out to everyone below (only
        # ``suppress_email`` for the EMAIL channel is flipped), and the per-user preference gate
        # inside ``_per_recipient_fan_out`` still runs — so an opted-out selected member is still
        # skipped. ``email_only_user_ids is None`` leaves every existing caller byte-unchanged.
        user_suppress_email = suppress_user_email or (
            email_only_user_ids is not None and user.pk not in email_only_user_ids
        )
        _per_recipient_fan_out(
            event_key=event_key,
            user=user,
            message_for=message_for,
            channel_attachments=channel_attachments,
            period=period,
            suppress_email=user_suppress_email,
            override_preferences=override_preferences,
            delivered=delivered,
            skipped_duplicates=skipped_duplicates,
        )

    _explicit_email_fan_out(
        event_key,
        explicit_emails + additive_emails,
        message_for,
        channel_attachments,
        period,
        delivered,
        skipped_duplicates,
    )

    broadcast_channels = _broadcast_fan_out(
        event,
        message_for,
        period,
        ctx,
        delivered,
        skipped_duplicates,
        suppress_broadcast=suppress_broadcast,
        suppress_guild_broadcast=suppress_guild_broadcast,
    )

    return EmitResult(
        event_key=event_key,
        activity=activity,
        recipient_count=len(recipients),
        delivered=delivered,
        skipped_duplicates=skipped_duplicates,
        broadcast_channels=broadcast_channels,
    )


def _broadcast_fan_out(
    event: Any,
    message_for: Callable[[Channel], Message],
    period: str,
    ctx: dict[str, Any],
    delivered: list[tuple[int, Channel]],
    skipped_duplicates: list[tuple[int, Channel]],
    suppress_broadcast: bool = False,
    suppress_guild_broadcast: bool = False,
) -> list[Channel]:
    """Post each broadcast channel (Discord) ONCE for the event, not per recipient.

    A broadcast channel fires when the event declares it — independent of the
    per-recipient preferences (it has no per-user target). Deduped on the same
    :class:`core.models.EventDelivery` ledger using a synthetic ``broadcast`` target
    ref so a re-run from a scheduler does not double-post. Best-effort: the adapter
    swallows ordinary failures. ``message_for`` renders the per-channel message (so a
    broadcast channel gets its own DB/seeded copy, same as the per-recipient ones).

    For a guild-scoped event (``ctx["guild"]`` set, e.g. ``guild_announcement``), the
    Discord channel ALSO dual-routes to the guild's own webhook — see
    :func:`_guild_broadcast`. That second post is purely additive: it claims its own
    independent ledger slot and never blocks the central post.

    When the caller supplies an explicit ``ctx["discord_broadcast_webhook"]`` (the
    announcement channel picker), the central DISCORD iteration is skipped entirely: the
    chosen webhook — resolved in :func:`_guild_broadcast` — owns the single Discord post,
    so the event does not also hit the global/route webhook. This holds **with or without a
    guild** in context: a site-wide announcement routed to #general / #leadership (no guild)
    still posts through :func:`_guild_broadcast`. Callers that never set the key (every other
    event) keep the byte-for-byte central-post behavior.
    """
    if suppress_broadcast:
        return []
    posted: list[Channel] = []
    override_discord = "discord_broadcast_webhook" in ctx
    for spec in event.channels:
        channel = spec.channel
        if not channel_module.is_implemented(channel):
            continue
        adapter = channel_module.get_adapter(channel)
        if not adapter.is_broadcast:
            continue
        if channel is Channel.DISCORD and override_discord:
            # The chosen-webhook override owns the single Discord post (_guild_broadcast).
            continue
        if _record_broadcast(event.key, channel, period):
            channel_module.broadcast(adapter, message_for(channel))
            delivered.append((0, channel))
            posted.append(channel)
        else:
            skipped_duplicates.append((0, channel))
    # Guild dual-route: a Discord-broadcasting event ALSO posts to the in-context
    # guild's own webhook. This runs AFTER (and independently of) the central claim
    # above — a SIBLING, not nested in the central success branch — so a
    # central-duplicate re-emit can still post the guild side the first time a guild
    # webhook is added. Discord is the only broadcast channel, so this is the gate.
    if event.has_channel(Channel.DISCORD) and not suppress_guild_broadcast:
        _guild_broadcast(event, Channel.DISCORD, period, ctx, message_for, delivered, skipped_duplicates)
    return posted


def _guild_broadcast(
    event: Any,
    channel: Channel,
    period: str,
    ctx: dict[str, Any],
    message_for: Callable[[Channel], Message],
    delivered: list[tuple[int, Channel]],
    skipped_duplicates: list[tuple[int, Channel]],
) -> None:
    """Post the Discord embed to a chosen / guild-own webhook (the picker's single post).

    The destination webhook is resolved two ways:

    * an explicit ``ctx["discord_broadcast_webhook"]`` (the announcement channel picker,
      which may deliberately be ``""`` for "Don't post") — this posts **whether or not a
      guild is in context**, so a *site-wide* announcement that routed to #general /
      #leadership (no guild) still fires. The ledger slot keys to
      ``broadcast:guild:<id>`` when a guild exists, else the stable ``broadcast:chosen``
      so a guild-less picker post still dedups independently of the central slot.
    * else the in-context guild's own :func:`core.events.discord.guild_webhook` (toggle
      on AND a webhook set) for any other guild-scoped caller — a no-op without a guild.

    Claims an independent ledger slot so it dedups separately from the central post, then
    posts best-effort via ``post_embed`` (which logs and never raises on a bad/blank
    webhook), so a chosen/guild failure can never block the central post.
    """
    from core.events import discord as discord_module

    guild = ctx.get("guild")
    if "discord_broadcast_webhook" in ctx:
        webhook = ctx["discord_broadcast_webhook"]
        target_ref = f"broadcast:guild:{guild.pk}" if guild is not None else "broadcast:chosen"
    else:
        if guild is None:
            return
        webhook = discord_module.guild_webhook(guild)
        target_ref = f"broadcast:guild:{guild.pk}"
    if not webhook:
        return
    if _record_broadcast(event.key, channel, period, target_ref=target_ref):
        discord_module.post_embed(webhook, message_for(channel))
        delivered.append((0, channel))
    else:
        skipped_duplicates.append((0, channel))


def _per_recipient_fan_out(
    *,
    event_key: str,
    user: User,
    message_for: Callable[[Channel], Message],
    channel_attachments: dict[Channel, list[Attachment]],
    period: str,
    suppress_email: bool,
    override_preferences: bool,
    delivered: list[tuple[int, Channel]],
    skipped_duplicates: list[tuple[int, Channel]],
) -> None:
    """Fan one recipient out across their enabled, implemented, non-broadcast channels.

    Each (event, user, channel) delivery is claimed once on the :class:`core.models.EventDelivery` ledger so a re-emit does not double-send. When
    ``suppress_email`` is set the EMAIL channel is skipped here (the email goes to an
    explicit ``email_to`` address instead — see :func:`_explicit_email_fan_out`).
    """
    from core.events.registry import get_event

    channels = (
        [spec.channel for spec in get_event(event_key).channels]
        if override_preferences
        else preferences.enabled_channels(user, event_key)
    )
    for channel in channels:
        if channel is Channel.EMAIL and suppress_email:
            continue
        if not channel_module.is_implemented(channel):
            # Registered-but-unbuilt channel: record nothing, do nothing.
            continue
        adapter = channel_module.get_adapter(channel)
        if adapter.is_broadcast:
            # Broadcast channels (Discord) post once per event, handled separately.
            continue
        if _record_delivery(event_key, user, channel, period):
            adapter.deliver(user, message_for(channel), attachments=channel_attachments.get(channel))
            delivered.append((user.pk, channel))
        else:
            skipped_duplicates.append((user.pk, channel))


def _explicit_email_fan_out(
    event_key: str,
    explicit_emails: list[str],
    message_for: Callable[[Channel], Message],
    channel_attachments: dict[Channel, list[Attachment]],
    period: str,
    delivered: list[tuple[int, Channel]],
    skipped_duplicates: list[tuple[int, Channel]],
) -> None:
    """Send the EMAIL channel to explicit ``email_to`` addresses (Phase 4).

    Mirrors a dedicated transactional send: each address gets the email once, through
    the choke-point, deduped on the :class:`core.models.EventDelivery` ledger using an
    ``email:<addr>`` target ref so re-runs don't double-send. Preferences are not
    consulted (a dedicated email never did). Best-effort: the choke-point swallows the
    SMTP failure (``best_effort=True`` inside the adapter call below).
    """
    if not explicit_emails:
        return
    from core.email import send as send_email
    from core.events.channels import email_category_for

    message = message_for(Channel.EMAIL)
    attachments = channel_attachments.get(Channel.EMAIL)
    category = email_category_for(message.trigger_kind or event_key)
    seen: set[str] = set()
    for raw in explicit_emails:
        address = (raw or "").strip()
        if not address or address.lower() in seen:
            continue
        seen.add(address.lower())
        if _record_explicit_email(event_key, address, period):
            send_email(
                to=address,
                subject=message.title,
                trigger_kind=message.trigger_kind or event_key,
                text_body=message.body,
                html_body=message.html_body,
                best_effort=True,
                attachments=attachments,
                category=category,
            )
            delivered.append((0, Channel.EMAIL))
        else:
            skipped_duplicates.append((0, Channel.EMAIL))


def _record_explicit_email(event_key: str, address: str, period: str) -> bool:
    """Claim the (event, explicit-address, email, period) delivery slot (idempotent)."""
    try:
        _row, created = EventDelivery.objects.get_or_create(
            event_key=event_key,
            target_ref=f"email:{address.lower()}",
            channel=Channel.EMAIL.value,
            period=period,
        )
    except IntegrityError:
        return False
    return created


def _record_broadcast(event_key: str, channel: Channel, period: str, target_ref: str = "broadcast") -> bool:
    """Claim a once-per-event broadcast slot for ``channel`` (idempotent).

    ``target_ref`` defaults to ``"broadcast"`` (the central post, unchanged). The
    per-guild dual-route post claims its OWN slot with a distinct
    ``"broadcast:guild:<id>"`` ref, so the central and guild posts dedup independently
    on the :class:`core.models.EventDelivery` unique constraint.
    """
    try:
        _row, created = EventDelivery.objects.get_or_create(
            event_key=event_key,
            target_ref=target_ref,
            channel=channel.value,
            period=period,
        )
    except IntegrityError:
        return False
    return created


def _record_delivery(event_key: str, user: User, channel: Channel, period: str) -> bool:
    """Claim the (event, user, channel, period) delivery slot.

    Returns ``True`` when this call is the one that should send (the row was
    created), ``False`` when a prior delivery already claimed the slot (skip the
    send). The unique constraint is the authority — a concurrent racer that loses
    the insert is treated as a duplicate.
    """
    target_ref = f"user:{user.pk}"
    try:
        _row, created = EventDelivery.objects.get_or_create(
            event_key=event_key,
            target_ref=target_ref,
            channel=channel.value,
            period=period,
        )
    except IntegrityError:
        # Lost an insert race with a concurrent emit — the other call sent.
        return False
    return created


class EmitResult:
    """The outcome of one :func:`emit` call — what was logged and delivered.

    Useful for tests and for callers that want to report fan-out without re-reading
    the database.
    """

    def __init__(
        self,
        *,
        event_key: str,
        activity: SiteActivity | None,
        recipient_count: int,
        delivered: list[tuple[int, Channel]],
        skipped_duplicates: list[tuple[int, Channel]],
        broadcast_channels: list[Channel] | None = None,
    ) -> None:
        self.event_key = event_key
        self.activity = activity
        self.recipient_count = recipient_count
        self.delivered = delivered
        self.skipped_duplicates = skipped_duplicates
        self.broadcast_channels = broadcast_channels or []

    @property
    def delivery_count(self) -> int:
        return len(self.delivered)

    def __repr__(self) -> str:
        return (
            f"EmitResult(event_key={self.event_key!r}, recipients={self.recipient_count}, "
            f"delivered={self.delivery_count}, skipped={len(self.skipped_duplicates)})"
        )

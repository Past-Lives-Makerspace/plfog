"""Channel adapters — pluggable delivery (design §2.4).

A :class:`ChannelAdapter` knows how to deliver one rendered message to one
recipient over one channel. The registry maps each :class:`core.events.registry.Channel`
to its adapter. Adding a future channel = one adapter + one registry entry +
(eventually) a preference column; events and call sites never change.

Phase-1 invariant: the three live adapters **wrap the existing mechanisms** with
no behavior change of their own —

* :class:`InAppAdapter` creates the same :class:`core.models.Notification` bell row
  ``dispatch()`` creates;
* :class:`EmailAdapter` sends through the ``core.email.send`` choke-point (so the
  ``TransactionalEmailLog`` audit row is still written);
* :class:`PushAdapter` sends through ``core/push.py`` for each of the user's
  subscriptions.

Phase 2 makes the three remaining adapters real:

* :class:`DiscordAdapter` is a **per-event BROADCAST** channel — it posts one embed
  to a configured webhook (``DISCORD_NOTIFY_WEBHOOK_URL`` by default, with a
  per-event routing override structure), not one message per recipient. It is a
  no-op when no webhook is configured and is best-effort (logged, never raises).
* :class:`ScheduledEmailAdapter` renders + sends through the email choke-point when
  invoked, and exposes a due-window helper for the (Phase-5) scheduler.
* :class:`DigestAdapter` does not send immediately — it **buffers** the delivery as a
  ``PENDING`` :class:`core.models.EventDelivery` row for a later batch flush.

:class:`ChannelNotImplemented` and :class:`_ShellAdapter` remain for any future
register-before-build channel; the three above no longer use them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from django.utils.text import Truncator

from core.email import send as send_email
from core.events import discord as discord_module
from core.events import discord_dm as discord_dm_module
from core.events.registry import Channel
from core.fcm import send_fcm
from core.models import EventDelivery, FcmDevice, Notification, PushSubscription
from core.push import send_web_push

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from django.contrib.auth.models import User

    from core.email import Attachment


class ChannelNotImplemented(NotImplementedError):
    """Raised when a registered-but-unbuilt channel adapter is invoked (Phase 2)."""


@dataclass(frozen=True)
class Message:
    """A rendered message ready to hand to a channel adapter.

    One ``Message`` is built per event emission and reused across recipients and
    channels. ``html_body`` is optional (text-only channels ignore it); ``url`` is
    where an in-app / push click navigates; ``trigger_kind`` is the audit label
    passed to the email choke-point.

    ``discord_mention`` is the opt-in ping literal (``"@here"`` / ``"@everyone"``, or
    ``""`` for none) — only the Discord broadcast reads it (via
    :func:`core.events.discord.build_embed_payload`); every other channel ignores it,
    so a blank value leaves every existing payload byte-identical.
    """

    title: str
    body: str
    url: str = ""
    html_body: str | None = None
    trigger_kind: str = ""
    discord_mention: str = ""


@runtime_checkable
class ChannelAdapter(Protocol):
    """The interface every channel implements.

    ``channel`` identifies the adapter in the registry; ``deliver`` hands one
    message to one user. Adapters are best-effort and must not raise on ordinary
    delivery failure (the spine keeps fanning out to other recipients/channels).

    ``is_broadcast`` distinguishes the per-event broadcast channel (Discord) from
    the per-recipient channels: a broadcast adapter is invoked **once per event
    emission** via :meth:`broadcast`, not once per recipient via :meth:`deliver`.
    Per-recipient adapters leave ``is_broadcast`` False and never implement
    ``broadcast``.

    ``attachments`` is the per-recipient channel hook for files that ride along
    with the message (an orientation ``.ics``); channels that cannot carry files
    (in-app, push) ignore it. It defaults to ``None`` so every existing caller and
    adapter is unaffected.
    """

    channel: Channel
    is_broadcast: bool

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None: ...


def _fit(value: str, limit: int) -> str:
    """Clip a value to a CharField's ``max_length``.

    Bell rows are one-liners (``title`` 200, ``body`` 500). A long announcement body
    or subject would otherwise overflow the column and crash the whole ``emit()`` — so
    every in-app write is clipped here (the full content still rides the email).
    """
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


class InAppAdapter:
    """Writes one in-app bell :class:`core.models.Notification` row.

    Identical to the row ``dispatch()`` creates today (same ``trigger`` column =
    the event key, same title/body/url).
    """

    channel = Channel.IN_APP
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        # In-app rows carry no file attachments — the bell shows title/body/url.
        Notification.objects.create(
            user=user,
            trigger=message.trigger_kind or "",
            title=_fit(message.title, 200),
            body=_fit(message.body, 500),
            url=message.url,
        )


def email_category_for(trigger_kind: str) -> str | None:
    """The event's registry category for the ``X-Category`` header, or ``None``.

    ``trigger_kind`` is the event key for spine sends; an empty fallback or a
    non-event kind has no registry category, so the header is simply omitted.
    """
    if not trigger_kind:
        return None
    from core.events.registry import get_event

    try:
        return get_event(trigger_kind).category
    except KeyError:
        return None


class EmailAdapter:
    """Sends through the ``core.email.send`` choke-point (audited, best-effort).

    Mirrors the email path inside ``dispatch()`` exactly: subject = title, body =
    text/html, ``best_effort=True`` so an SMTP failure is logged but never raised.
    Skips users with no usable email (defensive — resolvers already filter these).
    """

    channel = Channel.EMAIL
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        if not (user.email or "").strip():
            return
        send_email(
            to=user.email,
            subject=message.title,
            trigger_kind=message.trigger_kind or "notification",
            text_body=message.body,
            html_body=message.html_body,
            best_effort=True,
            attachments=attachments,
            category=email_category_for(message.trigger_kind),
        )


# A push shows a short bold title over a one-line body in the notification tray. Cap
# both generously — the OS truncates anyway, but a clean word-boundary ellipsis reads
# better than a hard mid-word cut, and it keeps a stray essay out of the tray.
_PUSH_TITLE_LIMIT = 80
_PUSH_BODY_LIMIT = 200


def _push_safe(title: str, body: str) -> tuple[str, str]:
    """Flatten copy to a single tag-free line and cap it for the notification tray."""
    from core.html_sanitize import rich_html_to_text

    flat_title = rich_html_to_text(title) or title
    flat_body = rich_html_to_text(body)
    return Truncator(flat_title).chars(_PUSH_TITLE_LIMIT), Truncator(flat_body).chars(_PUSH_BODY_LIMIT)


class PushAdapter:
    """Sends a push to every one of the user's devices.

    Two transports ride this one channel: browser Web Push subscriptions via
    ``core/push.py`` and native (Capacitor) FCM device tokens via ``core/fcm.py`` — the
    native app cannot receive Web Push, so it registers an FCM token instead. Both are
    best-effort and reap their own dead rows.
    """

    channel = Channel.PUSH
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        # A push renders as one tray line: no HTML, no multi-paragraph body. Flatten
        # any rich/multi-line copy (an announcement body is sanitized HTML) to a single
        # clean line and cap both fields so the OS shows a tidy ellipsis instead of a
        # hard mid-word cut. Push carries no file attachments.
        title, body = _push_safe(message.title, message.body)
        for sub in PushSubscription.objects.filter(user=user):
            send_web_push(sub, title=title, body=body, url=message.url)
        for device in FcmDevice.objects.filter(user=user):
            send_fcm(device, title=title, body=body, url=message.url)


class _ShellAdapter:
    """Base for any future registered-but-unbuilt adapter.

    Present so a new channel can be declared in the registry before its adapter is
    written; raises :class:`ChannelNotImplemented` if invoked — a premature call is
    a bug, not a silent no-op. (No live channel uses this base after Phase 2.)
    """

    channel: Channel
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        raise ChannelNotImplemented(f"The {self.channel.value!r} channel is registered but not implemented.")


class ScheduledEmailAdapter:
    """Timed email — sends through the choke-point when its fire time arrives.

    Same delivery as :class:`EmailAdapter` (the choke-point, audited, best-effort);
    the *timing* is the difference. When the (Phase-5) generalized scheduler finds
    a scheduled event due (via :mod:`core.events.scheduling`), it calls
    :meth:`deliver` exactly as for any email. The due-window math lives in
    :mod:`core.events.scheduling`; this adapter re-exports :meth:`is_due` for
    callers that have the anchor in hand.

    SCHEDULER HANDOFF (Phase 5): nothing here is wired into ``run_scheduled_tasks``.
    The scheduler that walks the registry for due scheduled events, computes each
    event's anchor, and calls :func:`core.events.emit.emit` is built in Phase 5;
    this adapter + :mod:`core.events.scheduling` are the pieces it composes.
    """

    channel = Channel.SCHEDULED_EMAIL
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        if not (user.email or "").strip():
            return
        send_email(
            to=user.email,
            subject=message.title,
            trigger_kind=message.trigger_kind or "notification",
            text_body=message.body,
            html_body=message.html_body,
            best_effort=True,
            attachments=attachments,
            category=email_category_for(message.trigger_kind),
        )

    @staticmethod
    def is_due(anchor: datetime, offset: timedelta, *, now: datetime | None = None) -> bool:
        """Whether an event anchored at ``anchor + offset`` is due this tick.

        Thin re-export of :func:`core.events.scheduling.is_due` so a caller holding
        the anchor (orientation slot, class session, month-end) can ask the adapter
        directly.
        """
        from core.events.scheduling import is_due as _is_due

        return _is_due(anchor, offset, now=now)


class DigestAdapter:
    """Buffers a delivery for a later batched digest email (#9) — does NOT send now.

    Instead of delivering immediately, :meth:`deliver` writes a ``PENDING``
    :class:`core.models.EventDelivery` row keyed to the recipient + event, carrying
    the rendered message. A later flush batches every pending row for a user into
    one email and flips them to ``SENT``.

    FLUSH/CRON HANDOFF (Phase 5): the flush cron is NOT wired here. :meth:`flush_due`
    is the documented entry point the Phase-5 scheduler will call (today it groups
    pending rows per recipient and marks them sent; the actual digest-email
    rendering + the cron registration land with the scheduler).
    """

    channel = Channel.DIGEST
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        # Buffered digest rows carry no attachments — the later batch is plain text.
        EventDelivery.objects.update_or_create(
            event_key=message.trigger_kind or "",
            target_ref=f"user:{user.pk}",
            channel=self.channel.value,
            period="digest",
            defaults={
                "status": EventDelivery.Status.PENDING,
                "title": message.title,
                "body": message.body,
                "url": message.url,
            },
        )

    @staticmethod
    def pending_for(user: User) -> list[EventDelivery]:
        """Every buffered (still-PENDING) digest row for ``user``, oldest first."""
        return list(
            EventDelivery.objects.filter(
                target_ref=f"user:{user.pk}",
                channel=Channel.DIGEST.value,
                status=EventDelivery.Status.PENDING,
            ).order_by("created_at")
        )

    @classmethod
    def flush_due(cls) -> dict[str, list[int]]:
        """Phase-5 entry point — group pending rows per recipient + mark them sent.

        Returns ``{target_ref: [delivery_id, ...]}`` describing what would batch into
        each recipient's digest, and flips those rows to ``SENT``. The actual digest
        email assembly + the cron that calls this on a cadence are the Phase-5
        scheduler's job; this method exists so the buffering has a documented,
        tested drain point and the rows do not accumulate forever.
        """
        pending = EventDelivery.objects.filter(
            channel=Channel.DIGEST.value,
            status=EventDelivery.Status.PENDING,
        ).order_by("created_at")
        grouped: dict[str, list[int]] = {}
        ids: list[int] = []
        for row in pending:
            grouped.setdefault(row.target_ref, []).append(row.pk)
            ids.append(row.pk)
        if ids:
            EventDelivery.objects.filter(pk__in=ids).update(status=EventDelivery.Status.SENT)
        return grouped


class DiscordAdapter:
    """Per-event BROADCAST to a configured Discord webhook (Decision 9).

    Posts ONE embed per event emission to the routed webhook
    (:func:`core.events.discord.webhook_for_event`, defaulting to
    ``DISCORD_NOTIFY_WEBHOOK_URL``) — not one message per recipient. It is a no-op
    when no webhook is configured (mirroring ``MailchimpClient.from_site_config``),
    and best-effort: failures are logged and never raised into ``emit()``.

    Because it is broadcast, :attr:`is_broadcast` is True and the spine invokes
    :meth:`broadcast` once per event, not :meth:`deliver` per recipient. ``deliver``
    is kept (interface compatibility) and routes to ``broadcast`` so an accidental
    per-recipient call still posts at most the same single embed payload.
    """

    channel = Channel.DISCORD
    is_broadcast = True

    def broadcast(self, message: Message) -> bool:
        """Post one embed for ``message`` to the event's routed webhook.

        Returns ``True`` when the post succeeded, ``False`` when disabled (blank
        webhook) or on any failure. Never raises.
        """
        webhook = discord_module.webhook_for_event(message.trigger_kind or "")
        return discord_module.post_embed(webhook, message)

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        # Broadcast channel: the recipient is irrelevant. Defer to broadcast so a
        # stray per-recipient call still posts the single event-level embed.
        self.broadcast(message)


class DiscordDMAdapter:
    """Per-recipient DM to a member's linked Discord account (opt-in, Decision 9 sibling).

    The mirror image of :class:`DiscordAdapter`: where that posts ONE webhook embed per
    event, this DMs each opted-in recipient through the FOG bot. It is per-recipient
    (``is_broadcast`` False), so the spine invokes :meth:`deliver` once per recipient.

    It is a no-op (never raises) when:

    * the recipient hasn't linked their Discord account (``discord_user_id`` blank), or
    * the bot token is blank (the channel is disabled).

    Otherwise it opens the DM channel and posts via :mod:`core.events.discord_dm`, which
    is best-effort — it logs and swallows any Discord failure so the fan-out continues.
    """

    channel = Channel.DISCORD_DM
    is_broadcast = False

    def deliver(self, user: User, message: Message, *, attachments: list[Attachment] | None = None) -> None:
        # Discord DMs carry no file attachments — the message is title/body/url text.
        if not discord_dm_module.bot_token():
            return
        discord_user_id = discord_dm_module.discord_user_id_for(user)
        if not discord_user_id:
            return
        discord_dm_module.post_dm(discord_user_id, message)


# --- Registry ----------------------------------------------------------------

_ADAPTERS: dict[Channel, ChannelAdapter] = {
    Channel.IN_APP: InAppAdapter(),
    Channel.EMAIL: EmailAdapter(),
    Channel.PUSH: PushAdapter(),
    Channel.SCHEDULED_EMAIL: ScheduledEmailAdapter(),
    Channel.DIGEST: DigestAdapter(),
    Channel.DISCORD: DiscordAdapter(),
    Channel.DISCORD_DM: DiscordDMAdapter(),
}


def get_adapter(channel: Channel) -> ChannelAdapter:
    """Return the adapter for ``channel``. Raises ``KeyError`` if unregistered."""
    return _ADAPTERS[channel]


def is_implemented(channel: Channel) -> bool:
    """Whether ``channel``'s adapter is a live implementation (vs an unbuilt shell)."""
    return not isinstance(_ADAPTERS[channel], _ShellAdapter)


def broadcast(adapter: ChannelAdapter, message: Message) -> None:
    """Invoke a broadcast adapter's per-event post (Discord).

    Broadcast adapters expose ``broadcast(message)``; this indirection keeps the
    emit spine from importing the concrete :class:`DiscordAdapter` and lets the
    interface stay duck-typed. A non-broadcast adapter passed here is a caller bug.
    """
    adapter.broadcast(message)  # type: ignore[attr-defined]

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

from core.email import send as send_email
from core.events import discord as discord_module
from core.events.registry import Channel
from core.models import EventDelivery, Notification, PushSubscription
from core.push import send_web_push

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from django.contrib.auth.models import User


class ChannelNotImplemented(NotImplementedError):
    """Raised when a registered-but-unbuilt channel adapter is invoked (Phase 2)."""


@dataclass(frozen=True)
class Message:
    """A rendered message ready to hand to a channel adapter.

    One ``Message`` is built per event emission and reused across recipients and
    channels. ``html_body`` is optional (text-only channels ignore it); ``url`` is
    where an in-app / push click navigates; ``trigger_kind`` is the audit label
    passed to the email choke-point.
    """

    title: str
    body: str
    url: str = ""
    html_body: str | None = None
    trigger_kind: str = ""


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
    """

    channel: Channel
    is_broadcast: bool

    def deliver(self, user: User, message: Message) -> None: ...


class InAppAdapter:
    """Writes one in-app bell :class:`core.models.Notification` row.

    Identical to the row ``dispatch()`` creates today (same ``trigger`` column =
    the event key, same title/body/url).
    """

    channel = Channel.IN_APP
    is_broadcast = False

    def deliver(self, user: User, message: Message) -> None:
        Notification.objects.create(
            user=user,
            trigger=message.trigger_kind or "",
            title=message.title,
            body=message.body,
            url=message.url,
        )


class EmailAdapter:
    """Sends through the ``core.email.send`` choke-point (audited, best-effort).

    Mirrors the email path inside ``dispatch()`` exactly: subject = title, body =
    text/html, ``best_effort=True`` so an SMTP failure is logged but never raised.
    Skips users with no usable email (defensive — resolvers already filter these).
    """

    channel = Channel.EMAIL
    is_broadcast = False

    def deliver(self, user: User, message: Message) -> None:
        if not (user.email or "").strip():
            return
        send_email(
            to=user.email,
            subject=message.title,
            trigger_kind=message.trigger_kind or "notification",
            text_body=message.body,
            html_body=message.html_body,
            best_effort=True,
        )


class PushAdapter:
    """Sends a web push to every one of the user's subscriptions via ``core/push.py``."""

    channel = Channel.PUSH
    is_broadcast = False

    def deliver(self, user: User, message: Message) -> None:
        for sub in PushSubscription.objects.filter(user=user):
            send_web_push(sub, title=message.title, body=message.body, url=message.url)


class _ShellAdapter:
    """Base for any future registered-but-unbuilt adapter.

    Present so a new channel can be declared in the registry before its adapter is
    written; raises :class:`ChannelNotImplemented` if invoked — a premature call is
    a bug, not a silent no-op. (No live channel uses this base after Phase 2.)
    """

    channel: Channel
    is_broadcast = False

    def deliver(self, user: User, message: Message) -> None:
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

    def deliver(self, user: User, message: Message) -> None:
        if not (user.email or "").strip():
            return
        send_email(
            to=user.email,
            subject=message.title,
            trigger_kind=message.trigger_kind or "notification",
            text_body=message.body,
            html_body=message.html_body,
            best_effort=True,
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

    def deliver(self, user: User, message: Message) -> None:
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

    def deliver(self, user: User, message: Message) -> None:
        # Broadcast channel: the recipient is irrelevant. Defer to broadcast so a
        # stray per-recipient call still posts the single event-level embed.
        self.broadcast(message)


# --- Registry ----------------------------------------------------------------

_ADAPTERS: dict[Channel, ChannelAdapter] = {
    Channel.IN_APP: InAppAdapter(),
    Channel.EMAIL: EmailAdapter(),
    Channel.PUSH: PushAdapter(),
    Channel.SCHEDULED_EMAIL: ScheduledEmailAdapter(),
    Channel.DIGEST: DigestAdapter(),
    Channel.DISCORD: DiscordAdapter(),
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

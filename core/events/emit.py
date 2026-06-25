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

from typing import TYPE_CHECKING, Any

from django.db import IntegrityError

from core.events import channels as channel_module
from core.events import preferences, resolvers
from core.events.channels import Message
from core.events.registry import Channel, get_event
from core.models import EventDelivery, SiteActivity

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.db.models import Model


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

    Returns:
        An :class:`EmitResult` describing what was logged and delivered.

    Raises:
        KeyError: If ``event_key`` is not registered (fails loudly).
    """
    event = get_event(event_key)
    ctx = context or {}

    activity: SiteActivity | None = None
    if event.activity_kind is not None:
        activity = SiteActivity.log(event.activity_kind, actor=actor, target=target)

    recipients = resolvers.resolve(event.recipient, ctx)

    message = Message(
        title=title,
        body=body,
        url=url,
        html_body=html_body,
        trigger_kind=event_key,
    )

    delivered: list[tuple[int, Channel]] = []
    skipped_duplicates: list[tuple[int, Channel]] = []
    for user, _reason in recipients:
        for channel in preferences.enabled_channels(user, event_key):
            if not channel_module.is_implemented(channel):
                # Registered-but-unbuilt channel: record nothing, do nothing — its
                # real delivery + dedupe arrive with its adapter.
                continue
            adapter = channel_module.get_adapter(channel)
            if adapter.is_broadcast:
                # Broadcast channels (Discord) post once per event, not per
                # recipient — handled below, after the per-recipient fan-out.
                continue
            if _record_delivery(event_key, user, channel, period):
                adapter.deliver(user, message)
                delivered.append((user.pk, channel))
            else:
                skipped_duplicates.append((user.pk, channel))

    broadcast_channels = _broadcast_fan_out(event, message, period, delivered, skipped_duplicates)

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
    message: Message,
    period: str,
    delivered: list[tuple[int, Channel]],
    skipped_duplicates: list[tuple[int, Channel]],
) -> list[Channel]:
    """Post each broadcast channel (Discord) ONCE for the event, not per recipient.

    A broadcast channel fires when the event declares it — independent of the
    per-recipient preferences (it has no per-user target). Deduped on the same
    :class:`core.models.EventDelivery` ledger using a synthetic ``broadcast`` target
    ref so a re-run from a scheduler does not double-post. Best-effort: the adapter
    swallows ordinary failures.
    """
    posted: list[Channel] = []
    for spec in event.channels:
        channel = spec.channel
        if not channel_module.is_implemented(channel):
            continue
        adapter = channel_module.get_adapter(channel)
        if not adapter.is_broadcast:
            continue
        if _record_broadcast(event.key, channel, period):
            channel_module.broadcast(adapter, message)
            delivered.append((0, channel))
            posted.append(channel)
        else:
            skipped_duplicates.append((0, channel))
    return posted


def _record_broadcast(event_key: str, channel: Channel, period: str) -> bool:
    """Claim the once-per-event broadcast slot for ``channel`` (idempotent)."""
    try:
        _row, created = EventDelivery.objects.get_or_create(
            event_key=event_key,
            target_ref="broadcast",
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

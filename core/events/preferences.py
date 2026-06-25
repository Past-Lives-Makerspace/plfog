"""Channel-generic preference resolution — BACKWARD-COMPATIBLE (design §2.7).

The new channel adapters ask one question: *"should this user receive event K on
channel C?"* This module answers it without disturbing the legacy
:class:`core.models.NotificationPreference` schema (still keyed
``(user, trigger)`` with ``push_enabled`` / ``email_enabled`` boolean columns,
which the old ``dispatch()`` still reads).

Resolution rules, in order:

1. **Forced channels are always on.** A ``FORCED`` channel default ignores all
   preferences (Decision 1: essentials + operational mail).
2. **Legacy columns are the source of truth for EMAIL and PUSH**, so the new path
   and the old ``dispatch()`` agree exactly for the channels that exist today:
   * an explicit :class:`NotificationPreference` row for ``(user, event_key)``
     uses ``email_enabled`` / ``push_enabled``;
   * absent a row, the event's channel **default** applies (``ON`` → opt-in,
     ``OFF`` → opted out).
3. **IN_APP is always on** when the event declares the channel — mirrors today's
   ``dispatch()`` which always writes a bell row.
4. **Channels with no legacy column** (scheduled email, digest, discord) fall back
   to their event default for now; the full per-channel preference model is a
   later phase (see the module note below).

Why no schema change yet: the design's unified
``NotificationPreference(user, event_key, channel, enabled)`` model (§2.7) is the
**deferred cutover** — flipping it requires a data migration off the boolean
columns *and* a rewrite of ``dispatch()`` and the settings page in lockstep, which
would change existing send behavior. Phase 1 must not. So we read the existing
columns and expose a channel-shaped API over them; the cutover lands in its own
phase. See the Phase-1 report for the explicit cutover plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.events.registry import Channel, ChannelDefault, get_event
from core.models import NotificationPreference

if TYPE_CHECKING:
    from django.contrib.auth.models import User

# Channels backed by a legacy NotificationPreference boolean column today.
_LEGACY_COLUMN: dict[Channel, str] = {
    Channel.EMAIL: "email_enabled",
    Channel.PUSH: "push_enabled",
}


def wants(user: User, event_key: str, channel: Channel) -> bool:
    """Return whether ``user`` should receive ``event_key`` on ``channel``.

    Backward-compatible: for EMAIL / PUSH this returns exactly what today's
    ``dispatch()`` would, reading the legacy ``NotificationPreference`` columns
    and the event's channel default. FORCED channels always return ``True``;
    IN_APP always returns ``True`` (when the event declares it).
    """
    event = get_event(event_key)
    spec = event.channel(channel)
    if spec is None:
        # The event does not fan out to this channel at all.
        return False
    if spec.is_forced:
        return True
    if channel is Channel.IN_APP:
        # In-app is always delivered (the bell row is non-optional today).
        return True

    column = _LEGACY_COLUMN.get(channel)
    if column is None:
        # No legacy column for this channel (scheduled_email / digest / discord):
        # fall back to the event default until the per-channel pref model lands.
        return spec.default is ChannelDefault.ON

    pref = (
        NotificationPreference.objects.filter(user=user, trigger=event_key)
        .only("push_enabled", "email_enabled")
        .first()
    )
    if pref is None:
        # No explicit preference row → the event's channel default decides.
        return spec.default is ChannelDefault.ON
    return bool(getattr(pref, column))


def enabled_channels(user: User, event_key: str) -> list[Channel]:
    """All channels ``user`` should receive ``event_key`` on, in declared order."""
    event = get_event(event_key)
    return [spec.channel for spec in event.channels if wants(user, event_key, spec.channel)]

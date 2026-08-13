"""Channel-generic preference resolution on the unified per-channel model (§2.7).

Each channel adapter asks one question: *"should this user receive event K on
channel C?"* This module answers it against the cutover-era
:class:`core.models.NotificationPreference` — now keyed
``(user, event_key, channel)`` with a single ``enabled`` boolean (one row per
channel), replacing the old two-boolean ``push_enabled`` / ``email_enabled``
columns.

Resolution rules, in order:

1. **The event must declare the channel.** A channel the event doesn't fan out to
   is never wanted.
2. **Forced channels are always on.** A ``FORCED`` channel default ignores all
   preferences (Decision 1: essentials + operational mail).
3. **IN_APP is always on** when the event declares the channel — the bell row is
   non-optional (mirrors the historical ``dispatch()`` which always wrote one).
4. **Otherwise the per-channel preference decides:** an explicit
   :class:`NotificationPreference` row for ``(user, event_key, channel)`` uses its
   ``enabled`` flag; absent a row, the event's channel **default** applies (``ON`` →
   opt-in, ``OFF`` → opted out). This now governs *every* opt-out-able channel —
   email, push, scheduled email, digest, and per-user Discord — so a new channel
   honors real per-channel preferences instead of only the event default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.events.registry import Channel, ChannelDefault, get_event
from core.models import NotificationPreference

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def wants(user: User, event_key: str, channel: Channel, *, is_optional: bool = False) -> bool:
    """Return whether ``user`` should receive ``event_key`` on ``channel``.

    FORCED channels always return ``True`` (even for an optional recipient — a forced
    channel is an essential the user can't opt out of).

    ``is_optional`` marks a recipient who was *offered* the event but does not hold the
    capability that owns it (a plain admin on a capability-scoped send — see
    :data:`core.events.resolvers.OPTIONAL_RECIPIENT_REASON`). For such a recipient there
    is NO implicit channel default and NO always-on IN_APP shortcut: the ONLY way a
    channel fires is an explicit :class:`NotificationPreference` row with ``enabled=True``.
    So an optional recipient with no saved preference receives the event on nothing — the
    bell included.

    For a normal recipient (``is_optional=False`` — every existing caller) the behavior is
    unchanged: IN_APP is always on, else an explicit per-channel row wins, else the event's
    channel default decides.
    """
    event = get_event(event_key)
    spec = event.channel(channel)
    if spec is None:
        # The event does not fan out to this channel at all.
        return False
    if spec.is_forced:
        return True
    if is_optional:
        # Optional recipient: no default, no always-on in-app — an explicit opt-in row is
        # the only path to a channel firing.
        pref = (
            NotificationPreference.objects.filter(user=user, event_key=event_key, channel=channel.value)
            .only("enabled")
            .first()
        )
        return pref is not None and pref.enabled
    if channel is Channel.IN_APP:
        # In-app is always delivered (the bell row is non-optional).
        return True

    pref = (
        NotificationPreference.objects.filter(user=user, event_key=event_key, channel=channel.value)
        .only("enabled")
        .first()
    )
    if pref is None:
        # No explicit preference row → the event's channel default decides.
        return spec.default is ChannelDefault.ON
    return pref.enabled


def enabled_channels(user: User, event_key: str, *, is_optional: bool = False) -> list[Channel]:
    """All channels ``user`` should receive ``event_key`` on, in declared order.

    ``is_optional`` is threaded through to :func:`wants` — an optional recipient with no
    saved preferences resolves to an empty list (nothing fires, in-app included).
    """
    event = get_event(event_key)
    return [spec.channel for spec in event.channels if wants(user, event_key, spec.channel, is_optional=is_optional)]

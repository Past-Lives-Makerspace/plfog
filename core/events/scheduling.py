"""Scheduled-event due-window computation (design §2.6).

Generalizes the proven ``send_due_class_reminders`` window+dedupe pattern: a
scheduled event fires relative to an **anchor** datetime, offset by a fixed
``±offset`` (e.g. *orientation slot start − 48h*, *class session start − 24h*,
*month-end − 48h*). A 15-minute cron tick only wants events whose fire time has
just crossed *now*, so :func:`is_due` checks whether the fire time falls in the
half-open window ``[now, now + window)`` — at most one tick per fire, deduped
downstream by :class:`core.models.EventDelivery`.

This module owns only the **time math**. The generalized SCHEDULER that walks the
registry for due scheduled events and calls :func:`core.events.emit.emit` is a
LATER phase (Phase 5) — see :func:`due_anchor_window` / :func:`is_due` and the
``ScheduledEmailAdapter`` for the pieces it will compose. It is intentionally NOT
wired into ``run_scheduled_tasks`` here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

# The cron cadence (design §2.6: the existing 15-minute ``run_scheduled_tasks``).
DEFAULT_WINDOW = timedelta(minutes=15)


@dataclass(frozen=True)
class DueWindow:
    """The fire time for a scheduled event and the tick window it's due in.

    ``fire_at`` = ``anchor + offset`` (``offset`` negative for "before the anchor",
    the common case). ``window_start`` / ``window_end`` bound the half-open tick
    window the scheduler compares against.
    """

    fire_at: datetime
    window_start: datetime
    window_end: datetime

    @property
    def is_due(self) -> bool:
        """Whether the fire time falls in this tick's ``[start, end)`` window."""
        return self.window_start <= self.fire_at < self.window_end


def fire_time(anchor: datetime, offset: timedelta) -> datetime:
    """The absolute time a scheduled event fires: ``anchor + offset``.

    Args:
        anchor: The reference datetime (slot start, session start, month-end, …).
        offset: Signed offset — negative for "N before the anchor"
            (``timedelta(hours=-48)``), positive for "N after".

    Returns:
        The datetime the event should fire at.
    """
    return anchor + offset


def due_anchor_window(
    anchor: datetime,
    offset: timedelta,
    *,
    now: datetime | None = None,
    window: timedelta = DEFAULT_WINDOW,
) -> DueWindow:
    """Compute the fire time and the current tick window for one scheduled event.

    The window is the proven ``send_due_class_reminders`` shape: ``[now, now +
    window)``. An event is due when its fire time has crossed into this window
    since the previous tick — exactly once, regardless of how often the cron runs
    (the per-target dedupe in :class:`core.models.EventDelivery` is the backstop).

    Args:
        anchor: The reference datetime the offset is measured from.
        offset: Signed ``±offset`` from the anchor.
        now: Override for "current time" (tests pass a fixed value); defaults to
            ``django.utils.timezone.now()``.
        window: The tick width; defaults to the 15-minute cron cadence.

    Returns:
        A :class:`DueWindow` whose :attr:`DueWindow.is_due` says whether to fire.
    """
    current = now or timezone.now()
    return DueWindow(
        fire_at=fire_time(anchor, offset),
        window_start=current,
        window_end=current + window,
    )


def is_due(
    anchor: datetime,
    offset: timedelta,
    *,
    now: datetime | None = None,
    window: timedelta = DEFAULT_WINDOW,
) -> bool:
    """Whether a scheduled event anchored at ``anchor + offset`` is due this tick."""
    return due_anchor_window(anchor, offset, now=now, window=window).is_due

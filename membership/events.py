"""Scheduled-send sources for community-event reminders + the "happening now" ping.

Both mirror :func:`classes.tasks.class_reminder_occurrences` and the voting sources
(:mod:`membership.voting`): window the query, yield one :class:`ScheduledOccurrence`
per (event × offset), and let :func:`core.events.scheduler.run_due` due-check each
against the 15-minute tick window and :func:`core.events.emit.emit` dedupe on
:class:`core.models.EventDelivery`.

* :func:`event_reminder_occurrences` — one occurrence per enabled 7/3/1-day offset on
  each date of a published event, period ``event:{pk}:reminder:{days}d:{date}``.
* :func:`event_happening_now_occurrences` — one "starting now" occurrence per date of an
  opted-in published event, period ``event:{pk}:happening_now:{date}``.

Both ask :meth:`CommunityEvent.reminder_sends` for the sends inside this tick, the same
method the event editor reads to show when each reminder goes out. A repeating series
reminds before every date, not only its first (v1 did only the first, while the editor let
a lead turn reminders on for a series anyway); the date in the period keeps each date's
send separate, and lets a moved one-off event remind again for its new date. A send whose
time has already passed is never yielded, so an event less than N days out gets no
N-day reminder and nothing is ever sent late.

The audience for both is the launch-announcement audience (by scope) via the
``event_audience`` resolver — the ``guild`` / ``event_type`` context keys drive it. Studio
hours never send either: they are standing hours, not an event, and their weekly rows are
exactly the shape a repeating series now reminds on.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.db.models import Q
from django.utils import timezone
from django.utils.html import format_html

from core.events.registry import EVENT_HAPPENING_NOW, EVENT_REMINDER
from core.events.scheduler import ScheduledOccurrence
from core.events.scheduling import DEFAULT_WINDOW

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from django.db.models import QuerySet

    from membership.models import CommunityEvent

# The seeded gold-button idiom (core/events/copy.py) — the reminder's "Join meeting"
# CTA matches the house email buttons pixel-for-pixel.
_JOIN_CTA_STYLE = (
    "display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;"
    "font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;"
)


def _join_url(event: CommunityEvent, date_start: datetime) -> str | None:
    """The linked meeting's video-call link for the date being reminded about, or ``None``.

    Resolves ``event.meetings`` for the occurrence on that date; first match wins
    (Meetings spec §6.6). ``None`` when no meeting is linked or the linked meeting has no
    ``video_call_url``.
    """
    occurrence = timezone.localtime(date_start).date()
    meeting = event.meetings.filter(event_occurrence=occurrence).first()
    if meeting is None:
        return None
    return meeting.video_call_url or None


def _event_context(event: CommunityEvent, date_start: datetime, *, days_before: int | None) -> dict[str, Any]:
    """The shared resolver + copy context for one date's reminder/happening-now ping.

    ``guild`` + ``event_type`` drive the ``event_audience`` resolver and the per-guild
    Discord routing; the rest feed the curated copy, whose ``when`` names ``date_start``
    (one date of a repeating series, not its first). ``days_before`` is ``None`` for the
    happening-now ping. ``join_url`` carries a linked meeting's video-call link (§6.6);
    the constrained copy renderer has no conditionals, so the guarded "Join meeting"
    CTA ships as the pre-built ``join_cta`` (trusted SafeString markup — pass-through
    per ``render_html``) and ``join_line`` values, both empty when there is no link.
    """
    join_url = _join_url(event, date_start)
    return {
        "guild": event.guild,
        "event_type": event.event_type,
        "guild_name": event.guild.name if event.guild is not None else "",
        "event_title": event.title,
        "when": event.when_display_for(date_start),
        "location": event.location,
        "days_before": days_before,
        "event_url": event.absolute_url,
        "join_url": join_url,
        "join_cta": (
            format_html(
                '<p style="text-align:center;margin:24px 0 8px;"><a href="{}" style="{}">Join meeting</a></p>',
                join_url,
                _JOIN_CTA_STYLE,
            )
            if join_url
            else ""
        ),
        "join_line": f"Join the meeting: {join_url}\n" if join_url else "",
    }


def _sending_events(now: datetime, *, days_before: int) -> QuerySet[CommunityEvent]:
    """Published events that could have a ``days_before`` send in this tick: a one-off starting
    within that reach, or a repeating series that has begun by then. Studio hours never send."""
    from membership.models import CommunityEvent

    # A coarse cut; reminder_sends makes the exact one. Counted in clock time from a UTC now,
    # with a day to spare for the hour a daylight saving change adds and for the tick window.
    reach = timezone.localtime(now) + timedelta(days=days_before + 1)
    one_off = Q(recurrence=CommunityEvent.Recurrence.NONE, starts_at__gte=now, starts_at__lte=reach)
    series = ~Q(recurrence=CommunityEvent.Recurrence.NONE) & Q(starts_at__lte=reach)
    return (
        CommunityEvent.objects.published()
        .exclude(event_type=CommunityEvent.EventType.STUDIO_HOURS)
        .filter(one_off | series)
        .select_related("guild")
    )


def event_reminder_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """One occurrence per enabled offset on each date whose send lands in this tick (a scheduler source)."""
    from membership.models import CommunityEvent

    furthest = max(days for _attr, days in CommunityEvent.REMINDER_OFFSETS)
    any_on = Q()
    for attr, _days in CommunityEvent.REMINDER_OFFSETS:
        any_on |= Q(**{attr: True})
    reminding = _sending_events(now, days_before=furthest).filter(any_on)
    for event in reminding:
        for days in event.enabled_reminder_offsets():
            for _send, date_start in event.reminder_sends(days, now, now + DEFAULT_WINDOW):
                yield ScheduledOccurrence(
                    event_key=EVENT_REMINDER,
                    anchor=date_start,
                    offset=timedelta(days=-days),
                    target=event,
                    context=_event_context(event, date_start, days_before=days),
                    url=event.absolute_url,
                    period=f"event:{event.pk}:reminder:{days}d:{date_start.date().isoformat()}",
                )


def event_happening_now_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """One 'starting now' occurrence per opted-in event's date that begins in this tick (a scheduler source)."""
    for event in _sending_events(now, days_before=0).filter(notify_happening_now=True):
        for _send, date_start in event.reminder_sends(0, now, now + DEFAULT_WINDOW):
            yield ScheduledOccurrence(
                event_key=EVENT_HAPPENING_NOW,
                anchor=date_start,
                offset=timedelta(0),
                target=event,
                context=_event_context(event, date_start, days_before=None),
                url=event.absolute_url,
                period=f"event:{event.pk}:happening_now:{date_start.date().isoformat()}",
            )

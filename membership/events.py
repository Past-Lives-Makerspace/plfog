"""Scheduled-send sources for community-event reminders + the "happening now" ping.

Both mirror :func:`classes.tasks.class_reminder_occurrences` and the voting sources
(:mod:`membership.voting`): window the query, yield one :class:`ScheduledOccurrence`
per (event × offset), and let :func:`core.events.scheduler.run_due` due-check each
against the 15-minute tick window and :func:`core.events.emit.emit` dedupe on
:class:`core.models.EventDelivery`.

* :func:`event_reminder_occurrences` — one occurrence per enabled 7/3/1-day offset on
  each published, upcoming event, period ``event:{pk}:reminder:{days}d``.
* :func:`event_happening_now_occurrences` — one "starting now" occurrence per opted-in
  published event, period ``event:{pk}:happening_now``.

The audience for both is the launch-announcement audience (by scope) via the
``event_audience`` resolver — the ``guild`` / ``event_type`` context keys drive it. A
past-anchor recurring series contributes nothing (``starts_at__gte=now``): recurring
events get their launch announcement but no ongoing reminders in v1. That same band
means a reminder offset already in the past (an event less than N days out) is simply
never yielded — no negative/awkward fire.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.utils import timezone
from django.utils.html import format_html

from core.events.registry import EVENT_HAPPENING_NOW, EVENT_REMINDER
from core.events.scheduler import ScheduledOccurrence
from core.events.scheduling import DEFAULT_WINDOW

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from membership.models import CommunityEvent

# The seeded gold-button idiom (core/events/copy.py) — the reminder's "Join meeting"
# CTA matches the house email buttons pixel-for-pixel.
_JOIN_CTA_STYLE = (
    "display:inline-block;padding:12px 28px;background-color:#EEB44B;color:#092E4C;"
    "font-size:14px;font-weight:700;text-decoration:none;border-radius:6px;"
)


def _join_url(event: CommunityEvent) -> str | None:
    """The linked meeting's video-call link for this event's occurrence, or ``None``.

    Resolves ``event.meetings`` for the occurrence matching the event's own start date
    (reminders anchor on ``starts_at``, so that IS the occurrence being reminded about);
    first match wins (Meetings spec §6.6). ``None`` when no meeting is linked or the
    linked meeting has no ``video_call_url``.
    """
    occurrence = timezone.localtime(event.starts_at).date()
    meeting = event.meetings.filter(event_occurrence=occurrence).first()
    if meeting is None:
        return None
    return meeting.video_call_url or None


def _event_context(event: CommunityEvent, *, days_before: int | None) -> dict[str, Any]:
    """The shared resolver + copy context for one event's reminder/happening-now ping.

    ``guild`` + ``event_type`` drive the ``event_audience`` resolver and the per-guild
    Discord routing; the rest feed the curated copy. ``days_before`` is ``None`` for the
    happening-now ping. ``join_url`` carries a linked meeting's video-call link (§6.6);
    the constrained copy renderer has no conditionals, so the guarded "Join meeting"
    CTA ships as the pre-built ``join_cta`` (trusted SafeString markup — pass-through
    per ``render_html``) and ``join_line`` values, both empty when there is no link.
    """
    join_url = _join_url(event)
    return {
        "guild": event.guild,
        "event_type": event.event_type,
        "guild_name": event.guild.name if event.guild is not None else "",
        "event_title": event.title,
        "when": event.when_display,
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


def event_reminder_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """One occurrence per enabled offset on each published, upcoming event (a scheduler source)."""
    from membership.models import CommunityEvent

    window = DEFAULT_WINDOW
    upcoming = (
        CommunityEvent.objects.published()
        .filter(starts_at__gte=now, starts_at__lte=now + timedelta(days=7) + window)
        .select_related("guild")
    )
    for event in upcoming:
        for days in event.enabled_reminder_offsets():
            yield ScheduledOccurrence(
                event_key=EVENT_REMINDER,
                anchor=event.starts_at,
                offset=timedelta(days=-days),
                target=event,
                context=_event_context(event, days_before=days),
                url=event.absolute_url,
                period=f"event:{event.pk}:reminder:{days}d",
            )


def event_happening_now_occurrences(now: datetime) -> Iterable[ScheduledOccurrence]:
    """One 'starting now' occurrence per published, upcoming event that opted in (a scheduler source)."""
    from membership.models import CommunityEvent

    window = DEFAULT_WINDOW
    starting = (
        CommunityEvent.objects.published()
        .filter(notify_happening_now=True, starts_at__gte=now, starts_at__lte=now + window)
        .select_related("guild")
    )
    for event in starting:
        yield ScheduledOccurrence(
            event_key=EVENT_HAPPENING_NOW,
            anchor=event.starts_at,
            offset=timedelta(0),
            target=event,
            context=_event_context(event, days_before=None),
            url=event.absolute_url,
            period=f"event:{event.pk}:happening_now",
        )

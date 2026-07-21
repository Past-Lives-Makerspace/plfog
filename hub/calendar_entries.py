"""Lightweight calendar-entry wrappers.

A guild's CMS classes and orientation slots don't live in ``CalendarEvent`` (the
iCal read-through cache) — class events there have ``guild=None`` and orientation
slots aren't calendar events at all. To show them on the shared calendar grid we
wrap them in objects that duck-type the attributes the calendar templates read
(``templates/hub/partials/calendar_content.html`` and ``calendar_event_item.html``):
``pk, title, start_dt, end_dt, all_day, source, source_key, is_in_progress, url,
location, description, guild, feed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.utils import timezone

if TYPE_CHECKING:
    from membership.models import CommunityEvent, Guild

# Offsets keep synthetic pks clear of real CalendarEvent pks so the shared
# focusEvent() JS and the month_event_pages map keep working untouched.
CLASS_PK_OFFSET = 1_000_000_000
ORIENTATION_PK_OFFSET = 2_000_000_000
EVENT_PK_OFFSET = 3_000_000_000
_OCC_STRIDE = 100  # max occurrences per event per window (a few months of monthly « 100)

# How far ahead the Events tab looks for a recurring series' next occurrence. A
# year comfortably contains the next hit for any monthly/weekly cadence, so each
# recurring event resolves to one upcoming row instead of its stale anchor date.
_EVENTS_TAB_HORIZON_DAYS = 365


@dataclass
class CalendarEntry:
    """Duck-types the CalendarEvent attributes the calendar templates read."""

    pk: int
    title: str
    start_dt: datetime
    end_dt: datetime
    source: str
    url: str = ""
    location: str = ""
    description: str = ""
    all_day: bool = False
    guild: Guild | None = None
    feed: None = None
    # The backing FOG-native event for a "community" entry, so the templates can draw
    # its Google-sync flag and (on the Events tab) its admin Edit/Delete controls.
    # Always None for feed / class / orientation entries.
    community_event: CommunityEvent | None = None

    @property
    def source_key(self) -> str:
        # Only classes route by their guild's color. Orientation stays "orientation"
        # and community stays "community" even though those entries also carry a guild
        # object — don't recolor them by guild.
        if self.source == "classes" and self.guild is not None:
            return str(self.guild.pk)
        return self.source

    @property
    def is_in_progress(self) -> bool:
        if self.all_day:
            return False
        return self.start_dt <= timezone.now() < self.end_dt


def guild_calendar_entries(guild: Guild, fetch_from: date, fetch_to: date) -> list[CalendarEntry]:
    """Build synthetic calendar entries for a guild's published class sessions and
    upcoming orientation slots whose start date falls within ``[fetch_from, fetch_to]``."""
    from django.urls import reverse

    from classes.models import ClassOffering, ClassSession

    entries: list[CalendarEntry] = []

    # Gate on the catalog's bookable() rule (published + non-private + not-yet-started)
    # so per-guild calendars drop a started series exactly as the catalog and the
    # Community Calendar do. Materialized pk list avoids a correlated subquery.
    bookable_ids = ClassOffering.objects.bookable().values_list("pk", flat=True)
    sessions = ClassSession.objects.filter(
        class_offering__category__guild=guild,
        class_offering_id__in=bookable_ids,
        starts_at__date__gte=fetch_from,
        starts_at__date__lte=fetch_to,
    ).select_related("class_offering")
    for session in sessions:
        offering = session.class_offering
        entries.append(
            CalendarEntry(
                pk=CLASS_PK_OFFSET + session.pk,
                title=offering.title,
                start_dt=session.starts_at,
                end_dt=session.ends_at,
                source="classes",
                url=reverse("classes:register", args=[offering.slug]),
                guild=guild,
            )
        )

    slots = guild.orientation_slots.upcoming().filter(starts_at__date__gte=fetch_from, starts_at__date__lte=fetch_to)
    for slot in slots:
        entries.append(
            CalendarEntry(
                pk=ORIENTATION_PK_OFFSET + slot.pk,
                title="Orientation",
                start_dt=slot.starts_at,
                end_dt=slot.ends_at,
                source="orientation",
                location=slot.location,
                guild=guild,
            )
        )

    return entries


def community_event_entries(fetch_from: date, fetch_to: date, guild: Guild | None = None) -> list[CalendarEntry]:
    """Build synthetic calendar entries for FOG-native ``CommunityEvent`` rows that
    contribute an occurrence to ``[fetch_from, fetch_to]``.

    A monthly series expands to one entry per in-window occurrence; a non-recurring
    event yields a single entry. ``guild=None`` returns site-wide + every guild's
    events (the Community Calendar); a guild returns just that guild's events.
    """
    from membership.models import CommunityEvent

    # Only PUBLISHED events surface on the calendar — pending/changes-requested/declined
    # member proposals must never leak onto the public grid.
    qs = CommunityEvent.objects.published().candidates_for_window(fetch_from, fetch_to)
    if guild is not None:
        qs = qs.for_guild(guild)

    entries: list[CalendarEntry] = []
    for ev in qs.select_related("guild"):
        duration = ev.ends_at - ev.starts_at
        for i, occ_start in enumerate(ev.occurrences_in(fetch_from, fetch_to)):
            entries.append(
                CalendarEntry(
                    # Unique synthetic pk per occurrence: base offset + ev.pk*stride + index.
                    pk=EVENT_PK_OFFSET + ev.pk * _OCC_STRIDE + i,
                    title=ev.title,
                    start_dt=occ_start,
                    end_dt=occ_start + duration,
                    source="community",
                    url=ev.absolute_url,
                    location=ev.location,
                    description=ev.description,
                    all_day=False,
                    guild=ev.guild,
                    community_event=ev,
                )
            )
    return entries


def upcoming_calendar_events() -> list[Any]:
    """Every upcoming event on the shared Community Calendar as one sorted list.

    This is the source for the Community Calendar's **Events tab**, built so the tab
    lists nothing narrower than the grid: the read-through feed / general / class
    ``CalendarEvent`` rows (echo-deduped exactly like the grid) *plus* one entry per
    published ``CommunityEvent`` at its next occurrence. A recurring series collapses
    to a single upcoming row (its next hit), so admins get one Edit/Delete affordance
    per event rather than one per occurrence.
    """
    from membership.models import CalendarEvent, CommunityEvent

    now = timezone.now()
    today = now.date()
    horizon = today + timedelta(days=_EVENTS_TAB_HORIZON_DAYS)

    # Feed / general / class iCal rows still upcoming, minus the iCal echo of any event
    # FOG itself pushed to Google (matched by the stored google_ical_uid) — the same
    # de-dup the grid applies, so a FOG event is never listed twice.
    pushed_uids = CommunityEvent.objects.pushed().values_list("google_ical_uid", flat=True)
    entries: list[Any] = list(
        CalendarEvent.objects.upcoming().exclude(uid__in=pushed_uids).select_related("guild", "feed")
    )

    # One entry per published CommunityEvent, anchored on its next (or in-progress) occurrence.
    for ev in CommunityEvent.objects.published().upcoming().select_related("guild"):
        duration = ev.ends_at - ev.starts_at
        upcoming_occ = [occ for occ in ev.occurrences_in(today, horizon) if occ + duration >= now]
        # No in-window occurrence → a still-running non-recurring event (started before
        # today) or one starting past the horizon: fall back to its own start.
        start = upcoming_occ[0] if upcoming_occ else ev.starts_at
        entries.append(
            CalendarEntry(
                pk=EVENT_PK_OFFSET + ev.pk * _OCC_STRIDE,
                title=ev.title,
                start_dt=start,
                end_dt=start + duration,
                source="community",
                url=ev.absolute_url,
                location=ev.location,
                description=ev.description,
                guild=ev.guild,
                community_event=ev,
            )
        )
    return sorted(entries, key=lambda e: e.start_dt)

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
from datetime import date, datetime
from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from membership.models import Guild

# Offsets keep synthetic pks clear of real CalendarEvent pks so the shared
# focusEvent() JS and the month_event_pages map keep working untouched.
CLASS_PK_OFFSET = 1_000_000_000
ORIENTATION_PK_OFFSET = 2_000_000_000
EVENT_PK_OFFSET = 3_000_000_000
_OCC_STRIDE = 100  # max occurrences per event per window (a few months of monthly « 100)


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

    @property
    def source_key(self) -> str:
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

    qs = CommunityEvent.objects.candidates_for_window(fetch_from, fetch_to)
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
                )
            )
    return entries

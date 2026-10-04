"""The dates a calendar render covers, and the grid, labels and lists drawn from them.

Every calendar shares this: the Community Calendar and a guild's (``hub.views._get_calendar_context``),
and the Orientations and Reservations calendars (``hub.calendar_pages``), so the date
arithmetic lives once. Events are placed on their local day (:func:`hub.calendar_entries.calendar_day`).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, NamedTuple

from django.utils import timezone

from hub.calendar_entries import calendar_day

#: Events per page in a calendar's Month list (and the Community Calendar's Events tab).
CALENDAR_PAGE_SIZE = 10


class CalendarWindow(NamedTuple):
    """The dates one calendar render covers: the navigated week and the rolling 4-week window."""

    today: date
    week_start: date
    week_end: date
    window_start: date
    window_end: date

    @property
    def fetch_from(self) -> date:
        """The first date any event must be read for: the earlier of the week and the window."""
        return min(self.week_start, self.window_start)

    @property
    def fetch_to(self) -> date:
        """The last date any event must be read for."""
        return max(self.week_end, self.window_end)

    def covers(self, day: date) -> bool:
        """Whether a render of this window draws anything on ``day`` (a :func:`calendar_day`)."""
        return self.fetch_from <= day <= self.fetch_to


def calendar_window(week_offset: int, month_offset: int) -> CalendarWindow:
    """The week and the rolling 4-week window for these offsets.

    The "month" view is a rolling 4-week window starting from the current week (current
    week + 3 upcoming weeks); ``month_offset`` shifts it in 4-week chunks, so members
    navigating forward see the next 4 weeks rather than jumping to a calendar month boundary.
    """
    # The local date: from 5 PM in Portland the UTC date is tomorrow, which moved "today".
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    week_start = current_week_start + timedelta(weeks=week_offset)
    window_start = current_week_start + timedelta(weeks=4 * month_offset)
    return CalendarWindow(
        today=today,
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        window_start=window_start,
        window_end=window_start + timedelta(days=27),
    )


def calendar_window_context(
    all_events: list[Any], window: CalendarWindow, *, week_offset: int, month_offset: int, event_page: int
) -> dict[str, Any]:
    """The grid, labels and paginated lists ``calendar_content.html`` draws from ``all_events``.

    Shared by every calendar (the Community Calendar, a guild's, the Orientations and the
    Reservations calendars), so the date arithmetic lives once. ``all_events`` is every
    event read for ``window.fetch_from`` to ``window.fetch_to``, sorted by start.

    Args:
        all_events: CalendarEvent rows and duck-typed CalendarEntry objects, sorted by start.
        window: The dates these offsets cover (:func:`calendar_window`).
        week_offset: Weeks relative to the current week (negative = past, positive = future).
        month_offset: 4-week chunks relative to the current window (negative = past, positive = future).
        event_page: 1-based page number for the event list (PAGE_SIZE events per page).
    """
    now = timezone.now()
    today, week_start, week_end = window.today, window.week_start, window.week_end
    window_start, window_end = window.window_start, window.window_end

    # Every event's local date, read once: the grids, the lists and the window all use it,
    # so an evening event sits on its own day (a UTC date put it on the next one).
    dated = [(calendar_day(e), e) for e in all_events]

    # Week event list: events whose start date falls within the navigated week
    week_events = [e for day, e in dated if week_start <= day <= week_end]

    # Month-view event list: events whose start date falls within the 4-week window (paginated)
    raw_month_events = [e for day, e in dated if window_start <= day <= window_end]
    total_pages = max(1, (len(raw_month_events) + CALENDAR_PAGE_SIZE - 1) // CALENDAR_PAGE_SIZE)
    event_page = max(1, min(event_page, total_pages))
    page_start = (event_page - 1) * CALENDAR_PAGE_SIZE
    month_events = raw_month_events[page_start : page_start + CALENDAR_PAGE_SIZE]

    # Map every event in the 4-week window to its 1-based pagination page so chip
    # clicks for events on a different page can hop pages before scrolling.
    month_event_pages: dict[int, int] = {
        evt.pk: (idx // CALENDAR_PAGE_SIZE) + 1 for idx, evt in enumerate(raw_month_events)
    }

    # Group events by date for calendar grid dots
    events_by_date: dict = defaultdict(list)
    for day, evt in dated:
        events_by_date[day].append(evt)

    # Week label (e.g. "Apr 14 – 20, 2026" or "Apr 28 – May 4, 2026")
    if week_start.month == week_end.month and week_start.year == week_end.year:
        week_label = f"{week_start.strftime('%b %-d')} – {week_end.strftime('%-d')}, {week_end.year}"
    else:
        week_label = f"{week_start.strftime('%b %-d')} – {week_end.strftime('%b %-d')}, {week_end.year}"

    # Week grid: 7 days starting from navigated Monday
    week_days = [
        {
            "date": week_start + timedelta(days=i),
            "is_today": (week_start + timedelta(days=i)) == today,
            "events": events_by_date.get(week_start + timedelta(days=i), []),
        }
        for i in range(7)
    ]

    # Window label, e.g. "Apr 27 – May 24, 2026" or "Dec 28, 2025 – Jan 24, 2026"
    if window_start.year != window_end.year:
        month_label = f"{window_start.strftime('%b %-d, %Y')} – {window_end.strftime('%b %-d, %Y')}"
    elif window_start.month == window_end.month:
        month_label = f"{window_start.strftime('%b %-d')} – {window_end.strftime('%-d')}, {window_end.year}"
    else:
        month_label = f"{window_start.strftime('%b %-d')} – {window_end.strftime('%b %-d')}, {window_end.year}"

    # 4-week grid: 28 days (Mon–Sun, exactly 4 rows). Every cell is "in window".
    month_headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    month_days = []
    for i in range(28):
        d = window_start + timedelta(days=i)
        month_days.append({"date": d, "is_today": d == today, "in_month": True, "events": events_by_date.get(d, [])})

    return {
        "week_events": week_events,
        "month_events": month_events,
        "event_page": event_page,
        "event_total_pages": total_pages,
        "week_days": week_days,
        "week_label": week_label,
        "week_offset": week_offset,
        "month_days": month_days,
        "month_headers": month_headers,
        "month_label": month_label,
        "month_offset": month_offset,
        "month_event_pages_json": json.dumps(month_event_pages),
        "now": now,
    }

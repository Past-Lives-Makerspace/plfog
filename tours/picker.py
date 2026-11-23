"""The month calendar and a day's tour times, shared by the visitor's pages (#948) and the staff reschedule (#949).

Built from a list of :class:`~tours.availability.OpenTour`: the days that have a tour, one of
them picked, and that day's times. Each page renders it in its own markup (the tours site's
links, the Tours page modal's HTMX buttons); only the shape is shared.

Visitors see each time once (#1312, :func:`tours.availability.open_tours`). A tour someone has
already booked is one to join: its day is marked on the calendar, and on the visitor's pages
(``join_first``) it comes first in the day's times, saying how many people are going.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

from membership.models import Member
from tours.availability import OpenTour, parse_tour_key
from tours.notify import guide_first_name


@dataclass(frozen=True)
class DayChoice:
    """One day in the calendar. ``going`` when a tour that day already has people booked and room left (#1312)."""

    day: date
    selected: bool
    going: bool = False


@dataclass(frozen=True)
class TimeChoice:
    """One tour among the picked day's times. ``guide_name`` is set where the page names guides.

    At the kiosk (#1273, ``name_guides``) every time names its guide; on the staff Reschedule, a
    time two guides give names each. ``going`` is how many people are already booked on it (#1312).
    """

    tour: OpenTour
    guide_name: str

    @property
    def going(self) -> int:
        """People already booked on this tour (the parties' sizes), 0 for a time nobody has booked."""
        return self.tour.seats_taken


@dataclass(frozen=True)
class CalendarCell:
    """One square of the month calendar: ``day`` is None outside the month, ``choice`` None on a day with no tour."""

    day: date | None
    choice: DayChoice | None = None


@dataclass(frozen=True)
class CalendarMonth:
    """One month of the booking page's calendar: ``first`` is its first day, ``weeks`` its rows, Sunday first."""

    first: date
    weeks: list[list[CalendarCell]]


#: The calendar's weekday order (Morlock: Sunday first, like a US wall calendar). ``calendar`` counts Monday as 0.
SUNDAY_FIRST = 6


def calendar_months(days: list[DayChoice]) -> list[CalendarMonth]:
    """``days`` laid out as month calendars (Morlock, 2026-11-22), from the first day's month to the last's.

    Every week is seven cells, Sunday to Saturday. A cell outside its month is blank, a day without a
    tour has no choice (drawn greyed), and a day with one carries its :class:`DayChoice`.
    """
    if not days:
        return []
    by_day = {choice.day: choice for choice in days}
    first, last = min(by_day), max(by_day)
    grid = calendar.Calendar(firstweekday=SUNDAY_FIRST)
    months: list[CalendarMonth] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        weeks = [
            [
                CalendarCell(day=day, choice=by_day.get(day)) if day.month == month else CalendarCell(day=None)
                for day in week
            ]
            for week in grid.monthdatescalendar(year, month)
        ]
        months.append(CalendarMonth(first=date(year, month, 1), weeks=weeks))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


#: The calendar's column headings, in :data:`SUNDAY_FIRST` order.
WEEKDAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


@dataclass(frozen=True)
class Picker:
    """The days with tours (a month calendar on the booking pages, a strip in the staff Reschedule) and the picked day's times."""

    days: list[DayChoice]
    day: date | None
    times: list[TimeChoice]

    @property
    def months(self) -> list[CalendarMonth]:
        """:attr:`days` as month calendars, for ``tours/public/_picker.html``."""
        return calendar_months(self.days)

    @property
    def weekday_names(self) -> tuple[str, ...]:
        return WEEKDAY_NAMES

    @property
    def any_going(self) -> bool:
        """Whether any day is marked as having people going (#1312): the calendar's key shows only then."""
        return any(choice.going for choice in self.days)


def build_picker(tours: list[OpenTour], raw_day: str, *, name_guides: bool = False, join_first: bool = False) -> Picker:
    """The days that have a tour, ``raw_day`` (``YYYY-MM-DD``) picked when it is one of them, else the first.

    A day with a tour people already booked, with room left, is marked ``going`` (#1312). With
    ``join_first`` (the visitor's pages) the day's booked tours come first, then the rest, each in
    time order. A time one guide gives is named only with ``name_guides`` (the kiosk, #1273); where
    two guides give one time (the staff Reschedule) each choice names its guide.
    """
    by_day: dict[date, list[OpenTour]] = {}
    for tour in tours:
        by_day.setdefault(timezone.localdate(tour.starts_at), []).append(tour)
    try:
        wanted: date | None = date.fromisoformat(raw_day)
    except ValueError:
        wanted = None
    picked = wanted if wanted in by_day else next(iter(by_day), None)
    day_tours = by_day.get(picked, []) if picked is not None else []
    by_start: dict[datetime, list[OpenTour]] = {}
    for tour in day_tours:
        by_start.setdefault(tour.starts_at, []).append(tour)
    times: list[TimeChoice] = []
    for same in by_start.values():
        if len(same) == 1:
            times.append(TimeChoice(tour=same[0], guide_name=guide_first_name(same[0].guide) if name_guides else ""))
        else:
            times.extend(TimeChoice(tour=tour, guide_name=guide_first_name(tour.guide)) for tour in same)
    if join_first:
        times.sort(key=lambda choice: (choice.going == 0, choice.tour.starts_at))
    days = [
        DayChoice(day=day, selected=day == picked, going=any(not tour.is_full and tour.seats_taken for tour in listed))
        for day, listed in by_day.items()
    ]
    return Picker(days=days, day=picked, times=times)


def find_tour(tours: list[OpenTour], key: str) -> OpenTour | None:
    """The tour ``key`` names among ``tours``, or None."""
    return next((tour for tour in tours if tour.key == key), None)


def guide_and_start(key: str) -> tuple[Member, datetime] | None:
    """The guide and UTC start a posted tour key names, or None for a key that names no member."""
    parsed = parse_tour_key(key)
    if parsed is None:
        return None
    guide = Member.objects.filter(pk=parsed[0]).first()
    return (guide, parsed[1]) if guide is not None else None

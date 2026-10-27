"""The date strip and a day's tour times, shared by the visitor's pages (#948) and the staff reschedule (#949).

Built from a list of :class:`~tours.availability.OpenTour`: the days that have a tour, one of
them picked, and that day's times. Each page renders it in its own markup (the tours site's
links, the Tours page modal's HTMX buttons); only the shape is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone

from membership.models import Member
from tours.availability import OpenTour, parse_tour_key
from tours.notify import guide_first_name


@dataclass(frozen=True)
class DayChoice:
    """One day in the date strip."""

    day: date
    selected: bool


@dataclass(frozen=True)
class TimeChoice:
    """One tour among the picked day's times. ``guide_name`` is set when two guides share the time."""

    tour: OpenTour
    guide_name: str


@dataclass(frozen=True)
class Picker:
    """The date strip and the picked day's times."""

    days: list[DayChoice]
    day: date | None
    times: list[TimeChoice]


def build_picker(tours: list[OpenTour], raw_day: str) -> Picker:
    """The days that have a tour, ``raw_day`` (``YYYY-MM-DD``) picked when it is one of them, else the first."""
    by_day: dict[date, list[OpenTour]] = {}
    for tour in tours:
        by_day.setdefault(timezone.localdate(tour.starts_at), []).append(tour)
    try:
        wanted: date | None = date.fromisoformat(raw_day)
    except ValueError:
        wanted = None
    picked = wanted if wanted in by_day else next(iter(by_day), None)
    day_tours = by_day.get(picked, []) if picked is not None else []
    starts = [tour.starts_at for tour in day_tours]
    times = [
        TimeChoice(tour=tour, guide_name=guide_first_name(tour.guide) if starts.count(tour.starts_at) > 1 else "")
        for tour in day_tours
    ]
    return Picker(days=[DayChoice(day=day, selected=day == picked) for day in by_day], day=picked, times=times)


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

"""The date strip and a day's tour times, shared by the visitor's pages (#948) and the staff reschedule (#949).

Built from a list of :class:`~tours.availability.OpenTour`: the days that have a tour, one of
them picked, and that day's times. Each page renders it in its own markup (the tours site's
links, the Tours page modal's HTMX buttons); only the shape is shared.

A time two or more guides share can also offer No preference (#1255), on the booking pages only:
its key is ``any-<UTC start>`` and :func:`tours.booking.book_any_guide` books it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import ClassVar

from django.utils import timezone

from membership.models import Member
from tours.availability import OpenTour, parse_tour_key
from tours.notify import guide_first_name


#: The first part of a No preference key (#1255), where a tour key has the guide's pk.
ANY_GUIDE = "any"


@dataclass(frozen=True)
class NoPreference(OpenTour):
    """No preference among the guides sharing one start (#1255).

    Shown with the most seats any one of them has left, since a party is never split. Its key names
    the start alone.
    """

    no_preference: ClassVar[bool] = True

    @property
    def key(self) -> str:
        """``any-<UTC start as YYYYMMDDTHHMMZ>``: the booking form's choice."""
        return f"{ANY_GUIDE}-{self.starts_at:%Y%m%dT%H%MZ}"

    @classmethod
    def among(cls, tours: list[OpenTour]) -> NoPreference:
        """The choice for ``tours`` (one start, two or more guides), carrying the roomiest one's seats."""
        roomiest = max(tours, key=lambda tour: tour.seats_left)
        return cls(
            guide=roomiest.guide,
            hours_id=roomiest.hours_id,
            starts_at=roomiest.starts_at,
            ends_at=roomiest.ends_at,
            seats=roomiest.seats,
            seats_taken=roomiest.seats_taken,
        )


def parse_any_guide_key(raw: str) -> datetime | None:
    """The UTC start a No preference key names, or None when ``raw`` is not one."""
    prefix, _, stamp = raw.partition("-")
    if prefix != ANY_GUIDE:
        return None
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass(frozen=True)
class DayChoice:
    """One day in the date strip."""

    day: date
    selected: bool


@dataclass(frozen=True)
class TimeChoice:
    """One tour among the picked day's times. ``guide_name`` is set when two guides share the time.

    ``tour`` is a :class:`NoPreference` for that time's No preference choice (#1255).
    """

    tour: OpenTour
    guide_name: str
    no_preference: bool = False


@dataclass(frozen=True)
class Picker:
    """The date strip and the picked day's times."""

    days: list[DayChoice]
    day: date | None
    times: list[TimeChoice]


def build_picker(tours: list[OpenTour], raw_day: str, *, no_preference: bool = False) -> Picker:
    """The days that have a tour, ``raw_day`` (``YYYY-MM-DD``) picked when it is one of them, else the first.

    With ``no_preference`` (the visitor's booking page and the kiosk, #1255), a time two or more
    guides share gets a No preference choice after theirs.
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
    starts = [tour.starts_at for tour in day_tours]
    last_index = {tour.starts_at: index for index, tour in enumerate(day_tours)}
    times: list[TimeChoice] = []
    for index, tour in enumerate(day_tours):
        shared = starts.count(tour.starts_at) > 1
        times.append(TimeChoice(tour=tour, guide_name=guide_first_name(tour.guide) if shared else ""))
        if no_preference and shared and last_index[tour.starts_at] == index:
            same = [other for other in day_tours if other.starts_at == tour.starts_at]
            times.append(TimeChoice(tour=NoPreference.among(same), guide_name="", no_preference=True))
    return Picker(days=[DayChoice(day=day, selected=day == picked) for day in by_day], day=picked, times=times)


def find_tour(tours: list[OpenTour], key: str) -> OpenTour | None:
    """The tour ``key`` names among ``tours``, or None.

    A No preference key (#1255) names a :class:`NoPreference` when two or more guides offer that start.
    """
    starts_at = parse_any_guide_key(key)
    if starts_at is not None:
        same = [tour for tour in tours if tour.starts_at == starts_at]
        return NoPreference.among(same) if len(same) > 1 else None
    return next((tour for tour in tours if tour.key == key), None)


def guide_and_start(key: str) -> tuple[Member, datetime] | None:
    """The guide and UTC start a posted tour key names, or None for a key that names no member."""
    parsed = parse_tour_key(key)
    if parsed is None:
        return None
    guide = Member.objects.filter(pk=parsed[0]).first()
    return (guide, parsed[1]) if guide is not None else None

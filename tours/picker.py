"""The date strip and a day's tour times, shared by the visitor's pages (#948) and the staff reschedule (#949).

Built from a list of :class:`~tours.availability.OpenTour`: the days that have a tour, one of
them picked, and that day's times. Each page renders it in its own markup (the tours site's
links, the Tours page modal's HTMX buttons); only the shape is shared.

On the booking pages (#1255) a time two or more guides share is one button, keyed No preference
(``any-<UTC start>``, booked by :func:`tours.booking.book_any_guide`); the form then offers the
Guide dropdown (:func:`guide_choices`): No preference first, then each guide.
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

    On the booking pages (#1255) a time two or more guides share is one choice, ``tour`` a
    :class:`NoPreference` and ``no_preference`` set, with no guide named. At the kiosk (#1273,
    ``name_guides``) every time names its guide, and a No preference time all of them ("Amber or Sam").
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


#: The Guide dropdown's first option (#1255).
NO_PREFERENCE_LABEL = "No preference"


@dataclass(frozen=True)
class GuideChoice:
    """One option of the booking form's Guide dropdown (#1255): No preference, or one guide's tour."""

    key: str
    name: str
    seats_left: int
    no_preference: bool = False


def either_guide(tours: list[OpenTour]) -> str:
    """The guides of ``tours`` by first name, ``Amber or Sam`` (``Amber, Sam or Lee``); a guide with no name is left out."""
    names = [name for name in (guide_first_name(tour.guide) for tour in tours) if name]
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} or {names[-1]}"


def build_picker(
    tours: list[OpenTour], raw_day: str, *, no_preference: bool = False, name_guides: bool = False
) -> Picker:
    """The days that have a tour, ``raw_day`` (``YYYY-MM-DD``) picked when it is one of them, else the first.

    With ``no_preference`` (the visitor's booking page and the kiosk, #1255), a time two or more
    guides share is one choice, No preference, carrying the most seats any one of them has left;
    the form's Guide dropdown then names them. Without it (the Reschedule pages) each guide's tour
    is its own choice, named. With ``name_guides`` (the kiosk, #1273) a time one guide gives names
    that guide too, and a No preference time names every guide sharing it.
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
            times.append(TimeChoice(tour=same[0], guide_name=either_guide(same) if name_guides else ""))
        elif no_preference:
            named = either_guide(same) if name_guides else ""
            times.append(TimeChoice(tour=NoPreference.among(same), guide_name=named, no_preference=True))
        else:
            times.extend(TimeChoice(tour=tour, guide_name=guide_first_name(tour.guide)) for tour in same)
    return Picker(days=[DayChoice(day=day, selected=day == picked) for day in by_day], day=picked, times=times)


def guide_choices(tours: list[OpenTour], chosen: OpenTour) -> list[GuideChoice]:
    """The Guide dropdown for ``chosen``'s time (#1255): No preference, then each guide by their public
    first name, in availability's order. Empty when one guide alone offers that time, so no dropdown shows.
    """
    same = [tour for tour in tours if tour.starts_at == chosen.starts_at]
    if len(same) < 2:
        return []
    anyone = NoPreference.among(same)
    return [
        GuideChoice(key=anyone.key, name=NO_PREFERENCE_LABEL, seats_left=anyone.seats_left, no_preference=True),
        *(GuideChoice(key=tour.key, name=guide_first_name(tour.guide), seats_left=tour.seats_left) for tour in same),
    ]


def party_room(guides: list[GuideChoice], chosen: OpenTour) -> int:
    """The largest party the form offers: the roomiest guide's seats where the Guide dropdown shows
    (No preference can take it), else the chosen tour's."""
    return max((guide.seats_left for guide in guides), default=chosen.seats_left)


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

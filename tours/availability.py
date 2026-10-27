"""Open facility tour times, computed live from the guides' hours (#947).

Nothing is generated ahead: every read carves :class:`~tours.models.TourHours` into tours, drops
the days a guide is off, everything past the booking horizon and everything inside the minimum
notice (:class:`~tours.models.FacilityTourSettings`), and counts the seats bookings hold. A change
to the hours, a day off or a setting is in the very next answer.

Two entry points:

- :func:`open_tours` lists the tours visitors can book over a range of days (the booking page's
  date strip and times, part 2; staff reschedule, part 3).
- :func:`open_tour_at` checks one tour, by guide and start, and with ``lock=True`` locks the hours
  rows that yield it. Booking (part 2) calls it inside ``transaction.atomic()`` before counting
  seats and saving, so two visitors racing for the last seat queue on the same row lock and the
  second one sees the first one's booking.

Neither reads the "Accepting tour bookings" switch: the booking page decides what a closed site
shows, and staff keep moving tours while it is closed.

Every instant here is compared and keyed in UTC (#948). On the fall back night the carve yields
1:00 AM in the repeated hour as a local datetime whose offset depends on ``fold``; PEP 495 makes
such a value never compare equal to the same instant in another zone, so a booking read back from
the database (UTC) would miss its tour. :class:`OpenTour` therefore carries UTC instants (templates
show them in local time through the ``date`` filter as always).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from membership.models import Member
from tours.models import FacilityTourSettings, TourBooking, TourDayOff, TourHours


@dataclass(frozen=True)
class OpenTour:
    """One tour: a guide, a start and end (both UTC), and its seats.

    ``hours_id`` is the :class:`~tours.models.TourHours` row that yields it.
    """

    guide: Member
    hours_id: int
    starts_at: datetime
    ends_at: datetime
    seats: int
    seats_taken: int

    @property
    def seats_left(self) -> int:
        """Seats still free, never below zero (a guide may lower seats under existing bookings)."""
        return max(self.seats - self.seats_taken, 0)

    @property
    def is_full(self) -> bool:
        """Whether no seat is left."""
        return self.seats_left == 0

    @property
    def key(self) -> str:
        """This tour as one string, ``<guide pk>-<UTC start as YYYYMMDDTHHMMZ>``: the booking form's choice."""
        return f"{self.guide.pk}-{self.starts_at:%Y%m%dT%H%MZ}"


@dataclass(frozen=True)
class BookingWindow:
    """The days and earliest start visitors may book right now, from the settings.

    ``first_day`` is today and ``last_day`` is today plus the horizon (both local, both included,
    as the equipment horizon counts it). ``earliest_start`` is now plus the minimum notice.
    """

    first_day: date
    last_day: date
    earliest_start: datetime

    @classmethod
    def at(cls, now: datetime, tour_settings: FacilityTourSettings) -> BookingWindow:
        """The window as it stands at ``now``."""
        today = timezone.localdate(now)
        return cls(
            first_day=today,
            last_day=today + timedelta(days=tour_settings.booking_horizon_days),
            earliest_start=now + timedelta(hours=tour_settings.min_notice_hours),
        )

    def admits(self, starts_at: datetime) -> bool:
        """Whether a tour starting at ``starts_at`` may be booked now (compared in UTC)."""
        start = starts_at.astimezone(UTC)
        return start >= self.earliest_start.astimezone(UTC) and timezone.localdate(start) <= self.last_day


def parse_tour_key(raw: str) -> tuple[int, datetime] | None:
    """The guide pk and UTC start an :attr:`OpenTour.key` names, or None when ``raw`` is not one."""
    guide_pk, _, stamp = raw.partition("-")
    if not guide_pk.isdigit():
        return None
    try:
        starts_at = datetime.strptime(stamp, "%Y%m%dT%H%MZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return int(guide_pk), starts_at


def _local_midnight(day: date) -> datetime:
    """The aware start of a local day."""
    return timezone.make_aware(datetime.combine(day, time.min))


def open_tours(
    first_day: date,
    last_day: date,
    *,
    guide: Member | None = None,
    include_full: bool = False,
    now: datetime | None = None,
) -> list[OpenTour]:
    """The tours visitors can book from ``first_day`` to ``last_day`` (local days, both included).

    Each offered guide's hours are carved into tours of the row's length; a guide's days off, any
    tour past the booking horizon and any tour inside the minimum notice drop out. Two guides with
    the same hours give two tours. A tour with no seat left drops out too unless ``include_full``.
    Four queries however long the range (settings, hours, days off, seats), none when no guide has
    hours.

    Args:
        first_day: the first local day to list; days before today are skipped.
        last_day: the last local day to list; days past the horizon are skipped.
        guide: only this guide's tours, when given.
        include_full: keep tours with no seat left (with ``seats_left`` 0).
        now: the moment to judge the horizon and notice from; defaults to now.

    Returns:
        The tours, soonest first, guides by name within a start time.
    """
    window = BookingWindow.at(now or timezone.now(), FacilityTourSettings.load())
    first_day = max(first_day, window.first_day)
    last_day = min(last_day, window.last_day)
    if last_day < first_day:
        return []
    rows_query = TourHours.objects.offered().select_related("guide")
    if guide is not None:
        rows_query = rows_query.filter(guide=guide)
    rows = list(rows_query)
    if not rows:
        return []
    guide_ids = {row.guide_id for row in rows}
    days_off = list(TourDayOff.objects.touching(first_day, last_day).filter(guide_id__in=guide_ids))
    taken = TourBooking.objects.seats_taken_by_tour(
        guide_ids, _local_midnight(first_day), _local_midnight(last_day + timedelta(days=1))
    )
    tours: list[OpenTour] = []
    seen: set[tuple[int, datetime]] = set()
    day = first_day
    while day <= last_day:
        off = {day_off.guide_id for day_off in days_off if day_off.covers(day)}
        for row in rows:
            if row.weekday != day.weekday() or row.guide_id in off:
                continue
            for span_start, span_end in row.carve_spans(day):
                starts_at, ends_at = span_start.astimezone(UTC), span_end.astimezone(UTC)
                key = (row.guide_id, starts_at)
                # A guide's rows never overlap through the editor; one written another way must not
                # offer the same tour twice.
                if key in seen or not window.admits(starts_at):
                    continue
                seen.add(key)
                tour = OpenTour(
                    guide=row.guide,
                    hours_id=row.pk,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    seats=row.seats,
                    seats_taken=taken.get(key, 0),
                )
                if include_full or not tour.is_full:
                    tours.append(tour)
        day += timedelta(days=1)
    tours.sort(key=lambda tour: (tour.starts_at, tour.guide.display_name.casefold()))
    return tours


def open_tour_at(
    guide: Member, starts_at: datetime, *, lock: bool = False, now: datetime | None = None
) -> OpenTour | None:
    """The guide's tour starting at ``starts_at`` if visitors may book it now, full or not; else None.

    None when the guide gives no tours, no hours of theirs yield that start, they are off that
    day, or the start is past the horizon or inside the minimum notice. A full tour comes back
    with ``seats_left`` 0, so a booking page can say it just filled up rather than that it never
    existed.

    Args:
        guide: the tour's guide.
        starts_at: the tour's aware start.
        lock: lock the guide's hours rows for that weekday (``SELECT ... FOR UPDATE``) before the
            seats are counted. Booking passes True inside ``transaction.atomic()`` and saves its
            row before the transaction ends; every booking of one tour takes the same lock, so
            the seat count each one reads includes every booking committed before it.
        now: the moment to judge the horizon and notice from; defaults to now.

    Returns:
        The tour, or None.

    Raises:
        RuntimeError: ``lock`` outside a transaction, where the lock would end at once.
    """
    if lock and not transaction.get_connection().in_atomic_block:
        raise RuntimeError("open_tour_at(lock=True) must run inside transaction.atomic().")
    starts_at = starts_at.astimezone(UTC)
    window = BookingWindow.at(now or timezone.now(), FacilityTourSettings.load())
    if not window.admits(starts_at):
        return None
    day = timezone.localdate(starts_at)
    rows_query = TourHours.objects.offered().filter(guide=guide, weekday=day.weekday())
    if lock:
        rows_query = rows_query.select_for_update()
    for row in rows_query:
        for span_start, span_end in row.carve_spans(day):
            if span_start.astimezone(UTC) != starts_at:
                continue
            if TourDayOff.objects.filter(guide=guide).touching(day, day).exists():
                return None
            return OpenTour(
                guide=guide,
                hours_id=row.pk,
                starts_at=starts_at,
                ends_at=span_end.astimezone(UTC),
                seats=row.seats,
                seats_taken=TourBooking.objects.seats_taken(guide, starts_at),
            )
    return None

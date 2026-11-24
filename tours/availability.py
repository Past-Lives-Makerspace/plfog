"""Open facility tour times, computed live from the guides' hours (#947).

Nothing is generated ahead: every read carves :class:`~tours.models.TourHours` into tours, drops
the days a guide is off, everything past the booking horizon (:class:`~tours.models.FacilityTourSettings`)
and, for website visitors, everything inside the guide's own minimum notice (#1312,
:class:`~tours.models.TourGuideSettings`, Tour Settings' being the default), and counts the seats
bookings hold. A change to the hours, a day off or a setting is in the very next answer.

Weekly hours yield tours on their weekday; dated hours (#1160) on their one date. Both follow the
same notice, horizon, seats, days off and busy time rules, and both are locked the same way.

Guides' hours may overlap, but one tour runs at a time (#1312): the first booking at a time
claims it for one guide, later bookings at that start join that tour, and every other guide's tour
overlapping it stops being offered that day. Visitors see each time once, with no guide named.

Entry points:

- :func:`open_tours` lists the tours over a range of days: one per time for visitors (the booking
  page and the kiosk), every guide's for staff (the Reschedule modal, #949).
- :func:`tour_at` is the tour visitors get at one start, whoever gives it; :func:`open_tour_at`
  checks one guide's tour. With ``lock=True`` both take :func:`lock_bookings` first. Booking calls
  them inside ``transaction.atomic()`` before counting seats and saving, so two visitors racing for
  one time queue on that lock and the second one sees the first one's booking.

Neither reads the "Accepting tour bookings" switch: the booking page decides what a closed site
shows, and staff keep moving tours while it is closed.

A guide is never double booked (#1300): a tour is not offered over anything else holding the
guide's time (:func:`hours.busy.busy_spans`): an orientation of theirs holding seats, a session of
a class they teach, a day off (one Days Off for every kind), and, for visitors, a time their
Google Calendar shows them busy (#951, read from the database only, never from Google). Staff
(``staff=True``) see the Google busy tours anyway: a guide or an admin moving a tour may choose
such a time, and the guide hears of it by bell (:func:`tours.calendar_sync.alert_busy_conflicts`).
Bookings already made are never affected. A booking or a move takes the guide's person lock
(:func:`hours.follow.lock_person`) before it reads, the lock every orientation booking of theirs
takes too, so a tour and an orientation racing for one person cannot both land.

Every instant here is compared and keyed in UTC (#948). On the fall back night the carve yields
1:00 AM in the repeated hour as a local datetime whose offset depends on ``fold``; PEP 495 makes
such a value never compare equal to the same instant in another zone, so a booking read back from
the database (UTC) would miss its tour. :class:`OpenTour` therefore carries UTC instants (templates
show them in local time through the ``date`` filter as always).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from hours.busy import busy_spans
from hours.follow import lock_person
from membership.models import Member
from tours.calendar_sync import overlaps
from tours.models import FacilityTourSettings, TourBooking, TourGuideSettings, TourHours


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
    """The days and earliest start anyone may book right now, from the settings.

    ``first_day`` is today and ``last_day`` is today plus the horizon (both local, both included,
    as the equipment horizon counts it). ``earliest_start`` is now: a tour that has not started.
    The minimum notice is each guide's own since #1312 (:func:`_guide_earliest`), applied to
    website visitors only; Tour Settings' is the default, never a floor.
    """

    first_day: date
    last_day: date
    earliest_start: datetime

    @classmethod
    def at(cls, now: datetime, tour_settings: FacilityTourSettings) -> BookingWindow:
        """The window as it stands at ``now``: today to the horizon, any tour that has not started."""
        today = timezone.localdate(now)
        return cls(
            first_day=today,
            last_day=today + timedelta(days=tour_settings.booking_horizon_days),
            earliest_start=now,
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


def _offered(
    window: BookingWindow, starts_at: datetime, ends_at: datetime, busy: list[tuple[datetime, datetime]]
) -> bool:
    """Whether a carved tour may be booked now: inside the window, and not over a busy time of its guide's."""
    return window.admits(starts_at) and not overlaps(starts_at, ends_at, busy)


def _guide_earliest(
    guide_ids: Iterable[int], now: datetime, tour_settings: FacilityTourSettings
) -> dict[int, datetime]:
    """The earliest start a website visitor may book with each listed guide (#1312): now plus their own notice.

    A guide who never set one has Tour Settings' default; 0 is no notice. One query.
    """
    notices = TourGuideSettings.notice_minutes_by_guide(guide_ids, tour_settings)
    return {guide_id: now + timedelta(minutes=minutes) for guide_id, minutes in notices.items()}


@dataclass(frozen=True)
class _Booked:
    """What the bookings over a range hold: seats per tour, and each booked tour's span.

    Both are keyed ``(guide id, UTC start)``. A tour with a booking holding seats is claimed (#1312):
    from then on it is the tour at its time, and no other guide's tour overlapping it is offered.
    """

    taken: dict[tuple[int, datetime], int]
    spans: dict[tuple[int, datetime], datetime]

    def blocks(self, tour: OpenTour) -> bool:
        """Whether another guide's booked tour overlaps ``tour``, even partly (4:00 against 4:30).

        A tour that is itself booked is never blocked: two guides booked at overlapping times before
        #1312 both keep their visitors.
        """
        if (tour.guide.pk, tour.starts_at) in self.spans:
            return False
        return any(
            guide_id != tour.guide.pk and starts_at < tour.ends_at and tour.starts_at < ends_at
            for (guide_id, starts_at), ends_at in self.spans.items()
        )


def _booked(first: datetime, before: datetime, moving: TourBooking | None) -> _Booked:
    """Every guide's bookings holding seats for tours starting from ``first`` up to ``before``, in one query.

    All guides, not only those with hours in range: a booking whose guide no longer offers that
    time still holds it. ``moving`` (a booking staff are moving) counts nowhere, so moving it to
    another guide at the same time is not blocked by its own claim.
    """
    rows = TourBooking.objects.holding_seats().filter(guide__isnull=False, starts_at__gte=first, starts_at__lt=before)
    if moving is not None:
        rows = rows.exclude(pk=moving.pk)
    taken: dict[tuple[int, datetime], int] = {}
    spans: dict[tuple[int, datetime], datetime] = {}
    for guide_id, starts_at, ends_at, party_size in rows.values_list("guide_id", "starts_at", "ends_at", "party_size"):
        # Keyed in UTC (#948): a local start in the repeated fall back hour never equals the same instant read back.
        key = (guide_id, starts_at.astimezone(UTC))
        taken[key] = taken.get(key, 0) + party_size
        spans[key] = max(spans.get(key, ends_at.astimezone(UTC)), ends_at.astimezone(UTC))
    return _Booked(taken=taken, spans=spans)


def upcoming_tours_by_guide(now: datetime, *, moving: TourBooking | None = None) -> dict[int, int]:
    """How many upcoming tours each guide has booked (#1312): distinct starts with a booking or request, from ``now`` on.

    Who gets a time nobody has booked yet is the guide with the fewest (:func:`_one_per_time`), so
    new guides get tours too. One query.
    """
    rows = TourBooking.objects.filter(status__in=TourBooking.UPCOMING, guide__isnull=False, starts_at__gte=now)
    if moving is not None:
        rows = rows.exclude(pk=moving.pk)
    counted = rows.values("guide_id").annotate(tours=Count("starts_at", distinct=True))
    return {row["guide_id"]: int(row["tours"]) for row in counted}


def _one_per_time(
    offered: list[tuple[TourHours, OpenTour]], booked: _Booked, now: datetime, moving: TourBooking | None
) -> list[OpenTour]:
    """One tour per start time for visitors (#1312), named for the guide who gives it.

    A booked tour is the tour at its time (the earliest posted hours win when two guides were booked
    at one start before #1312). A time nobody has booked goes to the guide with the fewest upcoming
    booked tours (:func:`upcoming_tours_by_guide`), the hours posted first breaking a tie
    (:attr:`TourHours.posted_order`). Only guides who can give it are here: ``offered`` has already
    dropped days off, notice, busy times and the horizon, and tours another booking blocks.
    """
    by_start: dict[datetime, list[tuple[TourHours, OpenTour]]] = {}
    for pair in offered:
        by_start.setdefault(pair[1].starts_at, []).append(pair)
    counts: dict[int, int] | None = None
    kept: list[OpenTour] = []
    for start, pairs in by_start.items():
        held = [pair for pair in pairs if (pair[1].guide.pk, start) in booked.spans]
        if held or len(pairs) == 1:
            row, tour = min(held or pairs, key=lambda pair: pair[0].posted_order)
        else:
            counts = upcoming_tours_by_guide(now, moving=moving) if counts is None else counts
            loads = counts
            row, tour = min(pairs, key=lambda pair: (loads.get(pair[1].guide.pk, 0), pair[0].posted_order))
        kept.append(tour)
    return kept


def open_tours(
    first_day: date,
    last_day: date,
    *,
    guide: Member | None = None,
    include_full: bool = False,
    now: datetime | None = None,
    staff: bool = False,
    guide_notice: bool = True,
    moving: TourBooking | None = None,
) -> list[OpenTour]:
    """The tours that can be booked from ``first_day`` to ``last_day`` (local days, both included).

    Each offered guide's hours are carved into tours of the row's length; any tour over the guide's
    busy time (:func:`hours.busy.busy_spans`: their orientations, classes and days off), any
    tour past the booking horizon or already started, and on the website any tour inside its guide's
    own minimum notice (#1312) drop out. Guides' hours may
    overlap (#1312), but only one tour runs at a time: once a tour has a booking, every other
    guide's tour overlapping it, even partly, drops out. Visitors then see each time once
    (:func:`_one_per_time`): the booked tour, or for a time nobody has booked, the tour of the
    guide with the fewest upcoming tours among those whose notice allows it. Staff see every
    guide's tour that no booking blocks. A
    tour with no seat left drops out too unless ``include_full``, and for visitors one the guide's
    Google Calendar shows them busy (#951). Nine queries however long the range (settings, hours,
    bookings, the four or five of busy time, the guides' own notice; one fewer for staff and
    the kiosk), one more when two guides could give one time nobody has booked, and none past the
    settings when no guide has hours.

    Args:
        first_day: the first local day to list; days before today are skipped.
        last_day: the last local day to list; days past the horizon are skipped.
        guide: only this guide's tours, when given. For visitors, only the times this guide gives.
        include_full: keep tours with no seat left (with ``seats_left`` 0).
        now: the moment to judge the horizon and notice from; defaults to now.
        guide_notice: each guide's own minimum notice (#1312) applies, as it does on the website;
            the kiosk passes False, since a walk in is standing at the desk. Never for staff.
        staff: a guide or an admin is choosing, so no minimum notice applies (#949), nor the
            guide's Google Calendar busy times (#951), and every guide's tours are listed.
        moving: the booking being moved, whose own seats and claim count nowhere.

    Returns:
        The tours, soonest first, guides by name within a start time.
    """
    moment = now or timezone.now()
    tour_settings = FacilityTourSettings.load()
    window = BookingWindow.at(moment, tour_settings)
    first_day = max(first_day, window.first_day)
    last_day = min(last_day, window.last_day)
    if last_day < first_day:
        return []
    rows_query = TourHours.objects.offered().touching_days(first_day, last_day).select_related("guide")
    if guide is not None and staff:
        rows_query = rows_query.filter(guide=guide)
    rows = list(rows_query)
    if not rows:
        return []
    guide_ids = {row.guide_id for row in rows}
    first, before = _local_midnight(first_day), _local_midnight(last_day + timedelta(days=1))
    booked = _booked(first, before, moving)
    busy = busy_spans(guide_ids, first, before, tours=False, google_busy=not staff)
    earliest = _guide_earliest(guide_ids, moment, tour_settings) if guide_notice and not staff else {}
    offered: list[tuple[TourHours, OpenTour]] = []
    seen: set[tuple[int, datetime]] = set()
    day = first_day
    while day <= last_day:
        for row in rows:
            if not row.applies_on(day):
                continue
            for span_start, span_end in row.carve_spans(day):
                starts_at, ends_at = span_start.astimezone(UTC), span_end.astimezone(UTC)
                key = (row.guide_id, starts_at)
                # A guide's rows never overlap through the editor; one written another way must not
                # offer the same tour twice.
                too_soon = starts_at < earliest.get(row.guide_id, starts_at)
                if key in seen or too_soon or not _offered(window, starts_at, ends_at, busy[row.guide_id]):
                    continue
                seen.add(key)
                tour = OpenTour(
                    guide=row.guide,
                    hours_id=row.pk,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    seats=row.seats,
                    seats_taken=booked.taken.get(key, 0),
                )
                if not booked.blocks(tour):
                    offered.append((row, tour))
        day += timedelta(days=1)
    tours = [tour for _row, tour in offered] if staff else _one_per_time(offered, booked, moment, moving)
    tours = [
        tour for tour in tours if (include_full or not tour.is_full) and (guide is None or tour.guide.pk == guide.pk)
    ]
    tours.sort(key=lambda tour: (tour.starts_at, tour.guide.greeting_name.casefold()))
    return tours


def lock_bookings() -> None:
    """Make every booking, and every move into a time, wait its turn until this transaction ends (#948, #1312).

    ``SELECT ... FOR UPDATE`` on the Tour Settings row (made by :meth:`FacilityTourSettings.load`
    when missing). Every path that puts a visitor on a tour (:func:`tours.booking.book_tour`, a
    visitor's reschedule, an accepted proposal) takes it before reading who holds a time, so two
    visitors booking the same time nobody has booked yet end on one guide: the second reads the
    first's booking, which claims the time, and joins that tour (or is told it filled up).

    One row, not the hours rows of the guide booked (the lock before #1312): a claim reaches across
    guides (another guide's overlapping tour stops being offered), and who gets a time nobody has
    booked depends on every guide's upcoming tours on other days, so no set of hours rows covers
    what a booking reads. Tour bookings come a handful a day, so queueing them all on one row costs
    nothing anyone notices. Only an admin saving Tour Settings takes the row besides; an hours save
    does not (:func:`tours.models.lock_guide_hours`).

    Raises:
        RuntimeError: outside a transaction, where the lock would end at once.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("open_tour_at(lock=True) must run inside transaction.atomic().")
    FacilityTourSettings.load()
    list(FacilityTourSettings.objects.select_for_update().filter(pk=1).values_list("pk", flat=True))


def open_tour_at(
    guide: Member,
    starts_at: datetime,
    *,
    lock: bool = False,
    now: datetime | None = None,
    staff: bool = False,
    guide_notice: bool = True,
    moving: TourBooking | None = None,
) -> OpenTour | None:
    """The guide's tour starting at ``starts_at`` if it may be booked now, full or not; else None.

    None when the guide gives no tours, no hours of theirs yield that start, they are off that
    day, the start is past the horizon or has passed, on the website it is inside the guide's own
    minimum notice (#1312), another guide's booked tour
    overlaps it (#1312), or, for visitors, their Google Calendar shows them busy then (#951) or the
    time is another guide's to give (:func:`_one_per_time`). A full tour comes back with
    ``seats_left`` 0, so a booking page can say it just filled up rather than that it never existed.

    Args:
        guide: the tour's guide.
        starts_at: the tour's aware start.
        lock: take :func:`lock_bookings`, then the guide's person lock, first. Every booking and move passes True inside
            ``transaction.atomic()`` and saves before the transaction ends, so what each one reads
            includes every booking committed before it.
        now: the moment to judge the horizon and notice from; defaults to now.
        staff: a guide or an admin is moving a tour, so no minimum notice applies (#949), nor the
            guide's Google Calendar busy times (#951), nor who would get the time for visitors.
        guide_notice: the guide's own minimum notice (#1312) applies; False for the kiosk.
        moving: the booking staff are moving, whose own claim never blocks its new tour.

    Returns:
        The tour, or None.

    Raises:
        RuntimeError: ``lock`` outside a transaction, where the lock would end at once.
    """
    if lock:
        lock_bookings()
        lock_person(guide.pk)
    starts_at = starts_at.astimezone(UTC)
    day = timezone.localdate(starts_at)
    tours = open_tours(
        day, day, guide=guide, include_full=True, now=now, staff=staff, guide_notice=guide_notice, moving=moving
    )
    return next((tour for tour in tours if tour.starts_at == starts_at), None)


def tour_at(
    starts_at: datetime,
    *,
    lock: bool = False,
    now: datetime | None = None,
    guide_notice: bool = True,
    moving: TourBooking | None = None,
) -> OpenTour | None:
    """The tour visitors get at ``starts_at``, whoever gives it, full or not; None when nobody can (#1312).

    Visitors pick a time, never a guide: the booked tour there, or for a time nobody has booked the
    guide :func:`_one_per_time` picks among those whose own notice allows it. Once the time is
    booked, its guide's notice is the one later visitors meet. ``moving`` (a visitor's own booking
    being moved) never blocks or claims anything. Booking calls it with ``lock``
    (:func:`lock_bookings`), so
    the first booking claims the time for one guide and every later one joins that tour. With
    ``lock`` the guide it picks is person locked (:func:`hours.follow.lock_person`) and the time
    read again, until the guide it picks is one already locked: an orientation that landed on them
    meanwhile is seen, and none can land until this transaction ends.

    Raises:
        RuntimeError: ``lock`` outside a transaction, where the lock would end at once.
    """
    if lock:
        lock_bookings()
    starts_at = starts_at.astimezone(UTC)
    day = timezone.localdate(starts_at)
    locked: set[int] = set()
    while True:
        tours = open_tours(day, day, include_full=True, now=now, guide_notice=guide_notice, moving=moving)
        tour = next((tour for tour in tours if tour.starts_at == starts_at), None)
        if not lock or tour is None or tour.guide.pk in locked:
            return tour
        lock_person(tour.guide.pk)
        locked.add(tour.guide.pk)

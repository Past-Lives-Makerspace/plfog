"""What blocks a person's time, whatever kind of thing they give (#1300, part 2).

One person, one calendar: a time is busy for someone while any of these holds it.

- A tour they guide that holds seats (booked, attended, or a request waiting on them). A tour
  hour nobody booked blocks nothing; the tour itself keeps selling its own seats.
- An orientation slot of theirs that holds seats (a checkout hold, a request, a confirmed one).
  A fixed slot keeps selling its own seats (``except_slot``).
- A session of a class they teach, unless the class is a Draft or Cancelled.
- A time their Google Calendar shows them busy (``tours.CalendarBusySpan``, read only while the
  calendar is connected, its busy times were read, and Google sign in is set up).
- A day off (``hours.DayOff``): every local day from its first to its last, whole.

:func:`busy_holds` reads them for many people over a range in a fixed number of queries, one
:class:`Hold` per row (My Hours draws them, #1300 part 3); :func:`busy_spans` merges those;
:func:`busy_q` is its SQL twin, for a queryset that must drop busy rows (bookable orientation
slots). Both read the same rules; change one, change the other.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import NamedTuple, cast

from django.db.models import DateTimeField, Exists, ExpressionWrapper, OuterRef, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from classes.models import ClassOffering, ClassSession
from hours.models import DayOff
from membership.models import OrientationBooking, OrientationSlot
from tours import google
from tours.models import CalendarBusySpan, TourBooking, TourCalendarConnection

Span = tuple[datetime, datetime]

#: A class in one of these states blocks nobody's time.
IDLE_CLASSES = (ClassOffering.Status.DRAFT, ClassOffering.Status.CANCELLED)
#: An orientation booking in one of these holds its slot's seat, and so its orientor's time.
SEAT_HOLDING = (
    OrientationBooking.Status.PENDING_PAYMENT,
    OrientationBooking.Status.REQUESTED,
    OrientationBooking.Status.CONFIRMED,
)


def _midnight(day: date) -> datetime:
    """The aware start of a local day."""
    return timezone.make_aware(datetime.combine(day, time.min))


def _merge(spans: list[Span]) -> list[Span]:
    """Spans sorted, with overlapping and touching ones joined."""
    merged: list[Span] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


class Hold(NamedTuple):
    """One thing holding a person's time, before merging: who, when, what kind and which row.

    ``source`` is one of :data:`SOURCES`; ``ref`` is that row's pk (a tour booking, an orientation
    slot, a class session, a Google busy span or a day off), so a reader can name what it is.
    """

    member_id: int
    starts_at: datetime
    ends_at: datetime
    source: str
    ref: int


TOUR, ORIENTATION, CLASS, GOOGLE, DAY_OFF = "tour", "orientation", "class", "google", "day_off"
#: Every kind of hold, in the order :func:`busy_holds` reads them.
SOURCES = (TOUR, ORIENTATION, CLASS, GOOGLE, DAY_OFF)


def _as_holds(rows: Iterable[tuple[int, datetime, datetime, int]], source: str) -> list[Hold]:
    """``(member, start, end, pk)`` rows as holds of one ``source``."""
    return [Hold(member_id, starts_at, ends_at, source, pk) for member_id, starts_at, ends_at, pk in rows]


def busy_holds(
    member_ids: Iterable[int],
    first: datetime,
    before: datetime,
    *,
    tours: bool = True,
    google_busy: bool = True,
    except_slot: int | None = None,
) -> list[Hold]:
    """Everything holding the listed people's time from ``first`` to ``before``, unmerged (My Hours' week, #1300).

    The rows :func:`busy_spans` merges, one per tour booking, seat holding slot (once however many
    seats it holds), class session taught, Google busy span and day off (whole local days). The
    keywords are :func:`busy_spans`'s. At most five queries.
    """
    ids = set(member_ids)
    if not ids:
        return []
    overlap = {"starts_at__lt": before, "ends_at__gt": first}
    holds: list[Hold] = []
    if tours:
        tour_rows = (
            TourBooking.objects.holding_seats()
            .filter(guide_id__in=ids, **overlap)
            .values_list("guide_id", "starts_at", "ends_at", "pk")
        )
        holds += _as_holds(tour_rows, TOUR)
    slots = OrientationSlot.objects.filter(
        orienter_id__in=ids, is_cancelled=False, bookings__status__in=SEAT_HOLDING, **overlap
    )
    if except_slot is not None:
        slots = slots.exclude(pk=except_slot)
    holds += _as_holds(slots.values_list("orienter_id", "starts_at", "ends_at", "pk").distinct(), ORIENTATION)
    class_rows = (
        ClassSession.objects.filter(class_offering__instructor_rows__member_id__in=ids, **overlap)
        .exclude(class_offering__status__in=IDLE_CLASSES)
        .values_list("class_offering__instructor_rows__member_id", "starts_at", "ends_at", "pk")
    )
    holds += _as_holds(class_rows, CLASS)
    if google_busy and google.is_configured():
        google_rows = CalendarBusySpan.objects.filter(
            connection__guide_id__in=ids,
            connection__status=TourCalendarConnection.Status.CONNECTED,
            connection__busy_checked_at__isnull=False,
            **overlap,
        ).values_list("connection__guide_id", "starts_at", "ends_at", "pk")
        holds += _as_holds(google_rows, GOOGLE)
    days_off = DayOff.objects.filter(
        member_id__in=ids, starts_on__lte=timezone.localdate(before), ends_on__gte=timezone.localdate(first)
    )
    for member_id, starts_on, ends_on, pk in days_off.values_list("member_id", "starts_on", "ends_on", "pk"):
        holds.append(Hold(member_id, _midnight(starts_on), _midnight(ends_on + timedelta(days=1)), DAY_OFF, pk))
    return holds


def busy_spans(
    member_ids: Iterable[int],
    first: datetime,
    before: datetime,
    *,
    tours: bool = True,
    google_busy: bool = True,
    except_slot: int | None = None,
) -> dict[int, list[Span]]:
    """Each listed person's busy time overlapping ``first`` to ``before``, merged and in order.

    Args:
        member_ids: whose time.
        first: the range's aware start.
        before: the range's aware end.
        tours: count their booked tours. The tours engine passes False: a guide's own tour keeps
            selling its seats, and another guide's booked tour is its own rule (#1312).
        google_busy: count their Google Calendar's busy times. Staff choosing a time pass False,
            as tours always have (#951).
        except_slot: an orientation slot whose own seats never block it (the slot being booked).

    Returns:
        A list for every listed person, empty when nothing holds their time. At most five queries
        (:func:`busy_holds`, merged).
    """
    ids = set(member_ids)
    found: dict[int, list[Span]] = {member_id: [] for member_id in ids}
    holds = busy_holds(ids, first, before, tours=tours, google_busy=google_busy, except_slot=except_slot)
    for hold in holds:
        found[hold.member_id].append((hold.starts_at, hold.ends_at))
    return {member_id: _merge(spans) for member_id, spans in found.items()}


def is_busy(
    member_id: int,
    starts_at: datetime,
    ends_at: datetime,
    *,
    google_busy: bool = True,
    except_slot: int | None = None,
) -> bool:
    """Whether anything in :func:`busy_spans` holds ``member_id``'s time from ``starts_at`` to ``ends_at``.

    Touching ends do not count. The keywords are :func:`busy_spans`'s.
    """
    spans = busy_spans([member_id], starts_at, ends_at, google_busy=google_busy, except_slot=except_slot)
    return any(start < ends_at and starts_at < end for start, end in spans[member_id])


def busy_q(member: str, starts_at: str, ends_at: str, *, except_slot: str | None = None) -> Q:
    """The SQL twin of :func:`busy_spans` (tours and Google included) for the rows of an outer queryset.

    ``member``, ``starts_at`` and ``ends_at`` name the outer row's fields (an orientation slot's
    ``orienter_id``, ``starts_at``, ``ends_at``); ``except_slot`` names the field holding the slot
    whose own seats never block it. True where the person's time is busy; a row with no person
    (a shared slot) is never busy. A day off is busy from its first local day to its last, so a
    row is caught when it starts on one, or runs into one.
    """
    person, start, end = OuterRef(member), OuterRef(starts_at), OuterRef(ends_at)
    overlap = {"starts_at__lt": end, "ends_at__gt": start}
    slots = OrientationSlot.objects.filter(
        orienter_id=person, is_cancelled=False, bookings__status__in=SEAT_HOLDING, **overlap
    )
    if except_slot is not None:
        slots = slots.exclude(pk=OuterRef(except_slot))
    first_instant = ExpressionWrapper(start, output_field=DateTimeField())
    last_instant = ExpressionWrapper(end - timedelta(microseconds=1), output_field=DateTimeField())
    # ``Exists | Exists`` is a Q at runtime; django-stubs widens the chain to Combinable.
    found = cast(
        Q,
        Exists(TourBooking.objects.holding_seats().filter(guide_id=person, **overlap))
        | Exists(slots)
        | Exists(
            ClassSession.objects.filter(class_offering__instructor_rows__member_id=person, **overlap).exclude(
                class_offering__status__in=IDLE_CLASSES
            )
        )
        | Exists(
            DayOff.objects.filter(
                member_id=person, starts_on__lte=TruncDate(last_instant), ends_on__gte=TruncDate(first_instant)
            )
        ),
    )
    if google.is_configured():
        found = cast(
            Q,
            found
            | Exists(
                CalendarBusySpan.objects.filter(
                    connection__guide_id=person,
                    connection__status=TourCalendarConnection.Status.CONNECTED,
                    connection__busy_checked_at__isnull=False,
                    **overlap,
                )
            ),
        )
    return found

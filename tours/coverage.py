"""Who covers which days (#1274): the Tours page's Calendar tab and its Days With No Tour Hours card.

Every Tour Guide sees every guide's hours here, so a guide can spot the days nobody gives tours
and add their own. The calendar is the shared shell (``hub/partials/guild_calendar_app.html``)
fed through :func:`hub.calendar_pages.entries_calendar_context`: one legend chip per guide, each
guide's hours windows (weekly and Available On, minus their days off) and every tour that holds
seats (booked, requested or attended) in that guide's colour.

Hours are read as the guides put them in, never carved: a window offers tours whatever the
minimum notice, the seats taken or a Google Calendar busy time say, so a day is covered when
some guide's hours apply to it. A gap is a day from today to the booking horizon that no
offered guide's hours touch (:func:`uncovered_days`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from django.urls import reverse
from django.utils import timezone

from hub.calendar_entries import CalendarEntry
from hub.calendar_pages import ITEM_LEGEND_COLORS, LegendChip, entries_calendar_context
from hub.calendar_window import calendar_window
from hub.hour_grid import MINUTES_PER_DAY, Card, GridDay, HourGridWeek, Rail, clock, hour_range, lay_lanes
from hours.models import DayOff
from tours.models import FacilityTourSettings, TourBooking, TourHours
from tours.notify import short_when

if TYPE_CHECKING:
    from membership.models import Member

#: Synthetic pks for the calendar's entries, clear of every other calendar's offsets (hub.calendar_entries).
HOURS_PK_OFFSET = 6_000_000_000
#: One hours row yields at most one window a day; the day's ordinal keeps each copy apart.
HOURS_DAY_STRIDE = 1_000_000
BOOKING_PK_OFFSET = 7_000_000_000
#: The legend key of a tour whose guide's member record was deleted.
NO_GUIDE_KEY = "no-guide"
NO_GUIDE_COLOR = "var(--hub-text-muted)"
#: The Week view's hour grid (#1332): the shell draws this in place of its week cells.
WEEK_TEMPLATE = "tours/partials/_hour_grid_week.html"
#: What one day of that grid holds: the guides' rails and the tours' cards.
GRID_DAY_TEMPLATE = "tours/partials/_hour_grid_day.html"


@dataclass(frozen=True)
class GapDay:
    """A day in the booking window with no guide's tour hours."""

    day: date

    @property
    def add_url(self) -> str:
        """My Hours with Add Dates open on this day (``tours.views.tour_hours``'s ``?add=``)."""
        return f"{reverse('tours:hours')}?add={self.day.isoformat()}#available-on"


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _hours_by_day(first: date, last: date) -> dict[date, list[TourHours]]:
    """Every offered guide's hours windows on each day from ``first`` to ``last``, minus their days off (two queries)."""
    rows = list(
        TourHours.objects.offered().touching_days(first, last).select_related("guide").order_by("start_time", "pk")
    )
    off: dict[int, set[date]] = {}
    # One Days Off for every kind (#1300): an orientation day off is a day with no tours too.
    for day_off in DayOff.objects.filter(starts_on__lte=last, ends_on__gte=first):
        covered = off.setdefault(day_off.member_id, set())
        covered.update(day for day in _days(max(day_off.starts_on, first), min(day_off.ends_on, last)))
    return {
        day: [row for row in rows if row.applies_on(day) and day not in off.get(row.guide_id, set())]
        for day in _days(first, last)
    }


def uncovered_days(*, today: date | None = None) -> list[GapDay]:
    """The days from today to the booking horizon (Tour Settings) on which no guide has tour hours."""
    first = today or timezone.localdate()
    last = first + timedelta(days=FacilityTourSettings.load().booking_horizon_days)
    return [GapDay(day) for day, rows in _hours_by_day(first, last).items() if not rows]


def _short_name(guide: Member) -> str:
    """``Gina G.``: the guide's first name and last initial, as the grid chips have room for."""
    words = guide.staff_label.split()
    if len(words) < 2:
        return guide.staff_label.strip()
    return f"{words[0]} {words[-1][0].upper()}."


def _legend(guides: list[Member], *, has_unguided: bool) -> list[LegendChip]:
    ordered = sorted(guides, key=lambda guide: guide.staff_label.casefold())
    legend: list[LegendChip] = [
        {
            "key": str(guide.pk),
            "label": guide.staff_label,
            "color": ITEM_LEGEND_COLORS[index % len(ITEM_LEGEND_COLORS)],
            "logo_prefix": None,
        }
        for index, guide in enumerate(ordered)
    ]
    if has_unguided:
        legend.append({"key": NO_GUIDE_KEY, "label": "No guide", "color": NO_GUIDE_COLOR, "logo_prefix": None})
    return legend


def _aware(day: date, moment: Any) -> datetime:
    return timezone.make_aware(datetime.combine(day, moment))


def _hours_entries(by_day: dict[date, list[TourHours]], hours_url: Any) -> list[CalendarEntry]:
    entries = []
    for day, rows in by_day.items():
        for row in rows:
            name = _short_name(row.guide)
            entries.append(
                CalendarEntry(
                    pk=HOURS_PK_OFFSET + row.pk * HOURS_DAY_STRIDE + day.toordinal() % HOURS_DAY_STRIDE,
                    title=f"Tour hours: {row.guide.staff_label}",
                    chip_title=f"{name} hours",
                    start_dt=_aware(day, row.start_time),
                    end_dt=_aware(day, row.end_time),
                    # "reservation" draws the end time and links in place (calendar_event_item.html).
                    source="reservation",
                    url=hours_url(row.guide),
                    legend_key=str(row.guide_id),
                    owner_label="Tour hours",
                )
            )
    return entries


def _booking_entries(bookings: list[TourBooking]) -> list[CalendarEntry]:
    now = timezone.now()
    entries = []
    for booking in bookings:
        kind = "Request" if booking.is_request else "Tour"
        guide = f" with {booking.guide.staff_label}" if booking.guide is not None else ", no guide"
        tab = booking.table_url_name(now)
        owner_label = "Tour request" if booking.is_request else "Booked tour"
        waiting = booking.proposed_starts_at is not None
        if booking.proposed_starts_at is not None:
            # A reschedule waiting on the visitor's answer (#1283): the chip says Waiting, the list line says for what.
            owner_label = (
                f"Booked tour · Waiting for the Visitor, asked to move to {short_when(booking.proposed_starts_at)}"
            )
        entries.append(
            CalendarEntry(
                pk=BOOKING_PK_OFFSET + booking.pk,
                title=f"{kind}: {booking.kiosk_name}, {booking.party_label}{guide}",
                chip_title=f"{kind}: {booking.kiosk_name} ({booking.party_size}){' · Waiting' if waiting else ''}",
                start_dt=booking.starts_at,
                end_dt=booking.ends_at,
                source="reservation",
                url=f"{reverse(tab)}#tour-booking-{booking.pk}",
                legend_key=str(booking.guide_id) if booking.guide_id is not None else NO_GUIDE_KEY,
                owner_label=owner_label,
            )
        )
    return entries


def _legend_guides() -> tuple[list[Member], bool]:
    """Everyone with tour hours they still offer, or a tour holding seats, by name; and whether a tour has no guide.

    Read across all time, not the window shown, so a guide keeps their colour from week to week.
    """
    from django.db.models import Q

    from membership.models import Member
    from tours.models import guide_filter

    guides = list(
        Member.objects.filter(
            (guide_filter() & Q(tour_hours__isnull=False)) | Q(tour_bookings__status__in=TourBooking.HOLDS_SEATS)
        ).distinct()
    )
    unguided = TourBooking.objects.holding_seats().filter(guide__isnull=True).exists()
    return guides, unguided


def calendar_context(
    *, viewer: Member, is_admin: bool, week_offset: int = 0, month_offset: int = 0, event_page: int = 1
) -> dict[str, Any]:
    """The Calendar tab's ``cal``: every guide's hours and every tour holding seats, across the window shown.

    A guide's own hours link to My Hours, and an admin's to that guide's My Hours (``?guide=``);
    another guide's hours link nowhere for a guide. Each tour links to its row on Upcoming or Past.
    """

    def hours_url(guide: Member) -> str:
        if guide.pk == viewer.pk:
            return reverse("tours:hours")
        return f"{reverse('tours:hours')}?guide={guide.pk}" if is_admin else ""

    def entries_for(fetch_from: date, fetch_to: date) -> list[CalendarEntry]:
        starts = _aware(fetch_from, time.min)
        ends = _aware(fetch_to + timedelta(days=1), time.min)
        bookings = (
            TourBooking.objects.holding_seats()
            .filter(starts_at__gte=starts, starts_at__lt=ends)
            .select_related("guide")
            .order_by("starts_at", "pk")
        )
        return _hours_entries(_hours_by_day(fetch_from, fetch_to), hours_url) + _booking_entries(list(bookings))

    guides, unguided = _legend_guides()
    legend = _legend(guides, has_unguided=unguided)
    cal = entries_calendar_context(
        entries_for,
        week_offset=week_offset,
        month_offset=month_offset,
        event_page=event_page,
        legend=legend,
        events_url=reverse("tours:calendar_events"),
    )
    cal["week_template"] = WEEK_TEMPLATE
    cal["hour_grid"] = hour_grid(
        calendar_window(week_offset, month_offset).week_start, {chip["key"]: chip["color"] for chip in legend}
    )
    return cal


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def _booking_span(booking: TourBooking, day: date) -> tuple[int, int]:
    """A tour's local start and end in minutes of ``day``; one running past midnight ends at midnight."""
    starts, ends = timezone.localtime(booking.starts_at), timezone.localtime(booking.ends_at)
    return _minutes(starts.time()), _minutes(ends.time()) if ends.date() == day else MINUTES_PER_DAY


def hour_grid(week_start: date, colors: dict[str, str], *, now: datetime | None = None) -> HourGridWeek:
    """The Week view's hour grid (#1332): each guide's hours as a rail, each tour holding seats as a card.

    Hours are the same windows the calendar and the gaps card read (:func:`_hours_by_day`); a day
    none of them touch is ``uncovered`` and, from today on, carries Add My Hours (:class:`GapDay`).
    ``colors`` maps a legend key to its chip colour, so a guide's rail, card edge and chip agree.
    """
    moment = timezone.localtime(now)
    days = _days(week_start, week_start + timedelta(days=6))
    by_day = _hours_by_day(days[0], days[-1])
    bookings: dict[date, list[TourBooking]] = {day: [] for day in days}
    for booking in (
        TourBooking.objects.holding_seats()
        .filter(starts_at__gte=_aware(days[0], time.min), starts_at__lt=_aware(days[-1] + timedelta(days=1), time.min))
        .select_related("guide")
        .order_by("starts_at", "pk")
    ):
        bookings[timezone.localdate(booking.starts_at)].append(booking)
    hours_spans = {day: [(_minutes(row.start_time), _minutes(row.end_time)) for row in by_day[day]] for day in days}
    tour_spans = {day: [_booking_span(booking, day) for booking in bookings[day]] for day in days}
    first_hour, last_hour = hour_range([span for day in days for span in hours_spans[day] + tour_spans[day]])
    top = first_hour * 60
    now_minute = _minutes(moment.time()) - top
    grid_days = []
    for day in days:
        rails = [
            Rail(
                start=start - top,
                length=end - start,
                lane=lane,
                tone=colors[str(row.guide_id)],
                key=str(row.guide_id),
                title=f"{_short_name(row.guide)} hours, {clock(start)} to {clock(end)}",
            )
            for row, (start, end), (lane, _) in zip(by_day[day], hours_spans[day], lay_lanes(hours_spans[day]))
        ]
        cards = [
            _card(booking, span, lanes, top, colors, moment)
            for booking, span, lanes in zip(bookings[day], tour_spans[day], lay_lanes(tour_spans[day]))
        ]
        uncovered = not rails
        grid_days.append(
            GridDay(
                day=day,
                is_today=day == moment.date(),
                rails=rails,
                cards=cards,
                state="uncovered" if uncovered else "",
                note="No Tour Hours" if uncovered else "",
                note_url=GapDay(day).add_url if uncovered and day >= moment.date() else "",
                now=now_minute if day == moment.date() and 0 <= now_minute <= (last_hour - first_hour) * 60 else None,
            )
        )
    return HourGridWeek(first_hour=first_hour, last_hour=last_hour, days=grid_days, cell_template=GRID_DAY_TEMPLATE)


def _card(
    booking: TourBooking, span: tuple[int, int], lanes: tuple[int, int], top: int, colors: dict[str, str], now: datetime
) -> Card:
    """A tour's card, linking to its row on Upcoming or Past as the calendar's list lines do."""
    start, end = span
    key = str(booking.guide_id) if booking.guide is not None else NO_GUIDE_KEY
    guide = _short_name(booking.guide) if booking.guide is not None else "No guide"
    label = f"{booking.kiosk_name} ({booking.party_size}){' · Waiting' if booking.proposed_starts_at else ''}"
    kind = "Request" if booking.is_request else "Tour"
    return Card(
        start=start - top,
        length=end - start,
        lane=lanes[0],
        lanes=lanes[1],
        tone=colors[key],
        key=key,
        when=clock(start),
        label=label,
        guide=guide,
        title=f"{kind}: {label}, {clock(start)}, {guide}",
        pk=booking.pk,
        url=f"{reverse(booking.table_url_name(now))}#tour-booking-{booking.pk}",
        is_request=booking.is_request,
    )

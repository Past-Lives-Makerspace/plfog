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
from tours.models import FacilityTourSettings, TourBooking, TourDayOff, TourHours

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
    for day_off in TourDayOff.objects.touching(first, last):
        covered = off.setdefault(day_off.guide_id, set())
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
    entries = []
    for booking in bookings:
        kind = "Request" if booking.is_request else "Tour"
        guide = f" with {booking.guide.staff_label}" if booking.guide is not None else ", no guide"
        tab = "tours:upcoming" if booking.status in TourBooking.UPCOMING else "tours:past"
        entries.append(
            CalendarEntry(
                pk=BOOKING_PK_OFFSET + booking.pk,
                title=f"{kind}: {booking.kiosk_name}, {booking.party_label}{guide}",
                chip_title=f"{kind}: {booking.kiosk_name} ({booking.party_size})",
                start_dt=booking.starts_at,
                end_dt=booking.ends_at,
                source="reservation",
                url=f"{reverse(tab)}#tour-booking-{booking.pk}",
                legend_key=str(booking.guide_id) if booking.guide_id is not None else NO_GUIDE_KEY,
                owner_label="Tour request" if booking.is_request else "Booked tour",
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
    return entries_calendar_context(
        entries_for,
        week_offset=week_offset,
        month_offset=month_offset,
        event_page=event_page,
        legend=_legend(guides, has_unguided=unguided),
        events_url=reverse("tours:calendar_events"),
    )

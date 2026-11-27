"""The Tours Calendar tab's Week as an hour grid (#1332): guides' hours as rails, tours as cards, gaps striped."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourDayOffFactory, TourHoursFactory, local
from tours import coverage
from tours.models import TourBooking

pytestmark = pytest.mark.django_db

CALENDAR = reverse("tours:calendar")
EVENTS = reverse("tours:calendar_events")
HOURS = reverse("hours:my_hours")
UPCOMING = reverse("tours:upcoming")
PAST = reverse("tours:past")
HTMX = {"HTTP_HX_REQUEST": "true"}
COLORS = {"no-guide": "var(--hub-text-muted)"}


def _next_monday() -> date:
    today = timezone.localdate()
    return today - timedelta(days=today.weekday()) + timedelta(days=7)


def _colors(*guides: Member) -> dict[str, str]:
    return {**COLORS, **{str(guide.pk): f"#00000{index}" for index, guide in enumerate(guides)}}


@pytest.fixture
def other() -> Member:
    return make_guide(full_legal_name="Olive Other")


@pytest.fixture
def third() -> Member:
    return make_guide(full_legal_name="Tess Third")


def describe_hour_grid():
    def it_gives_each_overlapping_guide_a_rail_of_their_own(guide: Member, other: Member, third: Member):
        saturday = _next_monday() + timedelta(days=5)
        for member, start in ((guide, 10), (other, 11), (third, 12)):
            TourHoursFactory(guide=member, weekday=saturday.weekday(), start_time=time(start), end_time=time(start + 4))
        grid = coverage.hour_grid(_next_monday(), _colors(guide, other, third))
        day = grid.days[5]
        assert [(rail.start, rail.length) for rail in day.rails] == [(60, 240), (120, 240), (180, 240)]
        assert [rail.lane for rail in day.rails] == [0, 1, 2]
        assert [rail.tone for rail in day.rails] == ["#000000", "#000001", "#000002"]
        assert day.rails[0].title == "Gina G. hours, 10:00 AM to 2:00 PM"
        assert day.rails_px == 4 + 9 * 3
        assert (day.state, day.note) == ("", "")

    def it_places_each_tour_as_a_card_at_its_time(guide: Member):
        tuesday = _next_monday() + timedelta(days=1)
        TourHoursFactory(guide=guide, weekday=tuesday.weekday(), start_time=time(10), end_time=time(14))
        theo = TourBookingFactory(guide=guide, starts_at=local(tuesday, 11), visitor_name="Theo Ames", party_size=4)
        TourBookingFactory(
            guide=guide,
            starts_at=local(tuesday, 13),
            visitor_name="Lee Park",
            status=TourBooking.Status.PENDING,
            proposed_starts_at=local(tuesday, 15),
        )
        TourBookingFactory(guide=None, starts_at=local(tuesday, 12), visitor_name="Kai Nomad")
        TourBookingFactory(guide=guide, starts_at=local(tuesday, 12), status=TourBooking.Status.CANCELLED)
        grid = coverage.hour_grid(_next_monday(), _colors(guide))
        cards = grid.days[1].cards
        assert [(card.start, card.length, card.lane, card.lanes) for card in cards] == [
            (120, 60, 0, 1),
            (180, 60, 0, 1),
            (240, 60, 0, 1),
        ]
        first, unguided, request = cards
        assert (first.when, first.label, first.guide, first.tone) == ("11:00 AM", "Theo A. (4)", "Gina G.", "#000000")
        assert first.title == "Tour: Theo A. (4), 11:00 AM, Gina G."
        assert first.pk == theo.pk and not first.is_request
        assert first.url == f"{UPCOMING}#tour-booking-{theo.pk}"
        assert (unguided.key, unguided.guide, unguided.tone) == ("no-guide", "No guide", "var(--hub-text-muted)")
        assert request.is_request and request.label == "Lee P. (2) · Waiting"
        assert request.title.startswith("Request: ")

    def it_stripes_a_day_nobody_covers_and_offers_add_my_hours_from_today_on(guide: Member):
        monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
        now = local(monday + timedelta(days=2), 12)
        grid = coverage.hour_grid(monday, COLORS, now=now)
        past, today, later = grid.days[1], grid.days[2], grid.days[3]
        assert all(day.state == "uncovered" and day.note == "No Tour Hours" for day in grid.days)
        assert past.note_url == ""
        assert today.note_url == f"{HOURS}?add={today.day.isoformat()}#available-on"
        assert later.note_url.startswith(f"{HOURS}?add=")

    def it_leaves_out_hours_on_a_guides_day_off(guide: Member):
        wednesday = _next_monday() + timedelta(days=2)
        TourHoursFactory(guide=guide, weekday=wednesday.weekday())
        TourDayOffFactory(guide=guide, starts_on=wednesday, ends_on=wednesday)
        assert coverage.hour_grid(_next_monday(), _colors(guide)).days[2].state == "uncovered"

    def it_stretches_the_range_around_early_hours_and_a_late_tour(guide: Member):
        friday = _next_monday() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=friday.weekday(), start_time=time(8), end_time=time(10, 30))
        TourBookingFactory(guide=guide, starts_at=local(friday, 20, 30), ends_at=local(friday, 21, 30))
        grid = coverage.hour_grid(_next_monday(), _colors(guide))
        assert (grid.first_hour, grid.last_hour) == (8, 22)
        assert grid.days[4].rails[0].start == 0

    def it_ends_a_tour_running_past_midnight_at_midnight(guide: Member):
        sunday = _next_monday() + timedelta(days=6)
        TourBookingFactory(guide=guide, starts_at=local(sunday, 23), ends_at=local(sunday + timedelta(days=1), 0, 30))
        grid = coverage.hour_grid(_next_monday(), _colors(guide))
        assert grid.last_hour == 24
        assert grid.days[6].cards[0].length == 60

    def it_links_a_past_tour_to_the_past_table(guide: Member):
        last_monday = _next_monday() - timedelta(days=14)
        past = TourBookingFactory(guide=guide, starts_at=local(last_monday, 11), status=TourBooking.Status.ATTENDED)
        card = coverage.hour_grid(last_monday, _colors(guide)).days[0].cards[0]
        assert card.url == f"{PAST}#tour-booking-{past.pk}"

    def it_draws_the_now_line_on_today_inside_the_range():
        monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
        grid = coverage.hour_grid(monday, COLORS, now=local(monday + timedelta(days=1), 14, 20))
        assert [day.now for day in grid.days] == [None, 5 * 60 + 20, None, None, None, None, None]
        assert grid.days[1].is_today and grid.active_day == grid.days[1].key

    def it_draws_no_now_line_outside_the_range():
        monday = timezone.localdate() - timedelta(days=timezone.localdate().weekday())
        late = coverage.hour_grid(monday, COLORS, now=local(monday, 23))
        early = coverage.hour_grid(monday, COLORS, now=local(monday, 6))
        assert [day.now for day in late.days + early.days] == [None] * 14


def describe_calendar_week():
    def it_draws_the_hour_grid_in_place_of_the_week_cells(guide: Member):
        today = timezone.localdate()
        TourHoursFactory(guide=guide, weekday=today.weekday(), start_time=time(10), end_time=time(12))
        sam = TourBookingFactory(guide=guide, starts_at=local(today, 11), visitor_name="Sam Rivera", party_size=3)
        content = signed_in(guide).get(CALENDAR).content.decode()
        tab = reverse(sam.table_url_name(timezone.now()))
        assert f'<a href="{tab}#tour-booking-{sam.pk}"\n   class="pl-hourgrid__card"' in content
        # Week draws no list under the grid; the one list left is Month's, under Month's own divider.
        assert content.count('class="pl-calendar-list"') == 1
        assert '<div class="pl-calendar-divider" x-show="calView === \'month\'"></div>' in content
        assert "css/hour-grid" in content and "data-tours-hour-grid" in content
        assert 'data-guidance-target="tours.hour-grid"' in content
        assert f'data-hourgrid-day="{today.isoformat()}"' in content
        assert 'class="pl-hourgrid__card"' in content and "Sam R. (3)" in content
        assert "data-hourgrid-rail=" in content
        assert "pl-calendar-grid--week" not in content
        assert "pl-calendar-grid--month" in content

    def it_steps_weeks_with_the_hour_grid_over_htmx(guide: Member):
        TourHoursFactory(guide=guide, weekday=_next_monday().weekday(), start_time=time(16), end_time=time(18))
        fragment = signed_in(guide).get(EVENTS, {"week_offset": 1}, **HTMX).content.decode()
        assert "<html" not in fragment and "data-tours-hour-grid" in fragment
        assert f'data-hourgrid-day="{_next_monday().isoformat()}"' in fragment
        assert "Gina G. hours, 4:00 PM to 6:00 PM" in fragment
        assert "pl-calendar-grid--week" not in fragment and "pl-calendar-grid--month" in fragment


def describe_other_calendars():
    def it_leaves_the_shells_week_cells_for_every_calendar_that_does_not_opt_in():
        from django.template.loader import render_to_string

        from hub.calendar_pages import entries_calendar_context

        cal = entries_calendar_context(
            lambda first, last: [], week_offset=0, month_offset=0, event_page=1, legend=[], events_url="/x/"
        )
        html = render_to_string("hub/partials/guild_calendar_app.html", {"cal": cal, "cal_key": "spec"})
        assert "pl-calendar-grid--week" in html and "pl-hourgrid" not in html
        assert html.count('class="pl-calendar-list"') == 2

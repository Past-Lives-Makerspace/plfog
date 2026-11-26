"""BDD specs for My Hours' week on the hour grid (#1332, part 2): ``hours.page.week_grid``.

Every block still comes from the week's chips, which come from ``hours.busy.busy_holds``; the grid
only places them. A booking inside open hours sits inside that block; one no open hours hold stands
alone; overlapping blocks sit side by side; a day off is striped with Day Off.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone

from hours.page import BOOKED, BUSY, GRID_DAY_TEMPLATE, OPEN, Chip, Kind, MyHours, WeekDay, week_grid
from tests.membership.factories import GuildFactory, OrientationAvailabilityFactory
from tests.tours.conftest import make_guide, signed_in
from tests.tours.factories import TourBookingFactory, TourDayOffFactory, TourHoursFactory

TOURS = Kind("tours", "Tours", "pl-kind--tours")
PRINT = Kind("guild-1", "Printmaking Guild orientations", "pl-kind--a")
MONDAY = date(2026, 11, 23)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _next_week(weekday: int) -> date:
    """The day ``weekday`` (0 Monday) of next week, so ``week_offset=1`` always shows it."""
    today = timezone.localdate()
    return today - timedelta(days=today.weekday()) + timedelta(weeks=1, days=weekday)


def _week(**chips_by_day: list[Chip]) -> list[WeekDay]:
    """A week from Monday 23 Nov 2026; ``off=[...]`` days are days off, the rest by weekday name."""
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    off = chips_by_day.pop("off", [])
    return [
        WeekDay(
            date=MONDAY + timedelta(days=n),
            is_today=False,
            is_day_off=name in off,
            chips=chips_by_day.get(name, []),
        )
        for n, name in enumerate(names)
    ]


def _chip(style: str, day: date, start: tuple[int, int], end: tuple[int, int], what: str, **kw: object) -> Chip:
    return Chip(style, _at(day, *start), _at(day, *end), what, **kw)  # type: ignore[arg-type]


def describe_week_grid():
    def it_puts_each_booking_inside_the_open_hours_holding_it():
        monday = MONDAY
        grid = week_grid(
            _week(
                mon=[
                    _chip(OPEN, monday, (11, 0), (18, 0), "Open", kinds=(TOURS, PRINT)),
                    _chip(BOOKED, monday, (13, 0), (14, 0), "Tour · 3 visitors", kind=TOURS),
                    _chip(BOOKED, monday, (15, 0), (16, 0), "Printmaking Guild · Sam K.", kind=PRINT),
                ]
            ),
            now=_at(monday + timedelta(weeks=1), 12),
        )
        assert (grid.first_hour, grid.last_hour, grid.cell_template) == (9, 21, GRID_DAY_TEMPLATE)
        [block] = grid.days[0].blocks
        assert (block.style, block.start, block.length, block.lane, block.lanes) == (OPEN, 120, 420, 0, 1)
        assert block.when == "11:00 AM to 6:00 PM"
        assert block.dots == ("pl-kind--tours", "pl-kind--a")
        assert block.css == ""
        assert block.title == "Open for Tours and Printmaking Guild orientations, 11:00 AM to 6:00 PM"
        assert [(inner.start, inner.length, inner.lane, inner.lanes, inner.css) for inner in block.inner] == [
            (120, 60, 0, 1, "pl-kind--tours"),
            (240, 60, 0, 1, "pl-kind--a"),
        ]
        assert block.inner[0].when == "1:00 PM"
        assert block.inner[0].title == "Tour · 3 visitors, 1:00 PM to 2:00 PM"
        assert grid.days[0].has_items
        assert not grid.days[1].has_items

    def it_sets_overlapping_blocks_and_overlapping_bookings_side_by_side():
        wednesday = MONDAY + timedelta(days=2)
        grid = week_grid(
            _week(
                wed=[
                    _chip(OPEN, wednesday, (12, 0), (17, 0), "Open", kinds=(TOURS,)),
                    _chip(BOOKED, wednesday, (13, 0), (14, 0), "Tour · 2 visitors", kind=TOURS),
                    _chip(BOOKED, wednesday, (13, 30), (14, 30), "Printmaking Guild · requested", kind=PRINT),
                    _chip(BUSY, wednesday, (16, 0), (18, 0), "Class: Intro to Linocut"),
                ]
            ),
            now=_at(MONDAY, 12),
        )
        open_block, busy = grid.days[2].blocks
        assert (open_block.lane, open_block.lanes, busy.lane, busy.lanes) == (0, 2, 1, 2)
        assert [(inner.lane, inner.lanes) for inner in open_block.inner] == [(0, 2), (1, 2)]
        assert (busy.style, busy.css, busy.when, busy.inner) == (BUSY, "", "4:00 PM to 6:00 PM", [])
        assert busy.title == "Class: Intro to Linocut, 4:00 PM to 6:00 PM"

    def it_stands_a_booking_no_open_hours_hold_alone_and_stripes_a_day_off():
        tuesday = MONDAY + timedelta(days=1)
        grid = week_grid(
            _week(off=["tue"], tue=[_chip(BOOKED, tuesday, (13, 0), (14, 0), "Tour · 1 visitor", kind=TOURS)]),
            now=_at(MONDAY, 12),
        )
        day = grid.days[1]
        assert (day.state, day.note) == ("off", "Day Off")
        [block] = day.blocks
        assert (block.style, block.css, block.when, block.what) == (
            BOOKED,
            "pl-kind--tours",
            "1:00 PM",
            "Tour · 1 visitor",
        )
        assert (grid.days[0].state, grid.days[0].note) == ("", "")

    def it_stretches_the_hours_to_anything_before_nine_or_past_nine_and_ends_a_late_block_at_midnight():
        thursday, friday = MONDAY + timedelta(days=3), MONDAY + timedelta(days=4)
        late = Chip(BUSY, _at(thursday, 22), _at(friday, 0), "Busy in Google")
        early = _chip(BUSY, friday, (7, 30), (8, 0), "Busy in Google")
        grid = week_grid(_week(thu=[late], fri=[early]), now=_at(MONDAY, 12))
        assert (grid.first_hour, grid.last_hour) == (7, 24)
        assert (grid.days[3].blocks[0].start, grid.days[3].blocks[0].length) == (15 * 60, 120)
        assert grid.days[3].blocks[0].when == "10:00 PM to 12:00 AM"
        assert grid.days[4].blocks[0].start == 30

    def it_draws_the_now_line_on_today_only_while_now_is_on_the_grid():
        assert week_grid(_week(), now=_at(MONDAY, 14, 20)).days[0].now == 5 * 60 + 20
        assert week_grid(_week(), now=_at(MONDAY, 14, 20)).days[0].is_today
        assert [day.now for day in week_grid(_week(), now=_at(MONDAY, 14, 20)).days[1:]] == [None] * 6
        assert week_grid(_week(), now=_at(MONDAY, 6)).days[0].now is None
        assert week_grid(_week(), now=_at(MONDAY, 22)).days[0].now is None
        assert all(day.now is None for day in week_grid(_week(), now=_at(MONDAY - timedelta(days=1), 12)).days)


@pytest.mark.django_db
def describe_the_page():
    def it_builds_the_grid_from_the_same_week_busy_holds_gives():
        guide = make_guide()
        guild = GuildFactory(name="Printmaking Guild", guild_lead=guide)
        tuesday, wednesday = _next_week(1), _next_week(2)
        TourHoursFactory(guide=guide, weekday=1, start_time=time(11), end_time=time(18))
        OrientationAvailabilityFactory(guild=guild, orienter=guide, weekday=1, start_time=time(11), end_time=time(18))
        TourBookingFactory(guide=guide, starts_at=_at(tuesday, 13), ends_at=_at(tuesday, 14), party_size=3)
        TourDayOffFactory(guide=guide, starts_on=wednesday, ends_on=wednesday)
        page = MyHours.for_member(guide, week_offset=1)
        [block] = page.grid.days[1].blocks
        assert (block.style, [inner.what for inner in block.inner]) == (OPEN, ["Tour · 3 visitors"])
        assert page.grid.days[2].state == "off"

    def it_draws_the_hour_grid_on_the_page():
        guide = make_guide()
        tuesday = _next_week(1)
        TourHoursFactory(guide=guide, weekday=1, start_time=time(11), end_time=time(18))
        TourBookingFactory(guide=guide, starts_at=_at(tuesday, 13), ends_at=_at(tuesday, 14), party_size=3)
        TourDayOffFactory(guide=guide, starts_on=_next_week(2), ends_on=_next_week(2))
        html = signed_in(guide).get("/hours/?week_offset=1").content.decode()
        assert "css/hour-grid.css" in html
        assert 'data-hours-block="open"' in html
        assert 'data-hours-block="booked"' in html
        assert "pl-hourgrid__col--off" in html
        assert "Day Off" in html
        assert "--start: 120; --len: 420; --lane: 0; --lanes: 1;" in html
        assert "pl-calendar-grid--week" not in html

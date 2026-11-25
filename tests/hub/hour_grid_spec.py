"""The hour by hour week's layout (#1332): times, lanes for overlaps, the hour range and the day and week shapes."""

from __future__ import annotations

from datetime import date

from hub.hour_grid import Card, GridDay, HourGridWeek, Rail, clock, hour_label, hour_range, lay_lanes

MONDAY = date(2026, 11, 23)


def _rail(lane: int = 0) -> Rail:
    return Rail(start=0, length=60, lane=lane, tone="#e05d5d", key="1", title="Gina G. hours")


def _card() -> Card:
    return Card(
        start=0,
        length=60,
        lane=0,
        lanes=1,
        tone="#e05d5d",
        key="1",
        when="9:00 AM",
        label="Sam R. (2)",
        guide="Gina G.",
        title="Tour",
        event_pk=7,
    )


def _week(*, today: int | None) -> HourGridWeek:
    days = [GridDay(day=date(2026, 11, 23 + offset), is_today=offset == today) for offset in range(7)]
    return HourGridWeek(first_hour=8, last_hour=11, days=days, cell_template="x.html")


def describe_clock():
    def it_writes_the_house_style_with_a_no_break_space():
        assert clock(13 * 60) == "1:00 PM"
        assert clock(9 * 60 + 5) == "9:05 AM"

    def it_names_noon_and_midnight_twelve():
        assert clock(12 * 60 + 30) == "12:30 PM"
        assert clock(0) == "12:00 AM"
        assert clock(24 * 60) == "12:00 AM"


def describe_hour_label():
    def it_labels_each_hour_line():
        assert [hour_label(hour) for hour in (0, 9, 12, 21)] == ["12 AM", "9 AM", "12 PM", "9 PM"]


def describe_lay_lanes():
    def it_lays_nothing_out_of_nothing():
        assert lay_lanes([]) == []

    def it_gives_a_lone_span_the_whole_width():
        assert lay_lanes([(60, 120)]) == [(0, 1)]

    def it_puts_three_at_once_side_by_side_in_the_order_given():
        assert lay_lanes([(120, 300), (60, 240), (180, 360)]) == [(1, 3), (0, 3), (2, 3)]

    def it_does_not_count_back_to_back_as_overlap():
        assert lay_lanes([(60, 120), (120, 180)]) == [(0, 1), (0, 1)]

    def it_gives_the_longer_span_the_first_lane_on_a_tie():
        assert lay_lanes([(60, 90), (60, 180)]) == [(1, 2), (0, 2)]

    def it_reuses_a_lane_freed_inside_one_cluster():
        # A overlaps B, B overlaps C, A ends before C starts: C takes A's lane, and all three share 2 lanes.
        assert lay_lanes([(0, 60), (30, 120), (90, 150)]) == [(0, 2), (1, 2), (0, 2)]

    def it_starts_a_fresh_count_after_a_gap():
        assert lay_lanes([(0, 60), (30, 90), (200, 260)]) == [(0, 2), (1, 2), (0, 1)]


def describe_hour_range():
    def it_shows_nine_to_nine_by_default():
        assert hour_range([]) == (9, 21)
        assert hour_range([(10 * 60, 20 * 60)]) == (9, 21)

    def it_stretches_to_the_whole_hour_around_anything_outside():
        assert hour_range([(8 * 60, 10 * 60 + 30)]) == (8, 21)
        assert hour_range([(7 * 60 + 15, 9 * 60)]) == (7, 21)
        assert hour_range([(19 * 60, 21 * 60 + 30)]) == (9, 22)
        assert hour_range([(23 * 60, 24 * 60)]) == (9, 24)


def describe_grid_day():
    def it_keys_the_day_by_its_date():
        assert GridDay(day=MONDAY, is_today=False).key == "2026-11-23"

    def it_has_no_rail_room_and_no_items_when_empty():
        day = GridDay(day=MONDAY, is_today=False)
        assert (day.rails_px, day.has_items) == (0, False)

    def it_makes_room_for_each_rail_lane():
        day = GridDay(day=MONDAY, is_today=False, rails=[_rail(0), _rail(2), _rail(1)])
        assert day.rails_px == 4 + 9 * 3
        assert day.has_items

    def it_counts_a_card_alone_as_something_on_the_day():
        assert GridDay(day=MONDAY, is_today=False, cards=[_card()]).has_items


def describe_hour_grid_week():
    def it_counts_rows_and_labels_every_line_but_the_bottom():
        week = _week(today=None)
        assert week.rows == 3
        assert week.hour_labels == [(0, "8 AM"), (1, "9 AM"), (2, "10 AM")]

    def it_opens_a_phone_on_today():
        assert _week(today=2).active_day == "2026-11-25"

    def it_opens_a_phone_on_the_first_day_of_another_week():
        assert _week(today=None).active_day == "2026-11-23"

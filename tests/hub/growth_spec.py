"""Specs for the Growth dashboard's numbers and controls (#795, ``hub/growth.py``).

Rows are stamped into May 2026 (or around the November clock change), well before any day a
factory's own ``auto_now_add`` could land, so a row a factory makes along the way never counts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db.models import Model
from django.http import QueryDict

from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import CmsActivity
from hub.growth import (
    CMS_GROUPS,
    LAUNCH,
    LEGACY_SIGNUP_SOURCE,
    METRICS,
    TILES,
    Bucket,
    DateRange,
    GrowthForm,
    Layer,
    bar_chart,
    bucket_keys,
    buckets,
    class_import_count,
    cms_group,
    dashboard,
    preset_range,
    running_total_chart,
    stacked_chart,
    totals,
)
from tests.core.factories import FeedbackRequestFactory
from tests.membership.factories import (
    CommunityEventFactory,
    EquipmentReservationFactory,
    MemberFactory,
    OrientationBookingFactory,
)

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
MAY = DateRange(date(2026, 5, 4), date(2026, 5, 31))  # Monday May 4 to Sunday May 31
INSIDE = date(2026, 5, 12)
OUTSIDE = date(2026, 6, 2)
TODAY = date(2026, 10, 13)


def _at(day: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=PORTLAND)


def _stamp(obj: Model, **fields: Any) -> None:
    type(obj)._default_manager.filter(pk=obj.pk).update(**fields)


def _activity(when: datetime, kind: str = CmsActivity.Kind.CLASS_SUBMITTED, **fields: Any) -> CmsActivity:
    row = CmsActivity.objects.create(kind=kind, **fields)
    _stamp(row, created_at=when)
    return row


def _legacy_signup(when: datetime) -> Any:
    """A signup imported from the old class site: a registration and its marker row."""
    registration = RegistrationFactory()
    _stamp(registration, registered_at=when)
    _activity(
        when,
        CmsActivity.Kind.REGISTRATION_CREATED,
        registration=registration,
        payload={"source": LEGACY_SIGNUP_SOURCE, "legacy_signup_uuid": "abc"},
    )
    return registration


def _member(when: datetime) -> None:
    _stamp(MemberFactory(), created_at=when)


def _inquiry(when: datetime) -> None:
    _stamp(MemberFactory(), teaching_applied_at=when)


def _signup(when: datetime) -> None:
    _stamp(RegistrationFactory(), registered_at=when)


def _guest_signup(when: datetime) -> None:
    _stamp(RegistrationFactory(member=None), registered_at=when)


def _member_signup(when: datetime) -> None:
    _stamp(RegistrationFactory(member=MemberFactory()), registered_at=when)


def _class(when: datetime) -> None:
    _stamp(ClassOfferingFactory(), created_at=when)


def _published(when: datetime) -> None:
    _stamp(ClassOfferingFactory(), published_at=when)


def _cms(when: datetime) -> None:
    _activity(when)


def _reservation(when: datetime) -> None:
    _stamp(EquipmentReservationFactory(), created_at=when)


def _orientation(when: datetime) -> None:
    _stamp(OrientationBookingFactory(), requested_at=when)


def _event(when: datetime) -> None:
    _stamp(CommunityEventFactory(), created_at=when)


def _feedback(when: datetime) -> None:
    _stamp(FeedbackRequestFactory(), created_at=when)


MAKERS: dict[str, Callable[[datetime], None]] = {
    "new_members": _member,
    "instructor_inquiries": _inquiry,
    "class_signups": _signup,
    "class_signups_guest": _guest_signup,
    "class_signups_member": _member_signup,
    "classes_created": _class,
    "classes_published": _published,
    "cms_actions": _cms,
    "reservations": _reservation,
    "orientations": _orientation,
    "events": _event,
    "feedback_requests": _feedback,
}


def _count(key: str, rng: DateRange = MAY) -> int:
    return totals(METRICS[key], {"n": rng})["n"]


def describe_metrics():
    def it_has_a_maker_for_every_metric():
        assert set(MAKERS) == set(METRICS)

    @pytest.mark.parametrize("key", list(MAKERS))
    def it_counts_a_row_on_a_day_in_the_range_and_not_one_after(key: str):
        MAKERS[key](_at(INSIDE))
        MAKERS[key](_at(OUTSIDE))
        assert _count(key) == 1
        assert sum(b.value for b in buckets(METRICS[key], MAY, "week")) == 1
        assert sum(b.value for b in buckets(METRICS[key], MAY, "month")) == 1

    def it_splits_portal_signups_into_guests_and_members():
        _guest_signup(_at(INSIDE))
        _member_signup(_at(INSIDE))
        _member_signup(_at(INSIDE))
        assert (_count("class_signups"), _count("class_signups_guest"), _count("class_signups_member")) == (3, 1, 2)

    def it_counts_only_published_classes_by_the_day_they_published():
        _stamp(ClassOfferingFactory(), created_at=_at(INSIDE))
        assert _count("classes_published") == 0

    def it_counts_the_range_s_first_and_last_local_days_whole():
        _member(_at(MAY.start, 0, 0))
        _member(_at(MAY.end, 23, 59))
        _member(_at(date(2026, 5, 3), 23, 59))
        _member(_at(date(2026, 6, 1), 0, 0))
        assert _count("new_members") == 2


def describe_exclusions():
    def it_leaves_legacy_classes_out_of_classes_created_and_published():
        _stamp(ClassOfferingFactory(legacy_cms_id="drupal-1"), created_at=_at(INSIDE), published_at=_at(INSIDE))
        _class(_at(INSIDE))
        assert _count("classes_created") == 1
        assert _count("classes_published") == 0

    def it_leaves_the_class_import_rows_out_of_cms_actions():
        legacy = ClassOfferingFactory(legacy_cms_id="drupal-2")
        _activity(_at(INSIDE), CmsActivity.Kind.CLASS_CREATED, class_offering=legacy)
        _activity(_at(INSIDE), CmsActivity.Kind.CLASS_PUBLISHED, class_offering=legacy)
        _activity(_at(INSIDE), CmsActivity.Kind.CLASS_CREATED, class_offering=ClassOfferingFactory())
        assert _count("cms_actions") == 2
        # Saving a class logs its own class_created row too; every one on a legacy class is an import.
        imports = CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CREATED, class_offering__legacy_cms_id__gt="")
        assert class_import_count() == imports.count() >= 1

    def it_keeps_a_class_created_row_with_no_class():
        _activity(_at(INSIDE), CmsActivity.Kind.CLASS_CREATED, class_offering=None)
        assert _count("cms_actions") == 1

    def it_leaves_old_site_signups_and_their_markers_out():
        _legacy_signup(_at(INSIDE))
        _signup(_at(INSIDE))
        assert _count("class_signups") == 1
        assert _count("class_signups_guest") + _count("class_signups_member") == 1
        assert _count("cms_actions") == 0

    def it_keeps_activity_whose_payload_has_no_source_key():
        # The probe's trap: excluding on the JSON key drops every row without it.
        _activity(_at(INSIDE), payload={})
        _activity(_at(INSIDE), payload={"note": "x"})
        _activity(_at(INSIDE), payload={"source": "elsewhere"})
        assert _count("cms_actions") == 3

    def it_treats_a_registration_as_old_site_only_through_its_registration_created_marker():
        registration = RegistrationFactory()
        _stamp(registration, registered_at=_at(INSIDE))
        _activity(
            _at(INSIDE),
            CmsActivity.Kind.REGISTRATION_CONFIRMED,
            registration=registration,
            payload={"source": LEGACY_SIGNUP_SOURCE},
        )
        assert _count("class_signups") == 1
        assert _count("cms_actions") == 0


def describe_date_range():
    def it_counts_its_days_inclusively():
        assert MAY.days == 28

    def it_compares_with_the_same_number_of_days_just_before():
        assert MAY.previous == DateRange(date(2026, 4, 6), date(2026, 5, 3))

    def it_has_no_previous_period_when_that_would_start_before_launch():
        assert DateRange(LAUNCH, date(2026, 4, 30)).previous is None

    def it_has_a_previous_period_that_starts_on_launch_day():
        rng = DateRange(date(2026, 4, 6), date(2026, 4, 15))
        assert rng.previous == DateRange(LAUNCH, date(2026, 4, 5))

    def it_labels_itself_with_both_days():
        assert MAY.label == "May 4, 2026 to May 31, 2026"

    def describe_presets():
        def it_ends_every_preset_today():
            assert preset_range("30", TODAY) == DateRange(date(2026, 9, 14), TODAY)
            assert preset_range("90", TODAY) == DateRange(date(2026, 7, 16), TODAY)
            assert preset_range("launch", TODAY) == DateRange(LAUNCH, TODAY)

        def it_never_starts_before_launch():
            assert preset_range("90", date(2026, 4, 10)).start == LAUNCH


def describe_buckets():
    def it_lists_every_week_of_an_empty_range_at_zero_from_its_monday():
        weeks = buckets(METRICS["new_members"], DateRange(date(2026, 5, 6), date(2026, 5, 20)), "week")
        assert [b.key for b in weeks] == [date(2026, 5, 4), date(2026, 5, 11), date(2026, 5, 18)]
        assert [b.value for b in weeks] == [0, 0, 0]
        assert (weeks[0].start, weeks[-1].end) == (date(2026, 5, 6), date(2026, 5, 20))

    def it_lists_every_month_clipped_to_the_range():
        months = bucket_keys(DateRange(date(2026, 7, 16), date(2026, 10, 13)), "month")
        assert [b.key for b in months] == [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
        assert (months[0].start, months[-1].end) == (date(2026, 7, 16), date(2026, 10, 13))

    def it_buckets_weeks_by_the_portland_monday_across_the_clock_change():
        # Clocks fall back at 2am on Sunday November 1, 2026. 23:30 that Sunday is already
        # Monday in UTC, so a UTC week would move it into the next week.
        rng = DateRange(date(2026, 10, 26), date(2026, 11, 8))
        _member(_at(date(2026, 10, 26), 0, 15))
        _member(_at(date(2026, 11, 1), 23, 30))
        _member(_at(date(2026, 11, 2), 0, 30))
        weeks = buckets(METRICS["new_members"], rng, "week")
        assert [(b.key, b.value) for b in weeks] == [(date(2026, 10, 26), 2), (date(2026, 11, 2), 1)]

    def it_buckets_months_by_the_portland_day():
        _member(_at(date(2026, 10, 31), 23, 30))  # November 1 in UTC
        months = buckets(METRICS["new_members"], DateRange(date(2026, 10, 1), date(2026, 11, 30)), "month")
        assert [(b.key, b.value) for b in months] == [(date(2026, 10, 1), 1), (date(2026, 11, 1), 0)]

    def describe_labels():
        def it_names_a_week_by_its_monday_and_a_month_by_its_name():
            week = Bucket(date(2026, 9, 28), date(2026, 9, 28), date(2026, 10, 4), 3)
            assert week.label("week") == "Sep 28"
            assert Bucket(date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 30), 3).label("month") == "Sep"

        def it_titles_a_whole_week_and_a_whole_month_plainly():
            assert Bucket(date(2026, 9, 28), date(2026, 9, 28), date(2026, 10, 4), 0).title("week", TODAY) == (
                "Week of Sep 28"
            )
            assert Bucket(date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 30), 0).title("month", TODAY) == (
                "September 2026"
            )

        def it_says_when_only_part_of_a_bucket_is_in_range():
            assert Bucket(date(2026, 10, 12), date(2026, 10, 12), date(2026, 10, 13), 0).title("week", TODAY) == (
                "Week of Oct 12 (so far)"
            )
            assert Bucket(date(2026, 7, 13), date(2026, 7, 16), date(2026, 7, 19), 0).title("week", TODAY) == (
                "Week of Jul 13 (Jul 16 to Jul 19)"
            )
            assert Bucket(date(2026, 7, 1), date(2026, 7, 16), date(2026, 7, 31), 0).title("month", TODAY) == (
                "July 2026 (Jul 16 to Jul 31)"
            )


def describe_cms_group():
    def it_puts_every_activity_kind_in_a_group():
        groups = dict(CMS_GROUPS)
        for kind in CmsActivity.Kind.values:
            assert cms_group(kind) in groups

    def it_counts_money_moving_as_payments():
        assert cms_group(CmsActivity.Kind.REGISTRATION_CONFIRMED) == "payment"
        assert cms_group(CmsActivity.Kind.PAYMENT_LINK_SENT) == "payment"
        assert cms_group(CmsActivity.Kind.DUPLICATE_PAYMENT) == "payment"
        assert cms_group(CmsActivity.Kind.REGISTRATION_CREATED) == "registration"
        assert cms_group(CmsActivity.Kind.WAITLIST_JOINED) == "waitlist"
        assert cms_group(CmsActivity.Kind.DISCOUNT_CODE_REDEEMED) == "discount"
        assert cms_group(CmsActivity.Kind.CLASS_PUBLISHED) == "class"

    def it_refuses_a_kind_no_group_holds():
        with pytest.raises(ValueError, match="mystery_thing"):
            cms_group("mystery_thing")


def _weeks(*values: int) -> list[Bucket]:
    start = date(2026, 9, 7)
    out = []
    for i, value in enumerate(values):
        key = date.fromordinal(start.toordinal() + 7 * i)
        out.append(Bucket(key, key, date.fromordinal(key.toordinal() + 6), value))
    return out


def describe_charts():
    def describe_bar_chart():
        def it_labels_the_tallest_bar_and_the_last():
            chart = bar_chart(1090, 320, "T", "S", _weeks(3, 9, 2, 9, 4), "week", "New Members", TODAY)
            assert [v.text for v in chart.values] == ["9", "4"]
            assert len(chart.shapes) == 5
            assert chart.hits[1].tip == "Week of Sep 14\nNew Members: 9"

        def it_draws_no_bars_or_labels_for_an_empty_range():
            chart = bar_chart(316, 260, "T", "S", _weeks(0, 0, 0), "week", "New Members", TODAY)
            assert chart.shapes == []
            assert chart.values == []
            assert [line.label for line in chart.grid] == [0, 1]

        def it_rounds_the_axis_to_five_ticks_at_most():
            chart = bar_chart(1090, 320, "T", "S", _weeks(42), "week", "CMS Actions", TODAY)
            assert [line.label for line in chart.grid] == [0, 10, 20, 30, 40, 50]
            assert chart.grid[0].is_axis and not chart.grid[1].is_axis

        def it_draws_one_tick_for_a_count_past_every_step():
            chart = bar_chart(1090, 320, "T", "S", _weeks(60001), "week", "X", TODAY)
            assert [line.label for line in chart.grid] == [0, 60001]

        def it_thins_crowded_axis_labels_but_keeps_the_last():
            chart = bar_chart(316, 260, "T", "S", _weeks(*range(14)), "week", "X", TODAY)
            labels = [label.text for label in chart.x_labels]
            assert 1 < len(labels) < 14
            assert labels[0] == "Sep 7"
            assert labels[-1] == "Dec 7"

    def describe_running_total_chart():
        def it_adds_the_weeks_up_and_labels_the_end():
            chart = running_total_chart(1090, 320, "T", "S", _weeks(2, 0, 5), "Class Signups", TODAY)
            assert [v.text for v in chart.values] == ["7"]
            assert chart.line.startswith("M") and chart.area.endswith("Z")
            assert chart.dot is not None
            assert chart.hits[2].tip.endswith("Class Signups, running total: 7\nThat week: 5")
            assert all(hit.dot_x for hit in chart.hits)

    def describe_stacked_chart():
        def it_stacks_only_the_layers_with_a_count_and_tops_them_with_the_total():
            months = [Bucket(date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 30), 0)] * 2
            layers = [Layer("A", 1, [3, 0]), Layer("B", 2, [2, 0])]
            chart = stacked_chart(620, 300, "T", "S", months, layers, TODAY)
            assert [s.css for s in chart.shapes] == ["pl-growth__seg--1", "pl-growth__seg--2"]
            assert chart.shapes[0].x and not chart.shapes[0].d  # the lower segment is a plain rect
            assert chart.shapes[1].d  # the top one has rounded corners
            assert [v.text for v in chart.values] == ["5"]
            assert chart.hits[0].tip == "September 2026\nA: 3\nB: 2"


def describe_dashboard():
    def _board(rng: DateRange = MAY, metric: str = "cms_actions", mode: Any = "week") -> Any:
        return dashboard(rng, "Custom Range", metric, mode, TODAY)

    def it_builds_the_whole_page_in_fifteen_aggregate_queries(django_assert_num_queries: Any):
        # Five tiles (their weeks, then the period before), the trend and its since launch
        # total, the CMS card, the source card and the class import count.
        _member(_at(INSIDE))
        _cms(_at(INSIDE))
        with django_assert_num_queries(15):
            _board()

    def describe_tiles():
        def it_shows_the_five_tiles_in_order():
            assert [t.label for t in _board().tiles] == [label for _, label in TILES]

        def it_says_since_launch_when_there_is_nothing_before_to_compare():
            tile = _board(DateRange(LAUNCH, date(2026, 4, 30))).tiles[0]
            assert (tile.change, tile.previous) == ("since_launch", None)

        def it_says_none_in_either_period():
            tile = _board().tiles[0]
            assert (tile.value, tile.previous, tile.change, tile.percent) == (0, 0, "none", 0)

        def it_says_new_when_the_period_before_had_none():
            _member(_at(INSIDE))
            assert _board().tiles[0].change == "new"

        def it_compares_with_the_period_before_as_a_percentage():
            for _ in range(4):
                _member(_at(date(2026, 4, 20)))
            for _ in range(5):
                _member(_at(INSIDE))
            tile = _board().tiles[0]
            assert (tile.value, tile.previous, tile.change, tile.percent) == (5, 4, "up", 25)

        def it_shows_a_fall_as_down():
            for _ in range(4):
                _member(_at(date(2026, 4, 20)))
            _member(_at(INSIDE))
            tile = _board().tiles[0]
            assert (tile.change, tile.percent) == ("down", 75)

        def it_draws_a_sparkline_per_week():
            _member(_at(INSIDE))
            tile = _board().tiles[0]
            assert tile.spark_line.count("L") == 3  # four weeks, three segments
            assert tile.spark_area.endswith("L100 32L0 32Z")

        def it_centres_a_one_week_sparkline():
            tile = _board(DateRange(date(2026, 5, 4), date(2026, 5, 6))).tiles[0]
            assert tile.spark_line == "M50.0 30.0"

    def describe_trend():
        def it_counts_the_range_and_everything_since_launch():
            _cms(_at(INSIDE))
            _cms(_at(date(2026, 4, 1)))
            main = _board().main
            assert (main.total, main.since_launch) == (1, 2)
            assert main.chart.wide.title == "CMS Actions per Week"
            assert main.chart.wide.width == 1090 and main.chart.narrow.width == 316
            assert main.filename == "growth-cms-actions-2026-05-04-to-2026-05-31.png"

        def it_draws_months_and_the_running_total():
            assert _board(mode="month").main.chart.wide.title == "CMS Actions per Month"
            assert len(_board(mode="month").main.chart.wide.hits) == 1
            assert _board(metric="new_members", mode="total").main.chart.wide.title == "New Members, Running Total"

    def describe_cms_card():
        def it_counts_each_group_per_month_with_totals():
            _activity(_at(INSIDE), CmsActivity.Kind.CLASS_SUBMITTED)
            _activity(_at(INSIDE), CmsActivity.Kind.CLASS_APPROVED)
            _activity(_at(INSIDE), CmsActivity.Kind.REGISTRATION_REFUNDED)
            _activity(_at(INSIDE), CmsActivity.Kind.WAITLIST_LEFT)
            cms = _board(DateRange(date(2026, 4, 20), date(2026, 5, 31))).cms
            assert cms.months == ["Apr", "May"]
            assert {row.name: row.values for row in cms.rows} == {
                "Class Lifecycle": [0, 2],
                "Registrations": [0, 0],
                "Payments": [0, 1],
                "Waitlist": [0, 1],
                "Discounts": [0, 0],
            }
            assert (cms.column_totals, cms.total) == ([0, 4], 4)

    def describe_source_card():
        def it_splits_signups_by_where_they_came_from():
            _legacy_signup(_at(INSIDE))
            _legacy_signup(_at(INSIDE))
            _guest_signup(_at(INSIDE))
            _member_signup(_at(INSIDE))
            source = _board().source
            assert (source.portal, source.old_site, source.guests, source.members, source.total) == (2, 2, 1, 1, 4)
            assert source.chart.wide.title == "Class Signups by Source"


def _form(today: date = TODAY, **params: str) -> GrowthForm:
    data = QueryDict(mutable=True)
    data.update(params)
    form = GrowthForm(data, today=today)
    form.is_valid()
    return form


def describe_growth_form():
    def it_defaults_to_the_last_90_days_of_cms_actions_per_week():
        selection = _form().selection()
        assert (selection.preset, selection.rng, selection.metric, selection.mode) == (
            "90",
            DateRange(date(2026, 7, 16), TODAY),
            "cms_actions",
            "week",
        )
        assert selection.range_name == "Last 90 Days"

    def it_fills_the_inputs_with_a_preset_s_days():
        form = _form(range="30")
        assert form.selection().rng == DateRange(date(2026, 9, 14), TODAY)
        assert (form["date_from"].value(), form["date_to"].value()) == ("2026-09-14", "2026-10-13")

    def it_takes_a_custom_range():
        selection = _form(range="custom", date_from="2026-05-04", date_to="2026-05-31").selection()
        assert (selection.rng, selection.range_name) == (MAY, "Custom Range")

    def it_treats_bare_dates_as_custom():
        assert _form(date_from="2026-05-04").selection().rng == DateRange(date(2026, 5, 4), TODAY)

    def it_reads_a_custom_range_with_no_days_as_since_launch():
        assert _form(range="custom").selection().rng == DateRange(LAUNCH, TODAY)

    def it_moves_a_future_day_to_today_and_shows_it():
        form = _form(range="custom", date_from="2026-10-01", date_to="2027-01-01")
        assert form.selection().rng == DateRange(date(2026, 10, 1), TODAY)
        assert form["date_to"].value() == "2026-10-13"

    def it_moves_a_day_before_launch_to_launch():
        assert _form(range="custom", date_from="2025-01-01", date_to="2026-04-01").selection().rng == DateRange(
            LAUNCH, date(2026, 4, 1)
        )

    def it_refuses_a_to_before_from_on_to_and_shows_the_default():
        form = _form(range="custom", date_from="2026-05-31", date_to="2026-05-04")
        assert form.errors["date_to"] == ["To is before From. Pick a later day."]
        assert form.selection().rng == preset_range("90", TODAY)

    def it_falls_back_on_an_unknown_preset_metric_or_mode():
        form = _form(range="7", metric="nope", mode="daily")
        selection = form.selection()
        assert (selection.preset, selection.metric, selection.mode) == ("90", "cms_actions", "week")
        assert form["metric"].value() == "cms_actions"

    def it_keeps_the_metric_and_mode_when_the_dates_are_wrong():
        selection = _form(date_from="2026-05-31", date_to="2026-05-04", metric="new_members", mode="total").selection()
        assert (selection.metric, selection.mode) == ("new_members", "total")

    def it_limits_the_date_inputs_to_launch_through_today():
        widget = _form().fields["date_from"].widget
        assert (widget.attrs["min"], widget.attrs["max"], widget.input_type) == ("2026-03-27", "2026-10-13", "date")

    def it_lists_every_metric_in_the_picker_by_group():
        groups = dict(_form().fields["metric"].choices)
        assert list(groups) == ["People", "Classes and CMS", "Shop and Spaces", "Community"]
        assert sum(len(options) for options in groups.values()) == len(METRICS)

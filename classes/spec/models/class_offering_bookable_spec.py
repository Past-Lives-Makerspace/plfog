"""BDD specs for the booking window — a dated class drops out once it starts.

You can't join a single class after its date, and you can't join a series
part-way through, so a started series is never bookable.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _published(**kwargs):
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    return ClassOfferingFactory(**kwargs)


def _with_sessions(offering, *offsets_days):
    """Attach one session per offset (negative = past, positive = future)."""
    base = timezone.now()
    for days in offsets_days:
        start = base + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_has_started():
    def it_is_false_for_a_future_single(db):
        offering = _with_sessions(_published(), 5)
        assert offering.has_started is False

    def it_is_true_for_a_past_single(db):
        offering = _with_sessions(_published(), -5)
        assert offering.has_started is True

    def it_is_true_once_a_series_first_session_has_passed(db):
        series = _published(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
        _with_sessions(series, -3, 4, 11)  # first date already happened
        assert series.has_started is True

    def it_is_false_with_no_sessions(db):
        assert _published().has_started is False


def describe_is_bookable():
    def it_is_true_for_a_future_single(db):
        assert _with_sessions(_published(), 5).is_bookable is True

    def it_is_false_for_a_past_single(db):
        assert _with_sessions(_published(), -1).is_bookable is False

    def it_is_true_for_a_fully_future_series(db):
        series = _published(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
        _with_sessions(series, 7, 14, 21)
        assert series.is_bookable is True

    def it_is_false_for_a_started_series(db):
        series = _published(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
        _with_sessions(series, -3, 4, 11)  # later sessions remain, but it has begun
        assert series.is_bookable is False

    def it_is_true_for_a_flexible_class_with_no_dates(db):
        flexible = _published(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert flexible.is_bookable is True

    def it_is_false_for_a_fixed_class_with_no_dates(db):
        assert _published().is_bookable is False


def describe_bookable_queryset():
    def it_includes_a_future_single(db):
        offering = _with_sessions(_published(slug="future-single"), 5)
        assert offering in ClassOffering.objects.bookable()

    def it_includes_a_fully_future_series(db):
        series = _published(slug="future-series", scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
        _with_sessions(series, 7, 14, 21)
        assert series in ClassOffering.objects.bookable()

    def it_excludes_a_started_series(db):
        series = _published(slug="started-series", scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
        _with_sessions(series, -3, 4, 11)
        assert series not in ClassOffering.objects.bookable()

    def it_excludes_a_past_single(db):
        offering = _with_sessions(_published(slug="past-single"), -2)
        assert offering not in ClassOffering.objects.bookable()

    def it_excludes_a_draft(db):
        offering = _with_sessions(ClassOfferingFactory(slug="draft-future"), 5)
        assert offering not in ClassOffering.objects.bookable()

    def it_includes_a_flexible_class(db):
        flexible = _published(slug="flex", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        assert flexible in ClassOffering.objects.bookable()

    def it_orders_soonest_first(db):
        later = _with_sessions(_published(slug="later", title="Later"), 20)
        sooner = _with_sessions(_published(slug="sooner", title="Sooner"), 2)
        ordered = list(ClassOffering.objects.bookable())
        assert ordered.index(sooner) < ordered.index(later)


def describe_a_flexible_classs_window():
    """A flexible class is bookable through its last day on the site's local date and gone the day after (#545)."""

    def _flexible(**traits):
        return _published(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, **traits)

    def it_is_bookable_on_its_last_day_and_not_the_day_after(db):
        today = timezone.localdate()
        last_day = _flexible(slug="last-day", flexible_ends_on=today)
        over = _flexible(slug="over", flexible_ends_on=today - timedelta(days=1))
        assert last_day.is_bookable is True
        assert over.is_bookable is False
        assert last_day in ClassOffering.objects.bookable()
        assert over not in ClassOffering.objects.bookable()

    def it_stays_bookable_with_only_a_first_day_however_far_back(db):
        offering = _flexible(slug="from-only", flexible_starts_on=timezone.localdate() - timedelta(days=400))
        assert offering.is_bookable is True
        assert offering in ClassOffering.objects.bookable()

    def it_is_bookable_before_its_first_day(db):
        # The window says when the class runs, not when sign-ups open.
        offering = _flexible(slug="ahead", flexible_starts_on=timezone.localdate() + timedelta(days=30))
        assert offering.is_bookable is True
        assert offering in ClassOffering.objects.bookable()

    def it_reads_the_last_day_on_the_local_calendar_not_the_clock(db):
        from datetime import date
        from unittest import mock

        offering = _flexible(slug="calendar", flexible_ends_on=date(2026, 12, 1))
        with mock.patch("classes.models.timezone.localdate", return_value=date(2026, 12, 1)):
            assert offering.is_bookable is True
            assert offering in ClassOffering.objects.bookable()
        with mock.patch("classes.models.timezone.localdate", return_value=date(2026, 12, 2)):
            assert offering.is_bookable is False
            assert offering not in ClassOffering.objects.bookable()

    def it_never_reads_the_session_rows_a_flexible_class_still_carries(db):
        # Production class 665's shape: a future session standing in for a window that has ended.
        over = _with_sessions(
            _flexible(slug="stale-session", flexible_ends_on=timezone.localdate() - timedelta(days=1)), 5
        )
        assert over.is_bookable is False
        assert over not in ClassOffering.objects.bookable()
        # And a past session on an open window changes nothing either.
        open_window = _with_sessions(_flexible(slug="past-session"), -40)
        assert open_window.is_bookable is True
        assert open_window in ClassOffering.objects.bookable()

    def it_leaves_a_fixed_class_alone_whatever_the_window_columns_hold(db):
        # The form clears the window on a Fixed save; even stale columns never gate a dated class.
        stale = _with_sessions(
            _published(slug="fixed-stale", flexible_ends_on=timezone.localdate() - timedelta(days=1)), 5
        )
        assert stale.is_bookable is True
        assert stale in ClassOffering.objects.bookable()

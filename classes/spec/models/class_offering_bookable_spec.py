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

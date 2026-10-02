"""BDD specs for the registration cutoff: sign-ups close a set number of hours before a class starts.

``bookable()`` and ``is_bookable`` keep meaning "not started"; the cutoff is the narrower gate
read by the register view, the class page rail, the run switcher and the sibling list.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db


def _published(**kwargs):
    kwargs.setdefault("status", ClassOffering.Status.PUBLISHED)
    return ClassOfferingFactory(**kwargs)


def _with_sessions(offering, *offsets_hours):
    """Attach one session per offset in hours (negative = past, positive = future)."""
    base = timezone.now()
    for hours in offsets_hours:
        start = base + timedelta(hours=hours)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return offering


def describe_registration_closes_at():
    def it_is_the_first_session_minus_the_hours_for_a_single():
        offering = _with_sessions(_published(registration_cutoff_hours=48), 100)
        first = offering.sessions.get().starts_at
        assert offering.registration_closes_at == first - timedelta(hours=48)

    def it_counts_a_series_from_its_first_session():
        series = _published(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE, registration_cutoff_hours=24)
        _with_sessions(series, 100, 200, 300)
        first = series.sessions.order_by("starts_at").first().starts_at
        assert series.registration_closes_at == first - timedelta(hours=24)

    def it_is_none_for_a_flexible_class():
        flexible = _published(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, registration_cutoff_hours=48)
        assert flexible.registration_closes_at is None

    def it_is_none_with_no_sessions():
        assert _published(registration_cutoff_hours=48).registration_closes_at is None

    def it_is_none_when_the_cutoff_is_off():
        offering = _with_sessions(_published(registration_cutoff_hours=None), 100)
        assert offering.registration_closes_at is None

    def it_defaults_to_48_hours():
        assert ClassOffering._meta.get_field("registration_cutoff_hours").default == 48
        assert _published().registration_cutoff_hours == 48


def describe_registration_open():
    def it_is_true_well_before_the_cutoff():
        assert _with_sessions(_published(registration_cutoff_hours=48), 100).registration_open is True

    def it_is_false_inside_the_cutoff():
        assert _with_sessions(_published(registration_cutoff_hours=48), 47).registration_open is False

    def it_is_false_at_the_cutoff_instant_and_true_one_second_before():
        offering = _with_sessions(_published(registration_cutoff_hours=48), 100)
        closes_at = offering.registration_closes_at
        with mock.patch("classes.models.timezone.now", return_value=closes_at):
            assert offering.registration_open is False
        with mock.patch("classes.models.timezone.now", return_value=closes_at + timedelta(seconds=1)):
            assert offering.registration_open is False
        with mock.patch("classes.models.timezone.now", return_value=closes_at - timedelta(seconds=1)):
            assert offering.registration_open is True

    def it_is_true_right_up_to_the_start_when_the_cutoff_is_off():
        assert _with_sessions(_published(registration_cutoff_hours=None), 1).registration_open is True

    def it_is_true_for_a_flexible_class_whatever_the_hours():
        flexible = _published(scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, registration_cutoff_hours=48)
        assert flexible.registration_open is True

    def it_is_false_once_the_class_has_started_even_with_the_cutoff_off():
        assert _with_sessions(_published(registration_cutoff_hours=None), -1).registration_open is False

    def it_is_false_with_no_sessions():
        assert _published(registration_cutoff_hours=None).registration_open is False

    def it_reads_the_first_session_annotation_with_no_query_of_its_own(django_assert_num_queries):
        _with_sessions(_published(slug="annotated-open", registration_cutoff_hours=48), 100)
        _with_sessions(_published(slug="annotated-closed", registration_cutoff_hours=48), 10)
        rows = {row.slug: row for row in ClassOffering.objects.bookable()}
        with django_assert_num_queries(0):
            assert rows["annotated-open"].registration_open is True
            assert rows["annotated-closed"].registration_open is False

    def it_runs_one_session_query_on_a_row_without_the_annotation(django_assert_num_queries):
        offering = _with_sessions(_published(registration_cutoff_hours=48), 100)
        row = ClassOffering.objects.get(pk=offering.pk)
        with django_assert_num_queries(1):
            assert row.registration_closes_at is not None


def describe_registration_open_queryset():
    def it_keeps_a_class_well_before_its_cutoff():
        offering = _with_sessions(_published(slug="qs-open", registration_cutoff_hours=48), 100)
        assert offering in ClassOffering.objects.registration_open()

    def it_drops_a_class_inside_its_cutoff_that_bookable_still_lists():
        offering = _with_sessions(_published(slug="qs-closed", registration_cutoff_hours=48), 47)
        assert offering in ClassOffering.objects.bookable()
        assert offering not in ClassOffering.objects.registration_open()

    def it_is_closed_just_inside_the_cutoff_and_open_just_outside_it():
        inside = _with_sessions(_published(slug="qs-inside", registration_cutoff_hours=48), 48 - 1 / 60)
        outside = _with_sessions(_published(slug="qs-outside", registration_cutoff_hours=48), 48 + 1 / 60)
        pks = set(ClassOffering.objects.registration_open().values_list("pk", flat=True))
        assert outside.pk in pks
        assert inside.pk not in pks

    def it_keeps_a_class_with_the_cutoff_off_until_it_starts():
        offering = _with_sessions(_published(slug="qs-off", registration_cutoff_hours=None), 1)
        assert offering in ClassOffering.objects.registration_open()

    def it_keeps_a_flexible_class():
        flexible = _published(
            slug="qs-flex", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, registration_cutoff_hours=48
        )
        assert flexible in ClassOffering.objects.registration_open()

    def it_drops_a_started_class():
        offering = _with_sessions(_published(slug="qs-started", registration_cutoff_hours=None), -1)
        assert offering not in ClassOffering.objects.registration_open()

    def it_counts_a_series_from_its_first_session():
        series = _published(
            slug="qs-series",
            scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE,
            registration_cutoff_hours=48,
        )
        _with_sessions(series, 47, 200, 300)
        assert series in ClassOffering.objects.bookable()
        assert series not in ClassOffering.objects.registration_open()

    def it_agrees_with_the_property_row_by_row():
        _with_sessions(_published(slug="agree-open", registration_cutoff_hours=48), 100)
        _with_sessions(_published(slug="agree-closed", registration_cutoff_hours=48), 10)
        _with_sessions(_published(slug="agree-off", registration_cutoff_hours=None), 10)
        _published(slug="agree-flex", scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE)
        expected = {row.slug for row in ClassOffering.objects.bookable() if row.registration_open}
        assert expected == {"agree-open", "agree-off", "agree-flex"}
        assert set(ClassOffering.objects.registration_open().values_list("slug", flat=True)) == expected

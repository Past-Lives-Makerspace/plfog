"""BDD specs for ClassOffering series-vs-single scheduling type."""

from __future__ import annotations

from classes.factories import ClassOfferingFactory, SeriesClassOfferingFactory
from classes.models import ClassOffering


def describe_ClassOffering_scheduling_type():
    def it_defaults_to_single_session(db):
        offering = ClassOfferingFactory()
        assert offering.scheduling_type == ClassOffering.SchedulingType.SINGLE_SESSION

    def describe_is_series():
        def it_is_true_for_series_package(db):
            offering = ClassOfferingFactory(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
            assert offering.is_series is True
            assert offering.is_single is False

        def it_is_false_for_single_session(db):
            offering = ClassOfferingFactory()
            assert offering.is_series is False
            assert offering.is_single is True

    def describe_series_session_count():
        def it_counts_the_offerings_sessions(db):
            offering = SeriesClassOfferingFactory(session_count=3)
            assert offering.series_session_count == 3

        def it_is_zero_with_no_sessions(db):
            offering = ClassOfferingFactory(scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE)
            assert offering.series_session_count == 0

    def describe_factory():
        def it_builds_a_three_session_series(db):
            offering = SeriesClassOfferingFactory(session_count=3)
            assert offering.is_series
            assert offering.series_session_count == 3


def describe_series_grouping():
    def it_does_not_group_series_offerings(db):
        offering = SeriesClassOfferingFactory(title="Blacksmithing 101", session_count=3)
        offering.refresh_from_db()
        assert offering.grouping_key == ""

    def it_keeps_grouping_key_for_single_offerings(db):
        offering = ClassOfferingFactory(title="Blacksmithing 101")
        offering.refresh_from_db()
        assert offering.grouping_key != ""

    def it_does_not_sweep_siblings_when_a_series_changes_category(db):
        from classes.factories import CategoryFactory

        single = ClassOfferingFactory(title="Forge Night", slug="forge-night-single")
        single.refresh_from_db()
        original_key = single.grouping_key
        series = SeriesClassOfferingFactory(title="Forge Night", slug="forge-night-series", session_count=2)
        new_category = CategoryFactory()
        series.category = new_category
        series.save()
        single.refresh_from_db()
        assert single.grouping_key == original_key

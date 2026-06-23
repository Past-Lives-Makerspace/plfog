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
    def it_groups_series_offerings_so_runs_collapse_into_one_card(db):
        offering = SeriesClassOfferingFactory(title="Blacksmithing 101", session_count=3)
        offering.refresh_from_db()
        assert offering.grouping_key == f"blacksmithing-101:{offering.category_id}"

    def it_keeps_grouping_key_for_single_offerings(db):
        offering = ClassOfferingFactory(title="Blacksmithing 101")
        offering.refresh_from_db()
        assert offering.grouping_key != ""

    def it_shares_a_grouping_key_across_runs_of_the_same_series(db):
        from classes.factories import CategoryFactory

        cat = CategoryFactory()
        june = SeriesClassOfferingFactory(title="Blacksmithing 101", slug="bs-june", category=cat, session_count=3)
        july = SeriesClassOfferingFactory(title="Blacksmithing 101", slug="bs-july", category=cat, session_count=3)
        june.refresh_from_db()
        july.refresh_from_db()
        assert june.grouping_key == july.grouping_key != ""

    def it_sweeps_sibling_runs_when_a_grouped_series_changes_category(db):
        from classes.factories import CategoryFactory

        cat = CategoryFactory()
        run_a = SeriesClassOfferingFactory(title="Forge Night", slug="forge-a", category=cat, session_count=2)
        SeriesClassOfferingFactory(title="Forge Night", slug="forge-b", category=cat, session_count=2)
        new_category = CategoryFactory()
        run_a.category = new_category
        run_a.save()
        run_b = ClassOffering.objects.get(slug="forge-b")
        # The group stays coherent: re-homing one run re-homes its siblings too.
        assert run_b.category_id == new_category.pk
        assert run_b.grouping_key == run_a.grouping_key

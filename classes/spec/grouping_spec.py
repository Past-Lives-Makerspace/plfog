"""BDD specs for class catalog grouping — keys, regrouping, batched seat counts."""

from __future__ import annotations

from classes.factories import (
    CategoryFactory,
    ClassOfferingFactory,
    RegistrationFactory,
)
from classes.grouping import grouping_key_for, regroup_offerings
from classes.models import ClassOffering, Registration


def describe_grouping_key_for():
    def it_strips_date_suffixes_so_dated_copies_share_a_key():
        with_date = grouping_key_for("Blacksmithing 101 with Glen - 6/5/26", 3)
        without = grouping_key_for("Blacksmithing 101 with Glen", 3)
        assert with_date == without == "blacksmithing-101-with-glen:3"

    def it_separates_same_title_in_different_categories():
        assert grouping_key_for("Open Studio", 1) != grouping_key_for("Open Studio", 2)

    def it_returns_empty_for_a_blank_title():
        assert grouping_key_for("", 3) == ""
        assert grouping_key_for(None, 3) == ""

    def it_omits_category_suffix_when_category_is_missing():
        assert grouping_key_for("Solo Class", None) == "solo-class"


def describe_class_offering_save():
    def it_sets_grouping_key_from_title_and_category(db):
        offering = ClassOfferingFactory(title="Welding Basics with Sam")
        assert offering.grouping_key == f"welding-basics-with-sam:{offering.category_id}"

    def it_recomputes_grouping_key_when_saving_a_title_change(db):
        offering = ClassOfferingFactory(title="Old Title")
        offering.title = "New Title"
        offering.save(update_fields=["title"])
        offering.refresh_from_db()
        assert offering.grouping_key == f"new-title:{offering.category_id}"

    def context_when_an_unrelated_field_is_saved():
        def it_leaves_the_stored_grouping_key_untouched(db):
            offering = ClassOfferingFactory(title="Stable Title")
            original_key = offering.grouping_key
            offering.capacity = 12
            offering.save(update_fields=["capacity"])
            offering.refresh_from_db()
            assert offering.grouping_key == original_key


def describe_regroup_offerings():
    def it_collapses_same_title_offerings_into_one_group(db):
        category = CategoryFactory()
        for slug in ("a", "b", "c"):
            ClassOfferingFactory(title="Forging 101 with Glen", slug=f"forging-{slug}", category=category)
        ClassOfferingFactory(title="Totally Different", slug="different", category=category)

        examined, groups = regroup_offerings()

        assert examined == 4
        assert groups == 2

    def it_repairs_a_stale_key_without_touching_others(db):
        offering = ClassOfferingFactory(title="Repair Me")
        ClassOffering.objects.filter(pk=offering.pk).update(grouping_key="stale")

        regroup_offerings()

        offering.refresh_from_db()
        assert offering.grouping_key == f"repair-me:{offering.category_id}"


def describe_spots_remaining_map():
    def it_returns_seats_left_per_offering_in_the_queryset(db):
        offering = ClassOfferingFactory(capacity=6)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        RegistrationFactory(class_offering=offering, status=Registration.Status.PENDING)
        # Cancelled registrations must not consume a seat.
        RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)

        result = ClassOffering.objects.filter(pk=offering.pk).spots_remaining_map()

        assert result == {offering.pk: 4}

    def it_matches_the_single_object_property(db):
        offering = ClassOfferingFactory(capacity=5)
        RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)

        batched = ClassOffering.objects.filter(pk=offering.pk).spots_remaining_map()

        assert batched[offering.pk] == offering.spots_remaining

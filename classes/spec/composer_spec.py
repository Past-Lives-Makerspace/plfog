"""BDD specs for the composer step map: the guard that no form field can silently vanish from the UI."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.composer import (
    COMPOSER_STEPS,
    FORMSET_STEPS,
    NON_FIELD_STEP,
    STEP_COUNT,
    anchor_steps,
    clamp_step,
    error_steps,
    error_summary,
    step_for_field,
    step_marks,
)
from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import ClassOfferingForm, ClassSessionFormSet, TeachClassOfferingForm, build_class_faq_formset
from classes.models import ClassOffering, readiness_items

pytestmark = pytest.mark.django_db


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def form_class(request):
    return request.param


def _teach_data(**overrides) -> dict:
    data = {
        "title": "Forge Basics",
        "category": str(CategoryFactory().pk),
        "description": "Hands-on intro.",
        "price_cents": "100.00",
        "member_discount_pct": "10",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
    }
    data.update(overrides)
    return data


def _sessions(**overrides) -> dict:
    data = {
        "sessions-TOTAL_FORMS": "0",
        "sessions-INITIAL_FORMS": "0",
        "sessions-MIN_NUM_FORMS": "0",
        "sessions-MAX_NUM_FORMS": "1000",
    }
    data.update(overrides)
    return data


def describe_composer_steps():
    def it_has_five_steps_numbered_in_order():
        assert STEP_COUNT == 5
        assert [step.number for step in COMPOSER_STEPS] == [1, 2, 3, 4, 5]
        assert [step.key for step in COMPOSER_STEPS] == ["basics", "photos", "dates", "details", "review"]

    def it_places_every_form_field_on_exactly_one_step(form_class):
        # Meta.fields plus the injected is_free, hero_crop, and card_focus: every one, once.
        for name in form_class().fields:
            owners = [step.number for step in COMPOSER_STEPS if name in step.fields]
            assert len(owners) == 1, f"{name} appears on steps {owners}"

    def it_names_no_field_the_forms_do_not_have():
        # The admin form is the superset (instructor, is_private, private_for_name are admin only).
        known = set(ClassOfferingForm().fields)
        for step in COMPOSER_STEPS:
            unknown = [name for name in step.fields if name not in known]
            assert unknown == [], f"step {step.number} names {unknown}"

    def it_keeps_the_admin_only_fields_off_the_teach_form():
        teach = set(TeachClassOfferingForm().fields)
        assert {"instructor", "is_private", "private_for_name"}.isdisjoint(teach)

    def it_puts_the_sale_fields_on_no_step():
        on_steps = {name for step in COMPOSER_STEPS for name in step.fields}
        assert not {name for name in on_steps if name.startswith("sale_")}

    def it_has_no_fields_on_the_review_step():
        assert COMPOSER_STEPS[-1].fields == ()

    def it_maps_every_formset_to_a_step():
        assert FORMSET_STEPS == {"gallery": 2, "sessions": 3, "faq": 4}


def describe_step_for_field():
    def it_finds_the_step():
        assert step_for_field("title") == 1
        assert step_for_field("card_focus") == 2
        assert step_for_field("price_cents") == 1
        assert step_for_field("is_free") == 1
        assert step_for_field("member_discount_pct") == 3
        assert step_for_field("age_minimum") == 4

    def it_raises_on_an_unknown_field():
        with pytest.raises(KeyError):
            step_for_field("sale_percent")


def describe_clamp_step():
    def it_reads_a_step_number():
        assert clamp_step("3") == 3

    def it_defaults_to_the_first_step():
        assert clamp_step(None) == 1
        assert clamp_step("") == 1
        assert clamp_step("abc") == 1

    def it_clamps_to_the_map():
        assert clamp_step("0") == 1
        assert clamp_step("-2") == 1
        assert clamp_step("9") == 5


def describe_anchor_steps():
    def it_maps_field_ids_and_section_anchors():
        mapping = anchor_steps()
        assert mapping["id_description"] == 1
        assert mapping["hero-preview"] == 2
        assert mapping["gallery-manager"] == 2
        assert mapping["class-dates"] == 3
        assert mapping["id_capacity"] == 3

    def it_resolves_every_readiness_anchor():
        items = readiness_items(
            has_hero=False,
            has_gallery=False,
            description="",
            scheduling_model="fixed",
            flexible_note="",
            has_future_session=False,
            capacity=0,
        )
        mapping = anchor_steps()
        for item in items:
            assert item.anchor in mapping, item.anchor


def describe_error_steps():
    def it_is_empty_for_an_unbound_form():
        form = TeachClassOfferingForm()
        assert error_steps(form, {"sessions": None, "faq": None}) == []
        assert error_summary(form, {"sessions": None, "faq": None}) == []

    def it_is_empty_when_everything_is_valid():
        form = TeachClassOfferingForm(data=_teach_data())
        formset = ClassSessionFormSet(_sessions(), prefix="sessions")
        assert error_steps(form, {"sessions": formset, "faq": None}) == []

    def it_lands_a_title_error_on_step_one():
        form = TeachClassOfferingForm(data=_teach_data(title=""))
        assert error_steps(form, {"sessions": None, "faq": None}) == [1]
        [(step, labels)] = error_summary(form, {"sessions": None, "faq": None})
        assert step.number == 1
        assert labels == ["Title"]

    def it_lands_a_price_error_on_step_one():
        # The price is the one field a draft cannot be saved without, so it lives on the first step.
        form = TeachClassOfferingForm(data=_teach_data(price_cents=""))
        assert error_steps(form, {"sessions": None, "faq": None}) == [1]
        [(_step, labels)] = error_summary(form, {"sessions": None, "faq": None})
        assert labels == ["Price"]

    def it_lands_a_capacity_error_on_step_three():
        form = TeachClassOfferingForm(data=_teach_data(capacity=""))
        assert error_steps(form, {"sessions": None, "faq": None}) == [3]
        [(_step, labels)] = error_summary(form, {"sessions": None, "faq": None})
        assert labels == ["Capacity"]

    def it_lands_a_card_focus_error_on_step_two():
        form = TeachClassOfferingForm(data=_teach_data(card_focus="{broken"))
        assert error_steps(form, {"sessions": None, "faq": None}) == [2]

    def it_lands_non_field_errors_on_the_first_step():
        form = TeachClassOfferingForm(data=_teach_data())
        assert form.is_valid(), form.errors
        form.add_error(None, "Too many gallery images.")
        assert error_steps(form, {"sessions": None, "faq": None}) == [NON_FIELD_STEP]
        [(step, labels)] = error_summary(form, {"sessions": None, "faq": None})
        assert step.number == 1
        assert labels == []

    def it_lands_a_broken_session_on_step_three():
        form = TeachClassOfferingForm(data=_teach_data())
        start = timezone.now() + timedelta(days=3)
        data = _sessions(
            **{
                "sessions-TOTAL_FORMS": "1",
                "sessions-0-starts_at": start.strftime("%Y-%m-%dT%H:%M"),
                "sessions-0-ends_at": (start - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            }
        )
        formset = ClassSessionFormSet(data, prefix="sessions")
        assert error_steps(form, {"sessions": formset, "faq": None}) == [3]
        [(_step, labels)] = error_summary(form, {"sessions": formset, "faq": None})
        assert labels == ["Dates"]

    def it_lands_a_broken_faq_row_on_step_four():
        offering = ClassOfferingFactory()
        form = TeachClassOfferingForm(data=_teach_data(), instance=offering)
        faq = build_class_faq_formset(
            {
                "faq-TOTAL_FORMS": "1",
                "faq-INITIAL_FORMS": "0",
                "faq-MIN_NUM_FORMS": "0",
                "faq-MAX_NUM_FORMS": "1000",
                "faq-0-question": "",
                "faq-0-answer": "An answer with no question.",
            },
            offering,
        )
        assert error_steps(form, {"sessions": None, "faq": faq}) == [4]
        [(_step, labels)] = error_summary(form, {"sessions": None, "faq": faq})
        assert labels == ["FAQ"]

    def it_sorts_and_dedupes_across_steps():
        form = TeachClassOfferingForm(data=_teach_data(title="", price_cents="", capacity=""))
        assert error_steps(form, {"sessions": None, "faq": None}) == [1, 3]
        summary = error_summary(form, {"sessions": None, "faq": None})
        assert [step.number for step, _labels in summary] == [1, 3]
        assert sorted(summary[0][1]) == ["Price", "Title"]
        assert summary[1][1] == ["Capacity"]

    def it_lands_the_admin_instructor_field_on_step_one():
        # instructor is optional on the model, so a blank passes; an unknown pk does not.
        form = ClassOfferingForm(data=_teach_data(instructor="999999", is_private="", private_for_name="", title=""))
        assert error_steps(form, {"sessions": None, "faq": None}) == [1]
        [(_step, labels)] = error_summary(form, {"sessions": None, "faq": None})
        assert sorted(labels) == ["Instructor", "Title"]


def describe_step_marks():
    def _items(**overrides):
        kwargs = {
            "has_hero": True,
            "has_gallery": True,
            "description": "A description long enough to count as a real one for readiness.",
            "scheduling_model": "fixed",
            "flexible_note": "",
            "has_future_session": True,
            "capacity": 6,
        }
        kwargs.update(overrides)
        return readiness_items(**kwargs)

    def it_marks_the_three_steps_that_own_readiness_items_when_all_are_ok():
        assert step_marks(_items()) == {1: True, 2: True, 3: True}

    def it_never_marks_the_details_or_review_steps():
        marks = step_marks(_items())
        assert 4 not in marks
        assert 5 not in marks

    def it_unmarks_photos_when_the_gallery_is_missing():
        assert step_marks(_items(has_gallery=False))[2] is False

    def it_unmarks_basics_when_the_description_is_short():
        marks = step_marks(_items(description="Short"))
        assert marks[1] is False
        assert marks[2] is True

    def it_unmarks_dates_when_capacity_is_zero():
        assert step_marks(_items(capacity=0))[3] is False

    def it_reads_a_saved_class(db):
        offering = ClassOfferingFactory(ready=True, instructor=InstructorFactory())
        assert step_marks(offering.readiness()) == {1: True, 2: True, 3: True}

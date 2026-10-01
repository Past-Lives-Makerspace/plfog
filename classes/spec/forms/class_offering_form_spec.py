"""BDD specs for the composer forms (ClassOfferingForm, TeachClassOfferingForm).

The price floor (#368 item 5): there is no free option. Both forms require a price and
refuse any price under $1.00 with one plain message. The hero crop mixin, the session form,
and the slug collision handling share the POST helpers below.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from django import forms
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import FLEXIBLE_WINDOW_ORDER_MESSAGE, ClassOfferingForm, ClassSessionForm, TeachClassOfferingForm
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db

FLOOR = "Classes cost at least $1.00."
REQUIRED = "This field is required."


def _admin_post_data(**overrides) -> dict:
    data = {
        "title": "Forge Basics",
        "slug": "forge-basics",
        "category": str(CategoryFactory().pk),
        "instructor": str(InstructorFactory().pk),
        "description": "Hands-on intro.",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_minimum": "",
        "age_guardian_note": "",
        "price_cents": "100.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "sale_kind": "percent",
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "flexible_note": "",
        "is_private": "",
        "private_for_name": "",
        "recurring_pattern": "",
        "image": "",
        "requires_model_release": "",
    }
    data.update(overrides)
    return data


def _instructor_post_data(**overrides) -> dict:
    data = {
        "title": "Studio Demo",
        "category": str(CategoryFactory().pk),
        "description": "A walkthrough.",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_minimum": "",
        "age_guardian_note": "",
        "price_cents": "20.00",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "sale_kind": "percent",
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "flexible_note": "",
        "recurring_pattern": "",
        "image": "",
        "requires_model_release": "",
    }
    data.update(overrides)
    return data


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def form_class(request):
    return request.param


def _form(form_class, instance: ClassOffering | None = None, **overrides):
    """A bound form of either class, valid unless an override says otherwise."""
    if form_class is ClassOfferingForm:
        return ClassOfferingForm(data=_admin_post_data(**overrides), instance=instance)
    return TeachClassOfferingForm(
        data=_instructor_post_data(**overrides), teaching_member=InstructorFactory(), instance=instance
    )


def describe_the_price_floor():
    def it_has_no_free_checkbox(form_class):
        assert "is_free" not in form_class().fields

    def it_requires_a_price(form_class):
        form = _form(form_class, price_cents="")
        assert not form.is_valid()
        assert form.errors["price_cents"] == [REQUIRED]

    def it_refuses_zero(form_class):
        form = _form(form_class, price_cents="0")
        assert not form.is_valid()
        assert form.errors["price_cents"] == [FLOOR]

    def it_refuses_ninety_nine_cents(form_class):
        form = _form(form_class, price_cents="0.99")
        assert not form.is_valid()
        assert form.errors["price_cents"] == [FLOOR]

    def it_accepts_exactly_one_dollar(form_class):
        form = _form(form_class, price_cents="1.00")
        assert form.is_valid(), form.errors
        assert form.save().price_cents == 100

    def it_keeps_the_price_as_typed(form_class):
        form = _form(form_class, price_cents="25.00")
        assert form.is_valid(), form.errors
        assert form.save().price_cents == 2500

    def describe_on_an_existing_class():
        def it_refuses_an_edit_under_the_floor(form_class):
            offering = ClassOfferingFactory(price_cents=5000)
            form = _form(form_class, instance=offering, price_cents="0.50")
            assert not form.is_valid()
            assert form.errors["price_cents"] == [FLOOR]
            offering.refresh_from_db()
            assert offering.price_cents == 5000

        def it_leaves_the_instance_price_alone_when_the_price_is_blank(form_class):
            # A blank used to reach construct_instance and null the in-memory row (the 500 behind
            # #368 item 3a). A required field never reaches cleaned_data, so the row is untouched.
            offering = ClassOfferingFactory(price_cents=5000)
            form = _form(form_class, instance=offering, price_cents="")
            assert not form.is_valid()
            assert offering.price_cents == 5000

        def it_moves_a_legacy_zero_priced_class_up_to_the_floor_or_not_at_all(form_class):
            # Existing $0 rows stay as they are until someone edits them; the edit then needs a price.
            offering = ClassOfferingFactory(price_cents=0)
            refused = _form(form_class, instance=offering, price_cents="0")
            assert not refused.is_valid()
            assert refused.errors["price_cents"] == [FLOOR]
            accepted = _form(form_class, instance=offering, price_cents="1.00")
            assert accepted.is_valid(), accepted.errors
            assert accepted.save().price_cents == 100


def describe_the_flexible_window():
    """A flexible class takes an optional first and last day instead of session times (#545)."""

    def it_stores_a_window_in_order(form_class):
        form = _form(
            form_class, scheduling_model="flexible", flexible_starts_on="2026-11-02", flexible_ends_on="2026-12-01"
        )
        assert form.is_valid(), form.errors
        offering = form.save()
        assert (offering.flexible_starts_on, offering.flexible_ends_on) == (date(2026, 11, 2), date(2026, 12, 1))

    def it_accepts_either_day_alone_and_neither(form_class):
        for starts_on, ends_on in (("2026-11-02", ""), ("", "2026-12-01"), ("", "")):
            form = _form(
                form_class, scheduling_model="flexible", flexible_starts_on=starts_on, flexible_ends_on=ends_on
            )
            assert form.is_valid(), form.errors

    def it_accepts_a_one_day_window(form_class):
        form = _form(
            form_class, scheduling_model="flexible", flexible_starts_on="2026-11-02", flexible_ends_on="2026-11-02"
        )
        assert form.is_valid(), form.errors

    def it_refuses_a_last_day_before_the_first_day_on_the_last_day_field(form_class):
        form = _form(
            form_class, scheduling_model="flexible", flexible_starts_on="2026-12-01", flexible_ends_on="2026-11-02"
        )
        assert not form.is_valid()
        assert form.errors["flexible_ends_on"] == [FLEXIBLE_WINDOW_ORDER_MESSAGE]
        assert "flexible_starts_on" not in form.errors

    def it_clears_a_posted_window_on_a_fixed_class(form_class):
        # The window block is hidden, not removed, under Fixed sessions, so its inputs still post.
        offering = ClassOfferingFactory(
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE,
            flexible_starts_on=date(2026, 11, 2),
            flexible_ends_on=date(2026, 12, 1),
        )
        form = _form(
            form_class,
            instance=offering,
            scheduling_model="fixed",
            flexible_starts_on="2026-11-02",
            flexible_ends_on="2026-12-01",
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["flexible_starts_on"] is None
        assert form.cleaned_data["flexible_ends_on"] is None
        saved = form.save()
        saved.refresh_from_db()
        assert (saved.flexible_starts_on, saved.flexible_ends_on) == (None, None)

    def it_never_refuses_a_reversed_window_on_a_fixed_class(form_class):
        form = _form(
            form_class, scheduling_model="fixed", flexible_starts_on="2026-12-01", flexible_ends_on="2026-11-02"
        )
        assert form.is_valid(), form.errors

    def it_labels_the_days_and_the_note_for_students(form_class):
        form = form_class()
        assert form.fields["flexible_starts_on"].label == "First day"
        assert form.fields["flexible_ends_on"].label == "Last day"
        assert form.fields["flexible_note"].label == "Note for students"
        assert form.fields["flexible_note"].required is False
        assert form.fields["flexible_note"].help_text.startswith("Optional. Hours you teach")
        assert "scheduling_model" not in form.fields["flexible_note"].help_text
        assert form.fields["flexible_starts_on"].required is False
        assert form.fields["flexible_ends_on"].required is False

    def it_renders_the_days_as_date_pickers_in_the_schedulers_clothes(form_class):
        # type=date, never a time (FRONTEND.md rule 20); the scheduler's input class carries the
        # rule 14 dark mode picker fix and the whole field opens the picker; no hint under either
        # day, because the one hint sits under the pair on step 3.
        form = form_class()
        for name in ("flexible_starts_on", "flexible_ends_on"):
            widget = form.fields[name].widget
            assert isinstance(widget, forms.DateInput), name
            assert widget.input_type == "date"
            assert widget.attrs["class"] == "session-cal__input"
            assert widget.attrs["@click"] == "(() => { try { $el.showPicker() } catch (e) {} })()"
            assert widget.format == "%Y-%m-%d"
            assert form.fields[name].help_text == ""

    def it_renders_a_saved_day_in_the_format_a_date_input_reads(form_class):
        offering = ClassOfferingFactory(
            scheduling_model=ClassOffering.SchedulingModel.FLEXIBLE, flexible_starts_on=date(2026, 11, 2)
        )
        html = str(form_class(instance=offering)["flexible_starts_on"])
        assert 'value="2026-11-02"' in html
        assert 'type="date"' in html and 'class="session-cal__input"' in html

    def it_binds_the_scheduling_model_select_to_the_composers_alpine(form_class):
        # The composer's step 3 swaps the scheduler for the window on this state (class_composer.html).
        assert form_class().fields["scheduling_model"].widget.attrs["x-model"] == "schedulingModel"


def describe_HeroCropMixin():
    def describe_add_hero_crop_field():
        def it_pre_populates_from_existing_crop_data():
            offering = ClassOfferingFactory(
                price_cents=5000,
                hero_crop_x=10,
                hero_crop_y=20,
                hero_crop_w=300,
                hero_crop_h=200,
            )
            form = ClassOfferingForm(instance=offering)
            initial = form.fields["hero_crop"].initial
            parsed = json.loads(initial)
            assert parsed == {"x": 10, "y": 20, "w": 300, "h": 200}

        def it_leaves_initial_empty_on_a_class_whose_only_photo_is_imported():
            # The composer withholds the crop box on an imported photo; pre-filling the
            # saved box would let a Save write it back over a focal point set with Adjust.
            offering = ClassOfferingFactory(
                price_cents=5000,
                image="",
                legacy_image_url="https://classes.pastlives.space/sites/default/files/glen.jpg",
                hero_crop_x=10,
                hero_crop_y=20,
                hero_crop_w=300,
                hero_crop_h=200,
            )
            form = ClassOfferingForm(instance=offering)
            assert form.fields["hero_crop"].initial == ""

        def it_leaves_initial_empty_when_crop_dimensions_are_zero():
            offering = ClassOfferingFactory(price_cents=5000)
            # hero_crop_w and hero_crop_h default to 0 — no saved crop
            form = ClassOfferingForm(instance=offering)
            assert form.fields["hero_crop"].initial == ""

    def describe_clean_hero_crop():
        def it_rejects_malformed_json():
            form = ClassOfferingForm(data=_admin_post_data(hero_crop="not-json"))
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_rejects_crop_with_missing_keys():
            form = ClassOfferingForm(data=_admin_post_data(hero_crop=json.dumps({"x": 0, "y": 0})))
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_rejects_crop_with_zero_width():
            crop = json.dumps({"x": 0, "y": 0, "w": 0, "h": 100})
            form = ClassOfferingForm(data=_admin_post_data(hero_crop=crop))
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_rejects_crop_with_negative_x():
            crop = json.dumps({"x": -1, "y": 0, "w": 100, "h": 100})
            form = ClassOfferingForm(data=_admin_post_data(hero_crop=crop))
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_accepts_valid_crop_and_returns_int_dict():
            crop = json.dumps({"x": 5, "y": 10, "w": 200, "h": 150})
            form = ClassOfferingForm(data=_admin_post_data(hero_crop=crop))
            assert form.is_valid(), form.errors
            assert form.cleaned_data["hero_crop"] == {"x": 5, "y": 10, "w": 200, "h": 150}

        def it_returns_none_when_hero_crop_is_blank():
            form = ClassOfferingForm(data=_admin_post_data(hero_crop=""))
            assert form.is_valid(), form.errors
            assert form.cleaned_data["hero_crop"] is None

    def describe_apply_hero_crop_to_instance():
        def it_writes_crop_coords_to_offering():
            crop = json.dumps({"x": 7, "y": 3, "w": 400, "h": 250})
            form = ClassOfferingForm(
                data=_admin_post_data(price_cents="25.00", hero_crop=crop),
            )
            assert form.is_valid(), form.errors
            offering = form.save()
            assert offering.hero_crop_x == 7
            assert offering.hero_crop_y == 3
            assert offering.hero_crop_w == 400
            assert offering.hero_crop_h == 250


def describe_ClassSessionForm():
    def it_rejects_session_where_end_is_not_after_start():
        now = timezone.now()
        form = ClassSessionForm(
            data={
                "starts_at": now.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            }
        )
        assert not form.is_valid()
        assert "__all__" in form.errors or any("end time" in str(e).lower() for e in form.errors.values())

    def it_accepts_session_where_end_is_after_start():
        now = timezone.now()
        form = ClassSessionForm(
            data={
                "starts_at": now.strftime("%Y-%m-%dT%H:%M"),
                "ends_at": (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            }
        )
        assert form.is_valid(), form.errors


def describe_TeachClassOfferingForm_slug_collision():
    def it_generates_a_unique_slug_when_title_collides():
        instructor = InstructorFactory()
        # _instructor_post_data uses title="Studio Demo" → base slug "studio-demo".
        # Pre-occupy that slug so the form must increment to "studio-demo-2".
        ClassOfferingFactory(title="Studio Demo", slug="studio-demo")
        form = TeachClassOfferingForm(data=_instructor_post_data(), teaching_member=instructor)
        assert form.is_valid(), form.errors
        offering = form.save()
        assert offering.slug != "studio-demo"
        assert offering.slug.startswith("studio-demo")

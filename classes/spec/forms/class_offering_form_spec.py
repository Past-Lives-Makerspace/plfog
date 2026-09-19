"""BDD specs for the composer forms (ClassOfferingForm, TeachClassOfferingForm).

The price floor (#368 item 5): there is no free option. Both forms require a price and
refuse any price under $1.00 with one plain message. The member discount (#369 item 1) is
the admin's: only ClassOfferingForm carries it, and a new class starts at the studio
default. The hero crop mixin, the session form, and the slug collision handling share the
POST helpers below.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import ClassOfferingForm, ClassSessionForm, TeachClassOfferingForm
from classes.models import ClassOffering, ClassSettings

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
        "member_discount_pct": "10",
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
        "member_discount_pct": "10",
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
            offering = ClassOfferingFactory(price_cents=0, member_discount_pct=0)
            refused = _form(form_class, instance=offering, price_cents="0")
            assert not refused.is_valid()
            assert refused.errors["price_cents"] == [FLOOR]
            accepted = _form(form_class, instance=offering, price_cents="1.00")
            assert accepted.is_valid(), accepted.errors
            assert accepted.save().price_cents == 100


def _studio_default(pct: int) -> ClassSettings:
    settings_obj = ClassSettings.load()
    settings_obj.default_member_discount_pct = pct
    settings_obj.save(update_fields=["default_member_discount_pct"])
    return settings_obj


def describe_the_member_discount():
    def describe_on_the_admin_form():
        def it_is_required():
            form = _form(ClassOfferingForm, member_discount_pct="")
            assert not form.is_valid()
            assert form.errors["member_discount_pct"] == [REQUIRED]

        def it_starts_at_the_studio_default_on_a_new_class():
            _studio_default(15)
            assert ClassOfferingForm()["member_discount_pct"].value() == 15

        def it_shows_the_saved_value_on_an_existing_class():
            _studio_default(15)
            offering = ClassOfferingFactory(member_discount_pct=25)
            assert ClassOfferingForm(instance=offering)["member_discount_pct"].value() == 25

        def it_keeps_the_discount_as_typed():
            form = _form(ClassOfferingForm, member_discount_pct="15")
            assert form.is_valid(), form.errors
            assert form.save().member_discount_pct == 15

        def it_accepts_zero():
            form = _form(ClassOfferingForm, member_discount_pct="0")
            assert form.is_valid(), form.errors
            assert form.save().member_discount_pct == 0

        def it_accepts_one_hundred():
            form = _form(ClassOfferingForm, member_discount_pct="100")
            assert form.is_valid(), form.errors
            assert form.save().member_discount_pct == 100

        def it_refuses_more_than_one_hundred():
            # A percentage: the model field only bounds it below, so the form caps it above.
            form = _form(ClassOfferingForm, member_discount_pct="101")
            assert not form.is_valid()
            assert form.errors["member_discount_pct"] == ["Member discount must be between 0 and 100."]

        def it_refuses_a_negative_number():
            form = _form(ClassOfferingForm, member_discount_pct="-1")
            assert not form.is_valid()
            assert "member_discount_pct" in form.errors

    def describe_on_the_instructor_form():
        # The instructor payload still carries member_discount_pct: that is the crafted POST,
        # and the form has no such field to bind it to.
        def it_has_no_field():
            assert "member_discount_pct" not in TeachClassOfferingForm().fields

        def it_starts_a_new_class_at_the_studio_default_whatever_was_posted():
            _studio_default(15)
            form = _form(TeachClassOfferingForm, member_discount_pct="0")
            assert form.is_valid(), form.errors
            assert form.save().member_discount_pct == 15

        def it_leaves_an_existing_class_alone_whatever_was_posted():
            offering = ClassOfferingFactory(member_discount_pct=25)
            form = _form(TeachClassOfferingForm, instance=offering, member_discount_pct="0")
            assert form.is_valid(), form.errors
            assert form.save().member_discount_pct == 25


def describe_HeroCropMixin():
    def describe_add_hero_crop_field():
        def it_pre_populates_from_existing_crop_data():
            offering = ClassOfferingFactory(
                price_cents=5000,
                member_discount_pct=10,
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
                member_discount_pct=10,
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
            offering = ClassOfferingFactory(price_cents=5000, member_discount_pct=10)
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
                data=_admin_post_data(price_cents="25.00", member_discount_pct="10", hero_crop=crop),
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

"""BDD specs for the `is_free` checkbox on ClassOffering forms."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory, UserFactory
from classes.forms import ClassOfferingForm, ClassSessionForm, InstructorClassOfferingForm, PromoteUserToInstructorForm
from classes.models import ClassOffering, Instructor

pytestmark = pytest.mark.django_db


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
        "flexible_note": "",
        "is_private": "",
        "private_for_name": "",
        "recurring_pattern": "",
        "image": "",
        "requires_model_release": "",
    }
    data.update(overrides)
    return data


def describe_ClassOfferingForm():
    def describe_is_free_checkbox():
        def it_zeroes_price_and_discount_when_checked():
            form = ClassOfferingForm(
                data=_admin_post_data(is_free="on", price_cents="", member_discount_pct=""),
            )
            assert form.is_valid(), form.errors
            offering = form.save()
            assert offering.price_cents == 0
            assert offering.member_discount_pct == 0

        def it_keeps_paid_pricing_when_unchecked():
            form = ClassOfferingForm(data=_admin_post_data(price_cents="25.00", member_discount_pct="15"))
            assert form.is_valid(), form.errors
            offering = form.save()
            assert offering.price_cents == 2500
            assert offering.member_discount_pct == 15

        def it_requires_a_price_when_not_free():
            form = ClassOfferingForm(data=_admin_post_data(price_cents="", member_discount_pct=""))
            assert not form.is_valid()
            assert "price_cents" in form.errors

        def describe_minimum_paid_price():
            def it_rejects_paid_price_below_one_dollar():
                form = ClassOfferingForm(data=_admin_post_data(price_cents="0.99"))
                assert not form.is_valid()
                assert "price_cents" in form.errors
                assert any("$1.00" in e for e in form.errors["price_cents"])

            def it_accepts_paid_price_at_exactly_one_dollar():
                form = ClassOfferingForm(data=_admin_post_data(price_cents="1.00"))
                assert form.is_valid(), form.errors

            def it_still_allows_zero_when_marked_free():
                form = ClassOfferingForm(
                    data=_admin_post_data(is_free="on", price_cents="", member_discount_pct=""),
                )
                assert form.is_valid(), form.errors
                assert form.save().price_cents == 0

        def it_pre_checks_for_existing_free_class():
            offering = ClassOfferingFactory(price_cents=0, member_discount_pct=0)
            form = ClassOfferingForm(instance=offering)
            assert form.fields["is_free"].initial is True

        def it_pre_unchecks_for_existing_paid_class():
            offering = ClassOfferingFactory(price_cents=4500)
            form = ClassOfferingForm(instance=offering)
            assert form.fields["is_free"].initial is False


def _instructor_post_data(**overrides) -> dict:
    data = {
        "title": "Free Demo",
        "category": str(CategoryFactory().pk),
        "description": "A walkthrough.",
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_minimum": "",
        "age_guardian_note": "",
        "price_cents": "",
        "member_discount_pct": "",
        "capacity": "6",
        "scheduling_model": ClassOffering.SchedulingModel.FIXED,
        "flexible_note": "",
        "recurring_pattern": "",
        "image": "",
        "requires_model_release": "",
    }
    data.update(overrides)
    return data


def describe_InstructorClassOfferingForm():
    def describe_is_free_checkbox():
        def it_zeroes_pricing_when_checked():
            instructor = InstructorFactory()
            form = InstructorClassOfferingForm(
                data=_instructor_post_data(is_free="on"),
                instructor=instructor,
            )
            assert form.is_valid(), form.errors
            offering = form.save()
            assert offering.price_cents == 0
            assert offering.member_discount_pct == 0

        def it_requires_price_for_paid_class():
            instructor = InstructorFactory()
            form = InstructorClassOfferingForm(
                data=_instructor_post_data(),
                instructor=instructor,
            )
            assert not form.is_valid()
            assert "price_cents" in form.errors

        def it_rejects_paid_price_below_one_dollar():
            instructor = InstructorFactory()
            form = InstructorClassOfferingForm(
                data=_instructor_post_data(price_cents="0.50"),
                instructor=instructor,
            )
            assert not form.is_valid()
            assert "price_cents" in form.errors
            assert any("$1.00" in e for e in form.errors["price_cents"])


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

        def it_leaves_initial_empty_when_crop_dimensions_are_zero():
            offering = ClassOfferingFactory(price_cents=5000, member_discount_pct=10)
            # hero_crop_w and hero_crop_h default to 0 — no saved crop
            form = ClassOfferingForm(instance=offering)
            assert form.fields["hero_crop"].initial == ""

    def describe_clean_hero_crop():
        def it_rejects_malformed_json():
            form = ClassOfferingForm(
                data=_admin_post_data(is_free="on", price_cents="", member_discount_pct="", hero_crop="not-json"),
            )
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_rejects_crop_with_missing_keys():
            form = ClassOfferingForm(
                data=_admin_post_data(
                    is_free="on", price_cents="", member_discount_pct="", hero_crop=json.dumps({"x": 0, "y": 0})
                ),
            )
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_rejects_crop_with_zero_width():
            crop = json.dumps({"x": 0, "y": 0, "w": 0, "h": 100})
            form = ClassOfferingForm(
                data=_admin_post_data(is_free="on", price_cents="", member_discount_pct="", hero_crop=crop),
            )
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_rejects_crop_with_negative_x():
            crop = json.dumps({"x": -1, "y": 0, "w": 100, "h": 100})
            form = ClassOfferingForm(
                data=_admin_post_data(is_free="on", price_cents="", member_discount_pct="", hero_crop=crop),
            )
            assert not form.is_valid()
            assert "hero_crop" in form.errors

        def it_accepts_valid_crop_and_returns_int_dict():
            crop = json.dumps({"x": 5, "y": 10, "w": 200, "h": 150})
            form = ClassOfferingForm(
                data=_admin_post_data(is_free="on", price_cents="", member_discount_pct="", hero_crop=crop),
            )
            assert form.is_valid(), form.errors
            assert form.cleaned_data["hero_crop"] == {"x": 5, "y": 10, "w": 200, "h": 150}

        def it_returns_none_when_hero_crop_is_blank():
            form = ClassOfferingForm(
                data=_admin_post_data(is_free="on", price_cents="", member_discount_pct="", hero_crop=""),
            )
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


def describe_InstructorClassOfferingForm_slug_collision():
    def it_generates_a_unique_slug_when_title_collides():
        instructor = InstructorFactory()
        # _instructor_post_data uses title="Free Demo" → base slug "free-demo".
        # Pre-occupy that slug so the form must increment to "free-demo-2".
        ClassOfferingFactory(title="Free Demo", slug="free-demo")
        form = InstructorClassOfferingForm(
            data=_instructor_post_data(is_free="on"),
            instructor=instructor,
        )
        assert form.is_valid(), form.errors
        offering = form.save()
        assert offering.slug != "free-demo"
        assert offering.slug.startswith("free-demo")


def describe_PromoteUserToInstructorForm():
    def it_saves_with_unique_slug_when_base_slug_is_taken():
        user1 = UserFactory()
        user2 = UserFactory()
        # Create an existing instructor whose slug occupies the base name.
        Instructor.objects.create(user=user1, display_name="Jane Doe", slug="jane-doe")

        form = PromoteUserToInstructorForm(data={"user": user2.pk, "display_name": "Jane Doe", "bio": ""})
        # Manually prime the queryset so user2 is available.
        form.fields["user"].queryset = type(user2).objects.filter(pk=user2.pk)
        assert form.is_valid(), form.errors
        instructor = form.save()
        assert instructor.slug != "jane-doe"
        assert instructor.slug.startswith("jane-doe")

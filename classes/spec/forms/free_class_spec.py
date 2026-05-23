"""BDD specs for the `is_free` checkbox on ClassOffering forms."""

from __future__ import annotations

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import ClassOfferingForm, InstructorClassOfferingForm
from classes.models import ClassOffering

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
        "price_cents": "10000",
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
            form = ClassOfferingForm(data=_admin_post_data(price_cents="2500", member_discount_pct="15"))
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
                form = ClassOfferingForm(data=_admin_post_data(price_cents="99"))
                assert not form.is_valid()
                assert "price_cents" in form.errors
                assert any("$1.00" in e for e in form.errors["price_cents"])

            def it_accepts_paid_price_at_exactly_one_dollar():
                form = ClassOfferingForm(data=_admin_post_data(price_cents="100"))
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
                data=_instructor_post_data(price_cents="50"),
                instructor=instructor,
            )
            assert not form.is_valid()
            assert "price_cents" in form.errors
            assert any("$1.00" in e for e in form.errors["price_cents"])

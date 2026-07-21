"""BDD specs for the Sale section on both class edit forms (_SaleMixin)."""

from __future__ import annotations

import pytest

from classes.factories import CategoryFactory, InstructorFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm, _SaleMixin
from classes.models import DEFAULT_SALE_BANNER_TEXT, ClassOffering

pytestmark = pytest.mark.django_db


def _base_data(**overrides) -> dict:
    data = {
        "title": "Forge Basics",
        "category": str(CategoryFactory().pk),
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
        "scheduling_type": ClassOffering.SchedulingType.SINGLE_SESSION,
        "flexible_note": "",
        "image": "",
        "sale_enabled": "",
        "sale_kind": "percent",
        "sale_percent": "",
        "sale_amount_cents": "",
        "sale_banner_text": DEFAULT_SALE_BANNER_TEXT,
        "sale_allow_discount_codes": "",
    }
    data.update(overrides)
    return data


def _admin_data(**overrides) -> dict:
    return _base_data(instructor=str(InstructorFactory().pk), is_private="", private_for_name="", **overrides)


def _form(form_class, **overrides):
    data = _admin_data(**overrides) if form_class is ClassOfferingForm else _base_data(**overrides)
    return form_class(data=data)


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def form_class(request):
    return request.param


def describe_sale_section():
    def describe_enabling_a_percent_sale():
        def it_requires_the_percent(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="percent", sale_percent="")
            assert not form.is_valid()
            assert "Enter the percent off (1–99)." in form.errors["sale_percent"]

        def it_rejects_percent_of_zero(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="percent", sale_percent="0")
            assert not form.is_valid()
            assert "sale_percent" in form.errors

        def it_rejects_percent_of_one_hundred(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="percent", sale_percent="100")
            assert not form.is_valid()
            assert "Percent off must be between 1 and 99." in form.errors["sale_percent"]

        def it_round_trips_a_valid_percent_sale_onto_the_instance(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="percent", sale_percent="25")
            assert form.is_valid(), form.errors
            offering = form.save()
            offering.refresh_from_db()
            assert offering.sale_enabled is True
            assert offering.sale_kind == ClassOffering.SaleKind.PERCENT
            assert offering.sale_percent == 25
            assert offering.sale_is_active is True

    def describe_enabling_a_fixed_sale():
        def it_requires_the_amount(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="fixed", sale_amount_cents="")
            assert not form.is_valid()
            assert "Enter the dollar amount off." in form.errors["sale_amount_cents"]

        def it_rejects_amount_at_or_above_the_price(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="fixed", sale_amount_cents="100.00")
            assert not form.is_valid()
            assert "The amount off must be less than the price." in form.errors["sale_amount_cents"]

        def it_round_trips_a_valid_fixed_sale_onto_the_instance(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="fixed", sale_amount_cents="15.00")
            assert form.is_valid(), form.errors
            offering = form.save()
            offering.refresh_from_db()
            assert offering.sale_kind == ClassOffering.SaleKind.FIXED
            assert offering.sale_amount_cents == 1500
            assert offering.sale_price_cents == 8500

    def describe_free_class_conflict():
        def it_attaches_the_error_to_the_visible_price_field(form_class):
            form = _form(form_class, sale_enabled="on", sale_percent="20", is_free="on", price_cents="")
            assert not form.is_valid()
            assert (
                "A free class can't be on sale. Uncheck the free option or turn the sale off."
                in (form.errors["price_cents"])
            )

        def it_requires_a_price_before_a_sale(form_class):
            form = _form(form_class, sale_enabled="on", sale_percent="20", price_cents="")
            assert not form.is_valid()
            assert "Set a price before putting this class on sale." in form.errors["price_cents"]

    def describe_stripe_floor():
        def it_rejects_a_percent_sale_landing_between_one_and_forty_nine_cents(form_class):
            # 99% off a $1.00 class = $0.01 — below the $0.50 online-charge floor.
            form = _form(form_class, sale_enabled="on", sale_kind="percent", price_cents="1.00", sale_percent="99")
            assert not form.is_valid()
            assert (
                "This sale would drop the price below the $0.50 minimum we can charge online."
                in (form.errors["sale_percent"])
            )

        def it_rejects_a_fixed_sale_landing_between_one_and_forty_nine_cents(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="fixed", price_cents="1.00", sale_amount_cents="0.60")
            assert not form.is_valid()
            assert (
                "This sale would drop the price below the $0.50 minimum we can charge online."
                in (form.errors["sale_amount_cents"])
            )

        def it_accepts_a_sale_landing_at_or_above_fifty_cents(form_class):
            form = _form(form_class, sale_enabled="on", sale_kind="fixed", price_cents="1.00", sale_amount_cents="0.50")
            assert form.is_valid(), form.errors

    def describe_banner_text():
        def it_falls_back_to_the_default_when_blank_while_enabled(form_class):
            form = _form(form_class, sale_enabled="on", sale_percent="20", sale_banner_text="  ")
            assert form.is_valid(), form.errors
            offering = form.save()
            offering.refresh_from_db()
            assert offering.sale_banner_text == DEFAULT_SALE_BANNER_TEXT

        def it_keeps_a_custom_banner(form_class):
            form = _form(form_class, sale_enabled="on", sale_percent="20", sale_banner_text="Summer blowout!")
            assert form.is_valid(), form.errors
            assert form.save().sale_banner_text == "Summer blowout!"

    def describe_allow_discount_codes_toggle():
        def it_persists_on(form_class):
            form = _form(form_class, sale_enabled="on", sale_percent="20", sale_allow_discount_codes="on")
            assert form.is_valid(), form.errors
            assert form.save().sale_allow_discount_codes is True

        def it_persists_off(form_class):
            form = _form(form_class, sale_enabled="on", sale_percent="20")
            assert form.is_valid(), form.errors
            assert form.save().sale_allow_discount_codes is False

    def describe_disabling_a_sale():
        def it_preserves_the_dormant_sale_settings(form_class):
            form = _form(
                form_class, sale_enabled="", sale_percent="30", sale_banner_text="Old banner", sale_kind="percent"
            )
            assert form.is_valid(), form.errors
            offering = form.save()
            offering.refresh_from_db()
            assert offering.sale_enabled is False
            assert offering.sale_percent == 30
            assert offering.sale_banner_text == "Old banner"
            assert offering.sale_is_active is False

    def describe_resulting_sale_price_helper():
        def it_returns_none_when_the_percent_is_absent():
            assert _SaleMixin._resulting_sale_price_cents({"sale_kind": "percent"}, 5000) is None

        def it_returns_none_when_the_fixed_amount_is_absent():
            assert _SaleMixin._resulting_sale_price_cents({"sale_kind": "fixed"}, 5000) is None

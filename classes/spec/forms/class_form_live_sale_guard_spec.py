"""BDD specs for the live-sale guard on both composer forms (_LiveSaleGuardMixin).

The sale fields left the composer for the manage page modal, so a price edit must be checked
against the sale already stored on the class: a $80 fixed sale on a class re-priced to $50
would sell for $0, and a 99% sale on a class re-priced to $1.00 would land under Stripe's
floor. The price change is refused; the sale is never touched.
"""

from __future__ import annotations

import pytest

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.forms import ClassOfferingForm, TeachClassOfferingForm
from classes.models import ClassOffering

pytestmark = pytest.mark.django_db

TOO_LOW = "Turn the sale off or change it from the manage page before setting a price this low."


def _data(offering: ClassOffering, **overrides) -> dict:
    data = {
        "title": offering.title,
        "category": str(offering.category_id),
        "description": offering.description,
        "prerequisites": "",
        "materials_included": "",
        "materials_to_bring": "",
        "safety_requirements": "",
        "age_minimum": "",
        "age_guardian_note": "",
        "price_cents": f"{offering.price_cents / 100:.2f}",
        "member_discount_pct": str(offering.member_discount_pct),
        "capacity": str(offering.capacity),
        "scheduling_model": offering.scheduling_model,
        "scheduling_type": offering.scheduling_type,
        "flexible_note": "",
        "image": "",
        "hero_crop": "",
        "card_focus": "",
    }
    data.update(overrides)
    return data


def _form(form_class, offering: ClassOffering, **overrides):
    data = _data(offering, **overrides)
    if form_class is ClassOfferingForm:
        data.update(instructor=str(InstructorFactory().pk), is_private="", private_for_name="")
    return form_class(data=data, instance=offering)


@pytest.fixture(params=[TeachClassOfferingForm, ClassOfferingForm], ids=["teach", "admin"])
def form_class(request):
    return request.param


def _fixed_sale(amount_cents: int = 8000, price_cents: int = 10000) -> ClassOffering:
    return ClassOfferingFactory(
        category=CategoryFactory(),
        price_cents=price_cents,
        sale_enabled=True,
        sale_kind=ClassOffering.SaleKind.FIXED,
        sale_amount_cents=amount_cents,
    )


def _percent_sale(percent: int = 99, price_cents: int = 10000) -> ClassOffering:
    return ClassOfferingFactory(
        category=CategoryFactory(),
        price_cents=price_cents,
        sale_enabled=True,
        sale_kind=ClassOffering.SaleKind.PERCENT,
        sale_percent=percent,
    )


def _assert_untouched(offering: ClassOffering, price_cents: int) -> None:
    offering.refresh_from_db()
    assert offering.price_cents == price_cents
    assert offering.sale_enabled is True
    assert offering.sale_is_active is True


def describe_live_sale_guard():
    def describe_a_fixed_sale():
        def it_refuses_a_price_below_the_amount_off(form_class):
            offering = _fixed_sale()
            form = _form(form_class, offering, price_cents="50.00")
            assert not form.is_valid()
            assert form.errors["price_cents"] == [f"This class is on sale for $80 off. {TOO_LOW}"]
            _assert_untouched(offering, 10000)

        def it_refuses_a_price_equal_to_the_amount_off(form_class):
            offering = _fixed_sale()
            form = _form(form_class, offering, price_cents="80.00")
            assert not form.is_valid()
            assert "on sale for $80 off" in form.errors["price_cents"][0]
            _assert_untouched(offering, 10000)

        def it_refuses_a_price_that_leaves_the_sale_under_the_stripe_floor(form_class):
            # $80 off a $80.40 class is 40 cents: chargeable by nobody.
            offering = _fixed_sale()
            form = _form(form_class, offering, price_cents="80.40")
            assert not form.is_valid()
            assert TOO_LOW in form.errors["price_cents"][0]

        def it_saves_a_raised_price_and_keeps_the_sale(form_class):
            offering = _fixed_sale()
            form = _form(form_class, offering, price_cents="150.00")
            assert form.is_valid(), form.errors
            form.save()
            offering.refresh_from_db()
            assert offering.price_cents == 15000
            assert offering.sale_is_active is True
            assert offering.sale_price_cents == 7000

        def it_saves_a_lowered_price_that_still_clears_the_sale(form_class):
            offering = _fixed_sale()
            form = _form(form_class, offering, price_cents="90.00")
            assert form.is_valid(), form.errors
            assert form.save().sale_price_cents == 1000

    def describe_a_percent_sale():
        def it_refuses_a_price_that_lands_under_the_stripe_floor(form_class):
            # 99% off $1.00 is one cent.
            offering = _percent_sale()
            form = _form(form_class, offering, price_cents="1.00")
            assert not form.is_valid()
            assert form.errors["price_cents"] == [f"This class is on sale for 99% off. {TOO_LOW}"]
            _assert_untouched(offering, 10000)

        def it_saves_a_price_that_keeps_the_sale_at_or_above_the_floor(form_class):
            offering = _percent_sale(percent=20)
            form = _form(form_class, offering, price_cents="2.00")
            assert form.is_valid(), form.errors
            assert form.save().sale_price_cents == 160

    def describe_the_free_tick():
        def it_refuses_making_a_class_on_sale_free(form_class):
            offering = _fixed_sale()
            form = _form(form_class, offering, is_free="on", price_cents="")
            assert not form.is_valid()
            assert form.errors["price_cents"] == [
                "This class is on sale for $80 off. Turn the sale off from the manage page before making it free."
            ]
            _assert_untouched(offering, 10000)

    def describe_without_a_live_sale():
        def it_lets_the_price_drop_freely(form_class):
            offering = ClassOfferingFactory(category=CategoryFactory(), price_cents=10000, sale_enabled=False)
            form = _form(form_class, offering, price_cents="1.00")
            assert form.is_valid(), form.errors
            assert form.save().price_cents == 100

        def it_ignores_a_switched_on_sale_with_no_amount(form_class):
            # sale_enabled without an amount is not an active sale; there is nothing to protect.
            offering = ClassOfferingFactory(category=CategoryFactory(), price_cents=10000, sale_enabled=True)
            assert offering.sale_is_active is False
            form = _form(form_class, offering, price_cents="1.00")
            assert form.is_valid(), form.errors

        def it_does_not_double_up_on_an_empty_price(form_class):
            offering = _fixed_sale()
            form = _form(form_class, offering, price_cents="")
            assert not form.is_valid()
            assert form.errors["price_cents"] == ["Set a price or check 'This is a free class / workshop'."]

        def it_leaves_a_new_class_alone(form_class):
            form = form_class(data=_data(ClassOfferingFactory.build(category=CategoryFactory()), price_cents="1.00"))
            if form_class is ClassOfferingForm:
                form = form_class(
                    data=_data(
                        ClassOfferingFactory.build(category=CategoryFactory()),
                        price_cents="1.00",
                        instructor=str(InstructorFactory().pk),
                        is_private="",
                        private_for_name="",
                    )
                )
            assert form.is_valid(), form.errors

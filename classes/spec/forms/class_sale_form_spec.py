"""BDD specs for the sale modal form (ClassSaleForm), which carries _SaleMixin's validation unchanged."""

from __future__ import annotations

import pytest

from classes.factories import ClassOfferingFactory
from classes.forms import ClassSaleForm, _SaleMixin
from classes.models import DEFAULT_SALE_BANNER_TEXT, ClassOffering

pytestmark = pytest.mark.django_db


def _form(offering: ClassOffering, **overrides) -> ClassSaleForm:
    data = {
        "sale_kind": "percent",
        "sale_percent": "",
        "sale_amount_cents": "",
        "sale_banner_text": "",
        "sale_allow_discount_codes": "",
    }
    data.update(overrides)
    return ClassSaleForm(data=data, instance=offering)


@pytest.fixture
def offering(db):
    return ClassOfferingFactory(price_cents=10000, sale_enabled=False)


def describe_ClassSaleForm():
    def describe_fields():
        def it_carries_only_the_five_amount_fields(offering):
            assert list(ClassSaleForm(instance=offering).fields) == [
                "sale_kind",
                "sale_percent",
                "sale_amount_cents",
                "sale_banner_text",
                "sale_allow_discount_codes",
            ]

        def it_uses_the_modal_labels(offering):
            form = ClassSaleForm(instance=offering)
            assert form.fields["sale_kind"].label == "How Much Off?"
            assert form.fields["sale_percent"].label == "Percent off"
            assert form.fields["sale_amount_cents"].label == "Amount off ($)"
            assert form.fields["sale_banner_text"].label == "Banner text"
            assert form.fields["sale_banner_text"].help_text == "Leave it blank to use the standard sale banner."
            assert form.fields["sale_allow_discount_codes"].label == "Allow discount codes on top"

        def it_shows_the_stored_amount_in_dollars(db):
            offering = ClassOfferingFactory(price_cents=10000, sale_amount_cents=1500)
            assert str(ClassSaleForm(instance=offering)["sale_amount_cents"].value()) == "15"

    def describe_a_percent_sale():
        def it_requires_the_percent(offering):
            form = _form(offering, sale_kind="percent", sale_percent="")
            assert not form.is_valid()
            assert "Enter the percent off (1–99)." in form.errors["sale_percent"]

        def it_rejects_percent_of_zero(offering):
            form = _form(offering, sale_kind="percent", sale_percent="0")
            assert not form.is_valid()
            assert "sale_percent" in form.errors

        def it_rejects_percent_of_one_hundred(offering):
            form = _form(offering, sale_kind="percent", sale_percent="100")
            assert not form.is_valid()
            assert "Percent off must be between 1 and 99." in form.errors["sale_percent"]

        def it_turns_the_sale_on_with_the_percent(offering):
            form = _form(offering, sale_kind="percent", sale_percent="25")
            assert form.is_valid(), form.errors
            saved = form.save()
            saved.refresh_from_db()
            assert saved.sale_enabled is True
            assert saved.sale_kind == ClassOffering.SaleKind.PERCENT
            assert saved.sale_percent == 25
            assert saved.sale_is_active is True
            assert saved.sale_price_cents == 7500

    def describe_a_fixed_sale():
        def it_requires_the_amount(offering):
            form = _form(offering, sale_kind="fixed", sale_amount_cents="")
            assert not form.is_valid()
            assert "Enter the dollar amount off." in form.errors["sale_amount_cents"]

        def it_rejects_an_amount_at_the_price(offering):
            form = _form(offering, sale_kind="fixed", sale_amount_cents="100.00")
            assert not form.is_valid()
            assert "The amount off must be less than the price." in form.errors["sale_amount_cents"]

        def it_rejects_an_amount_above_the_price(offering):
            form = _form(offering, sale_kind="fixed", sale_amount_cents="120.00")
            assert not form.is_valid()
            assert "The amount off must be less than the price." in form.errors["sale_amount_cents"]

        def it_turns_the_sale_on_with_the_amount(offering):
            form = _form(offering, sale_kind="fixed", sale_amount_cents="15.00")
            assert form.is_valid(), form.errors
            saved = form.save()
            saved.refresh_from_db()
            assert saved.sale_kind == ClassOffering.SaleKind.FIXED
            assert saved.sale_amount_cents == 1500
            assert saved.sale_price_cents == 8500

    def describe_a_free_class():
        def it_cannot_go_on_sale(db):
            # The page never offers the modal on a free class; a crafted POST still gets the mixin's
            # check, surfaced at the form level because the modal has no price field to hang it on.
            free = ClassOfferingFactory(price_cents=0, member_discount_pct=0)
            form = _form(free, sale_kind="percent", sale_percent="20")
            assert not form.is_valid()
            assert "price_cents" not in form.errors
            assert form.non_field_errors() == [
                "A free class can't be on sale. Uncheck the free option or turn the sale off."
            ]

    def describe_stripe_floor():
        def it_rejects_a_percent_sale_landing_between_one_and_forty_nine_cents(db):
            dollar = ClassOfferingFactory(price_cents=100)
            form = _form(dollar, sale_kind="percent", sale_percent="99")
            assert not form.is_valid()
            assert (
                "This sale would drop the price below the $0.50 minimum we can charge online."
                in form.errors["sale_percent"]
            )

        def it_rejects_a_fixed_sale_landing_between_one_and_forty_nine_cents(db):
            dollar = ClassOfferingFactory(price_cents=100)
            form = _form(dollar, sale_kind="fixed", sale_amount_cents="0.60")
            assert not form.is_valid()
            assert (
                "This sale would drop the price below the $0.50 minimum we can charge online."
                in form.errors["sale_amount_cents"]
            )

        def it_accepts_a_sale_landing_at_fifty_cents(db):
            dollar = ClassOfferingFactory(price_cents=100)
            form = _form(dollar, sale_kind="fixed", sale_amount_cents="0.50")
            assert form.is_valid(), form.errors

    def describe_banner_text():
        def it_falls_back_to_the_default_when_blank(offering):
            form = _form(offering, sale_percent="20", sale_banner_text="  ")
            assert form.is_valid(), form.errors
            saved = form.save()
            saved.refresh_from_db()
            assert saved.sale_banner_text == DEFAULT_SALE_BANNER_TEXT

        def it_keeps_a_custom_banner(offering):
            form = _form(offering, sale_percent="20", sale_banner_text="Summer blowout!")
            assert form.is_valid(), form.errors
            assert form.save().sale_banner_text == "Summer blowout!"

    def describe_allow_discount_codes_toggle():
        def it_persists_on(offering):
            form = _form(offering, sale_percent="20", sale_allow_discount_codes="on")
            assert form.is_valid(), form.errors
            assert form.save().sale_allow_discount_codes is True

        def it_persists_off(offering):
            form = _form(offering, sale_percent="20")
            assert form.is_valid(), form.errors
            assert form.save().sale_allow_discount_codes is False

    def describe_editing_a_live_sale():
        def it_keeps_the_sale_on_and_changes_the_amount(db):
            live = ClassOfferingFactory(price_cents=10000, sale_enabled=True, sale_percent=20)
            form = _form(live, sale_kind="percent", sale_percent="30")
            assert form.is_valid(), form.errors
            saved = form.save()
            saved.refresh_from_db()
            assert saved.sale_enabled is True
            assert saved.sale_percent == 30

    def describe_resulting_sale_price_helper():
        def it_returns_none_when_the_percent_is_absent():
            assert _SaleMixin._resulting_sale_price_cents({"sale_kind": "percent"}, 5000) is None

        def it_returns_none_when_the_fixed_amount_is_absent():
            assert _SaleMixin._resulting_sale_price_cents({"sale_kind": "fixed"}, 5000) is None

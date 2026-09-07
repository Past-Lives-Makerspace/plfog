"""BDD specs for PaymentRefundForm — amount bounds, prefill, optional reason."""

from __future__ import annotations

from decimal import Decimal

import pytest

from billing.models import PaymentRefund
from classes.factories import RegistrationFactory
from classes.forms import PaymentRefundForm
from classes.models import Registration
from tests.billing.factories import PaymentRefundFactory

pytestmark = pytest.mark.django_db


def _paid_registration(**kwargs) -> Registration:
    defaults = {
        "status": Registration.Status.CONFIRMED,
        "amount_paid_cents": 5000,
        "stripe_payment_id": "pi_form_spec",
    }
    defaults.update(kwargs)
    return RegistrationFactory(**defaults)


def describe_PaymentRefundForm():
    def describe_prefill():
        def it_defaults_the_amount_to_the_full_refundable_remainder():
            form = PaymentRefundForm(registration=_paid_registration())
            assert form["amount"].value() == Decimal("50")
            assert "Up to $50.00" in form.fields["amount"].help_text

        def it_prefills_the_remainder_after_a_partial_refund():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=1500, status=PaymentRefund.Status.SUCCEEDED)
            form = PaymentRefundForm(registration=registration)
            assert form["amount"].value() == Decimal("35")

    def describe_clean_amount():
        def it_accepts_the_exact_refundable_amount():
            form = PaymentRefundForm({"amount": "50.00"}, registration=_paid_registration())
            assert form.is_valid()
            assert form.amount_cents == 5000

        def it_accepts_a_partial_amount():
            form = PaymentRefundForm({"amount": "12.34"}, registration=_paid_registration())
            assert form.is_valid()
            assert form.amount_cents == 1234

        def it_rejects_zero():
            form = PaymentRefundForm({"amount": "0.00"}, registration=_paid_registration())
            assert not form.is_valid()
            assert "Enter an amount between $0.01 and $50.00." in form.errors["amount"]

        def it_rejects_an_amount_over_the_remainder():
            form = PaymentRefundForm({"amount": "50.01"}, registration=_paid_registration())
            assert not form.is_valid()
            assert "Enter an amount between $0.01 and $50.00." in form.errors["amount"]

        def it_bounds_against_the_remainder_not_the_original_payment():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=4000, status=PaymentRefund.Status.SUCCEEDED)
            form = PaymentRefundForm({"amount": "15.00"}, registration=registration)
            assert not form.is_valid()
            assert "Enter an amount between $0.01 and $10.00." in form.errors["amount"]

    def describe_reason():
        def it_is_optional():
            form = PaymentRefundForm({"amount": "50.00"}, registration=_paid_registration())
            assert form.is_valid()
            assert form.cleaned_data["reason"] == ""

        def it_passes_through_when_given():
            form = PaymentRefundForm(
                {"amount": "50.00", "reason": "duplicate signup"}, registration=_paid_registration()
            )
            assert form.is_valid()
            assert form.cleaned_data["reason"] == "duplicate signup"

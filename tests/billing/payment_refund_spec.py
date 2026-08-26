"""BDD specs for the PaymentRefund ledger model — constraints, properties, queryset."""

from __future__ import annotations

import pytest
from django.db import IntegrityError

from billing.models import PaymentRefund
from classes.factories import RegistrationFactory
from tests.billing.factories import PaymentRefundFactory, TabChargeFactory
from tests.membership.factories import OrientationBookingFactory

pytestmark = pytest.mark.django_db


def describe_PaymentRefund():
    def describe_exactly_one_source_constraint():
        def it_rejects_a_row_with_no_source():
            with pytest.raises(IntegrityError):
                PaymentRefund.objects.create(amount_cents=100)

        def it_rejects_a_row_with_both_sources():
            registration = RegistrationFactory()
            booking = OrientationBookingFactory()
            with pytest.raises(IntegrityError):
                PaymentRefund.objects.create(registration=registration, orientation_booking=booking, amount_cents=100)

    def describe_stripe_refund_id_uniqueness():
        def it_rejects_a_duplicate_stripe_refund_id():
            PaymentRefundFactory(stripe_refund_id="re_dup")
            with pytest.raises(IntegrityError):
                PaymentRefundFactory(stripe_refund_id="re_dup")

        def it_allows_many_blank_ids_at_once():
            # Blank means "Stripe hasn't answered yet" — several in-flight refunds
            # may hold it simultaneously, so uniqueness applies only to real ids.
            first = PaymentRefundFactory(stripe_refund_id="")
            second = PaymentRefundFactory(stripe_refund_id="")
            assert first.pk != second.pk

    def describe_source_object():
        def it_returns_the_registration_when_set():
            registration = RegistrationFactory()
            refund = PaymentRefundFactory(registration=registration)
            assert refund.source_object == registration
            assert refund.source_kind == "class"

        def it_returns_the_orientation_booking_when_set():
            booking = OrientationBookingFactory()
            refund = PaymentRefundFactory(registration=None, orientation_booking=booking)
            assert refund.source_object == booking
            assert refund.source_kind == "orientation"

    def describe_str():
        def it_shows_dollars_source_and_status():
            refund = PaymentRefundFactory(amount_cents=6550, status=PaymentRefund.Status.SUCCEEDED)
            text = str(refund)
            assert text.startswith("$65.50 refund for ")
            assert text.endswith("(Succeeded)")

    def describe_queryset():
        def it_filters_succeeded_rows():
            done = PaymentRefundFactory(status=PaymentRefund.Status.SUCCEEDED)
            PaymentRefundFactory(status=PaymentRefund.Status.PENDING)
            PaymentRefundFactory(status=PaymentRefund.Status.FAILED)
            assert list(PaymentRefund.objects.succeeded()) == [done]

        def it_scopes_for_source_to_one_registration():
            mine = PaymentRefundFactory()
            PaymentRefundFactory()  # another registration's refund
            assert list(PaymentRefund.objects.for_source(mine.registration)) == [mine]

        def it_scopes_for_source_to_one_orientation_booking():
            booking = OrientationBookingFactory()
            refund = PaymentRefundFactory(registration=None, orientation_booking=booking)
            PaymentRefundFactory()
            assert list(PaymentRefund.objects.for_source(booking)) == [refund]

        def it_rejects_a_non_refundable_source():
            charge = TabChargeFactory()
            with pytest.raises(TypeError, match="Not a refundable source"):
                PaymentRefund.objects.for_source(charge)

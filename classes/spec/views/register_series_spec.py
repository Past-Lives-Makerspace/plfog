"""BDD specs for series-package registration checkout.

The load-bearing invariant: one series purchase = ONE Stripe charge = ONE seat.
These specs guard against any future change that loops over sessions and
accidentally charges or seats a registrant more than once.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from classes.factories import (
    CategoryFactory,
    InstructorFactory,
    SeriesClassOfferingFactory,
)
from classes.models import ClassOffering, Registration

pytestmark = pytest.mark.django_db


@pytest.fixture
def series_offering(db):
    offering = SeriesClassOfferingFactory(
        title="Blacksmithing 101",
        slug="blacksmithing-101",
        category=CategoryFactory(),
        instructor=InstructorFactory(),
        status=ClassOffering.Status.PUBLISHED,
        price_cents=15000,
        member_discount_pct=0,
        capacity=6,
        session_count=3,
    )
    return offering


def _post_data(**overrides):
    data = {
        "first_name": "Sam",
        "last_name": "Smith",
        "pronouns": "",
        "email": "sam@example.com",
        "phone": "",
        "prior_experience": "",
        "looking_for": "",
        "discount_code": "",
        "liability_signature": "Sam Smith",
        "accepts_liability": "on",
    }
    data.update(overrides)
    return data


def describe_series_checkout():
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_charges_the_series_price_once_for_all_sessions(mock_checkout, series_offering, client):
        mock_checkout.return_value = {"id": "cs_test_series", "url": "https://checkout.stripe.com/c/pay/x"}

        response = client.post(
            reverse("classes:register", kwargs={"slug": series_offering.slug}),
            data=_post_data(),
        )

        assert response.status_code == 302
        # Charged exactly once.
        assert mock_checkout.call_count == 1
        kwargs = mock_checkout.call_args.kwargs
        # The series price, charged ONCE — not multiplied by session count.
        assert kwargs["amount_cents"] == 15000
        assert "series" in kwargs["product_name"].lower()
        assert "3-session" in kwargs["product_name"]
        # Exactly one registration created — not one per session.
        assert series_offering.registrations.count() == 1

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_consumes_one_seat_for_the_whole_series(mock_checkout, series_offering, client):
        mock_checkout.return_value = {"id": "cs_test_series", "url": "https://checkout.stripe.com/c/pay/x"}
        before = series_offering.spots_remaining

        client.post(
            reverse("classes:register", kwargs={"slug": series_offering.slug}),
            data=_post_data(),
        )

        registration = series_offering.registrations.get()
        # Pending registrations already hold a seat (spots_remaining counts PENDING).
        assert series_offering.spots_remaining == before - 1
        # Confirming the single registration must not consume any further seats.
        registration.status = Registration.Status.CONFIRMED
        registration.save(update_fields=["status"])
        assert series_offering.spots_remaining == before - 1

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_passes_a_single_registration_id_in_metadata(mock_checkout, series_offering, client):
        mock_checkout.return_value = {"id": "cs_test_series", "url": "https://checkout.stripe.com/c/pay/x"}

        client.post(
            reverse("classes:register", kwargs={"slug": series_offering.slug}),
            data=_post_data(),
        )

        registration = series_offering.registrations.get()
        kwargs = mock_checkout.call_args.kwargs
        assert kwargs["metadata"]["registration_id"] == str(registration.pk)
        assert kwargs["metadata"]["kind"] == "class_registration"

    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_rolls_back_the_single_registration_when_stripe_fails(mock_checkout, series_offering, client):
        mock_checkout.side_effect = RuntimeError("stripe down")
        with pytest.raises(RuntimeError):
            client.post(
                reverse("classes:register", kwargs={"slug": series_offering.slug}),
                data=_post_data(),
            )
        assert not series_offering.registrations.exists()

    def it_routes_a_sold_out_series_to_the_waitlist(series_offering, client):
        for _ in range(series_offering.capacity):
            Registration.objects.create(
                class_offering=series_offering,
                first_name="Full",
                last_name="House",
                email=f"full{_}@example.com",
                status=Registration.Status.CONFIRMED,
            )
        assert series_offering.spots_remaining == 0

        response = client.post(
            reverse("classes:register", kwargs={"slug": series_offering.slug}),
            data=_post_data(),
        )

        assert response.status_code == 302
        registration = series_offering.registrations.get(email="sam@example.com")
        assert registration.status == Registration.Status.WAITLISTED


def describe_series_confirmation_webhook():
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_confirms_one_registration_at_the_series_total(mock_checkout, series_offering, client):
        from classes.webhook_handlers import handle_checkout_session_completed

        mock_checkout.return_value = {"id": "cs_test_series", "url": "https://checkout.stripe.com/c/pay/x"}
        client.post(
            reverse("classes:register", kwargs={"slug": series_offering.slug}),
            data=_post_data(),
        )
        registration = series_offering.registrations.get()

        event = {
            "data": {
                "object": {
                    "id": "cs_test_series",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_series",
                    "amount_total": 15000,
                    "metadata": {
                        "kind": "class_registration",
                        "registration_id": str(registration.pk),
                    },
                }
            }
        }
        handle_checkout_session_completed(event)

        registration.refresh_from_db()
        assert registration.status == Registration.Status.CONFIRMED
        assert registration.amount_paid_cents == 15000
        # Still exactly one registration — the series did not fan out.
        assert series_offering.registrations.count() == 1


def describe_single_session_checkout_unchanged():
    @patch("billing.stripe_utils.create_class_checkout_session")
    def it_uses_the_plain_title_for_a_single_session(mock_checkout, client):
        from classes.factories import ClassOfferingFactory, ClassSessionFactory

        offering = ClassOfferingFactory(
            title="One-Off Welding",
            slug="one-off-welding",
            status=ClassOffering.Status.PUBLISHED,
            price_cents=8000,
            member_discount_pct=0,
            capacity=4,
        )
        ClassSessionFactory(
            class_offering=offering,
            starts_at=timezone.now() + timedelta(days=5),
            ends_at=timezone.now() + timedelta(days=5, hours=2),
        )
        mock_checkout.return_value = {"id": "cs_test_single", "url": "https://checkout.stripe.com/c/pay/y"}

        client.post(reverse("classes:register", kwargs={"slug": offering.slug}), data=_post_data())

        kwargs = mock_checkout.call_args.kwargs
        assert kwargs["product_name"] == "One-Off Welding"
        assert kwargs["amount_cents"] == 8000

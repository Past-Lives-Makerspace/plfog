"""BDD specs for the cubby rental webhooks (#978): the handlers, their registration, and a signed delivery.

Each handler self-filters on ``metadata.kind == "cubby_rental"`` and hands the work to
membership.cubby_rentals, which re-reads the subscription from Stripe (patched here with the dict
``retrieve_subscription`` returns), so a delivery out of order or twice changes nothing more.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client

from billing import views as billing_views
from billing.models import BillingSettings
from membership import webhook_handlers
from membership.models import CubbyPayment, CubbyRental, Member
from tests.billing.factories import TabFactory
from tests.membership.factories import (
    CubbyFactory,
    GuildFactory,
    MembershipPlanFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status
_WEBHOOK_SECRET = "whsec_cubby_978"


def _member() -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username="ferncastle", email="ferncastle@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    TabFactory(member=member, stripe_customer_id="cus_fern")
    return member


def _hold() -> CubbyRental:
    guild = GuildFactory(show_cubbies_tab=True)
    cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild, name="Glaze Wall"), label="B3")
    return CubbyRental.objects.create(
        cubby=cubby,
        member=_member(),
        amount_cents=2200,
        status=Status.PENDING_PAYMENT,
        stripe_checkout_session_id="cs_b3",
    )


def _metadata(rental: CubbyRental) -> dict[str, str]:
    return {
        "kind": "cubby_rental",
        "rental_pk": str(rental.pk),
        "cubby_pk": str(rental.cubby_id),
        "member_pk": str(rental.member_id),
    }


def _snapshot(rental: CubbyRental, status: str = "active") -> dict[str, Any]:
    return {
        "id": f"sub_{rental.pk}",
        "status": status,
        "customer": "cus_fern",
        "metadata": _metadata(rental),
        "cancel_at_period_end": False,
        "cancel_at": None,
        "default_payment_method": "",
        "latest_invoice": f"in_{rental.pk}",
        "item_id": "si_b3",
        "product_id": "prod_b3",
        "unit_amount": 2200,
        "current_period_end": 1795000000,
    }


def _invoice(rental: CubbyRental, **overrides: Any) -> dict[str, Any]:
    invoice = {
        "id": f"in_{rental.pk}",
        "object": "invoice",
        "amount_paid": 2200,
        "status": "paid",
        "status_transitions": {"paid_at": 1792400000},
        "lines": {"object": "list", "data": [{"period": {"start": 1792400000, "end": 1795000000}}]},
        "parent": {
            "type": "subscription_details",
            "subscription_details": {"subscription": f"sub_{rental.pk}", "metadata": _metadata(rental)},
        },
    }
    invoice.update(overrides)
    return invoice


def _event(event_type: str, obj: dict[str, Any]) -> dict[str, Any]:
    return {"id": "evt_978", "type": event_type, "data": {"object": obj}}


def describe_registration():
    def it_fans_the_checkout_events_in_with_the_other_kinds():
        assert webhook_handlers.handle_cubby_checkout_completed in billing_views._CHECKOUT_COMPLETED_HANDLERS
        assert webhook_handlers.handle_cubby_checkout_expired in billing_views._CHECKOUT_EXPIRED_HANDLERS

    def it_handles_the_four_subscription_events():
        handlers = billing_views._WEBHOOK_HANDLERS
        assert handlers["invoice.paid"] is webhook_handlers.handle_cubby_invoice_paid
        assert handlers["invoice.payment_failed"] is webhook_handlers.handle_cubby_invoice_payment_failed
        assert handlers["customer.subscription.updated"] is webhook_handlers.handle_cubby_subscription_changed
        assert handlers["customer.subscription.deleted"] is webhook_handlers.handle_cubby_subscription_changed


def describe_checkout_events():
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_activates_once_on_completed_through_the_shared_dispatch(mock_sub):
        hold = _hold()
        mock_sub.return_value = _snapshot(hold)
        session = {
            "id": "cs_b3",
            "metadata": _metadata(hold),
            "subscription": f"sub_{hold.pk}",
            "payment_status": "paid",
        }
        billing_views._dispatch_checkout_completed(_event("checkout.session.completed", session))
        billing_views._dispatch_checkout_completed(_event("checkout.session.completed", session))
        hold.refresh_from_db()
        assert hold.status == Status.ACTIVE
        assert [m.subject for m in mail.outbox] == ["B3 is yours"]

    def it_releases_the_hold_on_expired_through_the_shared_dispatch():
        hold = _hold()
        billing_views._dispatch_checkout_expired(
            _event("checkout.session.expired", {"id": "cs_b3", "metadata": _metadata(hold)})
        )
        assert not CubbyRental.objects.exists()


def describe_invoice_events():
    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_b3")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_records_one_payment_however_often_stripe_retries(mock_sub, mock_pi):
        hold = _hold()
        mock_sub.return_value = _snapshot(hold)
        for _ in range(3):
            webhook_handlers.handle_cubby_invoice_paid(_event("invoice.paid", _invoice(hold)))
        assert CubbyPayment.objects.count() == 1
        hold.refresh_from_db()
        assert hold.status == Status.ACTIVE

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_marks_past_due_on_a_failed_payment(mock_sub):
        rental = _hold()
        CubbyRental.objects.filter(pk=rental.pk).update(status=Status.ACTIVE, stripe_subscription_id=f"sub_{rental.pk}")
        mock_sub.return_value = _snapshot(rental, status="past_due")
        webhook_handlers.handle_cubby_invoice_payment_failed(
            _event("invoice.payment_failed", _invoice(rental, status="open"))
        )
        rental.refresh_from_db()
        assert rental.status == Status.PAST_DUE


def describe_subscription_events():
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_ends_the_rental_on_deleted(mock_sub):
        hold = _hold()
        CubbyRental.objects.filter(pk=hold.pk).update(status=Status.ACTIVE, start_date="2026-10-01")
        mock_sub.return_value = _snapshot(hold, status="canceled")
        webhook_handlers.handle_cubby_subscription_changed(
            _event("customer.subscription.deleted", {"id": f"sub_{hold.pk}", "metadata": _metadata(hold)})
        )
        hold.refresh_from_db()
        assert hold.status == Status.ENDED

    @patch("billing.stripe_utils.retrieve_subscription")
    def it_ignores_a_subscription_that_is_not_a_cubbys(mock_sub):
        webhook_handlers.handle_cubby_subscription_changed(
            _event("customer.subscription.updated", {"id": "sub_x", "metadata": {"kind": "something_else"}})
        )
        webhook_handlers.handle_cubby_subscription_changed(
            _event("customer.subscription.updated", {"id": "sub_y", "metadata": None})
        )
        mock_sub.assert_not_called()


def describe_a_signed_delivery():
    def _signed(payload: bytes) -> str:
        timestamp = int(time.time())
        signature = hmac.new(_WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={signature}"

    @patch("billing.stripe_utils.invoice_payment_intent", return_value="pi_b3")
    @patch("billing.stripe_utils.retrieve_subscription")
    def it_reaches_the_rental_through_the_stripe_endpoint(mock_sub, mock_pi, client: Client):
        settings = BillingSettings.load()
        settings.test_mode = False
        settings.connect_platform_secret_key = "sk_live_cubby"
        settings.connect_platform_webhook_secret = _WEBHOOK_SECRET
        settings.save()
        hold = _hold()
        mock_sub.return_value = _snapshot(hold)
        payload = json.dumps(
            {"id": "evt_978", "object": "event", "type": "invoice.paid", "data": {"object": _invoice(hold)}}
        ).encode()

        response = client.post(
            "/billing/webhooks/stripe/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=_signed(payload),
        )

        assert response.status_code == 200
        assert CubbyPayment.objects.get().rental == hold

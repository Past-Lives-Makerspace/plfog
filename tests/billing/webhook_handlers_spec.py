"""BDD-style tests for Stripe webhook handlers."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from django.test import Client
from factory.django import mute_signals

from billing.models import TabCharge
from billing.webhook_handlers import (
    handle_charge_dispute_created,
    handle_payment_intent_failed,
    handle_payment_intent_succeeded,
    handle_payment_method_detached,
    handle_payment_method_updated,
    handle_setup_intent_succeeded,
)
from membership.models import Member
from tests.billing.factories import TabChargeFactory, TabFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _seed_fog_admin(email: str = "admin@example.com") -> Member:
    """A Member with the Admin FOG role + a linked, email-bearing User.

    The charge-failure admin email resolves recipients via the FOG_ADMINS resolver
    (Phase 4 — replacing the old ``BILLING_ADMIN_EMAILS`` static list), so a test
    asserting the email fires must seed at least one such admin. Signals are muted
    so creating the User does not auto-create a second colliding Member.
    """
    member = MemberFactory(_pre_signup_email=email, fog_role=Member.FogRole.ADMIN)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"admin_{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    return member


def describe_handle_setup_intent_succeeded():
    @patch("billing.stripe_utils.retrieve_payment_method")
    def it_updates_tab_payment_method(mock_retrieve):
        mock_retrieve.return_value = {"id": "pm_new", "brand": "visa", "last4": "4242"}
        tab = TabFactory(stripe_customer_id="cus_123", stripe_payment_method_id="")

        handle_setup_intent_succeeded({"data": {"object": {"customer": "cus_123", "payment_method": "pm_new"}}})

        tab.refresh_from_db()
        assert tab.stripe_payment_method_id == "pm_new"
        assert tab.payment_method_last4 == "4242"

    def it_is_idempotent_when_already_set():
        tab = TabFactory(stripe_customer_id="cus_123", stripe_payment_method_id="pm_existing")

        handle_setup_intent_succeeded({"data": {"object": {"customer": "cus_123", "payment_method": "pm_existing"}}})

        tab.refresh_from_db()
        assert tab.stripe_payment_method_id == "pm_existing"

    def it_skips_when_no_tab_found():
        # Should not raise
        handle_setup_intent_succeeded({"data": {"object": {"customer": "cus_unknown", "payment_method": "pm_new"}}})


def describe_handle_payment_intent_succeeded():
    def it_marks_charge_succeeded():
        tab = TabFactory()
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.PROCESSING,
            stripe_payment_intent_id="pi_test",
            amount=Decimal("50.00"),
        )
        member = tab.member
        member._pre_signup_email = "test@example.com"
        member.save()

        handle_payment_intent_succeeded(
            {
                "data": {
                    "object": {
                        "id": "pi_test",
                        "charges": {"data": [{"id": "ch_test", "receipt_url": "https://stripe.com/r"}]},
                    }
                }
            }
        )

        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.SUCCEEDED
        assert charge.stripe_charge_id == "ch_test"
        assert charge.stripe_receipt_url == "https://stripe.com/r"
        assert charge.charged_at is not None

    def it_handles_empty_charges_data():
        member = MemberFactory(_pre_signup_email="nocharge@example.com")
        tab = TabFactory(member=member)
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.PROCESSING,
            stripe_payment_intent_id="pi_no_charges",
            amount=Decimal("10.00"),
        )

        handle_payment_intent_succeeded({"data": {"object": {"id": "pi_no_charges", "charges": {"data": []}}}})

        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.SUCCEEDED
        assert charge.stripe_charge_id == ""

    def it_is_idempotent():
        tab = TabFactory()
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.SUCCEEDED,
            stripe_payment_intent_id="pi_already",
        )

        handle_payment_intent_succeeded({"data": {"object": {"id": "pi_already", "charges": {"data": []}}}})

        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.SUCCEEDED

    def it_skips_unknown_payment_intent():
        handle_payment_intent_succeeded({"data": {"object": {"id": "pi_unknown", "charges": {"data": []}}}})


def describe_handle_payment_intent_failed():
    def it_marks_charge_failed():
        _seed_fog_admin()
        tab = TabFactory()
        charge = TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.PROCESSING,
            stripe_payment_intent_id="pi_fail",
        )

        handle_payment_intent_failed(
            {
                "data": {
                    "object": {
                        "id": "pi_fail",
                        "last_payment_error": {"message": "Card declined"},
                    }
                }
            }
        )

        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.FAILED
        assert charge.failure_reason == "Card declined"
        assert len(mail.outbox) == 1

    def it_is_idempotent():
        tab = TabFactory()
        TabChargeFactory(
            tab=tab,
            status=TabCharge.Status.FAILED,
            stripe_payment_intent_id="pi_already_fail",
        )

        handle_payment_intent_failed({"data": {"object": {"id": "pi_already_fail", "last_payment_error": {}}}})

        # No additional emails
        assert len(mail.outbox) == 0

    def it_skips_unknown_payment_intent():
        handle_payment_intent_failed({"data": {"object": {"id": "pi_unknown_fail", "last_payment_error": {}}}})


def describe_handle_payment_method_detached():
    def it_clears_tab_payment_fields():
        tab = TabFactory(
            stripe_payment_method_id="pm_detach",
            payment_method_last4="1234",
            payment_method_brand="visa",
        )

        handle_payment_method_detached({"data": {"object": {"id": "pm_detach"}}})

        tab.refresh_from_db()
        assert tab.stripe_payment_method_id == ""
        assert tab.payment_method_last4 == ""

    def it_ignores_unknown_payment_method():
        handle_payment_method_detached({"data": {"object": {"id": "pm_unknown"}}})


def describe_handle_payment_method_updated():
    def it_updates_card_details():
        tab = TabFactory(
            stripe_payment_method_id="pm_update",
            payment_method_last4="1111",
            payment_method_brand="visa",
        )

        handle_payment_method_updated(
            {"data": {"object": {"id": "pm_update", "card": {"last4": "9999", "brand": "mastercard"}}}}
        )

        tab.refresh_from_db()
        assert tab.payment_method_last4 == "9999"
        assert tab.payment_method_brand == "mastercard"

    def it_ignores_unknown_payment_method():
        handle_payment_method_updated(
            {"data": {"object": {"id": "pm_unknown", "card": {"last4": "0000", "brand": "amex"}}}}
        )

    def it_skips_update_when_no_card_key():
        tab = TabFactory(
            stripe_payment_method_id="pm_nocard",
            payment_method_last4="4242",
            payment_method_brand="visa",
        )

        handle_payment_method_updated({"data": {"object": {"id": "pm_nocard"}}})

        tab.refresh_from_db()
        assert tab.payment_method_last4 == "4242"  # unchanged
        assert tab.payment_method_brand == "visa"  # unchanged


def describe_handle_charge_dispute_created():
    def it_logs_without_error():
        handle_charge_dispute_created({"data": {"object": {"charge": "ch_disputed", "amount": 5000}}})


def describe_stripe_webhook_view():
    @patch("billing.views.stripe_utils.construct_webhook_event")
    def it_returns_200_for_valid_event(mock_construct, client: Client):
        mock_event = MagicMock()
        mock_event.type = "charge.succeeded"
        mock_event.to_dict.return_value = {"type": "charge.succeeded", "data": {"object": {}}}
        mock_construct.return_value = mock_event

        response = client.post(
            "/billing/webhooks/stripe/",
            data=b"payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig_test",
        )

        assert response.status_code == 200

    @patch("billing.views.stripe_utils.construct_webhook_event")
    def it_returns_400_on_invalid_signature(mock_construct, client: Client):
        mock_construct.side_effect = ValueError("Invalid signature")

        response = client.post(
            "/billing/webhooks/stripe/",
            data=b"payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="bad_sig",
        )

        assert response.status_code == 400

    @patch("billing.views.stripe_utils.construct_webhook_event")
    def it_dispatches_to_handler(mock_construct, client: Client):
        tab = TabFactory(stripe_payment_method_id="pm_detach_test", payment_method_last4="4242")
        mock_event = MagicMock()
        mock_event.type = "payment_method.detached"
        mock_event.to_dict.return_value = {
            "type": "payment_method.detached",
            "data": {"object": {"id": "pm_detach_test"}},
        }
        mock_construct.return_value = mock_event

        response = client.post(
            "/billing/webhooks/stripe/",
            data=b"payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig_test",
        )

        assert response.status_code == 200
        tab.refresh_from_db()
        assert tab.stripe_payment_method_id == ""

    @patch("billing.views.stripe_utils.construct_webhook_event")
    def it_returns_500_on_handler_error(mock_construct, client: Client):
        mock_event = MagicMock()
        mock_event.type = "setup_intent.succeeded"
        mock_event.to_dict.side_effect = Exception("Handler boom")
        mock_construct.return_value = mock_event

        response = client.post(
            "/billing/webhooks/stripe/",
            data=b"payload",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig_test",
        )

        assert response.status_code == 500

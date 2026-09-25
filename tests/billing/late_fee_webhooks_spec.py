"""BDD specs for the late cancellation fee Checkout webhook handlers (#456, part 2).

A completed paid session marks the fee paid and emails the receipt; unpaid sessions, other
kinds and missing ids are ignored; a paid session with no fee to credit alerts the Billing
Administrators; an expired session is a no-op; both handlers sit in the billing fan-in.
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from factory.django import mute_signals

from billing import views as billing_views
from billing import webhook_handlers
from billing.models import LateCancellationFee
from membership.models import AdminCapability
from tests.billing.factories import LateCancellationFeeFactory
from tests.membership.factories import MemberFactory, MembershipPlanFactory, OrientationBookingFactory

pytestmark = pytest.mark.django_db


def _event(kind: str = "late_cancel_fee", *, fee_id=None, payment_status: str = "paid", **extra):
    session = {
        "id": "cs_fee_hook_1",
        "metadata": {"kind": kind},
        "payment_status": payment_status,
        "payment_intent": "pi_fee_hook_1",
        "amount_total": 1500,
        "customer_email": "payer@example.com",
        **extra,
    }
    if fee_id is not None:
        session["metadata"]["fee_id"] = str(fee_id)
    return {"data": {"object": session}}


def _unpaid_fee(username: str = "hook_member") -> LateCancellationFee:
    MembershipPlanFactory()
    member = User.objects.create_user(username=username, email=f"{username}@example.com").member
    return LateCancellationFeeFactory(orientation_booking=OrientationBookingFactory(member=member))


def _billing_approver():
    member = MemberFactory(_pre_signup_email="fee-billing-approver@example.com")
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"fba{member.pk}", email="fee-billing-approver@example.com")
    member.user = user
    member.save(update_fields=["user"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


def describe_handle_late_fee_checkout_completed():
    def it_marks_the_fee_paid_and_emails_the_receipt():
        fee = _unpaid_fee()
        mail.outbox.clear()

        webhook_handlers.handle_late_fee_checkout_completed(_event(fee_id=fee.pk))

        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID
        assert fee.stripe_payment_id == "pi_fee_hook_1"
        assert fee.stripe_session_id == "cs_fee_hook_1"
        assert [m.subject for m in mail.outbox] == ["Your late cancellation fee is paid"]

    def it_ignores_an_unpaid_session():
        fee = _unpaid_fee("hook_unpaid")
        webhook_handlers.handle_late_fee_checkout_completed(_event(fee_id=fee.pk, payment_status="unpaid"))
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.UNPAID

    def it_ignores_other_kinds():
        fee = _unpaid_fee("hook_kind")
        webhook_handlers.handle_late_fee_checkout_completed(_event("orientation_booking", fee_id=fee.pk))
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.UNPAID

    def it_warns_and_stops_on_a_session_with_no_fee_id(caplog):
        with caplog.at_level(logging.WARNING, logger="billing.webhook_handlers"):
            webhook_handlers.handle_late_fee_checkout_completed(_event())
        assert "missing fee_id" in caplog.text

    def it_is_quiet_on_a_redelivery_for_a_fee_already_paid():
        _billing_approver()
        fee = _unpaid_fee("hook_redeliver")
        webhook_handlers.handle_late_fee_checkout_completed(_event(fee_id=fee.pk))
        mail.outbox.clear()
        webhook_handlers.handle_late_fee_checkout_completed(_event(fee_id=fee.pk))
        assert mail.outbox == []

    def it_logs_and_alerts_billing_admins_on_a_missing_fee(caplog):
        _billing_approver()
        mail.outbox.clear()
        with caplog.at_level(logging.ERROR, logger="billing.webhook_handlers"):
            webhook_handlers.handle_late_fee_checkout_completed(_event(fee_id=999999))
        assert "has no fee 999999" in caplog.text
        alert = mail.outbox[0]
        assert alert.to == ["fee-billing-approver@example.com"]
        assert alert.subject == "Orphaned late cancellation fee payment needs a manual refund"
        assert "Late cancellation fee 999999 no longer exists." in alert.body
        assert "The member paid $15.00" in alert.body
        assert "https://dashboard.stripe.com/payments/pi_fee_hook_1" in alert.body
        assert "Customer email: payer@example.com" in alert.body
        assert alert.alternatives, "the alert must ship an HTML body in the branded shell"

    def it_alerts_when_the_fee_was_resolved_before_the_money_landed(caplog):
        _billing_approver()
        fee = LateCancellationFeeFactory(status=LateCancellationFee.Status.WAIVED)
        mail.outbox.clear()
        with caplog.at_level(logging.ERROR, logger="billing.webhook_handlers"):
            webhook_handlers.handle_late_fee_checkout_completed(
                _event(
                    fee_id=fee.pk,
                    payment_intent="",
                    amount_total=None,
                    customer_email="",
                    customer_details={"email": "cd@example.com"},
                )
            )
        assert "which is waived, not paid" in caplog.text
        alert = mail.outbox[0]
        assert f"Late cancellation fee {fee.pk} was resolved before the payment landed." in alert.body
        assert "The member paid an unknown amount" in alert.body
        assert "Stripe payment: https://dashboard.stripe.com/payments\n" in alert.body
        assert "Customer email: cd@example.com" in alert.body
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.WAIVED

    def it_sends_no_alert_when_nobody_holds_the_billing_capability():
        mail.outbox.clear()
        webhook_handlers.handle_late_fee_checkout_completed(_event(fee_id=999999))
        assert mail.outbox == []


def describe_handle_late_fee_checkout_expired():
    def it_leaves_the_fee_unpaid(caplog):
        fee = LateCancellationFeeFactory(stripe_session_id="cs_fee_hook_1")
        with caplog.at_level(logging.INFO, logger="billing.webhook_handlers"):
            webhook_handlers.handle_late_fee_checkout_expired(_event(fee_id=fee.pk, payment_status="unpaid"))
        assert "the fee stays unpaid" in caplog.text
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.UNPAID
        assert fee.stripe_session_id == "cs_fee_hook_1"

    def it_ignores_other_kinds(caplog):
        with caplog.at_level(logging.INFO, logger="billing.webhook_handlers"):
            webhook_handlers.handle_late_fee_checkout_expired(_event("orientation_booking", payment_status="unpaid"))
        assert "late fee session" not in caplog.text


def describe_billing_fan_in():
    def it_registers_both_handlers():
        assert webhook_handlers.handle_late_fee_checkout_completed in billing_views._CHECKOUT_COMPLETED_HANDLERS
        assert webhook_handlers.handle_late_fee_checkout_expired in billing_views._CHECKOUT_EXPIRED_HANDLERS

    def it_reaches_the_fee_through_the_shared_dispatch():
        fee = _unpaid_fee("hook_dispatch")
        billing_views._dispatch_checkout_completed(_event(fee_id=fee.pk))
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID

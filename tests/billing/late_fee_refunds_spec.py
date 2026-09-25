"""BDD specs for refunding a paid late cancellation fee through the shared engine (#456, part 3).

``LateCancellationFee`` as a ``RefundableSource`` (the intent, the remainder, the state, the
receipt context), full and partial refunds through ``issue_refund`` with Stripe mocked the
way the orientation refund specs do, REFUNDED and its activity row on a full refund only,
the failure alert's admin link, the ``charge.refunded`` reconciliation finding the fee by
payment intent, and the ledger constraint refusing two sources or none.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from billing import late_fees, refunds
from billing.exceptions import RefundError, RefundNotPossibleError
from billing.models import LateCancellationFee, PaymentRefund
from classes.factories import RegistrationFactory
from classes.webhook_handlers import _refundable_source_for_payment_intent, handle_charge_refunded
from core.models import SiteActivity
from membership.models import AdminCapability, Member
from tests.billing.factories import LateCancellationFeeFactory, PaymentRefundFactory
from tests.membership.factories import (
    EquipmentReservationFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
)

pytestmark = pytest.mark.django_db


def _member_with_user(username: str) -> Member:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com").member


def _paid_fee(username: str = "fee_payer", **overrides: Any) -> LateCancellationFee:
    """A $15.00 fee paid through Stripe, on a cancelled orientation booking by a user-linked member."""
    defaults: dict[str, Any] = {
        "orientation_booking": OrientationBookingFactory(member=_member_with_user(username)),
        "status": LateCancellationFee.Status.PAID,
        "stripe_payment_id": "pi_fee_ref_1",
        "paid_at": timezone.now(),
    }
    defaults.update(overrides)
    return LateCancellationFeeFactory(**defaults)


def _stripe_result(refund_id: str = "re_fee_1", status: str = "succeeded") -> dict[str, Any]:
    return {"id": refund_id, "status": status, "amount": 0}


def _refund(fee: LateCancellationFee, **overrides: Any) -> PaymentRefund:
    defaults: dict[str, Any] = {"registration": None, "late_fee": fee, "amount_cents": 1500}
    defaults.update(overrides)
    return PaymentRefundFactory(**defaults)


def _billing_approver(email: str = "fee-refund-billing@example.com") -> Member:
    member = MemberFactory(_pre_signup_email=email)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"frb_{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


def describe_refundable_source():
    def it_refunds_against_the_payment_intent_that_paid_the_fee():
        fee = _paid_fee()
        assert fee.refund_payment_intent_id == "pi_fee_ref_1"
        assert LateCancellationFeeFactory().refund_payment_intent_id == ""

    def it_counts_only_succeeded_refunds_against_the_remainder():
        fee = _paid_fee()
        _refund(fee, amount_cents=500, status=PaymentRefund.Status.SUCCEEDED)
        _refund(fee, amount_cents=400, status=PaymentRefund.Status.FAILED)
        _refund(fee, amount_cents=300, status=PaymentRefund.Status.PENDING)
        assert fee.amount_refunded_cents == 500
        assert fee.refundable_cents == 1000

    def it_derives_the_refund_state_like_the_booking():
        fee = _paid_fee()
        assert fee.refund_state == "none"
        failed = _refund(fee, amount_cents=1500, status=PaymentRefund.Status.FAILED)
        assert fee.refund_state == "failed"
        failed.delete()
        _refund(fee, amount_cents=500, status=PaymentRefund.Status.SUCCEEDED)
        assert fee.refund_state == "partial"
        _refund(fee, amount_cents=1000, status=PaymentRefund.Status.SUCCEEDED)
        assert fee.refund_state == "full"

    def it_is_not_failed_once_a_later_refund_covers_the_amount():
        fee = _paid_fee("fee_covered")
        _refund(fee, amount_cents=1500, status=PaymentRefund.Status.FAILED)
        _refund(fee, amount_cents=1500, status=PaymentRefund.Status.SUCCEEDED)
        assert fee.refund_state == "full"

    def it_builds_the_receipt_context_around_the_fees_own_page():
        fee = _paid_fee("fee_ctx")
        ctx = fee.refund_receipt_context()
        assert ctx["item_title"] == f"Late cancellation fee: {fee.item_label}"
        assert ctx["recipient_email"] == fee.member.primary_email
        assert ctx["recipient_name"] == fee.member.display_name
        assert ctx["payer_name"] == fee.member.display_name
        assert ctx["member"] == fee.member
        assert ctx["manage_url"] == late_fees.pay_url(fee)
        assert ctx["in_app_url"] == reverse("hub_late_fee_detail", args=[fee.pk])
        assert ctx["manage_url"].startswith("http")


def describe_issue_refund():
    @patch("billing.stripe_utils.create_refund")
    def it_refunds_in_full_marks_the_fee_refunded_and_logs_it(mock_create, django_capture_on_commit_callbacks):
        fee = _paid_fee("fee_full")
        actor = User.objects.create_user(username="fee_refunder", email="refunder@example.com")
        mock_create.return_value = _stripe_result("re_fee_full")
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            refund = fee.issue_refund(reason="goodwill", actor=actor)

        assert refund.late_fee == fee
        assert refund.registration is None
        assert refund.orientation_booking is None
        assert refund.source_kind == "late_fee"
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.amount_cents == 1500
        assert refund.stripe_refund_id == "re_fee_full"
        kwargs = mock_create.call_args.kwargs
        assert kwargs["payment_intent_id"] == "pi_fee_ref_1"
        assert kwargs["amount_cents"] == 1500
        assert kwargs["idempotency_key"] == f"pay-refund-{refund.pk}-a1"
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.REFUNDED
        assert fee.is_refunded is True
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.LATE_FEE_REFUNDED)
        assert row.actor == actor
        assert row.target == fee
        assert row.payload == {"amount_cents": 1500, "item": fee.item_label, "reason": "goodwill"}
        receipt = next(m for m in mail.outbox if fee.member.primary_email in m.to)
        assert "Late cancellation fee" in receipt.subject
        assert "$15.00" in receipt.body
        assert late_fees.pay_url(fee) in receipt.body
        assert "[missing:" not in receipt.body

    @patch("billing.stripe_utils.create_refund")
    def it_lifts_nothing_because_a_paid_fee_never_blocked(mock_create):
        fee = _paid_fee("fee_noblock")
        mock_create.return_value = _stripe_result("re_fee_noblock")
        assert late_fees.unpaid_fee_for(fee.member) is None
        fee.issue_refund()
        assert late_fees.unpaid_fee_for(fee.member) is None

    @patch("billing.stripe_utils.create_refund")
    def it_keeps_a_partially_refunded_fee_paid_with_no_activity_row(mock_create, django_capture_on_commit_callbacks):
        fee = _paid_fee("fee_part")
        mock_create.return_value = _stripe_result("re_fee_part")
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            refund = fee.issue_refund(amount_cents=500, reason="half")

        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.amount_cents == 500
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID
        assert fee.refund_state == "partial"
        assert fee.refundable_cents == 1000
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_REFUNDED).exists()
        receipt = next(m for m in mail.outbox if fee.member.primary_email in m.to)
        assert "$5.00" in receipt.body

    def it_refuses_an_unpaid_or_waived_fee():
        for status in (LateCancellationFee.Status.UNPAID, LateCancellationFee.Status.WAIVED):
            fee = LateCancellationFeeFactory(status=status)
            with pytest.raises(RefundNotPossibleError, match="No Stripe payment on file."):
                fee.issue_refund()
        assert not PaymentRefund.objects.exists()

    @patch("billing.stripe_utils.create_refund")
    def it_stamps_a_stripe_rejection_failed_and_leaves_the_fee_paid(mock_create):
        fee = _paid_fee("fee_fail")
        mock_create.side_effect = stripe.StripeError("The charge is disputed.")
        with pytest.raises(RefundError, match="disputed"):
            fee.issue_refund()
        refund = fee.refunds.get()
        assert refund.status == PaymentRefund.Status.FAILED
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.PAID
        assert fee.refund_state == "failed"
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_REFUNDED).exists()

    @patch("billing.stripe_utils.create_refund")
    def it_logs_the_refunded_row_once_when_a_retry_re_succeeds(mock_create):
        fee = _paid_fee("fee_retry")
        mock_create.return_value = _stripe_result("re_fee_first")
        refund = fee.issue_refund()
        refunds.apply_refund_update(refund, stripe_status="failed", failure_reason="lost in transit")
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.REFUNDED  # a late failure never unwinds the status
        mock_create.return_value = _stripe_result("re_fee_second")

        refunds.retry_refund(refund)

        refund.refresh_from_db()
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.attempt == 2
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_REFUNDED).count() == 1


def describe_refund_failed_alert():
    def it_sends_the_billing_administrators_to_the_ledgers_failed_late_fee_rows():
        _billing_approver()
        fee = _paid_fee("fee_alert")
        refund = _refund(fee, amount_cents=1500, status=PaymentRefund.Status.PENDING)
        mail.outbox.clear()

        refunds.apply_refund_update(refund, stripe_status="failed", failure_reason="bank said no")

        alert = next(m for m in mail.outbox if "fee-refund-billing@example.com" in m.to)
        expected = late_fees._absolute_url(
            f"{reverse('billing_admin_dashboard')}?tab=payments&source=late_fee&status=failed"
        )
        assert expected in alert.body
        assert f"Late cancellation fee: {fee.item_label}" in alert.body


def describe_dashboard_refund_reconciliation():
    def _event(payment_intent: str, refund_items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"data": {"object": {"payment_intent": payment_intent, "refunds": {"data": refund_items}}}}

    def it_finds_a_late_fee_by_its_payment_intent():
        fee = _paid_fee("fee_lookup", stripe_payment_id="pi_fee_lookup")
        assert _refundable_source_for_payment_intent("pi_fee_lookup") == fee
        assert _refundable_source_for_payment_intent("pi_nobody") is None

    def it_prefers_a_registration_carrying_the_same_intent():
        registration = RegistrationFactory(stripe_payment_id="pi_shared")
        _paid_fee("fee_shared", stripe_payment_id="pi_shared")
        assert _refundable_source_for_payment_intent("pi_shared") == registration

    def it_reconciles_a_dashboard_refund_into_the_ledger_and_marks_the_fee_refunded(
        django_capture_on_commit_callbacks,
    ):
        fee = _paid_fee("fee_dash", stripe_payment_id="pi_fee_dash")
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            handle_charge_refunded(_event("pi_fee_dash", [{"id": "re_dash_1", "amount": 1500, "status": "succeeded"}]))

        refund = PaymentRefund.objects.get(stripe_refund_id="re_dash_1")
        assert refund.late_fee == fee
        assert refund.source == PaymentRefund.Source.STRIPE_DASHBOARD
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.initiated_by is None
        fee.refresh_from_db()
        assert fee.status == LateCancellationFee.Status.REFUNDED
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.LATE_FEE_REFUNDED)
        assert row.actor is None
        assert any(fee.member.primary_email in m.to for m in mail.outbox)

    def it_is_idempotent_under_re_delivery(django_capture_on_commit_callbacks):
        fee = _paid_fee("fee_redeliver", stripe_payment_id="pi_fee_redeliver")
        event = _event("pi_fee_redeliver", [{"id": "re_dash_2", "amount": 1500, "status": "succeeded"}])
        with django_capture_on_commit_callbacks(execute=True):
            handle_charge_refunded(event)
            handle_charge_refunded(event)
        assert fee.refunds.count() == 1
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.LATE_FEE_REFUNDED).count() == 1


def describe_PaymentRefund_one_source_constraint():
    def it_refuses_a_late_fee_beside_a_registration_or_a_booking():
        fee = _paid_fee("fee_ck")
        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentRefund.objects.create(late_fee=fee, registration=RegistrationFactory(), amount_cents=100)
        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentRefund.objects.create(
                late_fee=fee, orientation_booking=OrientationBookingFactory(), amount_cents=100
            )

    def it_refuses_a_row_with_no_source():
        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentRefund.objects.create(amount_cents=100)

    def it_accepts_a_late_fee_alone_and_scopes_for_source_to_it():
        fee = _paid_fee("fee_alone")
        refund = _refund(fee)
        _refund(LateCancellationFeeFactory(for_reservation=True, reservation=EquipmentReservationFactory()))
        assert refund.source_object == fee
        assert list(PaymentRefund.objects.for_source(fee)) == [refund]
        assert refunds.source_field_name(fee) == "late_fee"

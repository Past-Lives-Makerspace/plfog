"""BDD specs for billing.refunds — the shared refund service (issue, retry, transitions)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import stripe
from django.core import mail

from billing import refunds
from billing.exceptions import (
    AlreadyRefundedError,
    InvalidRefundAmountError,
    RefundError,
    RefundNotPossibleError,
)
from billing.models import PaymentRefund
from classes.factories import ClassOfferingFactory, RegistrationFactory, UserFactory
from classes.models import CmsActivity, Registration
from membership.models import OrientationBooking
from tests.billing.factories import PaymentRefundFactory
from tests.membership.factories import OrientationBookingFactory

pytestmark = pytest.mark.django_db


def _paid_registration(**overrides):
    defaults = {
        "status": Registration.Status.CONFIRMED,
        "amount_paid_cents": 5000,
        "stripe_payment_id": "pi_service_1",
        "email": "payer@example.com",
        "first_name": "Robin",
        "last_name": "Vale",
    }
    defaults.update(overrides)
    return RegistrationFactory(**defaults)


def _stripe_result(refund_id: str = "re_srv_1", status: str = "succeeded") -> dict:
    return {"id": refund_id, "status": status, "amount": 0}


def _emails_to(address: str) -> list:
    return [m for m in mail.outbox if address in m.to]


def describe_issue_refund():
    def describe_guards():
        def it_raises_when_no_stripe_payment_on_file():
            registration = _paid_registration(stripe_payment_id="")
            with pytest.raises(RefundNotPossibleError, match="No Stripe payment on file."):
                refunds.issue_refund(registration)

        def it_raises_when_the_registration_never_took_money():
            registration = _paid_registration(amount_paid_cents=0)
            with pytest.raises(AlreadyRefundedError):
                refunds.issue_refund(registration)

        def it_raises_when_already_fully_refunded():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
            with pytest.raises(AlreadyRefundedError, match="Nothing left to refund."):
                refunds.issue_refund(registration)

        def it_rejects_a_zero_amount():
            registration = _paid_registration()
            with pytest.raises(InvalidRefundAmountError):
                refunds.issue_refund(registration, amount_cents=0)

        def it_rejects_an_amount_over_the_refundable_remainder():
            registration = _paid_registration()
            PaymentRefundFactory(registration=registration, amount_cents=3000, status=PaymentRefund.Status.SUCCEEDED)
            with pytest.raises(InvalidRefundAmountError):
                refunds.issue_refund(registration, amount_cents=2001)

        @patch("billing.stripe_utils.create_refund")
        def it_allows_a_refunded_registration_whose_covering_refund_failed(mock_create):
            # Blocker-1 regression: refundability IS the guard — no status list. A
            # registration at Status.REFUNDED whose refund later FAILED must be
            # refundable again, or the Retry path is a dead end.
            registration = _paid_registration(status=Registration.Status.REFUNDED)
            PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.FAILED)
            mock_create.return_value = _stripe_result("re_again_1")

            refund = refunds.issue_refund(registration)

            assert refund.status == PaymentRefund.Status.SUCCEEDED

    def describe_success():
        @patch("billing.stripe_utils.create_refund")
        def it_issues_a_full_refund_and_runs_the_bookkeeping(mock_create):
            offering = ClassOfferingFactory(capacity=1)
            registration = _paid_registration(class_offering=offering)
            waiting = RegistrationFactory(
                class_offering=offering, status=Registration.Status.WAITLISTED, email="waiting@example.com"
            )
            mock_create.return_value = _stripe_result("re_full_9")
            actor = UserFactory(username="refunder@example.com")

            refund = refunds.issue_refund(registration, reason="rained out", actor=actor)

            assert refund.status == PaymentRefund.Status.SUCCEEDED
            assert refund.stripe_refund_id == "re_full_9"
            assert refund.amount_cents == 5000
            assert refund.settled_at is not None
            assert refund.initiated_by == actor
            assert refund.reason == "rained out"
            registration.refresh_from_db()
            assert registration.status == Registration.Status.REFUNDED
            waiting.refresh_from_db()
            assert waiting.waitlist_notified_at is not None
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["payment_intent_id"] == "pi_service_1"
            assert call_kwargs["amount_cents"] == 5000
            assert call_kwargs["idempotency_key"] == f"pay-refund-{refund.pk}-a1"

        @patch("billing.stripe_utils.create_refund")
        def it_emails_the_receipt_with_the_actual_partial_amount(mock_create):
            registration = _paid_registration(class_offering=ClassOfferingFactory(title="Bowl Turning"))
            mock_create.return_value = _stripe_result("re_part_9")
            mail.outbox.clear()

            refunds.issue_refund(registration, amount_cents=2000)

            registration.refresh_from_db()
            assert registration.status == Registration.Status.CONFIRMED  # still attending
            assert registration.refund_state == "partial"
            sent = _emails_to("payer@example.com")
            assert len(sent) == 1
            assert "Bowl Turning" in sent[0].subject
            assert "$20.00" in sent[0].body
            assert "[missing:" not in sent[0].body

        @patch("billing.stripe_utils.create_refund")
        def it_delivers_a_second_partial_receipt(mock_create):
            registration = _paid_registration()
            mock_create.side_effect = [_stripe_result("re_p1"), _stripe_result("re_p2")]
            mail.outbox.clear()

            refunds.issue_refund(registration, amount_cents=1000)
            refunds.issue_refund(registration, amount_cents=1500)

            sent = _emails_to("payer@example.com")
            assert len(sent) == 2  # unique period per refund row — no dedupe swallow
            assert "$15.00" in sent[1].body

        @patch("billing.stripe_utils.create_refund")
        def it_logs_a_partial_refund_activity_without_flipping_status(mock_create):
            registration = _paid_registration()
            mock_create.return_value = _stripe_result("re_part_act")

            refunds.issue_refund(registration, amount_cents=500)

            row = CmsActivity.objects.get(kind=CmsActivity.Kind.REGISTRATION_PARTIAL_REFUND, registration=registration)
            assert row.payload == {"amount_cents": 500}
            assert not CmsActivity.objects.filter(
                kind=CmsActivity.Kind.REGISTRATION_REFUNDED, registration=registration
            ).exists()

    def describe_stripe_failure():
        @patch("billing.stripe_utils.create_refund")
        def it_stamps_the_row_failed_and_reraises(mock_create):
            registration = _paid_registration()
            mock_create.side_effect = stripe.StripeError("The charge is disputed.")
            mail.outbox.clear()

            with pytest.raises(RefundError, match="disputed"):
                refunds.issue_refund(registration)

            refund = registration.refunds.get()
            assert refund.status == PaymentRefund.Status.FAILED
            assert "disputed" in refund.failure_reason
            assert refund.settled_at is not None
            registration.refresh_from_db()
            assert registration.status == Registration.Status.CONFIRMED  # no bookkeeping happened
            assert _emails_to("payer@example.com") == []
            assert CmsActivity.objects.filter(
                kind=CmsActivity.Kind.REGISTRATION_REFUND_FAILED, registration=registration
            ).exists()

        @patch("billing.stripe_utils.create_refund")
        def it_treats_a_failed_stripe_status_as_a_failure(mock_create):
            registration = _paid_registration()
            mock_create.return_value = _stripe_result("re_bad", status="failed")

            with pytest.raises(RefundError, match="reported the refund as failed"):
                refunds.issue_refund(registration)

            refund = registration.refunds.get()
            assert refund.status == PaymentRefund.Status.FAILED
            assert refund.stripe_refund_id == "re_bad"

        @patch("billing.stripe_utils.create_refund")
        def it_leaves_a_pending_stripe_status_pending(mock_create):
            registration = _paid_registration()
            mock_create.return_value = _stripe_result("re_slow", status="pending")
            mail.outbox.clear()

            refund = refunds.issue_refund(registration)

            assert refund.status == PaymentRefund.Status.PENDING
            assert refund.stripe_refund_id == "re_slow"
            assert refund.settled_at is None
            assert _emails_to("payer@example.com") == []  # receipt waits for the succeeded transition


def describe_succeeded_transition():
    def it_fires_side_effects_exactly_once_when_a_pending_row_flips():
        registration = _paid_registration()
        refund = PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.PENDING)
        mail.outbox.clear()

        refunds.apply_refund_update(refund, stripe_status="succeeded")
        refunds.apply_refund_update(refund, stripe_status="succeeded")

        refund.refresh_from_db()
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        registration.refresh_from_db()
        assert registration.status == Registration.Status.REFUNDED
        assert len(_emails_to("payer@example.com")) == 1  # second pass fires nothing

    def it_ignores_an_unknown_stripe_status():
        registration = _paid_registration()
        refund = PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.PENDING)

        refunds.apply_refund_update(refund, stripe_status="requires_action")

        refund.refresh_from_db()
        assert refund.status == PaymentRefund.Status.PENDING


def describe_retry_refund():
    def it_refuses_a_row_that_is_not_failed():
        pending = PaymentRefundFactory(
            registration=_paid_registration(), status=PaymentRefund.Status.PENDING, amount_cents=100
        )
        with pytest.raises(RefundError, match="Only a failed refund can be retried."):
            refunds.retry_refund(pending)

    @patch("billing.stripe_utils.create_refund")
    def it_bumps_the_attempt_and_uses_a_fresh_idempotency_key(mock_create):
        registration = _paid_registration()
        failed = PaymentRefundFactory(
            registration=registration,
            amount_cents=5000,
            status=PaymentRefund.Status.FAILED,
            failure_reason="card_declined",
        )
        mock_create.return_value = _stripe_result("re_retry_1")
        actor = UserFactory(username="retryer@example.com")

        result = refunds.retry_refund(failed, actor=actor)

        assert result.pk == failed.pk  # same ledger row, N attempts — that is the truth
        assert result.attempt == 2
        assert result.status == PaymentRefund.Status.SUCCEEDED
        assert result.initiated_by == actor
        assert mock_create.call_args.kwargs["idempotency_key"] == f"pay-refund-{failed.pk}-a2"

    @patch("billing.stripe_utils.create_refund")
    def it_records_a_failed_retry_with_the_new_reason(mock_create):
        failed = PaymentRefundFactory(
            registration=_paid_registration(),
            amount_cents=5000,
            status=PaymentRefund.Status.FAILED,
            failure_reason="old reason",
        )
        mock_create.side_effect = stripe.StripeError("Still no good.")

        with pytest.raises(RefundError, match="Still no good."):
            refunds.retry_refund(failed)

        failed.refresh_from_db()
        assert failed.status == PaymentRefund.Status.FAILED
        assert failed.attempt == 2
        assert "Still no good." in failed.failure_reason

    @patch("billing.stripe_utils.create_refund")
    def it_does_not_double_promote_the_waitlist_on_a_re_success(mock_create):
        # Pinned by the spec: on a Retry that re-succeeds on an already-REFUNDED
        # registration, mark_refunded runs again but promote_next_from_waitlist is
        # guarded by previously_held_a_spot — a REFUNDED row no longer holds one.
        offering = ClassOfferingFactory(capacity=1)
        registration = _paid_registration(class_offering=offering)
        first_waiting = RegistrationFactory(
            class_offering=offering, status=Registration.Status.WAITLISTED, email="first@example.com"
        )
        mock_create.return_value = _stripe_result("re_first")
        refund = refunds.issue_refund(registration)  # promotes first_waiting
        refunds.apply_refund_update(refund, stripe_status="failed", failure_reason="lost in transit")
        second_waiting = RegistrationFactory(
            class_offering=offering, status=Registration.Status.WAITLISTED, email="second@example.com"
        )
        mock_create.return_value = _stripe_result("re_second")

        refunds.retry_refund(refund)

        first_waiting.refresh_from_db()
        second_waiting.refresh_from_db()
        assert first_waiting.waitlist_notified_at is not None
        assert second_waiting.waitlist_notified_at is None  # no double promotion
        registration.refresh_from_db()
        assert registration.status == Registration.Status.REFUNDED


def describe_receipt_relocation():
    def it_no_longer_emails_on_the_save_transition():
        registration = _paid_registration()
        mail.outbox.clear()

        registration.status = Registration.Status.REFUNDED
        registration.save()

        assert mail.outbox == []  # the receipt lives with the PaymentRefund row now
        assert CmsActivity.objects.filter(
            kind=CmsActivity.Kind.REGISTRATION_REFUNDED, registration=registration
        ).exists()  # the audit log stays put


def describe_registration_issue_refund_delegate():
    @patch("billing.stripe_utils.create_refund")
    def it_delegates_to_the_shared_service(mock_create):
        registration = _paid_registration()
        mock_create.return_value = _stripe_result("re_delegate")
        actor = UserFactory(username="delegator@example.com")

        refund = registration.issue_refund(amount_cents=1200, reason="overcharge", actor=actor)

        assert refund.registration == registration
        assert refund.amount_cents == 1200
        assert refund.reason == "overcharge"
        assert refund.initiated_by == actor


def describe_refund_state():
    def it_is_none_with_no_refunds():
        assert _paid_registration().refund_state == "none"

    def it_is_partial_when_some_money_is_back():
        registration = _paid_registration()
        PaymentRefundFactory(registration=registration, amount_cents=1000, status=PaymentRefund.Status.SUCCEEDED)
        assert registration.refund_state == "partial"
        assert registration.refundable_cents == 4000

    def it_is_full_when_everything_is_back():
        registration = _paid_registration()
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        assert registration.refund_state == "full"

    def it_is_failed_when_the_latest_attempt_failed_with_money_outstanding():
        registration = _paid_registration()
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.FAILED)
        assert registration.refund_state == "failed"

    def it_is_not_failed_once_a_later_refund_covers_the_amount():
        registration = _paid_registration()
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.FAILED)
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        assert registration.refund_state == "full"


def describe_orientation_seam():
    """The engine is source-neutral: an orientation-side row exercises the same
    lifecycle through the RefundableSource protocol the companion spec implements."""

    @pytest.fixture
    def orientation_refund(monkeypatch):
        booking = OrientationBookingFactory()
        monkeypatch.setattr(
            OrientationBooking,
            "refund_receipt_context",
            lambda self: {
                "item_title": "Makerspace orientation",
                "recipient_email": "oriented@example.com",
                "recipient_name": "Casey",
                "payer_name": "Casey Payer",
                "member": None,
                "manage_url": "https://pastlives.example/orientations/manage/1/",
                "in_app_url": "/orientations/",
            },
            raising=False,
        )
        return PaymentRefundFactory(
            registration=None, orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.PENDING
        )

    def it_emails_the_receipt_for_a_partial_orientation_refund(orientation_refund, monkeypatch):
        monkeypatch.setattr(OrientationBooking, "refundable_cents", property(lambda self: 500), raising=False)
        mail.outbox.clear()

        refunds.apply_refund_update(orientation_refund, stripe_status="succeeded")

        sent = _emails_to("oriented@example.com")
        assert len(sent) == 1
        assert "Makerspace orientation" in sent[0].subject
        assert "$15.00" in sent[0].body

    def it_runs_full_refund_bookkeeping_through_the_protocol(orientation_refund, monkeypatch):
        calls: list[tuple[str, object]] = []
        monkeypatch.setattr(OrientationBooking, "refundable_cents", property(lambda self: 0), raising=False)
        monkeypatch.setattr(
            OrientationBooking,
            "on_fully_refunded",
            lambda self, reason, actor: calls.append((reason, actor)),
            raising=False,
        )

        refunds.apply_refund_update(orientation_refund, stripe_status="succeeded")

        assert calls == [("", None)]

    def it_alerts_with_the_manage_url_when_no_admin_page_exists(orientation_refund, settings):
        from django.contrib.auth.models import User
        from django.db.models.signals import post_save
        from factory.django import mute_signals

        from membership.models import AdminCapability
        from tests.membership.factories import MemberFactory

        member = MemberFactory(_pre_signup_email="billingadmin@example.com")
        with mute_signals(post_save):
            user = User.objects.create_user(username="billingadmin", email="billingadmin@example.com")
        member.user = user
        member.save(update_fields=["user"])
        member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
        mail.outbox.clear()

        refunds.apply_refund_update(orientation_refund, stripe_status="failed", failure_reason="bank said no")

        sent = _emails_to("billingadmin@example.com")
        assert len(sent) == 1
        assert "https://pastlives.example/orientations/manage/1/" in sent[0].body

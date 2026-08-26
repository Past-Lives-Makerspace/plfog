"""BDD specs for the refund reconciliation webhooks — charge.refunded / refund.updated."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from factory.django import mute_signals

from billing.models import PaymentRefund
from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import Registration
from classes.webhook_handlers import handle_charge_refunded, handle_refund_updated
from membership.models import AdminCapability
from tests.billing.factories import PaymentRefundFactory
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db


def _paid_registration(**overrides):
    defaults = {
        "status": Registration.Status.CONFIRMED,
        "amount_paid_cents": 5000,
        "stripe_payment_id": "pi_hook_1",
        "email": "payer@example.com",
        "first_name": "Robin",
        "last_name": "Vale",
    }
    defaults.update(overrides)
    return RegistrationFactory(**defaults)


def _charge_refunded_event(payment_intent: str, refunds: list[dict]) -> dict:
    return {"data": {"object": {"payment_intent": payment_intent, "refunds": {"data": refunds}}}}


def _refund_item(refund_id: str = "re_hook_1", amount: int = 5000, status: str = "succeeded") -> dict:
    return {"id": refund_id, "amount": amount, "status": status}


def _refund_updated_event(refund_id: str, status: str, failure_reason: str = "") -> dict:
    obj: dict = {"id": refund_id, "status": status}
    if failure_reason:
        obj["failure_reason"] = failure_reason
    return {"data": {"object": obj}}


def _seed_billing_approver(email: str = "billing@example.com"):
    member = MemberFactory(_pre_signup_email=email)
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"biller_{member.pk}", email=email)
    member.user = user
    member.save(update_fields=["user"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


def _emails_to(address: str) -> list:
    return [m for m in mail.outbox if address in m.to]


def describe_handle_charge_refunded():
    def it_reconciles_a_dashboard_refund_and_runs_the_bookkeeping(django_capture_on_commit_callbacks):
        registration = _paid_registration(class_offering=ClassOfferingFactory(title="Kolrosing"))
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            handle_charge_refunded(_charge_refunded_event("pi_hook_1", [_refund_item()]))

        refund = registration.refunds.get()
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.source == PaymentRefund.Source.STRIPE_DASHBOARD
        assert refund.initiated_by is None
        assert refund.amount_cents == 5000
        registration.refresh_from_db()
        assert registration.status == Registration.Status.REFUNDED
        sent = _emails_to("payer@example.com")
        assert len(sent) == 1
        assert "Kolrosing" in sent[0].subject

    def it_is_idempotent_under_stripe_re_delivery(django_capture_on_commit_callbacks):
        registration = _paid_registration()
        event = _charge_refunded_event("pi_hook_1", [_refund_item()])
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            handle_charge_refunded(event)
            handle_charge_refunded(event)

        assert registration.refunds.count() == 1
        assert len(_emails_to("payer@example.com")) == 1

    def it_does_not_duplicate_our_own_in_app_refund(django_capture_on_commit_callbacks):
        # issue_refund stamps stripe_refund_id + final status before its
        # transaction commits, so the webhook for our own refund finds the
        # stamped row and leaves it untouched — no duplicate row, no receipt.
        registration = _paid_registration()
        PaymentRefundFactory(
            registration=registration,
            stripe_refund_id="re_ours",
            amount_cents=5000,
            status=PaymentRefund.Status.SUCCEEDED,
            settled_at="2026-08-26T00:00:00Z",
        )
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            handle_charge_refunded(_charge_refunded_event("pi_hook_1", [_refund_item("re_ours")]))

        assert registration.refunds.count() == 1
        assert mail.outbox == []

    def it_flips_a_known_pending_row_and_fires_effects_once(django_capture_on_commit_callbacks):
        registration = _paid_registration()
        PaymentRefundFactory(
            registration=registration,
            stripe_refund_id="re_pending",
            amount_cents=5000,
            status=PaymentRefund.Status.PENDING,
        )
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            handle_charge_refunded(_charge_refunded_event("pi_hook_1", [_refund_item("re_pending")]))

        refund = registration.refunds.get()
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert len(_emails_to("payer@example.com")) == 1

    def it_leaves_a_non_succeeded_stripe_refund_pending():
        registration = _paid_registration()

        handle_charge_refunded(_charge_refunded_event("pi_hook_1", [_refund_item(status="pending")]))

        refund = registration.refunds.get()
        assert refund.status == PaymentRefund.Status.PENDING

    def it_fetches_the_refunds_when_the_charge_payload_embeds_none(django_capture_on_commit_callbacks):
        # Since Stripe API 2022-11-15 the Charge payload no longer embeds its
        # refunds list — the handler must fetch and reconcile, not no-op.
        registration = _paid_registration()
        mail.outbox.clear()

        with (
            patch(
                "billing.stripe_utils.list_refunds_for_payment_intent",
                return_value=[{"id": "re_fetched_1", "status": "succeeded", "amount": 5000}],
            ) as mock_list,
            django_capture_on_commit_callbacks(execute=True),
        ):
            handle_charge_refunded({"data": {"object": {"payment_intent": "pi_hook_1", "amount_refunded": 5000}}})

        assert mock_list.call_args.kwargs["payment_intent_id"] == "pi_hook_1"
        refund = registration.refunds.get()
        assert refund.stripe_refund_id == "re_fetched_1"
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.source == PaymentRefund.Source.STRIPE_DASHBOARD
        registration.refresh_from_db()
        assert registration.status == Registration.Status.REFUNDED
        assert len(_emails_to("payer@example.com")) == 1

    def it_warns_loudly_when_a_refunded_charge_yields_no_reconcilable_refunds(caplog):
        registration = _paid_registration()

        with (
            patch("billing.stripe_utils.list_refunds_for_payment_intent", return_value=[]),
            caplog.at_level(logging.WARNING),
        ):
            handle_charge_refunded({"data": {"object": {"payment_intent": "pi_hook_1", "amount_refunded": 2500}}})

        assert registration.refunds.count() == 0
        assert any("did NOT record" in record.message for record in caplog.records)

    def it_stays_quiet_when_an_unrefunded_charge_has_nothing_to_fetch(caplog):
        registration = _paid_registration()

        with (
            patch("billing.stripe_utils.list_refunds_for_payment_intent", return_value=[]),
            caplog.at_level(logging.WARNING),
        ):
            handle_charge_refunded({"data": {"object": {"payment_intent": "pi_hook_1"}}})

        assert registration.refunds.count() == 0
        assert not any("did NOT record" in record.message for record in caplog.records)

    def it_warns_loudly_on_an_unknown_payment_intent(caplog):
        _paid_registration()

        with caplog.at_level(logging.WARNING):
            handle_charge_refunded(_charge_refunded_event("pi_unknown", [_refund_item()]))

        assert PaymentRefund.objects.count() == 0
        assert any("pi_unknown" in record.message for record in caplog.records)

    def it_warns_loudly_when_the_payment_intent_is_missing(caplog):
        with caplog.at_level(logging.WARNING):
            handle_charge_refunded({"data": {"object": {}}})

        assert any("not a refundable source" in record.message for record in caplog.records)


def describe_handle_refund_updated():
    def it_flips_a_row_to_failed_and_alerts_the_billing_administrators():
        _seed_billing_approver()
        registration = _paid_registration(class_offering=ClassOfferingFactory(title="Kumiko"))
        refund = PaymentRefundFactory(
            registration=registration,
            stripe_refund_id="re_late_fail",
            amount_cents=5000,
            status=PaymentRefund.Status.SUCCEEDED,
        )
        registration.status = Registration.Status.REFUNDED
        registration.save(update_fields=["status"])
        mail.outbox.clear()

        handle_refund_updated(_refund_updated_event("re_late_fail", "failed", "expired_or_canceled_card"))

        refund.refresh_from_db()
        assert refund.status == PaymentRefund.Status.FAILED
        assert refund.failure_reason == "expired_or_canceled_card"
        assert refund.settled_at is not None
        registration.refresh_from_db()
        assert registration.status == Registration.Status.REFUNDED  # never silently unwound
        sent = _emails_to("billing@example.com")
        assert len(sent) == 1
        assert "A refund failed" in sent[0].subject
        assert "They've already received a refund receipt. Contact them after retrying." in sent[0].body
        assert "Kumiko" in sent[0].body
        assert "expired_or_canceled_card" in sent[0].body
        assert f"/classes/admin/registrations/{registration.pk}/" in sent[0].body

    def it_alerts_only_once_under_re_delivery():
        _seed_billing_approver()
        PaymentRefundFactory(
            registration=_paid_registration(),
            stripe_refund_id="re_fail_twice",
            amount_cents=5000,
            status=PaymentRefund.Status.SUCCEEDED,
        )
        mail.outbox.clear()
        event = _refund_updated_event("re_fail_twice", "failed", "lost_or_stolen_card")

        handle_refund_updated(event)
        handle_refund_updated(event)

        assert len(_emails_to("billing@example.com")) == 1

    def it_flips_a_pending_row_to_succeeded(django_capture_on_commit_callbacks):
        registration = _paid_registration()
        refund = PaymentRefundFactory(
            registration=registration,
            stripe_refund_id="re_late_ok",
            amount_cents=5000,
            status=PaymentRefund.Status.PENDING,
        )
        mail.outbox.clear()

        with django_capture_on_commit_callbacks(execute=True):
            handle_refund_updated(_refund_updated_event("re_late_ok", "succeeded"))

        refund.refresh_from_db()
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        registration.refresh_from_db()
        assert registration.status == Registration.Status.REFUNDED
        assert len(_emails_to("payer@example.com")) == 1

    def it_warns_loudly_on_an_unknown_refund_id(caplog):
        with caplog.at_level(logging.WARNING):
            handle_refund_updated(_refund_updated_event("re_never_seen", "failed", "whatever"))

        assert any("re_never_seen" in record.message for record in caplog.records)

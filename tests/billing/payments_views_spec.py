"""BDD specs for the Payments tab views — dashboard tab, table partial, CSV, refund retry."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing.models import PaymentRefund
from classes.factories import RegistrationFactory
from classes.models import Registration
from membership.models import AdminCapability, Member
from tests.billing.factories import PaymentRefundFactory

pytestmark = pytest.mark.django_db


def _login_user(client: Client, username: str) -> Member:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user.member  # type: ignore[attr-defined]


def _login_billing_approver(client: Client, username: str = "biller") -> Member:
    member = _login_user(client, username)
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


def _login_admin(client: Client, username: str = "fogadmin") -> User:
    user = User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _paid_registration(**kwargs) -> Registration:
    defaults = {
        "status": Registration.Status.CONFIRMED,
        "amount_paid_cents": 5000,
        "stripe_payment_id": "pi_test_views",
        "confirmed_at": timezone.now(),
    }
    defaults.update(kwargs)
    return RegistrationFactory(**defaults)


def describe_payments_tab():
    def it_renders_for_a_fog_admin_with_the_ledger(client: Client):
        _login_admin(client)
        _paid_registration()
        response = client.get("/billing/admin/dashboard/?tab=payments")
        assert response.status_code == 200
        assert response.context["active_tab"] == "payments"
        assert len(response.context["ledger"].rows) == 1

    def it_shows_the_payments_nav_link_to_a_billing_administrator(client: Client):
        _login_billing_approver(client)
        content = client.get("/billing/admin/dashboard/").content.decode()
        assert "?tab=payments" in content

    def it_renders_for_a_billing_administrator_without_refund_buttons(client: Client):
        _login_billing_approver(client, "biller-nobtn")
        registration = _paid_registration()
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert registration.class_offering.title in content
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) not in content
        # The payer stays plain text — no CMS link for a non-admin viewer.
        assert f"/classes/admin/registrations/{registration.pk}/" not in content

    def it_shows_the_refund_button_to_a_fog_admin(client: Client):
        _login_admin(client, "fogadmin-btn")
        registration = _paid_registration()
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "Refund" in content
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) in content
        assert f"/classes/admin/registrations/{registration.pk}/" in content

    def it_shows_the_refund_button_to_a_refunds_holding_billing_administrator(client: Client):
        member = _login_billing_approver(client, "biller-refunds")
        member.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)
        registration = _paid_registration()
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) in content

    def it_shows_retry_refund_on_a_failed_row(client: Client):
        _login_admin(client, "fogadmin-retry")
        registration = _paid_registration()
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.FAILED)
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "Retry refund" in content
        assert "Refund failed" in content

    def it_shows_the_pending_badge_with_age(client: Client):
        _login_admin(client, "fogadmin-pending")
        registration = _paid_registration()
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.PENDING)
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "Refund pending" in content

    def it_explains_tab_rows_and_links_stripe(client: Client):
        from billing.models import TabCharge
        from tests.billing.factories import TabChargeFactory

        _login_admin(client, "fogadmin-tab")
        TabChargeFactory(
            status=TabCharge.Status.SUCCEEDED,
            charged_at=timezone.now(),
            stripe_receipt_url="https://stripe.example/r",
        )
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "Tab charges are refunded in Stripe for now." in content
        assert "https://stripe.example/r" in content

    def it_shows_the_empty_state(client: Client):
        _login_admin(client, "fogadmin-empty")
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "No payments in this window." in content

    def it_shows_the_cap_banner_when_capped(client: Client, monkeypatch):
        _login_admin(client, "fogadmin-cap")
        _paid_registration()
        _paid_registration(stripe_payment_id="pi_test_views_2")
        monkeypatch.setattr("billing.payments_panel.MAX_ROWS", 1)
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "Narrow the date range." in content


def describe_payments_table_partial():
    def it_403s_a_plain_member(client: Client):
        _login_user(client, "plain-partial")
        assert client.get(reverse("billing_admin_payments_table")).status_code == 403

    def it_renders_for_a_billing_administrator(client: Client):
        _login_billing_approver(client, "biller-partial")
        _paid_registration()
        response = client.get(reverse("billing_admin_payments_table"))
        assert response.status_code == 200
        assert b"pl-table" in response.content


def describe_payments_csv():
    def it_403s_a_plain_member(client: Client):
        _login_user(client, "plain-csv")
        assert client.get(reverse("billing_admin_payments_csv")).status_code == 403

    def it_streams_the_filtered_ledger(client: Client):
        _login_admin(client, "fogadmin-csv")
        _paid_registration()
        response = client.get(reverse("billing_admin_payments_csv"))
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        body = b"".join(response.streaming_content).decode()
        assert body.startswith("Date,Source,Payer,Item,Amount,Status")


def describe_payment_refund_retry():
    def _failed_refund(**kwargs) -> PaymentRefund:
        registration = _paid_registration(**kwargs)
        return PaymentRefundFactory(
            registration=registration,
            amount_cents=5000,
            status=PaymentRefund.Status.FAILED,
            failure_reason="card_declined",
        )

    def it_403s_without_refund_authority(client: Client):
        _login_billing_approver(client, "biller-noretry")
        refund = _failed_refund()
        response = client.post(reverse("billing_payment_refund_retry", args=[refund.pk]))
        assert response.status_code == 403

    def it_rejects_a_get(client: Client):
        _login_admin(client, "fogadmin-retryget")
        refund = _failed_refund(stripe_payment_id="pi_retry_get")
        assert client.get(reverse("billing_payment_refund_retry", args=[refund.pk])).status_code == 405

    def it_404s_an_unknown_refund(client: Client):
        _login_admin(client, "fogadmin-retry404")
        assert client.post(reverse("billing_payment_refund_retry", args=[999999])).status_code == 404

    def it_retries_and_signals_refund_done_for_a_refunds_holder(client: Client):
        member = _login_user(client, "retry-holder")
        member.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)
        refund = _failed_refund(stripe_payment_id="pi_retry_ok")
        with patch(
            "billing.stripe_utils.create_refund",
            return_value={"id": "re_retry_ok", "status": "succeeded", "amount": 5000},
        ) as mock_refund:
            response = client.post(reverse("billing_payment_refund_retry", args=[refund.pk]))
        assert response.status_code == 204
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["refund-done"] is True
        assert triggers["showToast"]["type"] == "success"
        refund.refresh_from_db()
        assert refund.status == PaymentRefund.Status.SUCCEEDED
        assert refund.attempt == 2
        assert mock_refund.call_args.kwargs["idempotency_key"] == f"pay-refund-{refund.pk}-a2"

    def it_surfaces_a_stripe_rejection_loudly_without_refund_done(client: Client):
        _login_admin(client, "fogadmin-retryfail")
        refund = _failed_refund(stripe_payment_id="pi_retry_fail")
        with patch("billing.stripe_utils.create_refund", side_effect=stripe.StripeError("Still no.")):
            response = client.post(reverse("billing_payment_refund_retry", args=[refund.pk]))
        assert response.status_code == 204
        triggers = json.loads(response["HX-Trigger"])
        assert "refund-done" not in triggers
        assert triggers["showToast"]["type"] == "error"
        assert "Still no." in triggers["showToast"]["message"]
        refund.refresh_from_db()
        assert refund.status == PaymentRefund.Status.FAILED

    def it_refuses_to_retry_a_refund_that_is_not_failed(client: Client):
        _login_admin(client, "fogadmin-retrynotfailed")
        registration = _paid_registration(stripe_payment_id="pi_retry_notfailed")
        refund = PaymentRefundFactory(
            registration=registration, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED
        )
        response = client.post(reverse("billing_payment_refund_retry", args=[refund.pk]))
        assert response.status_code == 204
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["showToast"]["type"] == "error"
        assert "refund-done" not in triggers

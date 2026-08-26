"""BDD specs for the refund UI — shared modal partial, refund POST, detail Refunds card, teach portal."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import stripe
from django.urls import reverse
from django.utils import timezone

from billing.models import PaymentRefund
from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import Registration
from membership.models import AdminCapability
from tests.billing.factories import PaymentRefundFactory

pytestmark = pytest.mark.django_db


def _paid_registration(**kwargs) -> Registration:
    defaults = {
        "status": Registration.Status.CONFIRMED,
        "amount_paid_cents": 5000,
        "stripe_payment_id": "pi_test_ui",
        "confirmed_at": timezone.now(),
    }
    defaults.update(kwargs)
    return RegistrationFactory(**defaults)


def _instructor(client, username: str, slug: str):
    user = UserFactory(username=username)
    member = InstructorFactory(user=user, instructor_slug=slug)
    client.force_login(user)
    return member


def _grant_refunds(member) -> None:
    member.admin_capabilities.create(capability=AdminCapability.Capability.REFUNDS)


def describe_refund_form_partial():
    def it_403s_an_instructor_without_the_grant(client):
        member = _instructor(client, "form-noauth@example.com", "form-noauth")
        registration = _paid_registration(class_offering=ClassOfferingFactory(instructor=member, slug="fna-class"))
        response = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk]))
        assert response.status_code == 403

    def it_prefills_the_full_refundable_amount_for_an_admin(admin_user, client):
        registration = _paid_registration()
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk])).content.decode()
        assert 'value="50.00"' in content or 'value="50.0"' in content
        assert "Up to $50.00. Edit for a partial refund." in content
        assert "Paid $50.00" in content
        assert "The payer never sees this." in content

    def it_prefills_the_remainder_after_a_partial(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_partial_prefill")
        PaymentRefundFactory(registration=registration, amount_cents=2000, status=PaymentRefund.Status.SUCCEEDED)
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk])).content.decode()
        assert 'value="30.00"' in content or 'value="30.0"' in content
        assert "$20.00 already refunded" in content

    def it_shows_the_class_start_date_in_the_header(admin_user, client):
        from datetime import datetime, timezone as dt_timezone

        from classes.factories import ClassSessionFactory

        registration = _paid_registration()
        ClassSessionFactory(
            class_offering=registration.class_offering,
            starts_at=datetime(2026, 10, 7, 18, 0, tzinfo=dt_timezone.utc),
        )
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk])).content.decode()
        assert "Class starts Oct 7" in content

    def it_renders_the_retry_confirm_when_the_latest_attempt_failed(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_retry_confirm")
        failed = PaymentRefundFactory(
            registration=registration,
            amount_cents=5000,
            status=PaymentRefund.Status.FAILED,
            failure_reason="expired_card",
        )
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk])).content.decode()
        assert "Retry refund" in content
        assert "expired_card" in content
        assert reverse("billing_payment_refund_retry", args=[failed.pk]) in content
        assert 'name="amount"' not in content  # no editable fields on the retry confirm

    def it_admits_a_refunds_holder_to_any_registration(client):
        member = _instructor(client, "form-holder@example.com", "form-holder")
        _grant_refunds(member)
        registration = _paid_registration()  # someone else's class
        response = client.get(reverse("classes:admin_registration_refund_form", args=[registration.pk]))
        assert response.status_code == 200


def describe_refund_post():
    def it_issues_a_partial_refund_and_signals_refund_done(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_partial_post")
        client.force_login(admin_user)
        with patch(
            "billing.stripe_utils.create_refund",
            return_value={"id": "re_ui_partial", "status": "succeeded", "amount": 2000},
        ):
            response = client.post(
                reverse("classes:admin_registration_refund", args=[registration.pk]),
                {"amount": "20.00", "reason": "goodwill"},
            )
        assert response.status_code == 204
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["refund-done"] is True
        assert triggers["showToast"]["message"] == "Refunded $20.00."
        registration.refresh_from_db()
        assert registration.status == Registration.Status.CONFIRMED  # partial keeps the seat
        refund = registration.refunds.get()
        assert (refund.amount_cents, refund.reason) == (2000, "goodwill")
        assert refund.initiated_by == admin_user

    def it_rerenders_with_a_field_error_on_an_over_amount(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_over_post")
        client.force_login(admin_user)
        response = client.post(
            reverse("classes:admin_registration_refund", args=[registration.pk]),
            {"amount": "80.00"},
        )
        assert response.status_code == 200
        assert "Enter an amount between $0.01 and $50.00." in response.content.decode()
        assert registration.refunds.count() == 0

    def it_rerenders_with_a_field_error_on_a_missing_amount(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_missing_post")
        client.force_login(admin_user)
        response = client.post(reverse("classes:admin_registration_refund", args=[registration.pk]), {})
        assert response.status_code == 200
        assert b"This field is required" in response.content

    def it_surfaces_a_stripe_rejection_loudly_and_offers_retry(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_stripe_fail")
        client.force_login(admin_user)
        with patch("billing.stripe_utils.create_refund", side_effect=stripe.StripeError("No such payment.")):
            response = client.post(
                reverse("classes:admin_registration_refund", args=[registration.pk]),
                {"amount": "50.00"},
            )
        assert response.status_code == 200
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["showToast"]["type"] == "error"
        assert "No such payment." in triggers["showToast"]["message"]
        assert "refund-done" not in triggers
        # The FAILED audit row remains and the partial re-renders as the retry confirm.
        assert "Retry refund" in response.content.decode()
        assert registration.refunds.get().status == PaymentRefund.Status.FAILED

    def it_lets_a_refunds_holder_refund_any_registration(client):
        member = _instructor(client, "post-holder@example.com", "post-holder")
        _grant_refunds(member)
        registration = _paid_registration(stripe_payment_id="pi_holder_post")
        with patch(
            "billing.stripe_utils.create_refund",
            return_value={"id": "re_holder", "status": "succeeded", "amount": 5000},
        ):
            response = client.post(
                reverse("classes:admin_registration_refund", args=[registration.pk]),
                {"amount": "50.00"},
            )
        assert response.status_code == 204

    def it_403s_an_instructor_without_the_grant(client):
        member = _instructor(client, "post-noauth@example.com", "post-noauth")
        registration = _paid_registration(class_offering=ClassOfferingFactory(instructor=member, slug="pna-class"))
        response = client.post(
            reverse("classes:admin_registration_refund", args=[registration.pk]), {"amount": "50.00"}
        )
        assert response.status_code == 403


def describe_detail_refunds_card():
    def it_shows_history_without_internals_to_an_instructor_without_the_grant(client):
        member = _instructor(client, "card-instr@example.com", "card-instr")
        offering = ClassOfferingFactory(instructor=member, slug="card-instr-class")
        registration = _paid_registration(class_offering=offering)
        refunder = UserFactory(username="rex-refunder")
        PaymentRefundFactory(
            registration=registration,
            amount_cents=2000,
            status=PaymentRefund.Status.SUCCEEDED,
            reason="zebra-internal-note",
            initiated_by=refunder,
        )
        content = client.get(reverse("classes:admin_registration_detail", args=[registration.pk])).content.decode()
        assert "Refunds require the Refunds permission. Ask an admin." in content
        assert "$20.00" in content
        assert "zebra-internal-note" not in content
        assert "rex-refunder" not in content
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) not in content

    def it_shows_the_button_and_internals_to_a_refunds_holding_instructor(client):
        member = _instructor(client, "card-holder@example.com", "card-holder")
        _grant_refunds(member)
        offering = ClassOfferingFactory(instructor=member, slug="card-holder-class")
        registration = _paid_registration(class_offering=offering)
        PaymentRefundFactory(
            registration=registration,
            amount_cents=2000,
            status=PaymentRefund.Status.SUCCEEDED,
            reason="zebra-internal-note",
        )
        content = client.get(reverse("classes:admin_registration_detail", args=[registration.pk])).content.decode()
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) in content
        assert "zebra-internal-note" in content
        assert "Refunds require the Refunds permission." not in content

    def it_shows_retry_and_the_failure_reason_to_an_admin(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_card_failed")
        PaymentRefundFactory(
            registration=registration,
            amount_cents=5000,
            status=PaymentRefund.Status.FAILED,
            failure_reason="card_gone",
        )
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registration_detail", args=[registration.pk])).content.decode()
        assert "Retry refund" in content
        assert "card_gone" in content

    def it_hides_the_card_for_an_unpaid_registration(admin_user, client):
        registration = RegistrationFactory(status=Registration.Status.CONFIRMED, amount_paid_cents=0)
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registration_detail", args=[registration.pk])).content.decode()
        assert "Refunds require the Refunds permission." not in content
        assert reverse("classes:admin_registration_refunds_card", args=[registration.pk]) not in content

    def it_omits_the_refund_button_once_fully_refunded(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_card_full")
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED)
        client.force_login(admin_user)
        content = client.get(reverse("classes:admin_registration_detail", args=[registration.pk])).content.decode()
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) not in content
        assert "Succeeded" in content


def describe_refunds_card_partial():
    def it_serves_the_card_standalone_for_the_refresh(admin_user, client):
        registration = _paid_registration(stripe_payment_id="pi_card_partial_view")
        client.force_login(admin_user)
        response = client.get(reverse("classes:admin_registration_refunds_card", args=[registration.pk]))
        assert response.status_code == 200
        assert b"Refunds" in response.content

    def it_404s_outside_an_instructors_scope(client):
        member = _instructor(client, "cardpart-noscope@example.com", "cardpart-noscope")
        ClassOfferingFactory(instructor=member, slug="cardpart-own-class")  # passes the page gate
        registration = _paid_registration()  # someone else's class
        response = client.get(reverse("classes:admin_registration_refunds_card", args=[registration.pk]))
        assert response.status_code == 404

    def it_403s_a_member_with_no_registrations_access(client):
        _instructor(client, "cardpart-nogate@example.com", "cardpart-nogate")
        registration = _paid_registration()
        response = client.get(reverse("classes:admin_registration_refunds_card", args=[registration.pk]))
        assert response.status_code == 403


def describe_teach_portal_refunds():
    def it_hides_the_action_column_from_an_instructor_without_the_grant(client):
        member = _instructor(client, "teach-nogrant@example.com", "teach-nogrant")
        offering = ClassOfferingFactory(instructor=member, slug="teach-nogrant-class")
        registration = _paid_registration(class_offering=offering)
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "refund-modal" not in content
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) not in content
        # The teach portal stays uncluttered — no permission explainer here.
        assert "Refunds require the Refunds permission. Ask an admin." not in content

    def it_shows_the_refund_button_to_a_refunds_holding_instructor(client):
        member = _instructor(client, "teach-holder@example.com", "teach-holder")
        _grant_refunds(member)
        offering = ClassOfferingFactory(instructor=member, slug="teach-holder-class")
        registration = _paid_registration(class_offering=offering)
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert reverse("classes:admin_registration_refund_form", args=[registration.pk]) in content
        assert "refund-modal" in content

    def it_shows_retry_refund_on_a_failed_row(client):
        member = _instructor(client, "teach-retry@example.com", "teach-retry")
        _grant_refunds(member)
        offering = ClassOfferingFactory(instructor=member, slug="teach-retry-class")
        registration = _paid_registration(class_offering=offering, stripe_payment_id="pi_teach_retry")
        PaymentRefundFactory(registration=registration, amount_cents=5000, status=PaymentRefund.Status.FAILED)
        content = client.get(reverse("classes:teach_class_registrations", args=[offering.pk])).content.decode()
        assert "Retry refund" in content
        assert "Refund failed" in content

    def it_serves_the_table_partial_for_the_refresh(client):
        member = _instructor(client, "teach-table@example.com", "teach-table")
        _grant_refunds(member)
        offering = ClassOfferingFactory(instructor=member, slug="teach-table-class")
        _paid_registration(class_offering=offering)
        response = client.get(reverse("classes:teach_class_registrations_table", args=[offering.pk]))
        assert response.status_code == 200
        assert b"admin-table-wrap" in response.content

    def it_404s_the_table_partial_for_a_foreign_class(client):
        _instructor(client, "teach-foreign@example.com", "teach-foreign")
        other = ClassOfferingFactory(slug="teach-foreign-other")
        response = client.get(reverse("classes:teach_class_registrations_table", args=[other.pk]))
        assert response.status_code == 404

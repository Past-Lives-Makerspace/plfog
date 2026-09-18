"""BDD specs for the classes app's Stripe webhook handler."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core import mail

from classes.factories import (
    ClassOfferingFactory,
    DiscountCodeFactory,
    RegistrationFactory,
)
from classes.models import ClassOffering, Registration
from classes.webhook_handlers import handle_checkout_session_completed
from core.models import SiteConfiguration

pytestmark = pytest.mark.django_db


def _event(payment_status="paid", **session_overrides):
    session = {
        "id": "cs_test_abc",
        "payment_status": payment_status,
        "payment_intent": "pi_test_xyz",
        "amount_total": 9000,
        "metadata": {"kind": "class_registration", "registration_id": ""},
    }
    session.update(session_overrides)
    return {"data": {"object": session}}


@pytest.fixture
def pending_registration(db):
    offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, price_cents=10000)
    return RegistrationFactory(
        class_offering=offering,
        status=Registration.Status.PENDING,
        amount_paid_cents=10000,
        stripe_session_id="cs_test_abc",
        email="buyer@example.com",
    )


def describe_handle_checkout_session_completed():
    def it_confirms_the_registration_and_emails_the_registrant(pending_registration):
        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(pending_registration.pk)

        handle_checkout_session_completed(event)

        pending_registration.refresh_from_db()
        assert pending_registration.status == Registration.Status.CONFIRMED
        assert pending_registration.confirmed_at is not None
        assert pending_registration.stripe_payment_id == "pi_test_xyz"
        assert pending_registration.amount_paid_cents == 9000
        assert len(mail.outbox) == 2  # confirmation + instructor notification
        assert "confirmed" in mail.outbox[0].subject.lower()
        assert mail.outbox[0].to == ["buyer@example.com"]

    def it_is_idempotent_on_redelivery(pending_registration):
        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(pending_registration.pk)
        handle_checkout_session_completed(event)
        first_count = len(mail.outbox)
        handle_checkout_session_completed(event)  # second delivery
        # Second call short-circuits — no additional emails sent.
        assert len(mail.outbox) == first_count

    def it_increments_discount_use_count(pending_registration):
        code = DiscountCodeFactory(code="SAVE10", discount_pct=10, use_count=0)
        pending_registration.discount_code = code
        pending_registration.save(update_fields=["discount_code"])
        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(pending_registration.pk)

        handle_checkout_session_completed(event)

        code.refresh_from_db()
        assert code.use_count == 1

    def it_ignores_events_from_other_checkout_kinds(pending_registration):
        event = _event()
        event["data"]["object"]["metadata"] = {"kind": "tab_topup"}
        handle_checkout_session_completed(event)
        pending_registration.refresh_from_db()
        assert pending_registration.status == Registration.Status.PENDING
        assert mail.outbox == []

    def it_ignores_unpaid_sessions(pending_registration):
        event = _event(payment_status="unpaid")
        event["data"]["object"]["metadata"]["registration_id"] = str(pending_registration.pk)
        handle_checkout_session_completed(event)
        pending_registration.refresh_from_db()
        assert pending_registration.status == Registration.Status.PENDING

    def it_skips_when_registration_no_longer_exists(db):
        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = "999999"
        handle_checkout_session_completed(event)  # should not raise

    def it_subscribes_to_mailchimp_when_registrant_opted_in(pending_registration):
        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()
        pending_registration.wants_newsletter = True
        pending_registration.save(update_fields=["wants_newsletter"])

        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(pending_registration.pk)

        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=True,
        ) as spy:
            handle_checkout_session_completed(event)

        spy.assert_called_once()
        assert "class-registrant" in spy.call_args.kwargs["tags"]
        pending_registration.refresh_from_db()
        assert pending_registration.subscribed_to_mailchimp is True

    def it_does_not_subscribe_when_registrant_did_not_opt_in(pending_registration):
        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()

        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(pending_registration.pk)

        with patch("core.integrations.mailchimp.MailchimpClient.subscribe") as spy:
            handle_checkout_session_completed(event)

        spy.assert_not_called()

    def it_confirms_registration_even_when_mailchimp_fails(pending_registration):
        site = SiteConfiguration.load()
        site.mailchimp_api_key = "abc-us17"
        site.mailchimp_list_id = "LIST"
        site.save()
        pending_registration.wants_newsletter = True
        pending_registration.save(update_fields=["wants_newsletter"])

        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(pending_registration.pk)

        with patch(
            "core.integrations.mailchimp.MailchimpClient.subscribe",
            return_value=False,
        ):
            handle_checkout_session_completed(event)

        pending_registration.refresh_from_db()
        assert pending_registration.status == Registration.Status.CONFIRMED
        assert pending_registration.subscribed_to_mailchimp is False
        assert len(mail.outbox) == 2  # confirmation + instructor notification still sent


def describe_a_payment_landing_on_a_registration_that_lost_its_seat():
    """The abandoned-tab case, reachable through supported UI only.

    Register, self-cancel (which accepts a PENDING row), register again, then pay the
    first checkout in the tab that was left open. ``uq_registration_seat_email`` refuses
    to confirm the cancelled row back into a seat the new signup now holds. Raising there
    would be a 500 that Stripe retries forever, on a card that has already been charged.
    """

    def _pair(offering):
        dead = RegistrationFactory(
            class_offering=offering,
            email="buyer@example.com",
            status=Registration.Status.CANCELLED,
            stripe_session_id="cs_test_abc",
        )
        live = RegistrationFactory(
            class_offering=offering,
            email="buyer@example.com",
            status=Registration.Status.PENDING,
            stripe_session_id="cs_test_second",
        )
        return dead, live

    @pytest.fixture
    def offering(db):
        return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, price_cents=10000)

    def it_records_the_payment_and_alerts_instead_of_raising(offering, mailoutbox, admin_user):
        from classes.models import CmsActivity

        dead, live = _pair(offering)
        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(dead.pk)

        handle_checkout_session_completed(event)  # must not raise

        dead.refresh_from_db()
        live.refresh_from_db()
        assert dead.status == Registration.Status.CANCELLED  # the seat was not taken back
        assert dead.stripe_payment_id == "pi_test_xyz"  # the money is pinned to a row
        assert live.status == Registration.Status.PENDING  # the live signup is untouched
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.DUPLICATE_PAYMENT, registration=dead)
        assert row.payload["payment_intent"] == "pi_test_xyz"
        alerts = [m for m in mailoutbox if m.subject.startswith("Payment needs a decision:")]
        assert len(alerts) == 1
        assert "refund it or re-seat them" in alerts[0].body
        assert not any(m.subject.startswith("You're confirmed") for m in mailoutbox)

    def it_noops_a_redelivery_of_the_orphaned_payment(offering, mailoutbox, admin_user):
        from classes.models import CmsActivity

        dead, _live = _pair(offering)
        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(dead.pk)

        handle_checkout_session_completed(event)
        handle_checkout_session_completed(event)

        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.DUPLICATE_PAYMENT, registration=dead).count() == 1
        assert len([m for m in mailoutbox if m.subject.startswith("Payment needs a decision:")]) == 1

    def it_still_confirms_a_cancelled_row_when_nothing_else_holds_the_seat(offering, mailoutbox, admin_user):
        """Unchanged behaviour where the constraint has nothing to say: they paid, they are in."""
        dead = RegistrationFactory(
            class_offering=offering,
            email="lonely@example.com",
            status=Registration.Status.CANCELLED,
            stripe_session_id="cs_test_abc",
        )
        event = _event()
        event["data"]["object"]["metadata"]["registration_id"] = str(dead.pk)

        handle_checkout_session_completed(event)

        dead.refresh_from_db()
        assert dead.status == Registration.Status.CONFIRMED
        assert dead.stripe_payment_id == "pi_test_xyz"
        assert not any(m.subject.startswith("Payment needs a decision:") for m in mailoutbox)

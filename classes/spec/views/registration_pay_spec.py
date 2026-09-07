"""BDD specs for the token-rails pay page, the class_payment_link webhook branch, and the claim guard."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from classes.factories import ClassOfferingFactory, DiscountCodeFactory, RegistrationFactory
from classes.models import CmsActivity, Registration
from classes.webhook_handlers import handle_checkout_session_completed

pytestmark = pytest.mark.django_db


def _unpaid(**kwargs) -> Registration:
    kwargs.setdefault("status", Registration.Status.CONFIRMED)
    kwargs.setdefault("payment_due_cents", 4500)
    kwargs.setdefault("amount_paid_cents", 0)
    return RegistrationFactory(**kwargs)


def _pay_url(reg: Registration) -> str:
    return reverse("classes:my_registration_pay", kwargs={"token": reg.self_serve_token})


def _link_event(reg: Registration, *, payment_intent="pi_balance_1", session_id="cs_balance_1", amount=4500) -> dict:
    return {
        "data": {
            "object": {
                "id": session_id,
                "payment_status": "paid",
                "payment_intent": payment_intent,
                "amount_total": amount,
                "metadata": {"kind": "class_payment_link", "registration_id": str(reg.pk)},
            }
        }
    }


def describe_pay_page_get():
    def it_renders_without_creating_a_stripe_session(client):
        reg = _unpaid()
        with patch("billing.stripe_utils.create_class_checkout_session") as create:
            response = client.get(_pay_url(reg))
        assert response.status_code == 200
        create.assert_not_called()
        content = response.content.decode()
        assert "45.00" in content
        assert "Pay Now" in content

    def it_redirects_a_settled_row_with_the_all_set_message(client):
        reg = RegistrationFactory(status=Registration.Status.CONFIRMED, payment_due_cents=4500, amount_paid_cents=4500)
        response = client.get(_pay_url(reg), follow=True)
        assert "Nothing owed." in response.content.decode()
        assert "all set." in response.content.decode()

    def it_redirects_a_cancelled_row_without_congratulating(client):
        reg = RegistrationFactory(status=Registration.Status.CANCELLED, payment_due_cents=4500)
        response = client.get(_pay_url(reg), follow=True)
        content = response.content.decode()
        assert "This registration is no longer active." in content
        assert "You're all set" not in content

    def it_404s_a_bad_token(client):
        assert client.get(reverse("classes:my_registration_pay", kwargs={"token": "nope"})).status_code == 404

    def it_shows_the_give_it_a_minute_note_only_with_a_session_in_flight(client):
        reg = _unpaid()
        assert "give it a minute" not in client.get(_pay_url(reg)).content.decode()
        reg.stripe_session_id = "cs_inflight"
        reg.save(update_fields=["stripe_session_id"])
        assert "give it a minute" in client.get(_pay_url(reg)).content.decode()


def describe_pay_page_post():
    def it_creates_checkout_for_the_balance_and_redirects(client):
        reg = _unpaid()
        with patch(
            "billing.stripe_utils.create_class_checkout_session",
            return_value={"id": "cs_new", "url": "https://stripe.test/pay"},
        ) as create:
            response = client.post(_pay_url(reg))
        assert response.status_code == 302
        assert response["Location"] == "https://stripe.test/pay"
        kwargs = create.call_args.kwargs
        assert kwargs["amount_cents"] == 4500
        assert kwargs["metadata"]["kind"] == "class_payment_link"
        assert kwargs["metadata"]["registration_id"] == str(reg.pk)
        assert kwargs["idempotency_key"] == f"class-paylink-reg-{reg.pk}-4500"
        assert kwargs["product_name"].endswith("(balance)")
        assert kwargs["success_url"].endswith("?paid=1")
        reg.refresh_from_db()
        assert reg.stripe_session_id == "cs_new"

    def it_renders_the_friendly_error_for_a_sub_minimum_balance(client):
        reg = _unpaid(payment_due_cents=40)
        with patch("billing.stripe_utils.create_class_checkout_session") as create:
            response = client.post(_pay_url(reg))
        create.assert_not_called()
        assert response.status_code == 200
        assert "under $0.50" in response.content.decode()

    def it_shows_the_processing_banner_on_the_paid_return(client):
        reg = _unpaid()
        url = reverse("classes:my_registration", kwargs={"token": reg.self_serve_token})
        content = client.get(url, {"paid": "1"}).content.decode()
        assert "Your payment is processing" in content


def describe_class_payment_link_webhook():
    def it_records_the_payment_and_sends_the_receipt(db, mailoutbox):
        reg = _unpaid()
        handle_checkout_session_completed(_link_event(reg))
        reg.refresh_from_db()
        assert reg.amount_paid_cents == 4500
        assert reg.stripe_payment_id == "pi_balance_1"
        assert reg.stripe_session_id == "cs_balance_1"
        assert reg.is_unpaid is False
        assert len(mailoutbox) == 1
        assert mailoutbox[0].subject.startswith("You're confirmed for")

    def it_bumps_the_discount_code_exactly_once_even_across_redelivery(db, mailoutbox):
        code = DiscountCodeFactory(discount_pct=10, use_count=0)
        reg = _unpaid(discount_code=code)
        handle_checkout_session_completed(_link_event(reg))
        handle_checkout_session_completed(_link_event(reg))
        code.refresh_from_db()
        assert code.use_count == 1
        assert len(mailoutbox) == 1  # no double receipt on Stripe re-delivery

    def it_ignores_unpaid_sessions(db, mailoutbox):
        reg = _unpaid()
        event = _link_event(reg)
        event["data"]["object"]["payment_status"] = "unpaid"
        handle_checkout_session_completed(event)
        reg.refresh_from_db()
        assert reg.amount_paid_cents == 0
        assert len(mailoutbox) == 0

    def it_ignores_an_unknown_registration(db, mailoutbox):
        reg = _unpaid()
        event = _link_event(reg)
        event["data"]["object"]["metadata"]["registration_id"] = "999999"
        handle_checkout_session_completed(event)
        assert len(mailoutbox) == 0

    def it_ignores_a_session_with_no_registration_id(db, mailoutbox):
        event = _link_event(_unpaid())
        del event["data"]["object"]["metadata"]["registration_id"]
        handle_checkout_session_completed(event)
        assert len(mailoutbox) == 0

    def it_leaves_the_amount_untouched_when_stripe_omits_amount_total(db):
        reg = _unpaid()
        event = _link_event(reg)
        event["data"]["object"]["amount_total"] = None
        handle_checkout_session_completed(event)
        reg.refresh_from_db()
        assert reg.amount_paid_cents == 0
        assert reg.stripe_payment_id == "pi_balance_1"


def describe_duplicate_payment():
    def it_records_the_duplicate_distinctly_and_alerts_the_admins(db, mailoutbox, admin_user):
        reg = _unpaid()
        reg.mark_paid(actor=None, note="cash")  # staff settled it while checkout was in flight
        handle_checkout_session_completed(_link_event(reg, payment_intent="pi_dupe", session_id="cs_dupe"))
        reg.refresh_from_db()
        assert reg.amount_paid_cents == 4500  # the first settlement — never overwritten
        assert reg.stripe_payment_id == "pi_dupe"  # recorded so re-deliveries are idempotent
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.DUPLICATE_PAYMENT, registration=reg)
        assert row.payload == {"payment_intent": "pi_dupe", "amount_cents": 4500, "session_id": "cs_dupe"}
        alerts = [m for m in mailoutbox if m.subject.startswith("Duplicate payment:")]
        assert len(alerts) == 1
        assert "refund is owed" in alerts[0].body
        assert not any(m.subject.startswith("You're confirmed") for m in mailoutbox)  # no receipt

    def it_noops_a_redelivery_of_the_recorded_duplicate(db, mailoutbox, admin_user):
        reg = _unpaid()
        reg.mark_paid(actor=None)
        handle_checkout_session_completed(_link_event(reg, payment_intent="pi_dupe2"))
        handle_checkout_session_completed(_link_event(reg, payment_intent="pi_dupe2"))
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.DUPLICATE_PAYMENT, registration=reg).count() == 1
        assert len([m for m in mailoutbox if m.subject.startswith("Duplicate payment:")]) == 1

    def it_records_a_zero_amount_when_stripe_omits_amount_total_on_a_duplicate(db, admin_user):
        reg = _unpaid()
        reg.mark_paid(actor=None)
        event = _link_event(reg, payment_intent="pi_dupe_noamt")
        event["data"]["object"]["amount_total"] = None
        handle_checkout_session_completed(event)
        row = CmsActivity.objects.get(kind=CmsActivity.Kind.DUPLICATE_PAYMENT, registration=reg)
        assert row.payload["amount_cents"] == 0

    def it_stays_quiet_when_no_admin_addresses_exist(db, mailoutbox):
        reg = _unpaid()
        reg.mark_paid(actor=None)
        handle_checkout_session_completed(_link_event(reg, payment_intent="pi_dupe_noadmin"))
        assert CmsActivity.objects.filter(kind=CmsActivity.Kind.DUPLICATE_PAYMENT, registration=reg).exists()
        assert len(mailoutbox) == 0  # nothing to alert — no admin recipients configured

    def it_renders_the_banner_on_the_detail_page_while_the_activity_exists(db, admin_user, client):
        from classes import activity

        reg = _unpaid()
        client.force_login(admin_user)
        url = reverse("classes:admin_registration_detail", kwargs={"pk": reg.pk})
        assert "A refund is owed." not in client.get(url).content.decode()
        activity.log(
            CmsActivity.Kind.DUPLICATE_PAYMENT,
            class_offering=reg.class_offering,
            registration=reg,
            payload={"payment_intent": "pi_banner", "amount_cents": 4500, "session_id": "cs_banner"},
        )
        content = client.get(url).content.decode()
        assert "A refund is owed." in content
        assert "https://dashboard.stripe.com/payments/pi_banner" in content


def _bookable_offering(**kwargs):
    """A published, bookable free class with one upcoming session (the register-page shape)."""
    from datetime import timedelta

    from django.utils import timezone

    from classes.factories import ClassSessionFactory

    kwargs.setdefault("status", "published")
    kwargs.setdefault("price_cents", 0)
    kwargs.setdefault("capacity", 5)
    offering = ClassOfferingFactory(**kwargs)
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=3),
        ends_at=timezone.now() + timedelta(days=3, hours=2),
    )
    return offering


def describe_claim_link_guard():
    def _claim_click(client, offering, reg, *, follow=False):
        return client.get(
            reverse("classes:register", kwargs={"slug": offering.slug}),
            {"waitlist_token": reg.self_serve_token},
            follow=follow,
        )

    def it_bounces_a_stale_claim_link_for_a_promoted_registration(db, client):
        offering = _bookable_offering()
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.CONFIRMED)
        before = Registration.objects.count()
        response = _claim_click(client, offering, reg, follow=True)
        assert "already in this class." in response.content.decode()
        assert Registration.objects.count() == before

    def it_bounces_a_pending_seat_holder_too(db, client):
        offering = _bookable_offering()
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.PENDING)
        response = _claim_click(client, offering, reg)
        assert response.status_code == 302
        assert response["Location"] == reverse("classes:my_registration", kwargs={"token": reg.self_serve_token})

    def it_lets_a_cancelled_registrant_reach_the_register_form(db, client):
        # A staff-removed person was just told they're out — a stale claim click
        # must NOT congratulate them, and must let them legitimately re-register.
        offering = _bookable_offering()
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.CANCELLED)
        response = _claim_click(client, offering, reg)
        assert response.status_code == 200  # the register form renders normally
        assert "already in this class." not in response.content.decode()

    def it_lets_a_refunded_registrant_reach_the_register_form(db, client):
        offering = _bookable_offering()
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.REFUNDED)
        response = _claim_click(client, offering, reg)
        assert response.status_code == 200
        assert "already in this class." not in response.content.decode()

    def it_leaves_a_still_waitlisted_claim_click_alone(db, client):
        offering = _bookable_offering()
        reg = RegistrationFactory(class_offering=offering, status=Registration.Status.WAITLISTED)
        response = _claim_click(client, offering, reg)
        assert response.status_code == 200  # the register form renders normally

    def it_ignores_an_unknown_token(db, client):
        offering = _bookable_offering()
        response = client.get(reverse("classes:register", kwargs={"slug": offering.slug}), {"waitlist_token": "nope"})
        assert response.status_code == 200

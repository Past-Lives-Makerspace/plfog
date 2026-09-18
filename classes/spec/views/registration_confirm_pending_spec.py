"""BDD specs for the staff confirm action on a signup stuck at PENDING.

The point of the action is what it unlocks: before it, a stalled signup refuses Mark as Paid,
refuses Send Payment Link and refuses to be promoted, because all three need a CONFIRMED row
with a balance. So these specs check the follow-through, not just the status flip.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse

from classes.factories import ClassOfferingFactory, InstructorFactory, RegistrationFactory, UserFactory
from classes.models import ClassOffering, CmsActivity, Registration

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}


def _offering(capacity: int = 6, **overrides) -> ClassOffering:
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, capacity=capacity, **overrides)


def _stuck(offering, **overrides) -> Registration:
    """A signup that started a Checkout and never finished it.

    ``amount_paid_cents`` carries the session's price provisionally, which is the trap: it
    is the quote, not money received, and ``stripe_payment_id`` being empty is the proof.
    """
    overrides.setdefault("status", Registration.Status.PENDING)
    overrides.setdefault("amount_paid_cents", 6500)
    overrides.setdefault("stripe_session_id", "cs_stuck_1")
    return RegistrationFactory(class_offering=offering, **overrides)


def _confirm_url(registration) -> str:
    return reverse("classes:registration_confirm_pending", args=[registration.pk])


def _roster_url(offering) -> str:
    return reverse("classes:teach_class_registrations", args=[offering.pk])


def describe_registration_confirm_pending():
    def it_confirms_the_seat_with_the_balance_still_owed(admin_user, client):
        client.force_login(admin_user)
        offering = _offering(4)
        stuck = _stuck(offering, email="front.desk@example.com")
        assert offering.spots_remaining == 3

        response = client.post(_confirm_url(stuck), headers=HTMX)

        assert response.status_code == 200
        stuck.refresh_from_db()
        assert stuck.status == Registration.Status.CONFIRMED
        assert stuck.payment_due_cents == 6500
        assert stuck.amount_paid_cents == 0
        assert stuck.balance_due_cents == 6500
        # The seat was already consumed while PENDING, so confirming takes no second one.
        assert offering.spots_remaining == 3

    def it_tells_the_staff_member_what_is_still_owed(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering(), first_name="Rae", amount_paid_cents=4200)

        response = client.post(_confirm_url(stuck), headers=HTMX)

        assert "Rae is confirmed. They still owe $42.00." in response.headers["HX-Trigger"]

    def it_says_nothing_about_a_balance_on_a_free_signup(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering(), first_name="Noor", amount_paid_cents=0)

        response = client.post(_confirm_url(stuck), headers=HTMX)

        assert "Noor is confirmed." in response.headers["HX-Trigger"]
        assert "still owe" not in response.headers["HX-Trigger"]

    def it_emails_the_registrant_their_confirmation_once(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering(), email="rescued@example.com")
        mail.outbox.clear()

        client.post(_confirm_url(stuck), headers=HTMX)

        confirmations = [m for m in mail.outbox if "rescued@example.com" in m.to and "confirmed" in m.subject]
        assert len(confirmations) == 1

    def it_does_not_send_a_second_confirmation_when_the_payment_lands_later(admin_user, client):
        # The reg:{pk}:confirmation emit period is shared with the webhook.
        from classes.webhook_handlers import handle_checkout_session_completed

        client.force_login(admin_user)
        stuck = _stuck(_offering(), email="paid.after@example.com")
        mail.outbox.clear()
        client.post(_confirm_url(stuck), headers=HTMX)

        handle_checkout_session_completed(
            {
                "data": {
                    "object": {
                        "id": "cs_stuck_1",
                        "metadata": {"kind": "class_registration", "registration_id": str(stuck.pk)},
                        "payment_status": "paid",
                        "payment_intent": "pi_after_1",
                        "amount_total": 6500,
                    }
                }
            }
        )

        confirmations = [m for m in mail.outbox if "paid.after@example.com" in m.to and "confirmed" in m.subject]
        assert len(confirmations) == 1

    def it_records_who_confirmed_it(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering())

        client.post(_confirm_url(stuck), headers=HTMX)

        row = CmsActivity.objects.filter(registration=stuck, kind=CmsActivity.Kind.REGISTRATION_CONFIRMED).get()
        assert row.actor == admin_user

    def it_redirects_a_plain_post_back_to_the_detail_page(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering())

        response = client.post(_confirm_url(stuck))

        assert response.status_code == 302
        assert response.url == reverse("classes:admin_registration_detail", args=[stuck.pk])
        stuck.refresh_from_db()
        assert stuck.status == Registration.Status.CONFIRMED

    def describe_when_the_row_is_no_longer_pending():
        def it_reports_the_refusal_without_changing_anything(admin_user, client):
            client.force_login(admin_user)
            offering = _offering(4)
            already = _stuck(offering, status=Registration.Status.CONFIRMED, amount_paid_cents=6500)

            response = client.post(_confirm_url(already), headers=HTMX)

            assert response.status_code == 200
            assert "still waiting on payment" in response.headers["HX-Trigger"]
            already.refresh_from_db()
            assert already.amount_paid_cents == 6500
            assert offering.spots_remaining == 3

        def it_reports_the_refusal_on_a_plain_post_too(admin_user, client):
            client.force_login(admin_user)
            cancelled = _stuck(_offering(), status=Registration.Status.CANCELLED)

            response = client.post(_confirm_url(cancelled), follow=True)

            assert any("still waiting on payment" in str(m) for m in response.context["messages"])

    def describe_gating():
        def it_refuses_an_instructor_who_does_not_own_the_class(client):
            user = UserFactory(username="outsider@example.com")
            InstructorFactory(user=user, instructor_slug="outsider")
            client.force_login(user)
            stuck = _stuck(_offering())

            response = client.post(_confirm_url(stuck), headers=HTMX)

            assert response.status_code == 403
            stuck.refresh_from_db()
            assert stuck.status == Registration.Status.PENDING

        def it_admits_the_classs_own_instructor(client):
            user = UserFactory(username="owner@example.com")
            member = InstructorFactory(user=user, instructor_slug="owner")
            client.force_login(user)
            stuck = _stuck(_offering(instructor=member))

            response = client.post(_confirm_url(stuck), headers=HTMX)

            assert response.status_code == 200
            stuck.refresh_from_db()
            assert stuck.status == Registration.Status.CONFIRMED

        def it_rejects_a_get(admin_user, client):
            client.force_login(admin_user)
            stuck = _stuck(_offering())
            assert client.get(_confirm_url(stuck)).status_code == 405


def describe_what_confirming_unlocks():
    def it_lets_staff_mark_the_rescued_signup_as_paid(admin_user, client):
        client.force_login(admin_user)
        offering = _offering(4)
        stuck = _stuck(offering, amount_paid_cents=5500)

        client.post(_confirm_url(stuck), headers=HTMX)
        response = client.post(
            reverse("classes:registration_mark_paid", args=[stuck.pk]), {"note": "cash at the desk"}, headers=HTMX
        )

        assert response.status_code == 200
        stuck.refresh_from_db()
        assert stuck.amount_paid_cents == 5500
        assert stuck.balance_due_cents == 0
        assert CmsActivity.objects.filter(registration=stuck, kind=CmsActivity.Kind.REGISTRATION_MARKED_PAID).exists()
        assert offering.spots_remaining == 3

    def it_lets_staff_send_the_rescued_signup_a_payment_link(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering(), email="owes.money@example.com", amount_paid_cents=5500)

        client.post(_confirm_url(stuck), headers=HTMX)
        mail.outbox.clear()
        response = client.post(reverse("classes:registration_send_payment_link", args=[stuck.pk]), headers=HTMX)

        assert response.status_code == 200
        stuck.refresh_from_db()
        assert stuck.payment_link_sent_at is not None
        assert any("owes.money@example.com" in message.to for message in mail.outbox)

    def it_refuses_both_tools_while_the_signup_is_still_stuck(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering(), amount_paid_cents=5500)

        mark_paid = client.post(reverse("classes:registration_mark_paid", args=[stuck.pk]), headers=HTMX)
        pay_link = client.post(reverse("classes:registration_send_payment_link", args=[stuck.pk]), headers=HTMX)

        assert "no outstanding balance" in mark_paid.headers["HX-Trigger"]
        assert "no outstanding balance" in pay_link.headers["HX-Trigger"]
        stuck.refresh_from_db()
        assert stuck.status == Registration.Status.PENDING


def describe_the_roster_affordance():
    def it_offers_confirm_signup_on_a_pending_row(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = _offering()
        stuck = _stuck(offering)

        content = client.get(_roster_url(offering)).content.decode()

        menu = menu_region(content, f"reg-row-{stuck.pk}")
        assert ">Confirm Signup</button>" in menu
        assert f"confirmpending-reg-{stuck.pk}" in menu
        assert _confirm_url(stuck) in content

    def it_offers_it_on_no_other_row(admin_user, client, menu_region):
        client.force_login(admin_user)
        offering = _offering()
        confirmed = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=6500,
            email="paid@example.com",
        )
        cancelled = RegistrationFactory(
            class_offering=offering,
            status=Registration.Status.CANCELLED,
            amount_paid_cents=0,
            email="gone@example.com",
        )

        content = client.get(_roster_url(offering)).content.decode()

        assert ">Confirm Signup</button>" not in menu_region(content, f"reg-row-{confirmed.pk}")
        assert ">Confirm Signup</button>" not in menu_region(content, f"reg-row-{cancelled.pk}")

    def it_shows_the_action_on_the_admin_detail_page(admin_user, client):
        client.force_login(admin_user)
        stuck = _stuck(_offering())

        content = client.get(reverse("classes:admin_registration_detail", args=[stuck.pk])).content.decode()

        assert _confirm_url(stuck) in content
        assert "Confirm Signup" in content

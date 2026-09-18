"""BDD specs for the class checkout's expiry, its delayed-payment events, and the billing fan-in.

Four Stripe events decide what happens to a seat a checkout is holding: ``expired`` releases
it, ``async_payment_succeeded`` buys it, ``async_payment_failed`` releases it, and a late
``completed`` may arrive on a row that has already lost it. None of them may raise, because a
raise is a 500 and Stripe retries a 500 forever against a card that has already been charged.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.core import mail
from django.utils import timezone

from classes import webhook_handlers as classes_handlers
from classes.factories import ClassOfferingFactory, RegistrationFactory
from classes.models import (
    EXPIRED_HOLD_CANCEL_REASON,
    FAILED_PAYMENT_CANCEL_REASON,
    ClassOffering,
    CmsActivity,
    Registration,
)

pytestmark = pytest.mark.django_db


def _event(kind: str = "class_registration", *, registration_id=None, session_id: str = "cs_hook_1", **extra):
    session = {
        "id": session_id,
        "metadata": {"kind": kind},
        "payment_status": "unpaid",
        "payment_intent": "",
        "amount_total": 9000,
        **extra,
    }
    if registration_id is not None:
        session["metadata"]["registration_id"] = str(registration_id)
    return {"data": {"object": session}}


def _offering(capacity: int) -> ClassOffering:
    return ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, capacity=capacity, price_cents=9000)


def _hold(offering, **overrides) -> Registration:
    overrides.setdefault("status", Registration.Status.PENDING)
    overrides.setdefault("stripe_session_id", "cs_hook_1")
    overrides.setdefault("amount_paid_cents", 9000)
    return RegistrationFactory(class_offering=offering, **overrides)


def describe_class_checkout_session_lifetime():
    def it_mints_the_session_with_an_hour_to_live():
        from billing import stripe_utils

        before = timezone.now()
        with patch.object(stripe_utils, "create_checkout_session", return_value={"id": "cs_x", "url": "u"}) as create:
            stripe_utils.create_class_checkout_session(
                amount_cents=9000,
                product_name="Intro to Casting",
                customer_email="buyer@example.com",
                success_url="https://example.test/ok",
                cancel_url="https://example.test/no",
                metadata={"kind": "class_registration", "registration_id": "1"},
                idempotency_key="key-1",
            )

        expires_at = create.call_args.kwargs["expires_at"]
        assert (
            timedelta(minutes=59)
            <= timezone.datetime.fromtimestamp(expires_at, tz=timezone.get_current_timezone()) - before
            <= timedelta(minutes=61)
        )

    def it_stays_inside_the_window_stripe_accepts():
        from billing.stripe_utils import CLASS_CHECKOUT_SESSION_LIFETIME

        # Stripe rejects an expires_at under 30 minutes or over 24 hours out.
        assert timedelta(minutes=30) <= CLASS_CHECKOUT_SESSION_LIFETIME <= timedelta(hours=24)


def describe_handle_checkout_session_expired():
    def it_cancels_the_signup_and_frees_the_seat():
        offering = _offering(2)
        hold = _hold(offering)
        assert offering.spots_remaining == 1

        classes_handlers.handle_checkout_session_expired(_event(registration_id=hold.pk))

        hold.refresh_from_db()
        assert hold.status == Registration.Status.CANCELLED
        assert hold.cancellation_reason == EXPIRED_HOLD_CANCEL_REASON
        assert offering.spots_remaining == 2

    def it_is_idempotent_across_a_redelivery():
        offering = _offering(2)
        hold = _hold(offering)
        event = _event(registration_id=hold.pk)

        classes_handlers.handle_checkout_session_expired(event)
        classes_handlers.handle_checkout_session_expired(event)

        assert CmsActivity.objects.filter(registration=hold, kind=CmsActivity.Kind.REGISTRATION_CANCELLED).count() == 1
        assert offering.spots_remaining == 2

    def it_leaves_a_seat_that_was_already_paid_for_alone():
        offering = _offering(2)
        confirmed = _hold(offering, status=Registration.Status.CONFIRMED, stripe_payment_id="pi_paid")

        classes_handlers.handle_checkout_session_expired(_event(registration_id=confirmed.pk))

        confirmed.refresh_from_db()
        assert confirmed.status == Registration.Status.CONFIRMED
        assert offering.spots_remaining == 1

    def describe_when_the_expiring_session_has_been_superseded():
        def it_leaves_the_row_alone_because_someone_is_paying_on_the_new_one():
            # #419's resume path expires the stale session to mint a replacement, and that
            # expiry is what fires this event. Acting on it would cancel a live checkout.
            offering = _offering(2)
            resumed = _hold(offering, stripe_session_id="cs_reminted")

            classes_handlers.handle_checkout_session_expired(_event(registration_id=resumed.pk, session_id="cs_stale"))

            resumed.refresh_from_db()
            assert resumed.status == Registration.Status.PENDING
            assert offering.spots_remaining == 1

        def it_still_acts_when_the_row_never_stored_a_session():
            offering = _offering(2)
            hold = _hold(offering, stripe_session_id="")

            classes_handlers.handle_checkout_session_expired(_event(registration_id=hold.pk))

            hold.refresh_from_db()
            assert hold.status == Registration.Status.CANCELLED
            assert offering.spots_remaining == 2

    def describe_when_the_event_is_not_ours():
        def it_ignores_an_orientation_session():
            offering = _offering(2)
            hold = _hold(offering)

            classes_handlers.handle_checkout_session_expired(_event("orientation_booking", registration_id=hold.pk))

            hold.refresh_from_db()
            assert hold.status == Registration.Status.PENDING

        def it_ignores_a_class_session_naming_no_registration():
            offering = _offering(2)
            hold = _hold(offering)

            classes_handlers.handle_checkout_session_expired(_event())

            hold.refresh_from_db()
            assert hold.status == Registration.Status.PENDING

        def it_ignores_a_registration_that_no_longer_exists():
            classes_handlers.handle_checkout_session_expired(_event(registration_id=999999))


def describe_handle_checkout_session_async_payment_failed():
    def it_releases_the_seat_with_its_own_reason():
        offering = _offering(3)
        settling = _hold(offering)
        assert offering.spots_remaining == 2

        classes_handlers.handle_checkout_session_async_payment_failed(
            _event(registration_id=settling.pk, status="complete")
        )

        settling.refresh_from_db()
        assert settling.status == Registration.Status.CANCELLED
        assert settling.cancellation_reason == FAILED_PAYMENT_CANCEL_REASON
        assert offering.spots_remaining == 3

    def it_ignores_an_event_for_another_app():
        offering = _offering(3)
        settling = _hold(offering)

        classes_handlers.handle_checkout_session_async_payment_failed(
            _event("orientation_booking", registration_id=settling.pk)
        )

        settling.refresh_from_db()
        assert settling.status == Registration.Status.PENDING

    def it_ignores_a_class_session_naming_no_registration():
        classes_handlers.handle_checkout_session_async_payment_failed(_event())


def describe_a_delayed_notification_payment_end_to_end():
    def it_ignores_the_unpaid_completion_then_confirms_on_the_async_success():
        from billing import views as billing_views

        offering = _offering(3)
        settling = _hold(offering, email="slow.payer@example.com")
        mail.outbox.clear()

        # Stripe completes the session immediately with the money still in flight.
        billing_views._dispatch_checkout_completed(
            _event(registration_id=settling.pk, status="complete", payment_status="unpaid")
        )
        settling.refresh_from_db()
        assert settling.status == Registration.Status.PENDING
        assert mail.outbox == []
        assert offering.spots_remaining == 2

        # Days later the bank settles.
        success = _event(
            registration_id=settling.pk,
            status="complete",
            payment_status="paid",
            payment_intent="pi_async_1",
            amount_total=9000,
        )
        billing_views._dispatch_checkout_completed(success)

        settling.refresh_from_db()
        assert settling.status == Registration.Status.CONFIRMED
        assert settling.stripe_payment_id == "pi_async_1"
        assert settling.amount_paid_cents == 9000
        assert offering.spots_remaining == 2
        confirmations = [m for m in mail.outbox if "slow.payer@example.com" in m.to and "confirmed" in m.subject]
        assert len(confirmations) == 1

        # A late duplicate of the same payment must not send a second confirmation.
        billing_views._dispatch_checkout_completed(success)
        confirmations = [m for m in mail.outbox if "slow.payer@example.com" in m.to and "confirmed" in m.subject]
        assert len(confirmations) == 1


def describe_a_paid_webhook_landing_on_an_expiry_cancelled_row():
    def it_records_an_orphaned_payment_instead_of_raising():
        offering = _offering(1)
        abandoned = _hold(offering, email="came.back@example.com")

        classes_handlers.handle_checkout_session_expired(_event(registration_id=abandoned.pk))
        abandoned.refresh_from_db()
        assert abandoned.status == Registration.Status.CANCELLED
        assert offering.spots_remaining == 1

        # They signed up again on the freed seat, which is exactly what cancelling allows.
        fresh = _hold(offering, email="came.back@example.com", stripe_session_id="cs_hook_2")
        assert offering.spots_remaining == 0

        # Now the original session's payment finally lands. uq_registration_seat_email
        # refuses to confirm the cancelled row back in, and that refusal must not be a 500.
        classes_handlers.handle_checkout_session_completed(
            _event(
                registration_id=abandoned.pk,
                payment_status="paid",
                payment_intent="pi_orphan_1",
                amount_total=9000,
            )
        )

        abandoned.refresh_from_db()
        fresh.refresh_from_db()
        assert abandoned.status == Registration.Status.CANCELLED
        assert abandoned.stripe_payment_id == "pi_orphan_1"
        assert fresh.status == Registration.Status.PENDING
        assert offering.spots_remaining == 0
        orphan = CmsActivity.objects.filter(registration=abandoned, kind=CmsActivity.Kind.DUPLICATE_PAYMENT).get()
        assert orphan.payload["payment_intent"] == "pi_orphan_1"

    def it_confirms_the_seat_back_when_nobody_else_took_it():
        offering = _offering(2)
        abandoned = _hold(offering, email="late.but.paid@example.com")
        classes_handlers.handle_checkout_session_expired(_event(registration_id=abandoned.pk))
        assert offering.spots_remaining == 2

        classes_handlers.handle_checkout_session_completed(
            _event(
                registration_id=abandoned.pk,
                payment_status="paid",
                payment_intent="pi_late_1",
                amount_total=9000,
            )
        )

        abandoned.refresh_from_db()
        assert abandoned.status == Registration.Status.CONFIRMED
        assert abandoned.stripe_payment_id == "pi_late_1"
        assert offering.spots_remaining == 1


def describe_the_billing_fan_in():
    def it_registers_the_classes_handler_for_expired_sessions():
        from billing import views as billing_views
        from membership import webhook_handlers as membership_handlers

        assert billing_views._CHECKOUT_EXPIRED_HANDLERS == [
            classes_handlers.handle_checkout_session_expired,
            membership_handlers.handle_checkout_session_expired,
        ]

    def it_registers_the_classes_handler_for_async_failures():
        from billing import views as billing_views

        assert billing_views._CHECKOUT_ASYNC_FAILED_HANDLERS == [
            classes_handlers.handle_checkout_session_async_payment_failed
        ]

    def it_routes_the_async_events_in_the_webhook_map():
        from billing import views as billing_views

        assert (
            billing_views._WEBHOOK_HANDLERS["checkout.session.async_payment_succeeded"]
            is billing_views._dispatch_checkout_completed
        )
        assert (
            billing_views._WEBHOOK_HANDLERS["checkout.session.async_payment_failed"]
            is billing_views._dispatch_checkout_async_failed
        )

    def it_fans_an_async_failure_out_to_every_registered_handler():
        from billing import views as billing_views

        first, second = Mock(), Mock()
        event = _event(registration_id=1)
        with patch.object(billing_views, "_CHECKOUT_ASYNC_FAILED_HANDLERS", [first, second]):
            billing_views._dispatch_checkout_async_failed(event)
        first.assert_called_once_with(event)
        second.assert_called_once_with(event)

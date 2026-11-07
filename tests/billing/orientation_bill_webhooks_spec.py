"""BDD specs for the orientation bill Checkout webhook (#1111, part 1).

A paid ``kind=orientation_bill`` session stamps the bill paid once; a repeat of the same
event changes nothing and alerts nobody; the booking webhook never reads a bill payment as
an orphan; other kinds, unpaid sessions and bad ids are ignored; a payment with no bill to
credit, a second payment and a payment on a cancelled bill alert the Billing Administrators.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from billing import views as billing_views
from billing import webhook_handlers
from membership import webhook_handlers as membership_webhook_handlers
from membership.models import AdminCapability, Member, OrientationBooking
from tests.membership.factories import MemberFactory, MembershipPlanFactory, OrientationSlotFactory

pytestmark = pytest.mark.django_db

_ORPHAN_SUBJECT = "Orphaned orientation bill payment needs a manual refund"


def _event(
    kind: str = "orientation_bill", *, booking_id: int | str | None = None, payment_status: str = "paid", **extra: Any
) -> dict[str, Any]:
    session: dict[str, Any] = {
        "id": "cs_bill_hook_1",
        "metadata": {"kind": kind},
        "payment_status": payment_status,
        "payment_intent": "pi_bill_hook_1",
        "amount_total": 4000,
        "customer_email": "payer@example.com",
        **extra,
    }
    if booking_id is not None:
        session["metadata"]["booking_id"] = str(booking_id)
    return {"data": {"object": session}}


def _bill(**fields: Any) -> OrientationBooking:
    MembershipPlanFactory()
    member = User.objects.create_user(username="hook_billed", email="hook_billed@example.com").member
    starts = timezone.now() - timedelta(days=1)
    slot = OrientationSlotFactory(starts_at=starts, ends_at=starts + timedelta(hours=1), seats=1)
    booking = OrientationBooking.create_bill(slot=slot, member=member, oriented_by=None, amount_cents=4000)
    if fields:
        OrientationBooking.objects.filter(pk=booking.pk).update(**fields)
        booking.refresh_from_db()
    return booking


def _billing_approver() -> Member:
    member = MemberFactory(_pre_signup_email="bill-billing-approver@example.com")
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"bba{member.pk}", email="bill-billing-approver@example.com")
    member.user = user
    member.save(update_fields=["user"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


def _orphan_alerts() -> list[str]:
    return [m.subject for m in mail.outbox if "Orphaned" in m.subject]


def describe_handle_orientation_bill_checkout_completed():
    def it_stamps_the_bill_paid():
        booking = _bill()
        webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
        booking.refresh_from_db()
        assert (booking.amount_paid_cents, booking.stripe_payment_id, booking.stripe_session_id) == (
            4000,
            "pi_bill_hook_1",
            "cs_bill_hook_1",
        )
        assert booking.paid_at is not None
        assert booking.bill_state == "paid"

    def it_changes_nothing_on_a_repeat_and_alerts_nobody():
        _billing_approver()
        booking = _bill()
        webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
        booking.refresh_from_db()
        first_paid_at = booking.paid_at
        mail.outbox.clear()
        webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
        booking.refresh_from_db()
        assert (booking.paid_at, booking.amount_paid_cents) == (first_paid_at, 4000)
        assert mail.outbox == []

    def it_reaches_the_bill_through_the_fan_in_and_trips_no_orphan_alert():
        _billing_approver()
        booking = _bill()
        mail.outbox.clear()
        billing_views._dispatch_checkout_completed(_event(booking_id=booking.pk))
        billing_views._dispatch_checkout_completed(_event(booking_id=booking.pk))
        booking.refresh_from_db()
        assert booking.bill_state == "paid"
        assert _orphan_alerts() == []

    def it_is_ignored_by_the_booking_webhook():
        booking = _bill()
        with patch.object(membership_webhook_handlers, "_send_orphan_payment_alert") as mock_alert:
            membership_webhook_handlers.handle_checkout_session_completed(_event(booking_id=booking.pk))
        assert not mock_alert.called

    def it_is_registered_in_the_fan_in():
        assert webhook_handlers.handle_orientation_bill_checkout_completed in billing_views._CHECKOUT_COMPLETED_HANDLERS

    def it_ignores_other_kinds():
        booking = _bill()
        webhook_handlers.handle_orientation_bill_checkout_completed(
            _event("orientation_booking", booking_id=booking.pk)
        )
        booking.refresh_from_db()
        assert booking.bill_state == "unpaid"

    def it_ignores_an_unpaid_session():
        booking = _bill()
        webhook_handlers.handle_orientation_bill_checkout_completed(
            _event(booking_id=booking.pk, payment_status="unpaid")
        )
        booking.refresh_from_db()
        assert booking.bill_state == "unpaid"

    def it_warns_and_stops_on_a_missing_or_crafted_id(caplog):
        with caplog.at_level(logging.WARNING, logger="billing.webhook_handlers"):
            webhook_handlers.handle_orientation_bill_checkout_completed(_event())
            webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id="1 OR 1=1"))
        assert "missing or non-numeric booking_id None" in caplog.text
        assert "'1 OR 1=1'" in caplog.text

    def it_takes_a_session_with_no_amount():
        booking = _bill()
        webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk, amount_total=None))
        booking.refresh_from_db()
        assert booking.amount_paid_cents == 4000

    def describe_money_with_no_home():
        def it_alerts_when_the_bill_is_gone():
            _billing_approver()
            mail.outbox.clear()
            webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=999999))
            assert _orphan_alerts() == [_ORPHAN_SUBJECT]

        def it_alerts_when_a_plain_booking_is_named():
            from tests.membership.factories import OrientationBookingFactory

            _billing_approver()
            booking = OrientationBookingFactory()
            mail.outbox.clear()
            webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
            assert _orphan_alerts() == [_ORPHAN_SUBJECT]
            booking.refresh_from_db()
            assert booking.amount_paid_cents == 0

        def it_alerts_when_the_row_vanishes_mid_stamp():
            _billing_approver()
            booking = _bill()
            mail.outbox.clear()
            with patch("billing.orientation_bills.mark_paid", return_value="gone"):
                webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
            assert _orphan_alerts() == [_ORPHAN_SUBJECT]

        def it_alerts_on_a_second_payment_by_another_intent():
            _billing_approver()
            booking = _bill(paid_at=timezone.now(), amount_paid_cents=4000, stripe_payment_id="pi_first")
            mail.outbox.clear()
            webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
            assert _orphan_alerts() == [_ORPHAN_SUBJECT]
            assert "was already paid by pi_first" in mail.outbox[0].body
            booking.refresh_from_db()
            assert booking.stripe_payment_id == "pi_first"

        def it_alerts_on_a_payment_landing_on_a_cancelled_bill():
            _billing_approver()
            booking = _bill(bill_cancelled_at=timezone.now())
            mail.outbox.clear()
            webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
            assert _orphan_alerts() == [_ORPHAN_SUBJECT]
            assert "was cancelled before the payment landed" in mail.outbox[0].body
            booking.refresh_from_db()
            assert booking.amount_paid_cents == 0


def describe_a_payment_after_cancel_bill():
    def it_alerts_and_leaves_the_bill_cancelled():
        from billing import orientation_bills

        _billing_approver()
        booking = _bill(stripe_session_id="cs_stale_tab")
        actor = User.objects.create_user(username="hook_canceller", email="hook_canceller@example.com")
        with (
            patch("billing.stripe_utils.retrieve_checkout_session", return_value={"payment_status": "unpaid"}),
            patch("billing.stripe_utils.expire_checkout_session"),
        ):
            orientation_bills.cancel_bill(booking, actor=actor)
        mail.outbox.clear()
        webhook_handlers.handle_orientation_bill_checkout_completed(_event(booking_id=booking.pk))
        assert _orphan_alerts() == [_ORPHAN_SUBJECT]
        booking.refresh_from_db()
        assert (booking.bill_state, booking.amount_paid_cents) == ("cancelled", 0)

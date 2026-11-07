"""BDD specs for the orientation bill service and the booking's bill transitions (#1111, part 1).

``send_bill`` makes the past one-off slot, the completed booking and one activity row, and
sends the one bill email; its refusals write nothing. The Checkout mints a fresh session per
Pay click; ``mark_paid`` stamps the money once; the landing reconcile asks Stripe. The
nightly auto completion and the payment hold sweep leave a bill alone, and the prefill
follows each price kind.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail, signing
from django.utils import timezone

from billing import orientation_bills
from core.models import SiteActivity
from membership import orientations
from membership.models import Member, OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_SESSION = {"id": "cs_bill_svc_1", "url": "https://checkout.stripe.example/cs_bill_svc_1"}


def _account(username: str = "svc_member", name: str = "Sasha Billed") -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=username, email=f"{username}@example.com").member
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


def _sender() -> User:
    return User.objects.create_user(username="svc_sender", email="svc_sender@example.com")


def _send(member: Member, **overrides: Any) -> OrientationBooking:
    kwargs: dict[str, Any] = {
        "completed_on": timezone.localdate() - timedelta(days=3),
        "oriented_by": None,
        "amount_cents": 4000,
        "sent_by": _sender(),
        **overrides,
    }
    orientation_type = kwargs.pop("orientation_type", None) or OrientationTypeFactory(name="Service Lathe")
    return orientation_bills.send_bill(member, orientation_type, **kwargs)


def _bill(**fields: Any) -> OrientationBooking:
    booking = _send(_account())
    if fields:
        OrientationBooking.objects.filter(pk=booking.pk).update(**fields)
        booking.refresh_from_db()
    return booking


def describe_send_bill():
    def it_books_a_completed_confirmed_orientation_on_a_past_one_off_slot():
        member = _account()
        runner = MemberFactory(full_legal_name="Rae Runner")
        booking = _send(member, oriented_by=runner)
        assert (booking.status, booking.is_completed, booking.oriented_by) == (
            OrientationBooking.Status.CONFIRMED,
            True,
            runner,
        )
        assert booking.confirmed_at is not None
        assert booking.billed_at is not None
        assert (booking.bill_amount_cents, booking.amount_paid_cents, booking.bill_state) == (4000, 0, "unpaid")
        slot = booking.slot
        assert (slot.source, slot.seats, slot.orienter) == (OrientationSlot.Source.CUSTOM, 1, runner)
        local = timezone.localtime(slot.starts_at)
        assert (local.date(), local.hour) == (timezone.localdate() - timedelta(days=3), 12)

    def it_logs_one_activity_row_with_the_sender_and_the_note():
        booking = _send(_account(), note="Cash promised")
        row = SiteActivity.objects.get(kind=SiteActivity.Kind.ORIENTATION_BILL_SENT)
        assert row.actor.username == "svc_sender"
        assert row.target == booking
        assert row.payload == {
            "orientation_type": "Service Lathe",
            "owner": booking.orientation_type.owner_name,
            "amount_cents": 4000,
            "note": "Cash promised",
        }

    def it_sends_exactly_one_email_naming_the_owner_when_nobody_was_picked():
        mail.outbox.clear()
        booking = _send(_account())
        assert len(mail.outbox) == 1
        assert f"Run by: {booking.orientation_type.owner_name}" in mail.outbox[0].body

    def it_refuses_a_member_with_no_account_and_writes_nothing():
        member = MemberFactory(full_legal_name="Nico Nouser")
        with pytest.raises(OrientationError, match="Nico Nouser has no account yet, so they can't pay online."):
            _send(member)
        assert not OrientationSlot.objects.exists()
        assert not OrientationBooking.objects.exists()

    def it_refuses_a_member_with_a_live_booking_for_the_type():
        member = _account()
        orientation_type = OrientationTypeFactory(name="Held Lathe")
        OrientationBookingFactory(
            member=member,
            slot=OrientationSlotFactory(orientation_type=orientation_type, guild=orientation_type.guild),
            status=OrientationBooking.Status.PENDING_PAYMENT,
        )
        with pytest.raises(OrientationError, match="already has a booking for this orientation"):
            _send(member, orientation_type=orientation_type)
        assert OrientationBooking.objects.count() == 1

    @pytest.mark.parametrize("amount", [None, 0, 99, 50001])
    def it_refuses_an_amount_outside_one_to_five_hundred_dollars(amount: int | None):
        with pytest.raises(OrientationError):
            OrientationBooking.ensure_bill_amount(amount)

    def it_takes_both_ends_of_the_range():
        OrientationBooking.ensure_bill_amount(100)
        OrientationBooking.ensure_bill_amount(50000)

    def it_refuses_a_bad_amount_and_writes_nothing():
        with pytest.raises(OrientationError, match="A bill is at least"):
            _send(_account(), amount_cents=50)
        assert not OrientationSlot.objects.exists()

    def describe_the_slot_time():
        def it_starts_at_midday_on_a_past_day():
            orientation_type = OrientationTypeFactory(name="Midday Lathe", duration_minutes=90)
            starts = orientation_bills._billed_starts_at(orientation_type, date(2026, 3, 4))
            assert timezone.localtime(starts).hour == 12

        def it_never_starts_before_the_day_itself():
            orientation_type = OrientationTypeFactory(name="Night Lathe", duration_minutes=60)
            half_past_midnight = timezone.make_aware(datetime(2026, 3, 4, 0, 30))
            with patch("billing.orientation_bills.timezone.now", return_value=half_past_midnight):
                starts = orientation_bills._billed_starts_at(orientation_type, date(2026, 3, 4))
            assert starts == timezone.make_aware(datetime(2026, 3, 4, 0, 0))
            assert timezone.localtime(starts).date() == date(2026, 3, 4)

        def it_ends_before_now_when_midday_has_not_come_yet_today():
            orientation_type = OrientationTypeFactory(name="Morning Lathe", duration_minutes=90)
            morning = timezone.make_aware(datetime(2026, 3, 4, 9, 0))
            with patch("billing.orientation_bills.timezone.now", return_value=morning):
                starts = orientation_bills._billed_starts_at(orientation_type, date(2026, 3, 4))
            assert starts == morning - timedelta(minutes=90)


def describe_the_bill_model():
    def it_reads_no_bill_state_on_an_ordinary_booking():
        booking = OrientationBookingFactory()
        assert (booking.is_bill, booking.bill_state, booking.bill_is_unpaid) == (False, "", False)

    def it_reads_paid_before_cancelled():
        booking = _bill(paid_at=timezone.now(), bill_cancelled_at=timezone.now())
        assert booking.bill_state == "paid"

    def it_reads_cancelled():
        booking = _bill(bill_cancelled_at=timezone.now())
        assert (booking.bill_state, booking.bill_is_unpaid) == ("cancelled", False)

    def it_refuses_to_move_a_bill_even_after_undo_oriented():
        booking = _bill(is_completed=False)
        assert booking.is_movable is False
        with pytest.raises(OrientationError, match="already happened"):
            booking.ensure_movable()

    def it_counts_pay_clicks_in_the_database():
        booking = _bill()
        stale = OrientationBooking.objects.get(pk=booking.pk)
        assert booking.next_bill_checkout_attempt() == 1
        assert stale.next_bill_checkout_attempt() == 2

    def it_formats_the_amount():
        assert _bill(bill_amount_cents=1250).bill_amount_display == "$12.50"

    def it_dates_money_by_paid_at_when_set_else_requested_at():
        booking = _bill()
        assert booking.money_at == booking.requested_at
        paid = timezone.now() + timedelta(days=40)
        booking.paid_at = paid
        assert booking.money_at == paid


def describe_bill_prefill_cents():
    def it_is_the_fixed_price():
        assert OrientationTypeFactory(name="P Fixed", price_cents=3500).bill_prefill_cents == 3500

    def it_is_none_for_a_free_type():
        assert OrientationTypeFactory(name="P Free", price_cents=0).bill_prefill_cents is None

    def it_is_a_donations_suggestion_first():
        orientation_type = OrientationTypeFactory(
            name="P Suggest", is_donation=True, donation_suggested_cents=2000, donation_minimum_cents=500
        )
        assert orientation_type.bill_prefill_cents == 2000

    def it_falls_back_to_a_donations_minimum():
        orientation_type = OrientationTypeFactory(name="P Minimum", is_donation=True, donation_minimum_cents=500)
        assert orientation_type.bill_prefill_cents == 500

    def it_is_none_for_a_donation_with_neither():
        assert OrientationTypeFactory(name="P Open", is_donation=True, price_cents=900).bill_prefill_cents is None


def describe_start_bill_checkout():
    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_mints_a_tagged_session_with_a_fresh_key_per_click(mock_create):
        booking = _bill()
        orientation_bills.start_bill_checkout(booking)
        orientation_bills.start_bill_checkout(booking)
        keys = [call.kwargs["idempotency_key"] for call in mock_create.call_args_list]
        assert keys == [f"orientation-bill-{booking.pk}-1", f"orientation-bill-{booking.pk}-2"]
        kwargs = mock_create.call_args.kwargs
        assert kwargs["metadata"] == {"kind": "orientation_bill", "booking_id": str(booking.pk)}
        assert kwargs["product_name"] == orientation_bills.item_label(booking)
        assert kwargs["customer_email"] == "svc_member@example.com"

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    @patch("billing.stripe_utils.expire_checkout_session")
    def it_expires_the_previous_session_first(mock_expire, mock_create):
        booking = _bill(stripe_session_id="cs_old")
        orientation_bills.start_bill_checkout(booking)
        mock_expire.assert_called_once_with(session_id="cs_old")

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    @patch("billing.stripe_utils.expire_checkout_session", side_effect=RuntimeError("gone"))
    def it_mints_anyway_when_the_expire_fails(mock_expire, mock_create):
        booking = _bill(stripe_session_id="cs_old")
        assert orientation_bills.start_bill_checkout(booking) == _SESSION["url"]

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_refuses_a_bill_with_nothing_to_pay(mock_create):
        booking = _bill(paid_at=timezone.now())
        with pytest.raises(OrientationError):
            orientation_bills.start_bill_checkout(booking)
        assert not mock_create.called


def describe_mark_paid():
    def it_stamps_the_money_once():
        booking = _bill()
        assert (
            orientation_bills.mark_paid(booking, payment_intent="pi_1", session_id="cs_1", amount_total=4000) == "paid"
        )
        booking.refresh_from_db()
        assert (booking.amount_paid_cents, booking.stripe_payment_id, booking.stripe_session_id) == (
            4000,
            "pi_1",
            "cs_1",
        )
        assert booking.paid_at is not None
        assert booking.bill_state == "paid"
        assert orientation_bills.mark_paid(booking, payment_intent="pi_1", session_id="cs_1", amount_total=4000) == (
            "already"
        )

    def it_stores_the_session_that_paid():
        booking = _bill(stripe_session_id="cs_minted_later")
        orientation_bills.mark_paid(booking, payment_intent="pi_2", session_id="cs_that_paid", amount_total=None)
        booking.refresh_from_db()
        assert (booking.stripe_session_id, booking.amount_paid_cents) == ("cs_that_paid", 4000)

    def it_keeps_the_saved_session_when_stripe_names_none():
        booking = _bill(stripe_session_id="cs_saved")
        orientation_bills.mark_paid(booking, payment_intent="pi_2b", session_id="", amount_total=4000)
        booking.refresh_from_db()
        assert booking.stripe_session_id == "cs_saved"

    def it_logs_a_mismatch_between_stripe_and_the_bill(caplog):
        booking = _bill()
        with caplog.at_level(logging.WARNING, logger="billing.orientation_bills"):
            orientation_bills.mark_paid(booking, payment_intent="pi_3", session_id="cs_3", amount_total=3900)
        assert "collected 3900 cents against a 4000 cent bill" in caplog.text
        booking.refresh_from_db()
        assert booking.amount_paid_cents == 3900

    def it_leaves_a_cancelled_bill_alone():
        booking = _bill(bill_cancelled_at=timezone.now())
        assert (
            orientation_bills.mark_paid(booking, payment_intent="pi_4", session_id="", amount_total=4000) == "already"
        )
        booking.refresh_from_db()
        assert booking.amount_paid_cents == 0

    def it_reports_a_vanished_row():
        booking = _bill()
        OrientationBooking.objects.filter(pk=booking.pk).delete()
        assert orientation_bills.mark_paid(booking, payment_intent="pi_5", session_id="", amount_total=4000) == "gone"


def describe_reconcile_landed_checkout():
    def it_is_pending_with_no_session():
        assert orientation_bills.reconcile_landed_checkout(_bill()) == "pending"

    def it_is_unknown_when_stripe_is_unreachable():
        booking = _bill(stripe_session_id="cs_r")
        with patch("billing.stripe_utils.retrieve_checkout_session", side_effect=RuntimeError("down")):
            assert orientation_bills.reconcile_landed_checkout(booking) == "unknown"

    def it_is_pending_while_unpaid():
        booking = _bill(stripe_session_id="cs_r")
        with patch("billing.stripe_utils.retrieve_checkout_session", return_value={"payment_status": "unpaid"}):
            assert orientation_bills.reconcile_landed_checkout(booking) == "pending"

    def it_marks_paid_when_stripe_says_paid():
        booking = _bill(stripe_session_id="cs_r")
        session = {"id": "cs_r", "payment_status": "paid", "payment_intent": "pi_r", "amount_total": 4000}
        with patch("billing.stripe_utils.retrieve_checkout_session", return_value=session):
            assert orientation_bills.reconcile_landed_checkout(booking) == "paid"


def describe_the_checkout_token():
    def it_round_trips_to_the_bill():
        booking = _bill()
        assert orientation_bills.read_checkout_token(orientation_bills.make_checkout_token(booking)) == booking

    def it_refuses_a_forged_token():
        with pytest.raises(signing.BadSignature):
            orientation_bills.read_checkout_token("forged")


def describe_the_jobs_leave_a_bill_alone():
    def it_is_never_auto_completed_again():
        booking = _bill()
        mail.outbox.clear()
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            assert orientations.auto_complete() == 0
        assert mail.outbox == []
        assert not mock_post.called
        assert not SiteActivity.objects.filter(
            kind=SiteActivity.Kind.ORIENTATION_COMPLETED, target_id=booking.pk
        ).exists()

    def it_is_never_completed_again_after_undo_oriented():
        booking = _bill()
        booking.uncomplete()
        mail.outbox.clear()
        with patch("core.events.discord.post_embed", return_value=True) as mock_post:
            assert orientations.auto_complete() == 0
        booking.refresh_from_db()
        assert booking.is_completed is False
        assert mail.outbox == []
        assert not mock_post.called

    def it_still_completes_an_ordinary_booking():
        from datetime import timedelta as _td

        starts = timezone.now() - _td(days=1)
        ordinary = OrientationBookingFactory(
            slot=OrientationSlotFactory(starts_at=starts, ends_at=starts + _td(hours=1)),
            status=OrientationBooking.Status.CONFIRMED,
        )
        assert orientations.auto_complete() == 1
        ordinary.refresh_from_db()
        assert ordinary.is_completed is True

    def it_is_never_swept_as_a_payment_hold():
        booking = _bill(requested_at=timezone.now() - timedelta(days=5), stripe_session_id="cs_swept")
        with patch("billing.stripe_utils.retrieve_checkout_session") as mock_retrieve:
            assert orientations.expire_payment_holds() == (0, 0)
        assert not mock_retrieve.called
        assert OrientationBooking.objects.filter(pk=booking.pk).exists()


def describe_cancel_and_resend():
    def it_refuses_to_cancel_twice():
        booking = _bill()
        actor = User.objects.create_user(username="svc_canceller")
        orientation_bills.cancel_bill(booking, actor=actor)
        with pytest.raises(OrientationError):
            orientation_bills.cancel_bill(booking, actor=actor)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_BILL_CANCELLED).count() == 1

    def it_cancels_even_when_the_session_will_not_expire():
        booking = _bill(stripe_session_id="cs_stuck")
        with patch("billing.stripe_utils.expire_checkout_session", side_effect=RuntimeError("down")):
            orientation_bills.cancel_bill(booking, actor=User.objects.create_user(username="svc_c2"))
        booking.refresh_from_db()
        assert booking.bill_state == "cancelled"

    def it_refuses_to_resend_a_paid_bill():
        booking = _bill(paid_at=timezone.now())
        mail.outbox.clear()
        with pytest.raises(OrientationError):
            orientation_bills.resend_bill(booking)
        assert mail.outbox == []

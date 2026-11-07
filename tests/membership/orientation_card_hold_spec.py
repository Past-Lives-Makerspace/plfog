"""BDD specs for #1128: a paid orientation holds the card and charges it only when an Orientor confirms.

Stripe is faked at ``billing.stripe_utils`` (``tests/membership/stripe_intents.py``); the booking
rows, locks, refunds ledger, emails and the scheduler registry are real.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from billing.models import PaymentRefund
from core.models import SiteActivity
from membership import orientations, webhook_handlers
from membership.models import (
    AdminCapability,
    HoldCaptureFailed,
    HoldCaptureUnavailable,
    OrientationBooking,
    OrientationError,
)
from tests.membership.factories import (
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)
from tests.membership.stripe_intents import CANCEL, CAPTURE, READ, intent

pytestmark = pytest.mark.django_db

Status = OrientationBooking.Status


def _slot(price_cents: int = 1500, **slot_kwargs):
    settings_obj = GuildOrientationSettingsFactory()
    OrientationTypeFactory(guild=settings_obj.guild, price_cents=price_cents)
    return OrientationSlotFactory(guild=settings_obj.guild, **slot_kwargs)


def _held(status: str = Status.REQUESTED, **overrides) -> OrientationBooking:
    """A request whose card is held for $15 and not yet charged."""
    defaults = {
        "slot": _slot(),
        "status": status,
        "authorized_cents": 1500,
        "authorized_at": timezone.now(),
        "stripe_payment_id": "pi_hold",
        "stripe_session_id": "cs_hold",
    }
    defaults.update(overrides)
    return OrientationBookingFactory(**defaults)


def _billing_approver() -> None:
    member = MemberFactory(_pre_signup_email="hold-approver@example.com")
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"hold_ba{member.pk}", email="hold-approver@example.com")
    member.user = user
    member.save(update_fields=["user"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)


def _member_mail(booking: OrientationBooking) -> list:
    return [m for m in mail.outbox if booking.member.primary_email in m.to]


def _body(message) -> str:
    """The text and HTML parts together, so one assertion reads both."""
    html = "".join(part for part, _mime in getattr(message, "alternatives", []))
    return f"{message.body}\n{html}"


def describe_hold_state():
    def it_reads_blank_for_a_booking_that_never_held_a_card():
        booking = OrientationBookingFactory(amount_paid_cents=1500, stripe_payment_id="pi_old")
        assert booking.hold_state == ""
        assert not booking.hold_is_open

    def it_reads_held_while_the_card_waits_for_an_orientor():
        booking = _held()
        assert booking.hold_state == "held"
        assert booking.hold_is_open

    def it_reads_captured_once_the_hold_is_charged():
        booking = _held(amount_paid_cents=1500)
        assert booking.hold_state == "captured"
        assert not booking.hold_is_open

    def it_reads_released_once_stripe_gave_the_hold_back():
        booking = _held(status=Status.DECLINED, hold_released_at=timezone.now())
        assert booking.hold_state == "released"
        assert not booking.hold_is_open


def describe_starting_a_paid_checkout():
    _SESSION = {"id": "cs_new", "url": "https://checkout.example/cs_new"}

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_asks_stripe_to_hold_the_card_and_say_so(mock_create):
        orientations.start_orientation_checkout(_slot(), MemberFactory())
        kwargs = mock_create.call_args.kwargs
        assert kwargs["hold_only"] is True
        assert kwargs["hold_note"] == orientations.CHECKOUT_HOLD_NOTE
        assert "held, not charged, until an Orientor confirms" in orientations.CHECKOUT_HOLD_NOTE

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_holds_the_card_for_a_custom_time_too(mock_create):
        settings_obj = GuildOrientationSettingsFactory(is_enabled=True, allow_custom_requests=True)
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild, price_cents=1500)
        orientations.start_custom_orientation_checkout(
            settings_obj.guild,
            MemberFactory(),
            timezone.now() + timedelta(days=10),
            orientation_type=orientation_type,
        )
        assert mock_create.call_args.kwargs["hold_only"] is True

    @patch("billing.stripe_utils.create_checkout_session", return_value=_SESSION)
    def it_records_no_money_on_the_new_hold(mock_create):
        orientations.start_orientation_checkout(_slot(), MemberFactory())
        booking = OrientationBooking.objects.get(stripe_session_id="cs_new")
        assert (booking.amount_paid_cents, booking.authorized_cents) == (0, 0)


def describe_landed_payment():
    def it_reads_nothing_from_a_session_still_open():
        with patch(READ) as read:
            assert (
                orientations.landed_payment({"status": "open", "payment_status": "unpaid", "payment_intent": ""})
                is None
            )
            read.assert_not_called()

    def it_reads_nothing_from_a_complete_session_with_no_payment_intent():
        with patch(READ) as read:
            assert orientations.landed_payment({"status": "complete", "payment_status": "unpaid"}) is None
            read.assert_not_called()

    def it_reads_a_card_hold_from_a_complete_unpaid_session():
        session = {"status": "complete", "payment_status": "unpaid", "payment_intent": "pi_hold"}
        with patch(READ, return_value=intent("requires_capture", 2500)):
            landed = orientations.landed_payment(session)
        assert landed == orientations.LandedPayment(payment_intent="pi_hold", amount_cents=2500, held=True)

    def it_reads_a_charge_from_a_checkout_that_charged_at_once():
        session = {"payment_status": "paid", "payment_intent": "pi_old"}
        with patch(READ, return_value=intent("succeeded", 1500)):
            landed = orientations.landed_payment(session)
        assert landed == orientations.LandedPayment(payment_intent="pi_old", amount_cents=1500, held=False)

    def it_reads_nothing_from_a_payment_intent_that_neither_holds_nor_charged():
        session = {"status": "complete", "payment_status": "unpaid", "payment_intent": "pi_slow"}
        with patch(READ, return_value=intent("processing")):
            assert orientations.landed_payment(session) is None


def describe_finalizing_a_hold():
    def _checkout_hold() -> OrientationBooking:
        return OrientationBookingFactory(slot=_slot(), status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold")

    def it_records_the_hold_as_authorized_never_as_paid():
        hold = _checkout_hold()
        mail.outbox.clear()
        outcome = orientations.finalize_paid_booking(hold, payment_intent="pi_hold", amount_total=1500, held=True)
        assert outcome == "finalized"
        hold.refresh_from_db()
        assert hold.status == Status.REQUESTED
        assert hold.stripe_payment_id == "pi_hold"
        assert hold.authorized_cents == 1500
        assert hold.authorized_at is not None
        assert hold.amount_paid_cents == 0
        assert hold.paid_at is None

    def it_tells_the_member_their_card_is_held_not_charged():
        hold = _checkout_hold()
        mail.outbox.clear()
        orientations.finalize_paid_booking(hold, payment_intent="pi_hold", amount_total=1500, held=True)
        request_email = next(m for m in _member_mail(hold) if "request received" in m.subject.lower())
        body = _body(request_email)
        assert "holding $15 on your card" in body
        assert "not charged until an" in body
        assert "You paid" not in body

    def it_refuses_a_hold_with_no_amount():
        hold = _checkout_hold()
        with pytest.raises(ValueError, match="no amount"):
            orientations.finalize_paid_booking(hold, payment_intent="pi_hold", amount_total=None, held=True)


def describe_the_checkout_webhook_with_a_hold():
    def _event(booking_id, **extra):
        session = {
            "id": "cs_hold",
            "status": "complete",
            "payment_status": "unpaid",
            "payment_intent": "pi_hold",
            "amount_total": 1500,
            "metadata": {"kind": "orientation_booking", "booking_id": str(booking_id)},
            **extra,
        }
        return {"data": {"object": session}}

    def it_turns_the_checkout_into_a_request_holding_the_card():
        hold = OrientationBookingFactory(slot=_slot(), status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold")
        with patch(READ, return_value=intent("requires_capture")):
            webhook_handlers.handle_checkout_session_completed(_event(hold.pk))
        hold.refresh_from_db()
        assert hold.status == Status.REQUESTED
        assert (hold.authorized_cents, hold.amount_paid_cents) == (1500, 0)

    def it_releases_a_hold_whose_booking_is_gone_instead_of_asking_for_a_refund():
        _billing_approver()
        mail.outbox.clear()
        with (
            patch(READ, return_value=intent("requires_capture")),
            patch(CANCEL, return_value=intent("canceled")) as cancel,
        ):
            webhook_handlers.handle_checkout_session_completed(_event(999999))
        cancel.assert_called_once_with(payment_intent_id="pi_hold")
        assert not any("Orphaned orientation payment" in m.subject for m in mail.outbox)

    def it_alerts_when_an_orphan_hold_will_not_release():
        _billing_approver()
        mail.outbox.clear()
        with patch(READ, return_value=intent("requires_capture")), patch(CANCEL, side_effect=RuntimeError("down")):
            webhook_handlers.handle_checkout_session_completed(_event(999999))
        alert = next(m for m in mail.outbox if "Orphaned orientation payment" in m.subject)
        assert "could not be released" in alert.body

    def it_releases_a_hold_that_lands_on_a_booking_resolved_without_a_payment():
        booking = OrientationBookingFactory(slot=_slot(), status=Status.DECLINED, stripe_payment_id="")
        with (
            patch(READ, return_value=intent("requires_capture")),
            patch(CANCEL, return_value=intent("canceled")) as cancel,
        ):
            webhook_handlers.handle_checkout_session_completed(_event(booking.pk))
        cancel.assert_called_once()

    def it_releases_a_hold_whose_booking_vanished_mid_finalize():
        hold = OrientationBookingFactory(slot=_slot(), status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold")
        with (
            patch(READ, return_value=intent("requires_capture")),
            patch("membership.orientations.finalize_landed", return_value="gone"),
            patch(CANCEL, return_value=intent("canceled")) as cancel,
        ):
            webhook_handlers.handle_checkout_session_completed(_event(hold.pk))
        cancel.assert_called_once()


def describe_confirming_a_held_request():
    def it_charges_the_hold_then_confirms_and_counts_the_money():
        booking = _held()
        mail.outbox.clear()
        with patch(CAPTURE, return_value=intent("succeeded", 1500)) as capture:
            orientations.confirm_orientation(booking)
        capture.assert_called_once_with(payment_intent_id="pi_hold")
        assert booking.status == Status.CONFIRMED  # the caller's instance is current
        booking.refresh_from_db()
        assert booking.status == Status.CONFIRMED
        assert booking.amount_paid_cents == 1500
        assert booking.paid_at is not None
        assert booking.money_at == booking.paid_at  # finance files it under the charge, not the request
        confirmed = next(m for m in _member_mail(booking) if "confirmed" in m.subject.lower())
        assert "We charged the $15 held on your card." in confirmed.body

    def it_counts_a_charge_whose_reply_was_lost():
        booking = _held()
        with patch(CAPTURE, side_effect=RuntimeError("timeout")), patch(READ, return_value=intent("succeeded")):
            orientations.confirm_orientation(booking)
        booking.refresh_from_db()
        assert (booking.status, booking.amount_paid_cents) == (Status.CONFIRMED, 1500)

    def it_changes_nothing_when_stripe_cannot_be_reached():
        booking = _held()
        with (
            patch(CAPTURE, side_effect=RuntimeError("down")),
            patch(READ, side_effect=RuntimeError("down")),
            pytest.raises(HoldCaptureUnavailable),
        ):
            orientations.confirm_orientation(booking)
        booking.refresh_from_db()
        assert (booking.status, booking.amount_paid_cents) == (Status.REQUESTED, 0)

    def it_changes_nothing_when_the_hold_is_still_there_after_an_error():
        booking = _held()
        with (
            patch(CAPTURE, side_effect=RuntimeError("rate limited")),
            patch(READ, return_value=intent("requires_capture")),
            pytest.raises(HoldCaptureUnavailable),
        ):
            orientations.confirm_orientation(booking)
        booking.refresh_from_db()
        assert booking.status == Status.REQUESTED

    def it_declines_and_emails_the_member_when_the_hold_expired():
        booking = _held()
        mail.outbox.clear()
        with (
            patch(CAPTURE, side_effect=RuntimeError("expired")),
            patch(READ, return_value=intent("canceled")),
            patch(CANCEL, side_effect=RuntimeError("already canceled")),
            pytest.raises(HoldCaptureFailed),
        ):
            orientations.confirm_orientation(booking)
        booking.refresh_from_db()
        assert booking.status == Status.DECLINED
        assert booking.amount_paid_cents == 0  # never a confirmed unpaid booking
        assert booking.hold_state == "released"
        assert not PaymentRefund.objects.filter(orientation_booking=booking).exists()
        declined = next(m for m in _member_mail(booking) if "about your orientation request" in m.subject.lower())
        assert "We couldn't charge your card when your request was confirmed" in declined.body
        assert "You were not charged." in declined.body
        assert "Pick another time" in declined.body or "pick another time" in declined.body

    def it_declines_when_the_charge_answers_without_charging():
        booking = _held()
        with (
            patch(CAPTURE, return_value=intent("requires_payment_method")),
            patch(CANCEL, return_value=intent("canceled")),
            pytest.raises(HoldCaptureFailed),
        ):
            orientations.confirm_orientation(booking)
        booking.refresh_from_db()
        assert booking.status == Status.DECLINED

    def it_refuses_a_request_already_answered_without_touching_stripe():
        booking = _held()
        OrientationBooking.objects.filter(pk=booking.pk).update(status=Status.DECLINED)
        with patch(CAPTURE) as capture, pytest.raises(OrientationError, match="already answered"):
            orientations.confirm_orientation(booking)
        capture.assert_not_called()

    def it_confirms_a_booking_that_holds_no_card_without_stripe():
        booking = OrientationBookingFactory(slot=_slot(), amount_paid_cents=1500, stripe_payment_id="pi_old")
        with patch(CAPTURE) as capture:
            orientations.confirm_orientation(booking)
        capture.assert_not_called()
        booking.refresh_from_db()
        assert booking.status == Status.CONFIRMED


def describe_the_emailed_confirm_link_on_a_held_request():
    def it_reports_a_card_that_would_not_charge():
        booking = _held()
        with (
            patch(CAPTURE, return_value=intent("canceled")),
            patch(CANCEL, return_value=intent("canceled")),
        ):
            assert orientations.apply_token_action(booking, "confirm") == "card_failed"

    def it_reports_stripe_unreachable():
        booking = _held()
        with patch(CAPTURE, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            assert orientations.apply_token_action(booking, "confirm") == "try_again"

    def it_reports_a_request_answered_between_the_page_and_the_click():
        booking = _held()
        OrientationBooking.objects.filter(pk=booking.pk).update(status=Status.CANCELLED)
        assert orientations.apply_token_action(booking, "confirm") == "already"


def describe_declining_before_confirm():
    @patch("billing.refunds.issue_refund")
    def it_releases_the_hold_and_creates_no_refund(mock_issue):
        booking = _held()
        mail.outbox.clear()
        with patch(CANCEL, return_value=intent("canceled")) as cancel:
            orientations.decline_orientation(booking)
        cancel.assert_called_once_with(payment_intent_id="pi_hold")
        mock_issue.assert_not_called()
        assert not PaymentRefund.objects.filter(orientation_booking=booking).exists()
        booking.refresh_from_db()
        assert booking.status == Status.DECLINED
        assert booking.hold_released_at is not None
        assert booking.amount_paid_cents == 0
        declined = next(m for m in _member_mail(booking) if "about your orientation request" in m.subject.lower())
        assert "We released the $15 hold on your card." in declined.body
        assert "You were not charged." in declined.body
        assert "refund" not in declined.body.lower()

    def it_counts_a_release_whose_reply_was_lost():
        booking = _held()
        with patch(CANCEL, side_effect=RuntimeError("timeout")), patch(READ, return_value=intent("canceled")):
            orientations.decline_orientation(booking)
        booking.refresh_from_db()
        assert booking.hold_state == "released"

    @patch("billing.refunds.issue_refund")
    def it_refunds_a_hold_stripe_says_was_charged_after_all(mock_issue):
        booking = _held()
        with patch(CANCEL, side_effect=RuntimeError("already captured")), patch(READ, return_value=intent("succeeded")):
            orientations.decline_orientation(booking)
        booking.refresh_from_db()
        assert booking.amount_paid_cents == 1500
        mock_issue.assert_called_once()

    def it_flags_and_alerts_but_still_declines_when_stripe_will_not_release():
        _billing_approver()
        booking = _held()
        mail.outbox.clear()
        with patch(CANCEL, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            orientations.decline_orientation(booking)
        booking.refresh_from_db()
        assert booking.status == Status.DECLINED
        assert booking.hold_state == "held"  # still open: the sweep retries it
        alert = next(m for m in mail.outbox if m.subject == "An orientation card hold was not released")
        assert "hold-approver@example.com" in alert.to
        assert "dashboard.stripe.com/payments/pi_hold" in alert.body
        declined = next(m for m in _member_mail(booking) if "about your orientation request" in m.subject.lower())
        assert "We're releasing the $15 hold on your card." in declined.body

    def it_keeps_the_hold_open_when_stripe_still_holds_it_after_an_error():
        booking = _held()
        with patch(CANCEL, side_effect=RuntimeError("busy")), patch(READ, return_value=intent("requires_capture")):
            orientations.decline_orientation(booking)
        booking.refresh_from_db()
        assert booking.hold_state == "held"

    @patch("billing.refunds.issue_refund")
    def it_refunds_instead_when_a_racing_confirm_charged_the_hold_first(mock_issue):
        booking = _held()
        OrientationBooking.objects.filter(pk=booking.pk).update(amount_paid_cents=1500)
        with patch(CANCEL) as cancel:
            orientations.decline_orientation(booking)  # the caller's instance still reads "held"
        cancel.assert_not_called()
        mock_issue.assert_called_once()


def describe_cancelling():
    @patch("billing.refunds.issue_refund")
    def it_releases_a_held_request_the_member_cancels_with_no_fee_and_no_refund(mock_issue):
        booking = _held()
        mail.outbox.clear()
        with patch(CANCEL, return_value=intent("canceled")):
            fee = orientations.cancel_orientation(booking, actor_label="Member", self_cancel=True)
        assert fee is None
        mock_issue.assert_not_called()
        booking.refresh_from_db()
        assert (booking.status, booking.hold_state) == (Status.CANCELLED, "released")
        cancelled = next(m for m in _member_mail(booking) if "cancelled" in m.subject.lower())
        assert "We released the $15 hold on your card." in cancelled.body
        assert "refund" not in cancelled.body.lower()

    def it_releases_a_held_request_when_its_slot_is_cancelled():
        booking = _held()
        with patch(CANCEL, return_value=intent("canceled")) as cancel:
            orientations.cancel_slot(booking.slot, reason="closed")
        cancel.assert_called_once()
        booking.refresh_from_db()
        assert (booking.status, booking.hold_state) == (Status.CANCELLED, "released")

    @patch("billing.refunds.issue_refund")
    def it_refunds_a_confirmed_booking_whose_hold_was_charged(mock_issue):
        booking = _held(status=Status.CONFIRMED, amount_paid_cents=1500, confirmed_at=timezone.now())
        with patch(CANCEL) as cancel:
            orientations.cancel_orientation(booking, actor_label="the guild")
        cancel.assert_not_called()
        mock_issue.assert_called_once()

    @patch("billing.refunds.issue_refund")
    def it_keeps_todays_refund_for_a_booking_paid_before_holds(mock_issue):
        booking = OrientationBookingFactory(slot=_slot(), amount_paid_cents=1500, stripe_payment_id="pi_old")
        with patch(CANCEL) as cancel:
            orientations.decline_orientation(booking)
        cancel.assert_not_called()
        mock_issue.assert_called_once()

    def it_finalizes_then_releases_a_hold_whose_slot_was_cancelled_mid_checkout():
        hold = OrientationBookingFactory(slot=_slot(), status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold")
        hold.slot.mark_cancelled(reason="closed")
        with patch(CANCEL, return_value=intent("canceled")):
            outcome = orientations.finalize_paid_booking(hold, payment_intent="pi_hold", amount_total=1500, held=True)
        assert outcome == "cancelled_slot"
        hold.refresh_from_db()
        assert (hold.status, hold.hold_state) == (Status.CANCELLED, "released")


def describe_moving_a_held_request():
    def _later():
        return timezone.now() + timedelta(days=9)

    def it_charges_the_hold_because_the_move_confirms_it():
        booking = _held()
        mail.outbox.clear()
        with patch(CAPTURE, return_value=intent("succeeded")) as capture:
            moved = orientations.move_orientation(booking, _later(), actor=None)
        capture.assert_called_once()
        assert (moved.status, moved.amount_paid_cents) == (Status.CONFIRMED, 1500)
        email = next(m for m in _member_mail(booking) if "new time" in m.subject.lower())
        assert "we charged the $15 held on your card" in email.body
        assert "Nothing was refunded or charged" not in email.body

    def it_declines_instead_of_moving_when_the_card_will_not_charge():
        booking = _held()
        old_slot = booking.slot_id
        with (
            patch(CAPTURE, return_value=intent("canceled")),
            patch(CANCEL, return_value=intent("canceled")),
            pytest.raises(HoldCaptureFailed),
        ):
            orientations.move_orientation(booking, _later(), actor=None)
        booking.refresh_from_db()
        assert (booking.status, booking.slot_id, booking.amount_paid_cents) == (Status.DECLINED, old_slot, 0)

    def it_moves_nothing_when_stripe_cannot_be_reached():
        booking = _held()
        old_slot = booking.slot_id
        with (
            patch(CAPTURE, side_effect=RuntimeError("down")),
            patch(READ, side_effect=RuntimeError("down")),
            pytest.raises(HoldCaptureUnavailable),
        ):
            orientations.move_orientation(booking, _later(), actor=None)
        booking.refresh_from_db()
        assert (booking.status, booking.slot_id) == (Status.REQUESTED, old_slot)

    def it_never_charges_again_when_moving_a_confirmed_booking():
        booking = _held(status=Status.CONFIRMED, amount_paid_cents=1500, confirmed_at=timezone.now())
        with patch(CAPTURE) as capture:
            orientations.move_orientation(booking, _later(), actor=None)
        capture.assert_not_called()

    def _proposed(booking: OrientationBooking) -> OrientationBooking:
        return orientations.propose_orientation_time(booking, _later(), actor=None)

    def it_tells_the_member_accepting_a_new_time_charges_the_hold():
        booking = _held()
        mail.outbox.clear()
        _proposed(booking)
        email = next(m for m in _member_mail(booking) if "a new time" in m.subject.lower())
        assert "Accepting confirms it and charges the $15 held on your card." in email.body

    def it_charges_the_hold_when_the_member_accepts_a_new_time():
        proposed = _proposed(_held())
        with patch(CAPTURE, return_value=intent("succeeded")):
            assert orientations.answer_proposal(proposed, accept=True, proposal=proposed.proposal_key) == "accepted"
        proposed.refresh_from_db()
        assert (proposed.status, proposed.amount_paid_cents) == (Status.CONFIRMED, 1500)

    def it_declines_when_the_accepted_time_cannot_be_charged():
        proposed = _proposed(_held())
        with patch(CAPTURE, return_value=intent("canceled")), patch(CANCEL, return_value=intent("canceled")):
            assert orientations.answer_proposal(proposed, accept=True, proposal=proposed.proposal_key) == "card_failed"
        proposed.refresh_from_db()
        assert proposed.status == Status.DECLINED

    def it_changes_nothing_when_stripe_is_down_as_the_member_accepts():
        proposed = _proposed(_held())
        with patch(CAPTURE, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            assert orientations.answer_proposal(proposed, accept=True, proposal=proposed.proposal_key) == "try_again"
        proposed.refresh_from_db()
        assert proposed.status == Status.REQUESTED
        assert proposed.has_open_proposal


def describe_release_unconfirmed_holds():
    def _aged(days: float, **overrides) -> OrientationBooking:
        return _held(authorized_at=timezone.now() - timedelta(days=days), **overrides)

    def it_declines_a_request_unconfirmed_six_days_after_its_hold_and_releases_it():
        booking = _aged(6.1)
        mail.outbox.clear()
        with patch(CANCEL, return_value=intent("canceled")) as cancel:
            assert orientations.release_unconfirmed_holds() == (1, 0)
        cancel.assert_called_once_with(payment_intent_id="pi_hold")
        booking.refresh_from_db()
        assert (booking.status, booking.hold_state) == (Status.DECLINED, "released")
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_DECLINED).exists()
        declined = next(m for m in _member_mail(booking) if "about your orientation request" in m.subject.lower())
        assert "wasn't confirmed in time, so we closed it before the hold on your card ran out." in declined.body
        assert "We released the $15 hold on your card. You were not charged." in declined.body

    def it_leaves_a_request_still_inside_the_window():
        booking = _aged(5.9)
        with patch(CANCEL) as cancel:
            assert orientations.release_unconfirmed_holds() == (0, 0)
        cancel.assert_not_called()
        booking.refresh_from_db()
        assert booking.status == Status.REQUESTED

    def it_reads_the_window_from_the_now_it_is_given():
        booking = _held()
        with patch(CANCEL, return_value=intent("canceled")):
            assert orientations.release_unconfirmed_holds(now=timezone.now() + timedelta(days=7)) == (1, 0)
        booking.refresh_from_db()
        assert booking.status == Status.DECLINED

    def it_never_touches_a_confirmed_booking_or_one_with_no_hold():
        _aged(10, status=Status.CONFIRMED, amount_paid_cents=1500)
        OrientationBookingFactory(slot=_slot())
        with patch(CANCEL) as cancel:
            assert orientations.release_unconfirmed_holds() == (0, 0)
        cancel.assert_not_called()

    def it_retries_a_hold_stripe_did_not_release_earlier():
        booking = _held(status=Status.DECLINED)
        with patch(CANCEL, return_value=intent("canceled")):
            assert orientations.release_unconfirmed_holds() == (0, 1)
        booking.refresh_from_db()
        assert booking.hold_state == "released"

    def it_counts_nothing_when_the_retry_fails_again():
        _held(status=Status.CANCELLED)
        with patch(CANCEL, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            assert orientations.release_unconfirmed_holds() == (0, 0)

    def it_leaves_a_request_a_confirm_answered_first():
        booking = _aged(7)
        OrientationBooking.objects.filter(pk=booking.pk).update(status=Status.CONFIRMED)
        assert orientations._decline_unconfirmed(booking, reason=orientations.DECLINE_HOLD_EXPIRED) is False
        booking.refresh_from_db()
        assert booking.status == Status.CONFIRMED

    def it_runs_from_the_scheduler_every_fifteen_minutes():
        from core.scheduled_jobs import JOBS_BY_KEY, Cadence

        job = JOBS_BY_KEY["release_orientation_card_holds"]
        assert job.cadence == Cadence.ALWAYS
        assert job.command == "release_orientation_card_holds"

    def it_reports_its_counts_from_the_command():
        _aged(8)
        out = StringIO()
        with patch(CANCEL, return_value=intent("canceled")):
            call_command("release_orientation_card_holds", stdout=out)
        assert "Declined 1 unconfirmed request(s); released 0 leftover hold(s)." in out.getvalue()


def describe_review_probes():
    """The independent review of PR #1130: each probe, kept as a spec."""

    @patch("billing.refunds.issue_refund")
    def it_refunds_a_lost_capture_the_retry_sweep_finds(mock_issue):
        # Stripe captured on confirm but neither the reply nor the read-back arrived; the request
        # was then declined while Stripe was still unreachable; the retry finds it charged.
        booking = _held()
        with patch(CAPTURE, side_effect=RuntimeError("timeout")), patch(READ, side_effect=RuntimeError("down")):
            with pytest.raises(HoldCaptureUnavailable):
                orientations.confirm_orientation(booking)
        with patch(CANCEL, side_effect=RuntimeError("captured")), patch(READ, side_effect=RuntimeError("down")):
            orientations.decline_orientation(booking)
        booking.refresh_from_db()
        assert (booking.status, booking.hold_state) == (Status.DECLINED, "held")
        mock_issue.assert_not_called()
        with patch(CANCEL, side_effect=RuntimeError("captured")), patch(READ, return_value=intent("succeeded")):
            assert orientations.release_unconfirmed_holds() == (0, 1)
        booking.refresh_from_db()
        assert booking.amount_paid_cents == 1500
        mock_issue.assert_called_once()

    @patch("billing.refunds.issue_refund")
    def it_tells_the_truth_when_the_sweep_finds_a_capture_a_rollback_lost(mock_issue):
        booking = _held(authorized_at=timezone.now() - timedelta(days=7))
        with (
            patch(CAPTURE, return_value=intent("succeeded")),
            patch.object(OrientationBooking, "confirm", side_effect=RuntimeError("db blip")),
            pytest.raises(RuntimeError),
        ):
            orientations.confirm_orientation(booking)
        booking.refresh_from_db()
        assert (booking.status, booking.amount_paid_cents) == (Status.REQUESTED, 0)
        mail.outbox.clear()
        with patch(CANCEL, side_effect=RuntimeError("captured")), patch(READ, return_value=intent("succeeded")):
            assert orientations.release_unconfirmed_holds() == (1, 0)
        mock_issue.assert_called_once()
        declined = next(m for m in _member_mail(booking) if "about your orientation request" in m.subject.lower())
        assert "You were not charged" not in _body(declined)
        assert "refund" in declined.body

    def it_never_marks_a_request_holding_a_card_oriented():
        booking = _held(authorized_at=timezone.now() - timedelta(days=7))
        with pytest.raises(OrientationError, match="Confirm it first"):
            booking.mark_completed()
        booking.refresh_from_db()
        assert not booking.is_completed

    def it_says_the_card_was_not_charged_on_a_card_that_would_not_charge():
        booking = _held()
        mail.outbox.clear()
        with (
            patch(CAPTURE, return_value=intent("canceled")),
            patch(CANCEL, return_value=intent("canceled")),
            pytest.raises(HoldCaptureFailed),
        ):
            orientations.confirm_orientation(booking)
        declined = next(m for m in _member_mail(booking) if "about your orientation request" in m.subject.lower())
        assert "We couldn't charge your card when your request was confirmed, so we had to decline it." in declined.body
        assert "We released the $15 hold on your card. You were not charged." in declined.body

    def it_says_the_hold_is_still_being_released_when_the_sweep_release_fails():
        booking = _held(authorized_at=timezone.now() - timedelta(days=7))
        mail.outbox.clear()
        with patch(CANCEL, side_effect=RuntimeError("busy")), patch(READ, return_value=intent("requires_capture")):
            orientations.release_unconfirmed_holds()
        declined = next(m for m in _member_mail(booking) if "about your orientation request" in m.subject.lower())
        assert "We're releasing the $15 hold on your card." in declined.body
        assert "We released" not in declined.body

    def it_tells_billing_admins_the_card_was_not_charged_only_when_stripe_said_so():
        _billing_approver()
        still_held = _held()
        unreadable = _held()
        mail.outbox.clear()
        with patch(CANCEL, side_effect=RuntimeError("busy")), patch(READ, return_value=intent("requires_capture")):
            orientations.decline_orientation(still_held)
        with patch(CANCEL, side_effect=RuntimeError("down")), patch(READ, side_effect=RuntimeError("down")):
            orientations.decline_orientation(unreadable)
        alerts = {m.body for m in mail.outbox if m.subject == "An orientation card hold was not released"}
        held_alert = next(body for body in alerts if f"/{still_held.pk}/" in body)
        unknown_alert = next(body for body in alerts if f"/{unreadable.pk}/" in body)
        assert "They were not charged." in held_alert
        assert "They were not charged." not in unknown_alert
        assert "may not have been released" in unknown_alert
        assert "Check the payment in Stripe" in unknown_alert


def describe_stripes_capture_deadline():
    """The sweep declines a day before Stripe's own capture deadline when Stripe gives one."""

    def _session():
        return {"status": "complete", "payment_status": "unpaid", "payment_intent": "pi_hold"}

    def it_reads_the_deadline_from_the_charge():
        deadline = timezone.now() + timedelta(days=5)
        with patch(READ, return_value=intent("requires_capture", capture_before=int(deadline.timestamp()))):
            landed = orientations.landed_payment(_session())
        assert landed is not None
        assert landed.capture_before == deadline.replace(microsecond=0)

    def it_declines_a_day_before_a_short_stripe_deadline():
        hold = OrientationBookingFactory(slot=_slot(), status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold")
        deadline = timezone.now() + timedelta(days=5)
        orientations.finalize_paid_booking(
            hold, payment_intent="pi_hold", amount_total=1500, held=True, capture_before=deadline
        )
        hold.refresh_from_db()
        assert hold.hold_release_by == deadline - OrientationBooking.HOLD_RELEASE_MARGIN
        with patch(CANCEL, return_value=intent("canceled")):
            assert orientations.release_unconfirmed_holds(now=timezone.now() + timedelta(days=3)) == (0, 0)
            assert orientations.release_unconfirmed_holds(now=timezone.now() + timedelta(days=4, hours=1)) == (1, 0)

    def it_falls_back_to_six_days_without_a_stripe_deadline():
        hold = OrientationBookingFactory(slot=_slot(), status=Status.PENDING_PAYMENT, stripe_session_id="cs_hold")
        orientations.finalize_paid_booking(hold, payment_intent="pi_hold", amount_total=1500, held=True)
        hold.refresh_from_db()
        assert hold.hold_release_by == hold.authorized_at + OrientationBooking.HOLD_CONFIRM_WINDOW


def describe_finance_readers_ignore_uncharged_holds():
    """Payouts, the Payments panel, Reconciliation and the fee sync count captured money only."""

    def _window():
        from billing.payments_panel import PanelWindow

        today = timezone.localdate()
        return PanelWindow(start=today - timedelta(days=1), end=today + timedelta(days=1))

    def _orientor_booking(**overrides) -> OrientationBooking:
        orientor = MemberFactory()
        return _held(oriented_by=orientor, **overrides)

    def it_leaves_a_held_request_out_of_every_reader():
        from billing import payouts, stripe_fees
        from billing.payments_panel import _orientation_rows
        from billing.reconciliation import _orientation_lines

        booking = _orientor_booking()
        assert not payouts.has_earning(booking.oriented_by)
        assert booking not in payouts._bookings()
        assert _orientation_rows(_window(), viewer_is_admin=True) == []
        assert _orientation_lines(_window(), {}, {}) == []
        queryset, _field = stripe_fees.paid_sources()["orientation"]
        assert booking not in queryset

    def it_counts_the_booking_once_the_hold_is_charged():
        from billing import payouts
        from billing.payments_panel import _orientation_rows

        booking = _orientor_booking(status=Status.CONFIRMED, amount_paid_cents=1500, paid_at=timezone.now())
        assert payouts.has_earning(booking.oriented_by)
        assert booking in payouts._bookings()
        assert [row.amount_cents for row in _orientation_rows(_window(), viewer_is_admin=True)] == [1500]

    def it_leaves_a_released_hold_out_too():
        from billing import payouts

        booking = _orientor_booking(status=Status.DECLINED, hold_released_at=timezone.now())
        assert not payouts.has_earning(booking.oriented_by)

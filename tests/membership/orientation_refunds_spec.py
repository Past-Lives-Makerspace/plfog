"""BDD specs for automatic refunds on decline/cancel — the billing-service boundary is mocked."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.db.models.signals import post_save
from factory.django import mute_signals

from billing.exceptions import RefundError
from billing.models import PaymentRefund
from membership import orientations
from membership.models import OrientationBooking
from tests.membership.factories import (
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _paid_booking(status=OrientationBooking.Status.REQUESTED, **overrides):
    settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
    slot = OrientationSlotFactory(guild=settings_obj.guild)
    defaults = {
        "slot": slot,
        "status": status,
        "amount_paid_cents": 1500,
        "stripe_payment_id": "pi_paid_1",
        "stripe_session_id": "cs_paid_1",
    }
    defaults.update(overrides)
    return OrientationBookingFactory(**defaults)


def _member_with_user(username: str):
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(username=username, email=f"{username}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    return member


def describe_decline_refunds():
    @patch("billing.refunds.issue_refund")
    def it_issues_a_full_refund_exactly_once(mock_issue):
        booking = _paid_booking()
        orientations.decline_orientation(booking)
        mock_issue.assert_called_once()
        args, kwargs = mock_issue.call_args
        assert args[0] == booking
        assert kwargs["amount_cents"] is None  # full refund

    @patch("billing.refunds.issue_refund")
    def it_credits_the_acting_user(mock_issue):
        member = _member_with_user("decliner")
        booking = _paid_booking()
        orientations.decline_orientation(booking, actor=member.user)
        assert mock_issue.call_args.kwargs["actor"] == member.user

    @patch("billing.refunds.issue_refund")
    def it_never_touches_the_engine_for_a_free_booking(mock_issue):
        booking = OrientationBookingFactory()
        orientations.decline_orientation(booking)
        mock_issue.assert_not_called()

    @patch("billing.refunds.issue_refund")
    def it_never_double_refunds(mock_issue):
        booking = _paid_booking()
        PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.SUCCEEDED
        )
        orientations.decline_orientation(booking)
        mock_issue.assert_not_called()

    @patch("billing.refunds.issue_refund", side_effect=RefundError("bank said no"))
    def it_flags_a_refund_failure_without_blocking_the_decline(mock_issue):
        booking = _paid_booking()
        mail.outbox.clear()

        orientations.decline_orientation(booking)  # must not raise

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED
        assert any("about your orientation request" in m.subject.lower() for m in mail.outbox)


def describe_cancel_refunds():
    @patch("billing.refunds.issue_refund")
    def it_refunds_a_member_cancel(mock_issue):
        booking = _paid_booking(status=OrientationBooking.Status.CONFIRMED)
        orientations.cancel_orientation(booking, actor_label=booking.member.display_name)
        mock_issue.assert_called_once()

    @patch("billing.refunds.issue_refund")
    def it_refunds_every_paid_booking_on_a_slot_cancel(mock_issue):
        booking = _paid_booking()
        orientations.cancel_slot(booking.slot, reason="closed")
        mock_issue.assert_called_once()
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED


def describe_token_action_refund_attribution():
    @patch("billing.refunds.issue_refund")
    def it_credits_the_token_recipient_on_an_email_link_decline(mock_issue):
        recipient = _member_with_user("orienter1")
        booking = _paid_booking()
        token = orientations.make_action_token(booking, "decline", recipient=recipient)
        decoded_booking, action, decoded_recipient = orientations.read_action_token(token)

        result = orientations.apply_token_action(decoded_booking, action, recipient=decoded_recipient)

        assert result == "declined"
        assert mock_issue.call_args.kwargs["actor"] == recipient.user

    @patch("billing.refunds.issue_refund")
    def it_falls_back_to_system_for_legacy_payloads(mock_issue):
        booking = _paid_booking()
        token = orientations.make_action_token(booking, "decline")  # no recipient stamped
        decoded_booking, action, decoded_recipient = orientations.read_action_token(token)

        orientations.apply_token_action(decoded_booking, action, recipient=decoded_recipient)

        assert decoded_recipient is None
        assert mock_issue.call_args.kwargs["actor"] is None

    @patch("billing.refunds.issue_refund")
    def it_refunds_a_token_cancel_too(mock_issue):
        booking = _paid_booking(status=OrientationBooking.Status.CONFIRMED)
        result = orientations.apply_token_action(booking, "cancel", recipient=booking.member)
        assert result == "cancelled"
        mock_issue.assert_called_once()

    def it_stamps_lead_request_action_links_with_the_primary_responder():
        lead = _member_with_user("leadprime")
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
        settings_obj.guild.guild_lead = lead
        settings_obj.guild.save(update_fields=["guild_lead"])
        slot = OrientationSlotFactory(guild=settings_obj.guild)
        booking = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)

        orientations.finalize_paid_booking(booking, payment_intent="pi_x", amount_total=1500)

        lead_email = next(m for m in mail.outbox if "new orientation request" in m.subject.lower())
        token = lead_email.body.split("/orientation/act/")[1].split("/")[0]
        _booking, action, recipient = orientations.read_action_token(token)
        assert recipient == lead


def describe_token_recipient_edge_cases():
    def it_reads_a_deleted_recipient_as_none():
        recipient = MemberFactory()
        booking = _paid_booking()
        token = orientations.make_action_token(booking, "decline", recipient=recipient)
        recipient.delete()
        _booking, _action, decoded = orientations.read_action_token(token)
        assert decoded is None

    def it_stamps_the_members_own_cancel_link_with_the_member():
        booking = _paid_booking()
        url = orientations._context(booking)["cancel_url"]
        token = url.split("/orientation/act/")[1].split("/")[0]
        _booking, action, recipient = orientations.read_action_token(token)
        assert action == "cancel"
        assert recipient == booking.member

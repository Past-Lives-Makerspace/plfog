"""Specs for moving an orientation booking to another time (#853).

The service layer: Move it now, Ask the member first, the member's Accept and Keep (by link or
by button), the tidy of the slot a booking leaves, the no refund invariant, and the late fee a
moved booking never carries.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from billing import late_fees
from billing.models import LateCancellationFee, PaymentRefund
from core.models import Notification, SiteActivity, SiteConfiguration
from membership import orientations
from membership.late_cancel import booking_cancel_warning, policy_for
from membership.models import (
    Member,
    OrientationBooking,
    OrientationError,
    OrientationSlot,
)
from tests.membership.factories import (
    EquipmentReservationFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _member(username: str) -> Member:
    MembershipPlanFactory()
    return User.objects.create_user(username=username, email=f"{username}@example.com").member


def _guild_with_lead(username: str = "mv_lead") -> tuple[object, Member]:
    lead = _member(username)
    guild = GuildFactory(guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    return guild, lead


def _booking(
    *,
    username: str = "mv_member",
    status: str = OrientationBooking.Status.REQUESTED,
    lead_name: str = "mv_lead",
    **slot_kwargs: object,
) -> tuple[OrientationBooking, Member]:
    guild, lead = _guild_with_lead(lead_name)
    orientation_type = OrientationTypeFactory(guild=guild, name="Wheel", duration_minutes=90)
    slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type, **slot_kwargs)
    booking = OrientationBookingFactory(slot=slot, member=_member(username), status=status)
    return booking, lead


def _soon(days: int = 5, hour: int = 14) -> object:
    """A future local time on the half hour grid."""
    local = timezone.localtime(timezone.now() + timedelta(days=days))
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


def _ics(message: object) -> bytes:
    _filename, content, _mime = message.attachments[0]
    return content if isinstance(content, bytes) else content.encode()


def _paid(booking: OrientationBooking) -> OrientationBooking:
    booking.amount_paid_cents = 4000
    booking.stripe_session_id = "cs_move_1"
    booking.stripe_payment_id = "pi_move_1"
    booking.save(update_fields=["amount_paid_cents", "stripe_session_id", "stripe_payment_id"])
    return booking


def _money(booking: OrientationBooking) -> tuple[int, str, str, int]:
    booking.refresh_from_db()
    return (
        booking.amount_paid_cents,
        booking.stripe_session_id,
        booking.stripe_payment_id,
        PaymentRefund.objects.filter(orientation_booking=booking).count(),
    )


def describe_the_booking_model():
    def it_is_movable_only_while_requested_or_confirmed_and_not_oriented():
        booking = OrientationBookingFactory()
        assert booking.is_movable
        booking.status = OrientationBooking.Status.CONFIRMED
        assert booking.is_movable
        booking.is_completed = True
        assert not booking.is_movable
        for status in (
            OrientationBooking.Status.PENDING_PAYMENT,
            OrientationBooking.Status.DECLINED,
            OrientationBooking.Status.CANCELLED,
        ):
            booking.is_completed = False
            booking.status = status
            assert not booking.is_movable

    def it_has_no_proposal_key_until_a_proposal_is_made():
        booking = OrientationBookingFactory()
        assert booking.proposal_key == ""
        assert not booking.has_open_proposal
        assert not booking.moved_by_guild

    def it_keys_two_proposals_apart():
        booking = OrientationBookingFactory()
        booking.propose_time(_soon(), proposed_by=None)
        first = booking.proposal_key
        booking.propose_time(_soon(6), proposed_by=None)
        assert first and booking.proposal_key and first != booking.proposal_key

    def it_refuses_to_move_a_checkout_hold_with_the_hold_message():
        booking = OrientationBookingFactory(status=OrientationBooking.Status.PENDING_PAYMENT)
        with pytest.raises(OrientationError, match="still finishing checkout"):
            booking.ensure_movable()

    def it_refuses_to_move_a_completed_orientation():
        booking = OrientationBookingFactory(status=OrientationBooking.Status.CONFIRMED, is_completed=True)
        with pytest.raises(OrientationError, match="already happened"):
            booking.ensure_movable()


def describe_move_orientation():
    def it_puts_a_request_on_a_one_off_slot_with_the_same_orienter_confirmed():
        orienter = MemberFactory()
        booking, lead = _booking(orienter=orienter, location="Back room")
        old_slot = booking.slot
        new_start = _soon()

        orientations.move_orientation(booking, new_start, actor=lead)

        booking.refresh_from_db()
        slot = booking.slot
        assert slot.pk != old_slot.pk
        assert slot.starts_at == new_start
        assert slot.ends_at == new_start + timedelta(minutes=90)
        assert slot.orienter == orienter
        assert slot.location == "Back room"
        assert slot.seats == 1
        assert slot.source == OrientationSlot.Source.CUSTOM
        assert slot.guild_id == booking.guild_id
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert booking.confirmed_at is not None
        assert booking.oriented_by == lead
        assert booking.moved_by_guild_at is not None
        assert SiteActivity.objects.filter(
            kind=SiteActivity.Kind.ORIENTATION_CONFIRMED, actor=lead.user, target_id=booking.pk
        ).exists()

    def it_uses_the_types_default_place_when_the_old_slot_had_none():
        booking, lead = _booking(location="")
        booking.orientation_type.default_location = "Kiln yard"
        booking.orientation_type.save(update_fields=["default_location"])

        orientations.move_orientation(booking, _soon(), actor=lead)

        booking.refresh_from_db()
        assert booking.slot.location == "Kiln yard"

    def it_keeps_a_confirmed_bookings_runner_and_confirmation_time():
        booking, lead = _booking(status=OrientationBooking.Status.CONFIRMED)
        runner = MemberFactory()
        confirmed_at = timezone.now() - timedelta(days=3)
        booking.oriented_by = runner
        booking.confirmed_at = confirmed_at
        booking.save(update_fields=["oriented_by", "confirmed_at"])

        orientations.move_orientation(booking, _soon(), actor=lead)

        booking.refresh_from_db()
        assert booking.oriented_by == runner
        assert booking.confirmed_at == confirmed_at
        assert not SiteActivity.objects.filter(kind=SiteActivity.Kind.ORIENTATION_CONFIRMED).exists()

    def it_emails_the_member_the_old_and_new_time_with_an_updated_invite():
        booking, lead = _booking()
        old_start = booking.slot.starts_at
        new_start = _soon()

        orientations.move_orientation(booking, new_start, actor=lead)

        booking.refresh_from_db()
        sent = [m for m in mail.outbox if m.to == [booking.member.primary_email]]
        assert len(sent) == 1
        message = sent[0]
        assert message.subject == f"Your {booking.orientation_type.owner_name} orientation has a new time"
        old_local = timezone.localtime(old_start)
        new_local = timezone.localtime(new_start)
        assert f"Was: {old_local:%A, %B} {old_local.day}" in message.body
        assert f"Now: {new_local:%A, %B} {new_local.day}" in message.body
        assert "never costs a late fee" in message.body
        ics = _ics(message)
        assert b"METHOD:REQUEST" in ics
        assert b"STATUS:CONFIRMED" in ics
        assert f"UID:orientation-{booking.pk}@pastlives".encode() in ics
        assert b"SEQUENCE:" in ics
        assert Notification.objects.filter(
            user=booking.member.user, trigger="orientation_update", title="Your orientation moved"
        ).exists()

    def it_emails_again_on_a_second_move():
        booking, lead = _booking()

        orientations.move_orientation(booking, _soon(4), actor=lead)
        orientations.move_orientation(booking, _soon(6), actor=lead)

        assert len([m for m in mail.outbox if m.to == [booking.member.primary_email]]) == 2

    def it_never_refunds_or_charges_a_paid_booking():
        booking, lead = _booking(status=OrientationBooking.Status.CONFIRMED)
        _paid(booking)
        before = _money(booking)

        with patch("billing.refunds.issue_refund") as refund, patch("billing.stripe_utils.stripe") as stripe:
            orientations.move_orientation(booking, _soon(), actor=lead)

        assert _money(booking) == before == (4000, "cs_move_1", "pi_move_1", 0)
        refund.assert_not_called()
        assert stripe.mock_calls == []

    def it_frees_the_seat_on_a_shared_slot_and_keeps_the_slot():
        booking, lead = _booking(seats=4)
        old_slot = booking.slot

        orientations.move_orientation(booking, _soon(), actor=lead)

        old_slot.refresh_from_db()
        assert old_slot.seats_taken == 0
        assert not old_slot.is_cancelled

    def it_deletes_the_bookings_own_one_off_once_it_is_empty():
        booking, lead = _booking(seats=1, source=OrientationSlot.Source.CUSTOM)
        old_pk = booking.slot.pk

        orientations.move_orientation(booking, _soon(), actor=lead)

        assert not OrientationSlot.objects.filter(pk=old_pk).exists()

    def it_deletes_a_carved_window_segment_once_it_is_empty():
        booking, lead = _booking(seats=1, source=OrientationSlot.Source.FROM_BLOCK)
        old_pk = booking.slot.pk

        orientations.move_orientation(booking, _soon(), actor=lead)

        assert not OrientationSlot.objects.filter(pk=old_pk).exists()

    def it_deletes_the_slot_an_earlier_move_made():
        booking, lead = _booking()
        orientations.move_orientation(booking, _soon(4), actor=lead)
        booking.refresh_from_db()
        first_move = booking.slot.pk

        orientations.move_orientation(booking, _soon(6), actor=lead)

        assert not OrientationSlot.objects.filter(pk=first_move).exists()

    def it_keeps_a_one_seat_time_a_leader_posted_open_as_a_decline_would():
        booking, lead = _booking(seats=1, source=OrientationSlot.Source.MANUAL)
        old_slot = booking.slot

        orientations.move_orientation(booking, _soon(), actor=lead)

        old_slot.refresh_from_db()
        assert not old_slot.is_cancelled
        assert old_slot.seats_remaining == 1

    def it_cancels_an_own_one_off_with_history_instead_of_deleting_it():
        booking, lead = _booking(seats=1, source=OrientationSlot.Source.CUSTOM)
        old_slot = booking.slot
        earlier = OrientationBookingFactory(slot=old_slot, status=OrientationBooking.Status.DECLINED)

        orientations.move_orientation(booking, _soon(), actor=lead)

        old_slot.refresh_from_db()
        assert old_slot.is_cancelled
        assert old_slot.cancelled_reason == orientations._OWN_SLOT_REASON
        assert OrientationBooking.objects.filter(pk=earlier.pk).exists()

    def it_keeps_the_reason_on_an_own_one_off_already_cancelled():
        booking, lead = _booking(seats=1, source=OrientationSlot.Source.CUSTOM)
        old_slot = booking.slot
        OrientationBookingFactory(slot=old_slot, status=OrientationBooking.Status.CANCELLED)
        old_slot.mark_cancelled(reason="Called off by the guild")

        orientations.move_orientation(booking, _soon(), actor=lead)

        old_slot.refresh_from_db()
        assert old_slot.cancelled_reason == "Called off by the guild"

    def it_leaves_an_own_slot_that_still_holds_someone_alone():
        booking, lead = _booking(seats=2, source=OrientationSlot.Source.CUSTOM)
        old_slot = booking.slot
        OrientationBookingFactory(slot=old_slot, status=OrientationBooking.Status.REQUESTED)

        orientations.move_orientation(booking, _soon(), actor=lead)

        old_slot.refresh_from_db()
        assert not old_slot.is_cancelled
        assert old_slot.seats_taken == 1

    def it_retires_a_generated_slot_whose_hours_are_paused():
        guild, lead = _guild_with_lead("mv_gen_lead")
        rule = OrientationAvailabilityFactory(guild=guild, is_active=False)
        slot = OrientationSlotFactory(
            guild=guild,
            orientation_type=rule.orientation_type,
            availability=rule,
            source=OrientationSlot.Source.GENERATED,
            seats=1,
        )
        booking = OrientationBookingFactory(slot=slot, member=_member("mv_gen_member"))

        orientations.move_orientation(booking, _soon(), actor=lead)

        slot.refresh_from_db()
        assert slot.is_cancelled
        assert slot.availability is None

    def it_moves_an_equipment_owned_booking():
        manager = MemberFactory()
        booking = OrientationBookingFactory(equipment_owned=True, member=_member("mv_eq_member"))

        orientations.move_orientation(booking, _soon(), actor=manager)

        booking.refresh_from_db()
        assert booking.slot.guild_id is None
        assert booking.slot.orientation_type.is_equipment_owned
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert len(mail.outbox) == 1

    def it_clears_an_open_proposal():
        booking, lead = _booking()
        booking.propose_time(_soon(3), proposed_by=lead)

        orientations.move_orientation(booking, _soon(), actor=lead)

        booking.refresh_from_db()
        assert not booking.has_open_proposal
        assert booking.proposed_by is None
        assert booking.proposed_at is None

    def it_refuses_a_time_already_past_and_changes_nothing():
        booking, lead = _booking()
        old_slot_pk = booking.slot_id
        slots_before = OrientationSlot.objects.count()

        with pytest.raises(OrientationError, match="already passed"):
            orientations.move_orientation(booking, timezone.now() - timedelta(hours=1), actor=lead)

        booking.refresh_from_db()
        assert booking.slot_id == old_slot_pk
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert OrientationSlot.objects.count() == slots_before
        assert mail.outbox == []

    def it_refuses_a_time_the_equipment_is_reserved():
        booking = OrientationBookingFactory(equipment_owned=True, member=_member("mv_res_member"))
        equipment = booking.orientation_type.equipment
        new_start = _soon()
        EquipmentReservationFactory(
            equipment=equipment, starts_at=new_start + timedelta(minutes=15), ends_at=new_start + timedelta(hours=2)
        )

        with pytest.raises(OrientationError, match="reserved"):
            orientations.move_orientation(booking, new_start, actor=None)

        booking.refresh_from_db()
        assert booking.moved_by_guild_at is None

    @pytest.mark.parametrize(
        ("status", "completed"),
        [
            (OrientationBooking.Status.PENDING_PAYMENT, False),
            (OrientationBooking.Status.DECLINED, False),
            (OrientationBooking.Status.CANCELLED, False),
            (OrientationBooking.Status.CONFIRMED, True),
        ],
    )
    def it_refuses_a_booking_that_cannot_move(status: str, completed: bool):
        booking = OrientationBookingFactory(status=status, is_completed=completed)
        slot_pk = booking.slot_id

        with pytest.raises(OrientationError):
            orientations.move_orientation(booking, _soon(), actor=None)

        booking.refresh_from_db()
        assert booking.slot_id == slot_pk
        assert booking.moved_by_guild_at is None


def describe_propose_orientation_time():
    def it_records_the_proposal_and_leaves_the_time_and_status():
        booking, lead = _booking()
        slot_pk = booking.slot_id
        proposed = _soon()

        orientations.propose_orientation_time(booking, proposed, actor=lead)

        booking.refresh_from_db()
        assert booking.slot_id == slot_pk
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.proposed_starts_at == proposed
        assert booking.proposed_by == lead
        assert booking.proposed_at is not None
        assert booking.moved_by_guild_at is None

    def it_emails_accept_and_keep_links_and_rings_the_members_bell():
        booking, lead = _booking()

        orientations.propose_orientation_time(booking, _soon(), actor=lead)

        booking.refresh_from_db()
        message = mail.outbox[0]
        assert message.to == [booking.member.primary_email]
        assert message.subject == f"A new time for your {booking.orientation_type.owner_name} orientation"
        assert message.attachments == []
        assert "Accept the new time: " in message.body
        assert "Keep my current time: " in message.body
        assert Notification.objects.filter(
            user=booking.member.user, trigger="orientation_update", title="A new time for your orientation"
        ).exists()

    def it_links_tokens_that_name_this_proposal():
        booking, lead = _booking()
        orientations.propose_orientation_time(booking, _soon(), actor=lead)
        booking.refresh_from_db()

        body = mail.outbox[0].body
        accept_token = body.split("Accept the new time: ")[1].split()[0].rstrip("/").rsplit("/", 1)[1]
        decoded, action, recipient = orientations.read_action_token(accept_token)
        assert decoded == booking
        assert action == "accept_time"
        assert recipient == booking.member
        assert orientations.read_action_proposal(accept_token) == booking.proposal_key

    def it_replaces_an_open_proposal_and_emails_again():
        booking, lead = _booking()
        orientations.propose_orientation_time(booking, _soon(3), actor=lead)
        booking.refresh_from_db()
        first_key = booking.proposal_key

        orientations.propose_orientation_time(booking, _soon(6), actor=lead)

        booking.refresh_from_db()
        assert booking.proposed_starts_at == _soon(6)
        assert booking.proposal_key != first_key
        assert len(mail.outbox) == 2
        assert orientations.answer_proposal(booking, accept=True, proposal=first_key) == "stale"

    def it_never_refunds_or_charges():
        booking, lead = _booking(status=OrientationBooking.Status.CONFIRMED)
        _paid(booking)
        before = _money(booking)

        with patch("billing.refunds.issue_refund") as refund:
            orientations.propose_orientation_time(booking, _soon(), actor=lead)

        assert _money(booking) == before
        refund.assert_not_called()

    def it_refuses_a_past_time_and_records_nothing():
        booking, lead = _booking()

        with pytest.raises(OrientationError, match="already passed"):
            orientations.propose_orientation_time(booking, timezone.now() - timedelta(minutes=5), actor=lead)

        booking.refresh_from_db()
        assert not booking.has_open_proposal
        assert mail.outbox == []

    def it_refuses_a_cancelled_booking():
        booking = OrientationBookingFactory(status=OrientationBooking.Status.CANCELLED)

        with pytest.raises(OrientationError):
            orientations.propose_orientation_time(booking, _soon(), actor=None)


def _proposed(**kwargs: object) -> tuple[OrientationBooking, Member]:
    booking, lead = _booking(**kwargs)
    orientations.propose_orientation_time(booking, _soon(), actor=lead)
    booking.refresh_from_db()
    mail.outbox.clear()
    return booking, lead


def describe_answer_proposal():
    def it_accepts_moving_and_confirming_like_move_it_now():
        booking, lead = _proposed()
        key = booking.proposal_key

        assert orientations.answer_proposal(booking, accept=True, proposal=key) == "accepted"

        booking.refresh_from_db()
        assert booking.slot.starts_at == _soon()
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert booking.oriented_by == lead
        assert booking.moved_by_guild_at is not None
        assert not booking.has_open_proposal
        assert [m.to for m in mail.outbox] == [[booking.member.primary_email]]
        assert b"STATUS:CONFIRMED" in _ics(mail.outbox[0])

    def it_tells_the_leaders_who_got_the_request_when_accepted():
        booking, lead = _proposed()

        orientations.answer_proposal(booking, accept=True, proposal=booking.proposal_key)

        bell = Notification.objects.get(user=lead.user, trigger="orientation_requested")
        assert bell.title == "New orientation time accepted"
        assert booking.member.display_name in bell.body

    def it_keeps_the_current_time_and_tells_the_leaders():
        booking, lead = _proposed()
        slot_pk = booking.slot_id

        assert orientations.answer_proposal(booking, accept=False, proposal=booking.proposal_key) == "kept"

        booking.refresh_from_db()
        assert booking.slot_id == slot_pk
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert not booking.has_open_proposal
        assert booking.moved_by_guild_at is None
        bell = Notification.objects.get(user=lead.user, trigger="orientation_requested")
        assert bell.title == "Orientation time kept"
        assert mail.outbox == []

    def it_reports_stale_for_a_link_already_answered():
        booking, _lead = _proposed()
        key = booking.proposal_key
        orientations.answer_proposal(booking, accept=False, proposal=key)

        assert orientations.answer_proposal(booking, accept=True, proposal=key) == "stale"
        assert orientations.answer_proposal(booking, accept=False, proposal="") == "stale"

    def it_reports_passed_when_the_proposed_time_has_come_and_gone():
        booking, _lead = _proposed()
        booking.proposed_starts_at = timezone.now() - timedelta(minutes=1)
        booking.save(update_fields=["proposed_starts_at"])

        assert not booking.has_open_proposal
        assert booking.proposal_has_passed
        assert orientations.answer_proposal(booking, accept=True, proposal=booking.proposal_key) == "passed"
        assert orientations.answer_proposal(booking, accept=False, proposal=booking.proposal_key) == "passed"

    def it_reports_unavailable_when_the_equipment_was_reserved_since():
        booking = OrientationBookingFactory(equipment_owned=True, member=_member("mv_unavail"))
        orientations.propose_orientation_time(booking, _soon(), actor=None)
        booking.refresh_from_db()
        EquipmentReservationFactory(
            equipment=booking.orientation_type.equipment, starts_at=_soon(), ends_at=_soon() + timedelta(hours=1)
        )
        slot_pk = booking.slot_id

        assert orientations.answer_proposal(booking, accept=True, proposal=booking.proposal_key) == "unavailable"

        booking.refresh_from_db()
        assert booking.slot_id == slot_pk
        assert booking.proposed_starts_at == _soon()
        assert not OrientationSlot.objects.filter(source=OrientationSlot.Source.CUSTOM).exists()

    def it_reports_already_for_a_booking_no_longer_live():
        booking, _lead = _proposed()
        key = booking.proposal_key
        OrientationBooking.objects.filter(pk=booking.pk).update(status=OrientationBooking.Status.CANCELLED)
        booking.refresh_from_db()

        assert orientations.answer_proposal(booking, accept=True, proposal=key) == "already"

    def it_never_refunds_or_charges_on_accept_or_keep():
        booking, _lead = _proposed(status=OrientationBooking.Status.CONFIRMED)
        _paid(booking)
        before = _money(booking)

        with patch("billing.refunds.issue_refund") as refund:
            orientations.answer_proposal(booking, accept=True, proposal=booking.proposal_key)

        assert _money(booking) == before
        refund.assert_not_called()

    def it_reports_stale_when_no_proposal_is_open():
        booking = OrientationBookingFactory()
        assert orientations.answer_proposal(booking, accept=True, proposal="1") == "stale"
        assert orientations.answer_proposal(booking, accept=False, proposal="1") == "stale"


def describe_proposal_link_verdict():
    def it_says_nothing_about_another_actions_link():
        booking = OrientationBookingFactory()
        assert orientations.proposal_link_verdict(booking, "cancel", "") == ""

    def it_says_nothing_about_a_live_link():
        booking, _lead = _proposed()
        assert orientations.proposal_link_verdict(booking, "accept_time", booking.proposal_key) == ""

    def it_names_a_replaced_link_stale():
        booking, _lead = _proposed()
        assert orientations.proposal_link_verdict(booking, "keep_time", "99") == "stale"


def describe_races_on_a_stale_instance():
    """Two requests that loaded the booking before either wrote: the second decides on the locked row."""

    def it_lets_only_the_first_of_two_accepts_move_the_booking():
        booking, _lead = _proposed()
        key = booking.proposal_key
        first = OrientationBooking.objects.get(pk=booking.pk)
        second = OrientationBooking.objects.get(pk=booking.pk)

        assert orientations.answer_proposal(first, accept=True, proposal=key) == "accepted"
        assert orientations.answer_proposal(second, accept=True, proposal=key) == "stale"

        booking.refresh_from_db()
        custom = OrientationSlot.objects.filter(source=OrientationSlot.Source.CUSTOM)
        assert list(custom) == [booking.slot]
        assert len([m for m in mail.outbox if "has a new time" in m.subject]) == 1

    def it_never_revives_a_paid_booking_a_lead_cancelled_before_the_accept():
        booking, _lead = _proposed(status=OrientationBooking.Status.CONFIRMED)
        _paid(booking)
        key = booking.proposal_key
        stale = OrientationBooking.objects.get(pk=booking.pk)
        slot_pk = booking.slot_id

        with patch("billing.refunds.issue_refund") as refund:
            orientations.cancel_orientation(booking, actor_label="the guild")
            assert orientations.answer_proposal(stale, accept=True, proposal=key) == "already"

        refund.assert_called_once()
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert booking.slot_id == slot_pk
        assert booking.moved_by_guild_at is None

    def it_never_revives_a_booking_the_member_cancelled_before_a_move():
        booking, lead = _booking(status=OrientationBooking.Status.CONFIRMED)
        stale = OrientationBooking.objects.get(pk=booking.pk)
        orientations.cancel_orientation(booking, actor_label="Member", self_cancel=True)

        with pytest.raises(OrientationError):
            orientations.move_orientation(stale, _soon(), actor=lead)

        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert booking.moved_by_guild_at is None
        assert not OrientationSlot.objects.filter(source=OrientationSlot.Source.CUSTOM).exists()

    def it_never_proposes_on_a_booking_cancelled_first():
        booking, lead = _booking()
        stale = OrientationBooking.objects.get(pk=booking.pk)
        orientations.cancel_orientation(booking, actor_label="Member", self_cancel=True)
        mail.outbox.clear()

        with pytest.raises(OrientationError):
            orientations.propose_orientation_time(stale, _soon(), actor=lead)

        booking.refresh_from_db()
        assert booking.proposed_at is None
        assert mail.outbox == []

    def it_keeps_only_once():
        booking, lead = _proposed()
        key = booking.proposal_key
        stale = OrientationBooking.objects.get(pk=booking.pk)

        assert orientations.answer_proposal(booking, accept=False, proposal=key) == "kept"
        assert orientations.answer_proposal(stale, accept=False, proposal=key) == "stale"
        assert Notification.objects.filter(user=lead.user, trigger="orientation_requested").count() == 1


def describe_proposal_token_actions():
    def it_accepts_through_an_emailed_link():
        booking, _lead = _proposed()
        token = orientations.make_action_token(
            booking, "accept_time", recipient=booking.member, proposal=booking.proposal_key
        )
        decoded, action, recipient = orientations.read_action_token(token)

        result = orientations.apply_token_action(
            decoded, action, recipient=recipient, proposal=orientations.read_action_proposal(token)
        )

        assert result == "accepted"

    def it_keeps_through_an_emailed_link():
        booking, _lead = _proposed()
        token = orientations.make_action_token(booking, "keep_time", proposal=booking.proposal_key)

        result = orientations.apply_token_action(
            booking, "keep_time", proposal=orientations.read_action_proposal(token)
        )

        assert result == "kept"

    def it_reads_no_proposal_from_a_cancel_link():
        booking = OrientationBookingFactory()
        assert orientations.read_action_proposal(orientations.make_action_token(booking, "cancel")) == ""


def describe_decline_and_cancel_clear_the_proposal():
    def it_clears_it_on_decline():
        booking, _lead = _proposed()

        orientations.decline_orientation(booking)

        booking.refresh_from_db()
        assert not booking.has_open_proposal
        assert booking.proposed_by is None

    def it_clears_it_on_cancel():
        booking, _lead = _proposed(status=OrientationBooking.Status.CONFIRMED)

        orientations.cancel_orientation(booking, actor_label="Sam")

        booking.refresh_from_db()
        assert not booking.has_open_proposal
        assert booking.proposed_at is None


def _fee_site() -> None:
    config = SiteConfiguration.load()
    config.late_cancel_fees_enabled = True
    config.save()


def _late_confirmed(username: str) -> tuple[OrientationBooking, Member]:
    """A confirmed booking two hours out on a guild charging a $15 late fee."""
    _fee_site()
    guild, lead = _guild_with_lead(f"{username}_lead")
    guild.orientation_settings.late_cancel_fee_cents = 1500
    guild.orientation_settings.save(update_fields=["late_cancel_fee_cents"])
    starts = timezone.now() + timedelta(days=3)
    slot = OrientationSlotFactory(guild=guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
    booking = OrientationBookingFactory(slot=slot, member=_member(username), status=OrientationBooking.Status.CONFIRMED)
    return booking, lead


def describe_late_fee_after_a_move():
    def it_charges_an_unmoved_booking_cancelled_late_as_the_control():
        booking, lead = _late_confirmed("lf_control")
        orientations.move_orientation(booking, timezone.now() + timedelta(hours=2), actor=lead)
        OrientationBooking.objects.filter(pk=booking.pk).update(moved_by_guild_at=None)
        booking.refresh_from_db()

        assert late_fees.would_charge(booking)

    def it_never_charges_a_booking_moved_now():
        booking, lead = _late_confirmed("lf_moved")
        orientations.move_orientation(booking, timezone.now() + timedelta(hours=2), actor=lead)
        booking.refresh_from_db()

        fee = orientations.cancel_orientation(booking, actor_label="Member", self_cancel=True)

        assert fee is None
        assert not LateCancellationFee.objects.filter(orientation_booking=booking).exists()
        assert booking_cancel_warning(booking, policy_for(booking)) == ""

    def it_never_charges_a_booking_moved_by_an_accepted_proposal():
        booking, lead = _late_confirmed("lf_accepted")
        orientations.propose_orientation_time(booking, timezone.now() + timedelta(hours=2), actor=lead)
        booking.refresh_from_db()
        orientations.answer_proposal(booking, accept=True, proposal=booking.proposal_key)
        booking.refresh_from_db()

        assert not late_fees.would_charge(booking)
        assert orientations.cancel_orientation(booking, actor_label="Member", self_cancel=True) is None

    def it_still_refunds_the_paid_amount_in_full_on_that_cancel():
        booking, lead = _late_confirmed("lf_refund")
        _paid(booking)
        orientations.move_orientation(booking, timezone.now() + timedelta(hours=2), actor=lead)
        booking.refresh_from_db()

        with patch("billing.refunds.issue_refund") as refund:
            orientations.cancel_orientation(booking, actor_label="Member", self_cancel=True)

        refund.assert_called_once()
        assert refund.call_args.kwargs["amount_cents"] is None


def describe_build_ics_for_a_moved_booking():
    def it_adds_no_sequence_before_a_move():
        booking = OrientationBookingFactory()
        assert b"SEQUENCE" not in orientations.build_ics(booking, method="REQUEST", status="CONFIRMED")

    def it_adds_the_moves_sequence_after_a_move():
        booking = OrientationBookingFactory()
        booking.moved_by_guild_at = timezone.now()

        ics = orientations.build_ics(booking, method="REQUEST", status="CONFIRMED")

        assert f"SEQUENCE:{orientations.ics_sequence(booking.moved_by_guild_at)}".encode() in ics

    def it_counts_seconds_so_two_moves_in_one_minute_still_climb():
        moved = timezone.now().replace(second=10, microsecond=0)
        assert orientations.ics_sequence(moved + timedelta(seconds=20)) > orientations.ics_sequence(moved)

    def it_stays_inside_a_32_bit_integer_for_decades():
        assert orientations.ics_sequence(timezone.now() + timedelta(days=365 * 60)) < 2**31

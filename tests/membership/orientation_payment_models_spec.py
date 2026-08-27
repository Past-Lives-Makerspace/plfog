"""BDD specs for the paid-orientations state machine: seat holds, guards, and the refund surface."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from billing.models import PaymentRefund
from membership import orientations
from membership.models import OrientationBooking, OrientationError, OrientationSlot
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _hold(slot=None, member=None, **overrides):
    return OrientationBookingFactory(
        slot=slot or OrientationSlotFactory(),
        member=member or MemberFactory(),
        status=OrientationBooking.Status.PENDING_PAYMENT,
        amount_paid_cents=1500,
        **overrides,
    )


def describe_GuildOrientationSettings_price():
    def it_defaults_to_free():
        settings_obj = GuildOrientationSettingsFactory()
        assert settings_obj.price_cents == 0
        assert settings_obj.is_paid is False

    def it_is_paid_when_a_price_is_set():
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
        assert settings_obj.is_paid is True


def describe_seat_holds():
    def it_counts_a_pending_payment_hold_toward_seats_taken():
        slot = OrientationSlotFactory(seats=2)
        _hold(slot=slot)
        assert slot.seats_taken == 1
        assert slot.seats_remaining == 1
        assert slot.pending_hold_count == 1

    def it_fills_the_slot_when_holds_take_the_last_seat():
        slot = OrientationSlotFactory(seats=1)
        _hold(slot=slot)
        assert slot.is_full is True

    def it_frees_the_seat_when_the_hold_is_deleted():
        slot = OrientationSlotFactory(seats=1)
        hold = _hold(slot=slot)
        hold.delete()
        assert slot.is_full is False

    def describe_querysets():
        def it_excludes_holds_from_active(db):
            hold = _hold()
            assert hold not in OrientationBooking.objects.active()

        def it_includes_holds_in_seat_holding(db):
            hold = _hold()
            requested = OrientationBookingFactory()
            holding = OrientationBooking.objects.seat_holding()
            assert hold in holding
            assert requested in holding

        def it_annotates_hold_counts_on_slots(db):
            slot = OrientationSlotFactory(seats=3)
            _hold(slot=slot)
            annotated = OrientationSlot.objects.with_pending_hold_count().get(pk=slot.pk)
            assert annotated.hold_count == 1

    def describe_pending_payment_orientation_for():
        def it_returns_the_members_live_hold(db):
            hold = _hold()
            assert hold.member.pending_payment_orientation_for(hold.guild) == hold

        def it_returns_none_without_a_hold(db):
            booking = OrientationBookingFactory()
            assert booking.member.pending_payment_orientation_for(booking.guild) is None


def describe_booking_guards():
    def it_rejects_a_second_checkout_with_a_friendly_error():
        member = MemberFactory()
        guild = GuildFactory()
        _hold(slot=OrientationSlotFactory(guild=guild), member=member)
        other_slot = OrientationSlotFactory(guild=guild)
        with pytest.raises(OrientationError, match="checkout in progress"):
            other_slot.ensure_bookable_for(member)

    def it_rejects_a_checkout_alongside_a_live_booking():
        member = MemberFactory()
        guild = GuildFactory()
        OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)
        other_slot = OrientationSlotFactory(guild=guild)
        with pytest.raises(OrientationError, match="pending orientation"):
            other_slot.ensure_bookable_for(member)

    def it_names_the_checkout_hold_when_it_fills_the_last_seat():
        slot = OrientationSlotFactory(seats=1)
        _hold(slot=slot)
        with pytest.raises(OrientationError, match="held by a member finishing checkout"):
            slot.ensure_bookable_for(MemberFactory())

    def it_still_blocks_the_concurrent_race_at_the_database():
        member = MemberFactory()
        guild = GuildFactory()
        _hold(slot=OrientationSlotFactory(guild=guild), member=member)
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationBooking.objects.create(
                slot=OrientationSlotFactory(guild=guild),
                guild=guild,
                member=member,
                status=OrientationBooking.Status.PENDING_PAYMENT,
            )

    def it_books_a_free_guild_straight_to_requested():
        slot = OrientationSlotFactory()
        booking = slot.book(MemberFactory())
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.amount_paid_cents == 0


def describe_refund_engine_surface():
    def it_reports_none_with_no_refunds():
        booking = OrientationBookingFactory(amount_paid_cents=1500, stripe_payment_id="pi_1")
        assert booking.refund_state == "none"
        assert booking.refundable_cents == 1500
        assert booking.refund_payment_intent_id == "pi_1"

    def it_reports_full_when_a_succeeded_refund_covers_the_payment():
        booking = OrientationBookingFactory(amount_paid_cents=1500, stripe_payment_id="pi_2")
        PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.SUCCEEDED
        )
        assert booking.refund_state == "full"
        assert booking.refundable_cents == 0

    def it_reports_partial_for_a_smaller_succeeded_refund():
        booking = OrientationBookingFactory(amount_paid_cents=1500, stripe_payment_id="pi_3")
        PaymentRefund.objects.create(
            orientation_booking=booking, amount_cents=500, status=PaymentRefund.Status.SUCCEEDED
        )
        assert booking.refund_state == "partial"

    def it_reports_failed_when_the_latest_attempt_failed_uncovered():
        booking = OrientationBookingFactory(amount_paid_cents=1500, stripe_payment_id="pi_4")
        PaymentRefund.objects.create(orientation_booking=booking, amount_cents=1500, status=PaymentRefund.Status.FAILED)
        assert booking.refund_state == "failed"

    def it_builds_the_receipt_context_around_the_guild(settings):
        booking = OrientationBookingFactory(amount_paid_cents=1500)
        ctx = booking.refund_receipt_context()
        assert ctx["item_title"] == f"Orientation — {booking.guild.name}"
        assert ctx["member"] == booking.member
        assert f"/guilds/{booking.guild.slug}/" in ctx["manage_url"]
        assert ctx["manage_url"].startswith("http")

    def it_does_nothing_on_fully_refunded():
        booking = OrientationBookingFactory(
            amount_paid_cents=1500, stripe_payment_id="pi_5", status=OrientationBooking.Status.CONFIRMED
        )
        booking.on_fully_refunded("goodwill", None)
        booking.refresh_from_db()
        # Money and scheduling stay independent: a manual refund never cancels the booking.
        assert booking.status == OrientationBooking.Status.CONFIRMED


def describe_retire_rule_hold_guard():
    def it_spares_a_generated_slot_with_a_live_checkout_hold():
        rule = OrientationAvailabilityFactory()
        GuildOrientationSettingsFactory(guild=rule.guild, is_enabled=True)
        slot = OrientationSlotFactory(guild=rule.guild, availability=rule, source=OrientationSlot.Source.GENERATED)
        _hold(slot=slot)
        removed, kept = orientations.retire_rule(rule)
        assert (removed, kept) == (0, 1)
        assert OrientationSlot.objects.filter(pk=slot.pk).exists()

    def it_still_removes_an_open_generated_slot():
        rule = OrientationAvailabilityFactory()
        GuildOrientationSettingsFactory(guild=rule.guild, is_enabled=True)
        slot = OrientationSlotFactory(guild=rule.guild, availability=rule, source=OrientationSlot.Source.GENERATED)
        removed, _kept = orientations.retire_rule(rule)
        assert removed == 1
        assert not OrientationSlot.objects.filter(pk=slot.pk).exists()


def describe_price_changes():
    def it_never_alters_a_live_holds_amount():
        settings_obj = GuildOrientationSettingsFactory(price_cents=1500)
        slot = OrientationSlotFactory(guild=settings_obj.guild)
        hold = _hold(slot=slot)
        settings_obj.price_cents = 2500
        settings_obj.save(update_fields=["price_cents"])
        hold.refresh_from_db()
        assert hold.amount_paid_cents == 1500


def describe_checkout_hold_transition_guards():
    def it_refuses_to_decline_a_hold():
        hold = _hold()
        with pytest.raises(OrientationError, match="still finishing checkout"):
            hold.decline(note="no")
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT

    def it_refuses_to_cancel_a_hold():
        hold = _hold()
        with pytest.raises(OrientationError, match="still finishing checkout"):
            hold.cancel()
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT

    def it_refuses_to_confirm_a_hold():
        hold = _hold()
        with pytest.raises(OrientationError, match="still finishing checkout"):
            hold.confirm()
        hold.refresh_from_db()
        assert hold.status == OrientationBooking.Status.PENDING_PAYMENT

    def it_still_transitions_real_bookings():
        booking = OrientationBookingFactory()
        booking.decline(note="busy")
        assert booking.status == OrientationBooking.Status.DECLINED

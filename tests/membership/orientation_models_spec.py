"""Orientation models: settings, slots, bookings, and oriented-status derivation."""

from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

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


def describe_GuildOrientationSettings():
    def describe_thankyou_email_enabled():
        def it_defaults_to_true():
            # The thank-you is on by default (falls back to standard copy) — a guild has
            # to actively opt out, not opt in.
            settings = GuildOrientationSettingsFactory()
            assert settings.thankyou_email_enabled is True

    def describe_resolved_thankyou_subject():
        def it_returns_the_custom_subject_when_set():
            settings = GuildOrientationSettingsFactory(thankyou_email_subject="Hi there")
            assert settings.resolved_thankyou_subject == "Hi there"

        def it_returns_the_standard_subject_when_blank():
            from membership.orientation_copy import standard_thankyou_subject

            settings = GuildOrientationSettingsFactory(thankyou_email_subject="")
            assert settings.resolved_thankyou_subject == standard_thankyou_subject(settings.guild.name)

    def describe_resolved_thankyou_body():
        def it_returns_the_custom_body_when_set():
            settings = GuildOrientationSettingsFactory(thankyou_email_body="Welcome aboard.")
            assert settings.resolved_thankyou_body == "Welcome aboard."

        def it_returns_the_standard_body_when_blank():
            from membership.orientation_copy import STANDARD_THANKYOU_BODY

            settings = GuildOrientationSettingsFactory(thankyou_email_body="")
            assert settings.resolved_thankyou_body == STANDARD_THANKYOU_BODY

    def describe_is_accepting():
        def it_is_true_when_enabled_and_open():
            assert GuildOrientationSettingsFactory(is_enabled=True, is_closed=False).is_accepting is True

        def it_is_false_when_closed():
            assert GuildOrientationSettingsFactory(is_enabled=True, is_closed=True).is_accepting is False

        def it_is_false_when_disabled():
            assert GuildOrientationSettingsFactory(is_enabled=False, is_closed=False).is_accepting is False


def describe_OrientationAvailability():
    def it_rejects_an_end_time_before_the_start():
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationAvailabilityFactory(start_time=time(19, 0), end_time=time(18, 0))


def describe_OrientationSlot():
    def describe_seats():
        def it_counts_active_bookings_toward_seats_taken():
            slot = OrientationSlotFactory(seats=3)
            OrientationBookingFactory(slot=slot)
            confirmed = OrientationBookingFactory(slot=slot, member=MemberFactory())
            confirmed.confirm()
            assert slot.seats_taken == 2
            assert slot.seats_remaining == 1
            assert slot.is_full is False

        def it_frees_a_seat_when_a_booking_is_declined():
            slot = OrientationSlotFactory(seats=1)
            booking = OrientationBookingFactory(slot=slot)
            assert slot.is_full is True
            booking.decline()
            assert slot.is_full is False
            assert slot.seats_remaining == 1

        def it_does_not_count_cancelled_bookings():
            slot = OrientationSlotFactory(seats=2)
            booking = OrientationBookingFactory(slot=slot)
            booking.cancel()
            assert slot.seats_taken == 0

        def it_never_reports_negative_remaining():
            slot = OrientationSlotFactory(seats=1)
            OrientationBookingFactory(slot=slot)
            OrientationBookingFactory(slot=slot, member=MemberFactory())
            assert slot.seats_remaining == 0

    def describe_is_bookable():
        def it_is_true_for_a_future_open_seated_slot():
            assert OrientationSlotFactory(seats=2).is_bookable is True

        def it_is_false_when_cancelled():
            slot = OrientationSlotFactory()
            slot.cancel()
            assert slot.is_bookable is False

        def it_is_false_when_already_started():
            slot = OrientationSlotFactory(
                starts_at=timezone.now() - timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=1)
            )
            assert slot.is_bookable is False

        def it_is_false_when_full():
            slot = OrientationSlotFactory(seats=1)
            OrientationBookingFactory(slot=slot)
            assert slot.is_bookable is False

        def it_is_false_when_the_guild_is_closed():
            guild = GuildFactory()
            GuildOrientationSettingsFactory(guild=guild, is_enabled=True, is_closed=True)
            slot = OrientationSlotFactory(guild=guild, enabled_settings=False)
            assert slot.is_bookable is False

        def it_is_false_when_the_guild_has_no_settings():
            slot = OrientationSlotFactory(enabled_settings=False)
            assert slot.is_bookable is False

    def describe_is_past():
        def it_is_true_after_the_window_ends():
            slot = OrientationSlotFactory(
                starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(hours=1)
            )
            assert slot.is_past is True

        def it_is_false_before_the_window_ends():
            assert OrientationSlotFactory().is_past is False

    def describe_book():
        def it_creates_a_requested_booking():
            slot = OrientationSlotFactory(seats=2)
            member = MemberFactory()
            booking = slot.book(member, note="excited!")
            assert booking.status == OrientationBooking.Status.REQUESTED
            assert booking.member == member
            assert booking.guild_id == slot.guild_id
            assert booking.member_note == "excited!"

        def it_raises_when_the_slot_is_not_bookable():
            slot = OrientationSlotFactory(enabled_settings=False)
            with pytest.raises(OrientationError):
                slot.book(MemberFactory())

        def it_raises_when_already_oriented():
            slot = OrientationSlotFactory()
            member = MemberFactory()
            done = OrientationBookingFactory(slot=OrientationSlotFactory(guild=slot.guild), member=member)
            done.mark_completed()
            with pytest.raises(OrientationError):
                slot.book(member)

        def it_raises_when_a_live_booking_exists_for_the_guild():
            slot = OrientationSlotFactory(seats=5)
            member = MemberFactory()
            slot.book(member)
            with pytest.raises(OrientationError):
                slot.book(member)

    def describe_mark_cancelled():
        def it_flips_the_slot_state_without_touching_bookings():
            slot = OrientationSlotFactory(seats=3)
            booking = OrientationBookingFactory(slot=slot)
            slot.mark_cancelled(reason="weather")
            slot.refresh_from_db()
            booking.refresh_from_db()
            assert slot.is_cancelled is True
            assert slot.cancelled_reason == "weather"
            # mark_cancelled owns the slot state only — bookings are left alone.
            assert booking.status == OrientationBooking.Status.REQUESTED

        def it_defaults_the_reason_to_empty():
            slot = OrientationSlotFactory()
            slot.mark_cancelled()
            slot.refresh_from_db()
            assert slot.is_cancelled is True
            assert slot.cancelled_reason == ""

    def describe_cancel():
        def it_cancels_the_slot_and_its_active_bookings():
            slot = OrientationSlotFactory(seats=3)
            first = OrientationBookingFactory(slot=slot)
            second = OrientationBookingFactory(slot=slot, member=MemberFactory())
            slot.cancel(reason="instructor sick")
            slot.refresh_from_db()
            first.refresh_from_db()
            second.refresh_from_db()
            assert slot.is_cancelled is True
            assert slot.cancelled_reason == "instructor sick"
            assert first.status == OrientationBooking.Status.CANCELLED
            assert second.status == OrientationBooking.Status.CANCELLED


def describe_OrientationBooking():
    def describe_save():
        def it_denormalizes_guild_from_the_slot():
            slot = OrientationSlotFactory()
            booking = OrientationBookingFactory(slot=slot)
            assert booking.guild_id == slot.guild_id

    def describe_confirm():
        def it_sets_confirmed_status_and_timestamp():
            booking = OrientationBookingFactory()
            booking.confirm()
            assert booking.status == OrientationBooking.Status.CONFIRMED
            assert booking.confirmed_at is not None

        def it_defaults_oriented_by_to_the_guild_lead():
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
            booking.confirm()
            assert booking.oriented_by == lead

        def it_honors_an_explicit_giver():
            giver = MemberFactory()
            booking = OrientationBookingFactory()
            booking.confirm(oriented_by=giver)
            assert booking.oriented_by == giver

    def describe_decline():
        def it_sets_declined_status_and_note():
            booking = OrientationBookingFactory()
            booking.decline(note="try next week")
            assert booking.status == OrientationBooking.Status.DECLINED
            assert booking.declined_at is not None
            assert booking.lead_note == "try next week"

    def describe_cancel():
        def it_sets_cancelled_status_and_timestamp():
            booking = OrientationBookingFactory()
            booking.cancel()
            assert booking.status == OrientationBooking.Status.CANCELLED
            assert booking.cancelled_at is not None

    def describe_mark_completed():
        def it_marks_completed_and_defaults_the_giver_to_the_lead():
            lead = MemberFactory()
            guild = GuildFactory(guild_lead=lead)
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
            booking.mark_completed()
            assert booking.is_completed is True
            assert booking.oriented_by == lead

        def it_keeps_an_existing_giver():
            giver = MemberFactory()
            booking = OrientationBookingFactory()
            booking.confirm(oriented_by=giver)
            booking.mark_completed()
            assert booking.oriented_by == giver

        def it_overrides_the_giver_when_one_is_passed():
            giver = MemberFactory()
            booking = OrientationBookingFactory()
            booking.mark_completed(oriented_by=giver)
            assert booking.oriented_by == giver

    def describe_uncomplete():
        def it_clears_completed():
            booking = OrientationBookingFactory()
            booking.mark_completed()
            booking.uncomplete()
            assert booking.is_completed is False

    def describe_active_per_guild_constraint():
        def it_forbids_two_live_bookings_for_one_member_and_guild():
            guild = GuildFactory()
            member = MemberFactory()
            OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)
            with pytest.raises(IntegrityError), transaction.atomic():
                OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)


def describe_Member_orientation():
    def describe_is_oriented_for():
        def it_is_true_only_after_a_completed_orientation():
            guild = GuildFactory()
            member = MemberFactory()
            assert member.is_oriented_for(guild) is False
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)
            assert member.is_oriented_for(guild) is False
            booking.mark_completed()
            assert member.is_oriented_for(guild) is True

        def it_is_guild_specific():
            woodshop = GuildFactory()
            metalshop = GuildFactory()
            member = MemberFactory()
            OrientationBookingFactory(slot=OrientationSlotFactory(guild=woodshop), member=member).mark_completed()
            assert member.is_oriented_for(woodshop) is True
            assert member.is_oriented_for(metalshop) is False

    def describe_active_orientation_for():
        def it_returns_a_live_booking():
            guild = GuildFactory()
            member = MemberFactory()
            booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member)
            assert member.active_orientation_for(guild) == booking

        def it_returns_none_when_the_only_booking_was_declined():
            guild = GuildFactory()
            member = MemberFactory()
            OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild), member=member).decline()
            assert member.active_orientation_for(guild) is None


def _past_slot():
    return OrientationSlotFactory(
        starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(hours=1)
    )


def describe_OrientationSlotQuerySet():
    def describe_upcoming():
        def it_excludes_past_and_cancelled_slots():
            guild = GuildFactory()
            future = OrientationSlotFactory(guild=guild)
            past = OrientationSlotFactory(
                guild=guild, starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(hours=1)
            )
            cancelled = OrientationSlotFactory(guild=guild)
            cancelled.cancel()
            result = OrientationSlot.objects.upcoming()
            assert future in result
            assert past not in result
            assert cancelled not in result

    def describe_for_guild():
        def it_limits_to_one_guild():
            mine = OrientationSlotFactory()
            OrientationSlotFactory()
            assert list(OrientationSlot.objects.for_guild(mine.guild)) == [mine]

    def describe_bookable():
        def it_only_includes_slots_at_accepting_guilds():
            open_slot = OrientationSlotFactory()
            closed_guild = GuildFactory()
            GuildOrientationSettingsFactory(guild=closed_guild, is_closed=True)
            closed_slot = OrientationSlotFactory(guild=closed_guild, enabled_settings=False)
            unconfigured = OrientationSlotFactory(enabled_settings=False)
            result = OrientationSlot.objects.bookable()
            assert open_slot in result
            assert closed_slot not in result
            assert unconfigured not in result


def describe_OrientationBookingQuerySet():
    def describe_active():
        def it_includes_only_requested_and_confirmed():
            requested = OrientationBookingFactory()
            confirmed = OrientationBookingFactory()
            confirmed.confirm()
            declined = OrientationBookingFactory()
            declined.decline()
            result = OrientationBooking.objects.active()
            assert requested in result
            assert confirmed in result
            assert declined not in result

    def describe_pending():
        def it_is_only_requested_bookings():
            requested = OrientationBookingFactory()
            confirmed = OrientationBookingFactory()
            confirmed.confirm()
            assert list(OrientationBooking.objects.pending()) == [requested]

    def describe_completed():
        def it_is_only_completed_bookings():
            done = OrientationBookingFactory()
            done.mark_completed()
            other = OrientationBookingFactory()
            result = OrientationBooking.objects.completed()
            assert done in result
            assert other not in result

    def describe_upcoming():
        def it_is_active_and_in_the_future():
            future = OrientationBookingFactory()
            past = OrientationBookingFactory(slot=_past_slot())
            result = OrientationBooking.objects.upcoming()
            assert future in result
            assert past not in result

    def describe_for_guild():
        def it_limits_to_one_guild():
            mine = OrientationBookingFactory()
            OrientationBookingFactory()
            assert list(OrientationBooking.objects.for_guild(mine.guild)) == [mine]


def describe_is_upcoming():
    def it_is_true_for_a_live_future_booking():
        assert OrientationBookingFactory().is_upcoming is True

    def it_is_false_once_cancelled():
        booking = OrientationBookingFactory()
        booking.cancel()
        assert booking.is_upcoming is False

    def it_is_false_for_a_past_slot():
        booking = OrientationBookingFactory(slot=_past_slot())
        assert booking.is_upcoming is False

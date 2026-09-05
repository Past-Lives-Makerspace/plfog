"""Orientation models: settings, slots, bookings, and oriented-status derivation."""

from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership import orientations
from membership.models import OrientationBooking, OrientationError, OrientationSlot, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
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


def describe_OrientationType():
    def it_renders_the_guild_and_name_in_str():
        orientation_type = OrientationTypeFactory(guild=GuildFactory(name="Wood Guild"), name="Lathe")
        assert str(orientation_type) == "Wood Guild — Lathe"

    def it_is_paid_only_above_zero_cents():
        assert OrientationTypeFactory(name="Free Walkthrough").is_paid is False
        assert OrientationTypeFactory(name="Paid Walkthrough", price_cents=500).is_paid is True

    def it_enforces_one_name_per_guild():
        guild = GuildFactory()
        OrientationType.objects.create(guild=guild, name="Shop Basics")
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationType.objects.create(guild=guild, name="Shop Basics")

    def it_allows_the_same_name_at_another_guild():
        OrientationType.objects.create(guild=GuildFactory(), name="Shop Basics")
        OrientationType.objects.create(guild=GuildFactory(), name="Shop Basics")
        assert OrientationType.objects.filter(name="Shop Basics").count() == 2

    def describe_active_queryset():
        def it_excludes_retired_types():
            live = OrientationTypeFactory(name="Live")
            retired = OrientationTypeFactory(guild=live.guild, name="Retired", is_active=False)
            result = OrientationType.objects.active()
            assert live in result
            assert retired not in result

    def describe_first_active_orientation_type():
        def it_picks_the_lowest_sort_order():
            guild = GuildFactory()
            OrientationTypeFactory(guild=guild, name="Later", sort_order=5)
            first = OrientationTypeFactory(guild=guild, name="First", sort_order=1)
            OrientationTypeFactory(guild=guild, name="Retired", sort_order=0, is_active=False)
            assert guild.first_active_orientation_type() == first

        def it_is_none_with_no_active_types():
            guild = GuildFactory()
            OrientationTypeFactory(guild=guild, name="Retired", is_active=False)
            assert guild.first_active_orientation_type() is None


def describe_per_type_orientation():
    def _two_type_guild():
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        basics = OrientationTypeFactory(guild=guild, name="Shop Basics")
        lathe = OrientationTypeFactory(guild=guild, name="Lathe")
        return guild, basics, lathe

    def it_keeps_is_oriented_for_meaning_any_completed_type():
        guild, basics, lathe = _two_type_guild()
        member = MemberFactory()
        slot = OrientationSlotFactory(guild=guild, orientation_type=basics)
        OrientationBookingFactory(slot=slot, member=member).mark_completed()
        # Guild join gating keeps its meaning: any one completed type counts (issue #282).
        assert member.is_oriented_for(guild) is True
        assert member.is_oriented_for_type(basics) is True
        assert member.is_oriented_for_type(lathe) is False

    def it_lets_a_member_oriented_for_one_type_book_another():
        guild, basics, lathe = _two_type_guild()
        member = MemberFactory()
        OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=basics), member=member
        ).mark_completed()
        lathe_slot = OrientationSlotFactory(guild=guild, orientation_type=lathe)
        booking = lathe_slot.book(member)
        assert booking.orientation_type == lathe
        assert booking.status == OrientationBooking.Status.REQUESTED

    def it_blocks_rebooking_a_completed_type():
        guild, basics, _lathe = _two_type_guild()
        member = MemberFactory()
        OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=basics), member=member
        ).mark_completed()
        again = OrientationSlotFactory(
            guild=guild,
            orientation_type=basics,
            starts_at=timezone.now() + timedelta(days=5),
            ends_at=timezone.now() + timedelta(days=5, hours=1),
        )
        with pytest.raises(OrientationError, match="already completed this orientation"):
            again.book(member)

    def it_allows_live_bookings_for_two_types_of_one_guild():
        guild, basics, lathe = _two_type_guild()
        member = MemberFactory()
        first = OrientationSlotFactory(guild=guild, orientation_type=basics).book(member)
        second = OrientationSlotFactory(guild=guild, orientation_type=lathe).book(member)
        assert first.status == OrientationBooking.Status.REQUESTED
        assert second.status == OrientationBooking.Status.REQUESTED

    def it_blocks_a_second_live_booking_for_the_same_type_in_the_database():
        guild, basics, _lathe = _two_type_guild()
        member = MemberFactory()
        OrientationSlotFactory(guild=guild, orientation_type=basics).book(member)
        other_slot = OrientationSlotFactory(
            guild=guild,
            orientation_type=basics,
            starts_at=timezone.now() + timedelta(days=6),
            ends_at=timezone.now() + timedelta(days=6, hours=1),
        )
        # The DB constraint is the race backstop behind the friendly guard.
        with pytest.raises(IntegrityError), transaction.atomic():
            OrientationBooking.objects.create(slot=other_slot, guild=guild, orientation_type=basics, member=member)

    def it_guards_a_second_request_for_the_same_type_with_friendly_copy():
        guild, basics, _lathe = _two_type_guild()
        member = MemberFactory()
        OrientationSlotFactory(guild=guild, orientation_type=basics).book(member)
        other_slot = OrientationSlotFactory(
            guild=guild,
            orientation_type=basics,
            starts_at=timezone.now() + timedelta(days=6),
            ends_at=timezone.now() + timedelta(days=6, hours=1),
        )
        with pytest.raises(OrientationError, match="pending booking for this orientation"):
            other_slot.book(member)

    def describe_inactive_type_slots():
        def it_hides_them_from_bookable_and_blocks_new_bookings():
            guild, _basics, lathe = _two_type_guild()
            slot = OrientationSlotFactory(guild=guild, orientation_type=lathe)
            assert slot in OrientationSlot.objects.bookable()
            lathe.is_active = False
            lathe.save(update_fields=["is_active"])
            assert slot not in OrientationSlot.objects.bookable()
            assert slot.is_bookable is False
            with pytest.raises(OrientationError, match="not available to book"):
                slot.book(MemberFactory())


def describe_equipment_owned_types():
    """Equipment as a second OrientationType owner — constraints, helpers, gates."""

    def _equipment_type(**kwargs):
        return OrientationTypeFactory(equipment_owned=True, **kwargs)

    def describe_exactly_one_owner():
        def it_accepts_guild_only_and_equipment_only():
            assert OrientationTypeFactory().guild is not None
            equipment_type = _equipment_type()
            assert equipment_type.guild is None
            assert equipment_type.equipment is not None

        def it_rejects_both_owners():
            from tests.membership.factories import EquipmentFactory

            with pytest.raises(IntegrityError), transaction.atomic():
                OrientationType.objects.create(guild=GuildFactory(), equipment=EquipmentFactory(), name="Both owners")

        def it_rejects_no_owner():
            with pytest.raises(IntegrityError), transaction.atomic():
                OrientationType.objects.create(guild=None, equipment=None, name="Orphan")

    def describe_equipment_name_uniqueness():
        def it_rejects_a_duplicate_name_on_one_equipment():
            equipment_type = _equipment_type(name="Operator Basics")
            with pytest.raises(IntegrityError), transaction.atomic():
                OrientationType.objects.create(equipment=equipment_type.equipment, name="Operator Basics")

        def it_allows_the_same_name_on_two_different_equipment():
            first = _equipment_type(name="Operator Basics")
            second = _equipment_type(name="Operator Basics")
            assert first.pk != second.pk

        def it_still_enforces_guild_name_uniqueness():
            guild_type = OrientationTypeFactory(name="Shop Basics")
            with pytest.raises(IntegrityError), transaction.atomic():
                OrientationType.objects.create(guild=guild_type.guild, name="Shop Basics")

    def describe_owner_helpers():
        def it_resolves_the_owner_either_way():
            from tests.membership.factories import EquipmentFactory

            guild = GuildFactory(name="Woodshop")
            guild_type = OrientationTypeFactory(guild=guild, name="Shop Basics")
            equipment = EquipmentFactory(name="CNC Router")
            equipment_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
            assert guild_type.is_equipment_owned is False
            assert equipment_type.is_equipment_owned is True
            assert guild_type.owner == guild
            assert equipment_type.owner == equipment
            assert guild_type.owner_name == "Woodshop"
            assert equipment_type.owner_name == "CNC Router"
            assert guild_type.owner_page_path() == f"/guilds/{guild.slug}/"
            assert equipment_type.owner_page_path() == f"/equipment/{equipment.slug}/"
            assert (
                guild_type.orientation_anchor_path()
                == f"/guilds/{guild.slug}/?tab=orientations&type={guild_type.pk}#guild-orientation"
            )
            assert (
                equipment_type.orientation_anchor_path()
                == f"/equipment/{equipment.slug}/?type={equipment_type.pk}#equipment-orientation"
            )
            assert str(equipment_type) == "CNC Router — Operator Basics"

        def it_stringifies_slot_and_booking_without_a_guild():
            slot = OrientationSlotFactory(equipment_owned=True)
            assert slot.guild is None
            assert slot.orientation_type.owner_name in str(slot)
            booking = OrientationBookingFactory(slot=slot)
            assert booking.guild is None
            assert slot.orientation_type.owner_name in str(booking)

        def it_defaults_no_runner_for_equipment_and_the_lead_for_guilds():
            lead = MemberFactory()
            guild_type = OrientationTypeFactory(guild=GuildFactory(guild_lead=lead))
            assert guild_type.default_runner() == lead
            assert _equipment_type().default_runner() is None

    def describe_is_accepting():
        def it_is_true_for_an_active_type_on_active_equipment():
            assert _equipment_type().is_accepting is True

        def it_is_false_when_the_equipment_is_retired():
            equipment_type = _equipment_type()
            equipment_type.equipment.is_active = False
            equipment_type.equipment.save(update_fields=["is_active"])
            assert equipment_type.is_accepting is False

        def it_is_false_when_the_type_is_inactive():
            assert _equipment_type(is_active=False).is_accepting is False

        def it_never_consults_guild_settings_for_equipment_types():
            equipment_type = _equipment_type()
            # A settings row for an unrelated (closed) guild changes nothing.
            GuildOrientationSettingsFactory(is_enabled=False)
            assert equipment_type.is_accepting is True

    def describe_bookable_queryset():
        def it_includes_an_equipment_slot_with_active_equipment_and_type():
            slot = OrientationSlotFactory(equipment_owned=True)
            assert slot in OrientationSlot.objects.bookable()

        def it_excludes_when_the_equipment_is_retired():
            slot = OrientationSlotFactory(equipment_owned=True)
            equipment = slot.orientation_type.equipment
            equipment.is_active = False
            equipment.save(update_fields=["is_active"])
            assert slot not in OrientationSlot.objects.bookable()

        def it_excludes_when_the_type_is_inactive():
            slot = OrientationSlotFactory(equipment_owned=True)
            slot.orientation_type.is_active = False
            slot.orientation_type.save(update_fields=["is_active"])
            assert slot not in OrientationSlot.objects.bookable()

        def it_excludes_past_and_cancelled_equipment_slots():
            past = OrientationSlotFactory(
                equipment_owned=True,
                starts_at=timezone.now() - timedelta(days=1),
                ends_at=timezone.now() - timedelta(hours=23),
            )
            cancelled = OrientationSlotFactory(equipment_owned=True, is_cancelled=True)
            assert past not in OrientationSlot.objects.bookable()
            assert cancelled not in OrientationSlot.objects.bookable()

        def it_keeps_guild_slots_byte_identical():
            open_slot = OrientationSlotFactory()
            disabled = OrientationSlotFactory(enabled_settings=False)
            assert open_slot in OrientationSlot.objects.bookable()
            assert disabled not in OrientationSlot.objects.bookable()

    def describe_is_bookable_and_book():
        def it_skips_the_orienter_leadership_and_settings_checks_for_equipment():
            slot = OrientationSlotFactory(equipment_owned=True)
            assert slot.is_bookable is True

        def it_books_with_a_none_guild_and_type_scoped_guards():
            slot = OrientationSlotFactory(equipment_owned=True)
            member = MemberFactory()
            booking = slot.book(member)
            assert booking.guild is None
            assert booking.status == OrientationBooking.Status.REQUESTED
            with pytest.raises(OrientationError):
                slot.book(member)  # live-per-type duplicate guard still fires

        def it_confirms_and_completes_without_a_runner_crash():
            booking = OrientationBookingFactory(equipment_owned=True)
            booking.confirm()
            booking.refresh_from_db()
            assert booking.status == OrientationBooking.Status.CONFIRMED
            assert booking.oriented_by is None
            booking.mark_completed()
            booking.refresh_from_db()
            assert booking.is_completed is True
            assert booking.oriented_by is None

        def it_stamps_the_passed_member_as_runner():
            booking = OrientationBookingFactory(equipment_owned=True)
            manager = MemberFactory()
            booking.confirm(oriented_by=manager)
            booking.refresh_from_db()
            assert booking.oriented_by == manager

    def describe_refund_receipt_context():
        def it_carries_the_equipment_name_and_page():
            from tests.membership.factories import EquipmentFactory

            equipment = EquipmentFactory(name="CNC Router")
            slot = OrientationSlotFactory(
                equipment_owned=True,
                orientation_type=OrientationTypeFactory(
                    equipment_owned=True, equipment=equipment, name="Operator Basics"
                ),
            )
            booking = OrientationBookingFactory(slot=slot)
            context = booking.refund_receipt_context()
            assert context["item_title"] == "CNC Router orientation: Operator Basics"
            assert context["in_app_url"] == f"/equipment/{equipment.slug}/"
            assert context["manage_url"].endswith(f"/equipment/{equipment.slug}/")

    def it_protects_equipment_that_owns_a_type():
        from django.db.models.deletion import ProtectedError

        equipment_type = _equipment_type()
        with pytest.raises(ProtectedError):
            equipment_type.equipment.delete()


# ── Owner + rule gates (equipment-orientation-hours spec §5.4) ──────────────────────


def describe_OrientationType_is_accepting_for_equipment():
    def it_is_true_for_an_active_open_tool():
        assert OrientationTypeFactory(equipment_owned=True).is_accepting is True

    def it_is_false_when_the_tool_is_closed():
        orientation_type = OrientationTypeFactory(equipment_owned=True)
        orientation_type.equipment.is_closed = True
        orientation_type.equipment.save(update_fields=["is_closed"])
        assert orientation_type.is_accepting is False

    def it_is_false_when_the_tool_is_retired():
        orientation_type = OrientationTypeFactory(equipment_owned=True)
        orientation_type.equipment.is_active = False
        orientation_type.equipment.save(update_fields=["is_active"])
        assert orientation_type.is_accepting is False


def describe_bookable_closure_gate():
    def it_excludes_slots_on_a_closed_tool():
        open_slot = OrientationSlotFactory(equipment_owned=True)
        closed_slot = OrientationSlotFactory(equipment_owned=True)
        closed_slot.orientation_type.equipment.is_closed = True
        closed_slot.orientation_type.equipment.save(update_fields=["is_closed"])
        result = OrientationSlot.objects.bookable()
        assert open_slot in result
        assert closed_slot not in result
        assert open_slot.is_bookable is True
        assert closed_slot.is_bookable is False


def describe_paused_rule_gate():
    def _guild_generated(rule, **overrides):
        return OrientationSlotFactory(
            guild=rule.guild,
            orientation_type=rule.orientation_type,
            availability=rule,
            source=OrientationSlot.Source.GENERATED,
            **overrides,
        )

    def _tool_generated(rule, **overrides):
        return OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=rule.orientation_type,
            availability=rule,
            source=OrientationSlot.Source.GENERATED,
            **overrides,
        )

    def it_hides_a_paused_guild_rules_slot():
        rule = OrientationAvailabilityFactory(is_active=False)
        slot = _guild_generated(rule)
        assert slot not in OrientationSlot.objects.bookable()
        assert slot.is_bookable is False
        rule.is_active = True
        rule.save(update_fields=["is_active"])
        slot = OrientationSlot.objects.get(pk=slot.pk)
        assert slot in OrientationSlot.objects.bookable()
        assert slot.is_bookable is True

    def it_hides_a_paused_equipment_rules_slot():
        rule = OrientationAvailabilityFactory(equipment_owned=True, is_active=False)
        slot = _tool_generated(rule)
        assert slot not in OrientationSlot.objects.bookable()
        assert slot.is_bookable is False

    def it_leaves_a_one_time_slot_alone():
        slot = OrientationSlotFactory(equipment_owned=True)
        assert slot.availability is None
        assert slot in OrientationSlot.objects.bookable()
        assert slot.is_bookable is True

    def it_keeps_an_existing_booking_on_a_paused_rules_slot_confirmable():
        rule = OrientationAvailabilityFactory(equipment_owned=True)
        booking = OrientationBookingFactory(slot=_tool_generated(rule))
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        booking.confirm()
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_does_not_reopen_the_slot_when_a_hold_expires_while_paused():
        rule = OrientationAvailabilityFactory(equipment_owned=True)
        slot = _tool_generated(rule, seats=1)
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        assert orientations.release_hold_if_unpaid(hold) == "released"
        slot = OrientationSlot.objects.get(pk=slot.pk)
        assert slot.is_bookable is False
        assert slot not in OrientationSlot.objects.bookable()


def describe_reservation_gate():
    """A confirmed reservation over an equipment slot's span hides it until the reservation goes (PR 2)."""

    def _reserved_slot():
        slot = OrientationSlotFactory(equipment_owned=True)
        reservation = EquipmentReservationFactory(
            equipment=slot.orientation_type.equipment, starts_at=slot.starts_at, ends_at=slot.ends_at
        )
        return slot, reservation

    def it_hides_an_equipment_slot_under_a_confirmed_reservation():
        slot, _reservation = _reserved_slot()
        assert slot.is_bookable is False
        assert slot not in OrientationSlot.objects.bookable()
        with pytest.raises(OrientationError, match="not available to book"):
            slot.book(MemberFactory())

    def it_frees_the_slot_when_the_reservation_is_cancelled():
        slot, reservation = _reserved_slot()
        reservation.status = "cancelled"
        reservation.save(update_fields=["status"])
        assert slot.is_bookable is True
        assert slot in OrientationSlot.objects.bookable()

    def it_ignores_a_touching_reservation():
        slot = OrientationSlotFactory(equipment_owned=True)
        EquipmentReservationFactory(
            equipment=slot.orientation_type.equipment,
            starts_at=slot.ends_at,
            ends_at=slot.ends_at + timedelta(hours=1),
        )
        assert slot.is_bookable is True
        assert slot in OrientationSlot.objects.bookable()

    def it_ignores_a_reservation_ending_exactly_at_the_slot_start():
        slot = OrientationSlotFactory(equipment_owned=True)
        EquipmentReservationFactory(
            equipment=slot.orientation_type.equipment,
            starts_at=slot.starts_at - timedelta(hours=1),
            ends_at=slot.starts_at,
        )
        assert slot.is_bookable is True
        assert slot in OrientationSlot.objects.bookable()

    def it_leaves_guild_slots_alone():
        slot = OrientationSlotFactory()
        EquipmentReservationFactory(starts_at=slot.starts_at, ends_at=slot.ends_at)
        assert slot.is_bookable is True
        assert slot in OrientationSlot.objects.bookable()


def describe_departed_manager_gate():
    """A personal equipment slot stops taking new bookings once its manager no longer manages the tool."""

    def _personal_slot(equipment, manager):
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        return OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type, orienter=manager)

    def _assert_bookable(slot, expected: bool) -> None:
        slot = OrientationSlot.objects.get(pk=slot.pk)
        assert slot.is_bookable is expected
        assert (slot in OrientationSlot.objects.bookable()) is expected

    def it_hides_the_slot_when_the_staff_row_is_gone_but_keeps_its_booking():
        equipment = EquipmentFactory()
        manager = MemberFactory()
        row = EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        slot = _personal_slot(equipment, manager)
        booking = OrientationBookingFactory(slot=slot)
        _assert_bookable(slot, True)
        row.delete()
        _assert_bookable(slot, False)
        booking.confirm()
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_keeps_every_other_manager_tier_bookable():
        from membership.models import AdminCapability
        from tests.membership.factories import GuildStaffMembershipFactory

        guild = GuildFactory(guild_lead=MemberFactory())
        equipment = EquipmentFactory(guild=guild)
        _assert_bookable(_personal_slot(equipment, guild.guild_lead), True)
        staffer = MemberFactory()
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        _assert_bookable(_personal_slot(equipment, staffer), True)
        holder = MemberFactory()
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        _assert_bookable(_personal_slot(equipment, holder), True)

    def it_hides_a_plain_admins_slot_until_they_hold_the_capability():
        # An admin edits anyone's hours but is only booked by name once they hold the
        # EQUIPMENT capability, exactly as a guild admin needs a staff row.
        from membership.models import AdminCapability

        equipment = EquipmentFactory()
        admin = MemberFactory(fog_role="admin")
        slot = _personal_slot(equipment, admin)
        _assert_bookable(slot, False)
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        _assert_bookable(slot, True)

    def it_leaves_a_shared_slot_alone():
        slot = OrientationSlotFactory(equipment_owned=True)
        assert slot.orienter is None
        _assert_bookable(slot, True)

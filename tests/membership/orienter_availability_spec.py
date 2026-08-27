"""BDD specs for per-orienter orientation availability — model + service layer.

Covers the two new ``orienter`` FKs, the queryset helpers, the ``with_label``
phrase, slot generation threading, the departed-orienter bookability guard, the
confirm/complete crediting chain, and the retirement flows (``retire_rule`` /
``retire_orienter``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from membership import orientations
from membership.models import (
    GuildStaffMembership,
    OrientationAvailability,
    OrientationBooking,
    OrientationError,
    OrientationSlot,
)
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db


def _named_member(name: str) -> object:
    return MemberFactory(full_legal_name=name)


def _staffed_guild(*names: str) -> tuple[object, list[object]]:
    """An orientation-enabled guild (lead 'Lead Person') with one ORIENTER staffer per name."""
    lead = _named_member("Lead Person")
    guild = GuildFactory(guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    staffers = []
    for name in names:
        member = _named_member(name)
        GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.ORIENTER)
        staffers.append(member)
    return guild, staffers


def describe_queryset_helpers():
    def it_filters_personal_and_guild_level_rules():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        personal = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        legacy = OrientationAvailabilityFactory(guild=guild)
        assert list(OrientationAvailability.objects.for_orienter(bob)) == [personal]
        assert list(OrientationAvailability.objects.guild_level()) == [legacy]


def describe_rule_str():
    def it_names_the_orienter():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        assert "(Bob Placeholder)" in str(rule)

    def it_says_any_orienter_for_a_guild_rule():
        rule = OrientationAvailabilityFactory()
        assert "(any orienter)" in str(rule)


def describe_with_label():
    def it_uses_the_first_name():
        slot = OrientationSlotFactory(orienter=_named_member("Bob Placeholder"))
        assert slot.with_label == "with Bob"
        assert slot.orienter_first_name == "Bob"

    def it_is_empty_for_a_guild_slot():
        slot = OrientationSlotFactory()
        assert slot.with_label == ""
        assert slot.orienter_first_name == ""

    def it_handles_a_single_token_name():
        slot = OrientationSlotFactory(orienter=_named_member("Cher"))
        assert slot.with_label == "with Cher"

    def it_never_renders_a_bare_with_for_a_nameless_member():
        slot = OrientationSlotFactory(orienter=MemberFactory(full_legal_name="", preferred_name=""))
        assert slot.with_label == ""


def describe_orienter_name_labels():
    def it_disambiguates_shared_first_names_with_last_initials():
        guild, (bob_p, bob_q, alice) = _staffed_guild("Bob Placeholder", "Bob Quartz", "Alice Ash")
        labels = guild.orienter_name_labels()
        assert labels[bob_p.pk] == "Bob P."
        assert labels[bob_q.pk] == "Bob Q."
        assert labels[alice.pk] == "Alice"

    def it_keeps_a_single_token_name_plain_even_in_a_collision():
        guild, (bob, mono) = _staffed_guild("Bob Placeholder", "Bob")
        labels = guild.orienter_name_labels()
        assert labels[bob.pk] == "Bob P."
        assert labels[mono.pk] == "Bob"

    def it_skips_a_member_with_no_name():
        guild, _ = _staffed_guild()
        nameless = MemberFactory(full_legal_name="", preferred_name="")
        GuildStaffMembershipFactory(guild=guild, member=nameless, role=GuildStaffMembership.Role.ORIENTER)
        assert nameless.pk not in guild.orienter_name_labels()


def describe_generate_slots():
    def it_threads_each_rules_orienter_into_overlapping_slots():
        guild, (bob, alice) = _staffed_guild("Bob Placeholder", "Alice Ash")
        OrientationAvailabilityFactory(guild=guild, orienter=bob)
        OrientationAvailabilityFactory(guild=guild, orienter=alice)

        created = orientations.generate_slots(guild=guild)

        assert created > 0
        orienters = set(OrientationSlot.objects.filter(guild=guild).values_list("orienter", flat=True))
        assert orienters == {bob.pk, alice.pk}
        # Two people, same weekday + time → two distinct slots per occurrence.
        first = OrientationSlot.objects.filter(guild=guild).order_by("starts_at").first()
        assert OrientationSlot.objects.filter(guild=guild, starts_at=first.starts_at).count() == 2

    def it_stays_idempotent():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        OrientationAvailabilityFactory(guild=guild, orienter=bob)
        orientations.generate_slots(guild=guild)
        assert orientations.generate_slots(guild=guild) == 0

    def it_still_generates_guild_level_rules_with_no_orienter():
        guild, _ = _staffed_guild()
        OrientationAvailabilityFactory(guild=guild)
        orientations.generate_slots(guild=guild)
        slot = OrientationSlot.objects.filter(guild=guild).first()
        assert slot is not None
        assert slot.orienter is None

    def it_skips_todays_occurrence_when_its_start_already_passed():
        from datetime import datetime, time

        guild, (bob,) = _staffed_guild("Bob Placeholder")
        today = timezone.localdate()
        OrientationAvailabilityFactory(
            guild=guild, orienter=bob, weekday=today.weekday(), start_time=time(10, 0), end_time=time(11, 0)
        )
        # Reference "now" is noon today — the 10:00 occurrence has passed; next week's hasn't.
        reference = timezone.make_aware(datetime.combine(today, time(12, 0)))
        orientations.generate_slots(guild=guild, now=reference)
        first = OrientationSlot.objects.filter(guild=guild).order_by("starts_at").first()
        assert first is not None
        assert first.starts_at == timezone.make_aware(datetime.combine(today + timedelta(days=7), time(10, 0)))

    def it_skips_a_stale_personal_rule_whose_orienter_left_leadership():
        guild, _ = _staffed_guild()
        outsider = _named_member("Gone Person")
        OrientationAvailabilityFactory(guild=guild, orienter=outsider)
        assert orientations.generate_slots(guild=guild) == 0


def describe_departed_orienter_guard():
    @pytest.fixture
    def departed_slot():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        slot = OrientationSlotFactory(guild=guild, orienter=bob)
        GuildStaffMembership.objects.filter(guild=guild, member=bob).delete()
        return slot

    def it_blocks_new_bookings_on_a_departed_orienters_slot(departed_slot):
        assert departed_slot.is_bookable is False
        with pytest.raises(OrientationError):
            departed_slot.book(MemberFactory())

    def it_drops_the_slot_from_the_bookable_queryset(departed_slot):
        assert departed_slot.pk not in set(OrientationSlot.objects.bookable().values_list("pk", flat=True))

    def it_does_not_resurrect_the_slot_when_its_booking_is_declined():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        slot = OrientationSlotFactory(guild=guild, orienter=bob)
        booking = OrientationBookingFactory(slot=slot)
        GuildStaffMembership.objects.filter(guild=guild, member=bob).delete()
        booking.decline()
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.DECLINED  # the booking itself is untouched
        assert slot.pk not in set(OrientationSlot.objects.bookable().values_list("pk", flat=True))

    def it_keeps_guild_slots_bookable():
        guild, _ = _staffed_guild()
        slot = OrientationSlotFactory(guild=guild)
        assert slot.is_bookable is True
        assert slot.pk in set(OrientationSlot.objects.bookable().values_list("pk", flat=True))

    def it_keeps_the_leads_own_slots_bookable():
        lead = _named_member("Lead Person")
        guild = GuildFactory(guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        slot = OrientationSlotFactory(guild=guild, orienter=lead)
        assert slot.is_bookable is True
        assert slot.pk in set(OrientationSlot.objects.bookable().values_list("pk", flat=True))


def describe_confirm_crediting():
    def it_defaults_oriented_by_to_the_slots_orienter():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orienter=bob))
        booking.confirm()
        assert booking.oriented_by == bob

    def it_falls_back_to_the_guild_lead_for_a_guild_slot():
        guild, _ = _staffed_guild()
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild))
        booking.confirm()
        assert booking.oriented_by == guild.guild_lead

    def it_lets_an_explicit_actor_win():
        guild, (bob, alice) = _staffed_guild("Bob Placeholder", "Alice Ash")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orienter=bob))
        booking.confirm(oriented_by=alice)
        assert booking.oriented_by == alice

    def it_credits_the_slot_orienter_on_completion_fallback():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        booking = OrientationBookingFactory(slot=OrientationSlotFactory(guild=guild, orienter=bob))
        booking.mark_completed()
        assert booking.oriented_by == bob

    def it_credits_the_slot_orienter_through_auto_complete():
        MembershipPlanFactory()
        lead = User.objects.create_user(username="cred_lead", email="cred_lead@example.com").member
        guild = GuildFactory(guild_lead=lead)
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        bob = _named_member("Bob Placeholder")
        GuildStaffMembershipFactory(guild=guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        slot = OrientationSlotFactory(
            guild=guild,
            orienter=bob,
            starts_at=timezone.now() - timedelta(hours=2),
            ends_at=timezone.now() - timedelta(hours=1),
        )
        member = User.objects.create_user(username="cred_member", email="cred_member@example.com").member
        booking = OrientationBookingFactory(slot=slot, member=member, status=OrientationBooking.Status.CONFIRMED)

        assert orientations.auto_complete() == 1
        booking.refresh_from_db()
        assert booking.is_completed is True
        assert booking.oriented_by == bob


def describe_retire_rule():
    def it_deletes_future_open_generated_slots_and_spares_the_rest():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        open_future = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED
        )
        booked_future = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED
        )
        OrientationBookingFactory(slot=booked_future)
        past = OrientationSlotFactory(
            guild=guild,
            orienter=bob,
            availability=rule,
            source=OrientationSlot.Source.GENERATED,
            starts_at=timezone.now() - timedelta(days=7),
            ends_at=timezone.now() - timedelta(days=7) + timedelta(hours=1),
        )
        manual = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.MANUAL
        )

        removed, kept = orientations.retire_rule(rule)

        assert (removed, kept) == (1, 1)
        assert not OrientationSlot.objects.filter(pk=open_future.pk).exists()
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        for survivor in (booked_future, past, manual):
            survivor.refresh_from_db()
            assert survivor.availability is None
            assert survivor.orienter == bob  # provenance stays honest

    def it_removes_a_slot_whose_only_booking_no_longer_holds_a_seat():
        # Seat-holding guard: today that is bookings.active(); the paid-orientations build
        # MUST switch this to seat_holding() when PENDING_PAYMENT holds land (named seam).
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        slot = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED
        )
        booking = OrientationBookingFactory(slot=slot)
        booking.cancel()

        removed, kept = orientations.retire_rule(rule)

        assert (removed, kept) == (1, 0)
        assert not OrientationSlot.objects.filter(pk=slot.pk).exists()


def describe_retire_orienter():
    def it_sweeps_only_the_members_rules_in_that_guild():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        other_guild, _ = _staffed_guild()
        GuildStaffMembershipFactory(guild=other_guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        OrientationAvailabilityFactory(guild=guild, orienter=bob)
        OrientationAvailabilityFactory(guild=guild, orienter=bob, weekday=OrientationAvailability.Weekday.FRIDAY)
        elsewhere = OrientationAvailabilityFactory(guild=other_guild, orienter=bob)
        legacy = OrientationAvailabilityFactory(guild=guild)

        orientations.retire_orienter(guild, bob)

        assert not OrientationAvailability.objects.filter(guild=guild, orienter=bob).exists()
        assert OrientationAvailability.objects.filter(pk=elsewhere.pk).exists()
        assert OrientationAvailability.objects.filter(pk=legacy.pk).exists()

    def it_counts_their_remaining_booked_future_slots():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        rule = OrientationAvailabilityFactory(guild=guild, orienter=bob)
        booked = OrientationSlotFactory(
            guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED
        )
        OrientationBookingFactory(slot=booked)
        OrientationSlotFactory(guild=guild, orienter=bob, availability=rule, source=OrientationSlot.Source.GENERATED)

        removed, booked_remaining = orientations.retire_orienter(guild, bob)

        assert removed == 1
        assert booked_remaining == 1
        assert OrientationSlot.objects.filter(pk=booked.pk).exists()

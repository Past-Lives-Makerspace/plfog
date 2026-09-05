"""BDD specs for per-orienter orientation availability — model + service layer.

Covers the two new ``orienter`` FKs, the queryset helpers, the ``with_label``
phrase, slot generation threading, the departed-orienter bookability guard, the
confirm/complete crediting chain, and the retirement flows (``retire_rule`` /
``retire_orienter``).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from billing.models import PaymentRefund
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
    OrientationTypeFactory,
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

        # Removed from members' view, but cancelled rather than deleted: the slot carries
        # booking history and OrientationBooking.slot cascades.
        assert (removed, kept) == (1, 0)
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert OrientationBooking.objects.filter(pk=booking.pk).exists()


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


# ── Equipment orientation hours (equipment-orientation-hours spec, PR 1) ──────────────


def _tool_day(offset: int = 2) -> date:
    return timezone.localdate() + timedelta(days=offset)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _equipment_rule(**overrides):
    """A carved equipment rule on the weekday two days out: 10:00 to 12:00, 60 minute slots, 1 seat."""
    defaults = {
        "weekday": _tool_day().weekday(),
        "start_time": time(10, 0),
        "end_time": time(12, 0),
        "slot_minutes": 60,
        "seats": 1,
    }
    defaults.update(overrides)
    return OrientationAvailabilityFactory(equipment_owned=True, **defaults)


def _tool(rule):
    return rule.orientation_type.equipment


def _generated_slot(rule, *, hour: int = 10, minute: int = 0, seats: int = 4, day: date | None = None):
    """A GENERATED slot on ``rule`` (one hour long) — the shape retirement and cleanup act on."""
    day = day or _tool_day()
    return OrientationSlotFactory(
        equipment_owned=True,
        orientation_type=rule.orientation_type,
        availability=rule,
        source=OrientationSlot.Source.GENERATED,
        starts_at=_at(day, hour, minute),
        ends_at=_at(day, hour + 1, minute),
        seats=seats,
    )


def _local_times(instants) -> list[str]:
    return [timezone.localtime(instant).strftime("%H:%M") for instant in instants]


def describe_equipment_rules():
    def it_names_the_equipment_in_str():
        rule = _equipment_rule()
        assert str(rule).startswith(f"{_tool(rule).name} orientation:")
        assert "(any orienter)" in str(rule)

    def it_filters_rules_for_one_equipment():
        mine = _equipment_rule()
        _equipment_rule()
        OrientationAvailabilityFactory()
        assert list(OrientationAvailability.objects.for_equipment(_tool(mine))) == [mine]

    def it_rejects_a_zero_slot_length():
        with pytest.raises(IntegrityError), transaction.atomic():
            _equipment_rule(slot_minutes=0)

    def it_accepts_an_empty_or_positive_slot_length():
        assert _equipment_rule(slot_minutes=None).slot_minutes is None
        assert _equipment_rule(slot_minutes=45).slot_minutes == 45

    def describe_clean():
        def it_rejects_a_quarter_hour_time_on_a_carved_window():
            rule = _equipment_rule(start_time=time(9, 15))
            with pytest.raises(ValidationError) as excinfo:
                rule.clean()
            assert "start_time" in excinfo.value.message_dict
            assert "end_time" not in excinfo.value.message_dict

        def it_accepts_half_hour_times_on_a_carved_window():
            _equipment_rule(start_time=time(9, 30)).clean()

        def it_leaves_a_legacy_one_slot_rule_alone():
            OrientationAvailabilityFactory(start_time=time(9, 15), end_time=time(10, 15)).clean()


def describe_carve_starts():
    def it_yields_one_start_for_a_legacy_rule():
        rule = OrientationAvailabilityFactory(start_time=time(18, 0), end_time=time(20, 0))
        assert _local_times(rule.carve_starts(_tool_day())) == ["18:00"]
        ((_start, end),) = rule.carve_spans(_tool_day())
        assert _local_times([end]) == ["20:00"]

    def it_carves_a_full_day_into_hourly_starts():
        rule = _equipment_rule(start_time=time(10, 0), end_time=time(18, 0))
        expected = [f"{hour}:00" for hour in range(10, 18)]
        assert _local_times(rule.carve_starts(_tool_day())) == expected

    def it_drops_a_start_whose_slot_would_not_fit_after_the_break():
        rule = _equipment_rule(start_time=time(10, 0), end_time=time(12, 0), buffer_minutes=15)
        assert _local_times(rule.carve_starts(_tool_day())) == ["10:00"]

    def it_steps_by_slot_plus_break():
        rule = _equipment_rule(start_time=time(10, 0), end_time=time(12, 15), buffer_minutes=15)
        assert _local_times(rule.carve_starts(_tool_day())) == ["10:00", "11:15"]

    def it_yields_nothing_when_the_window_is_shorter_than_one_slot():
        rule = _equipment_rule(start_time=time(10, 0), end_time=time(10, 30))
        assert rule.carve_starts(_tool_day()) == []

    def it_ends_each_carved_slot_one_length_after_its_start():
        rule = _equipment_rule(start_time=time(10, 0), end_time=time(12, 0), slot_minutes=45)
        assert [_local_times([end]) for _start, end in rule.carve_spans(_tool_day())] == [["10:45"], ["11:30"]]

    def it_keeps_the_wall_clock_grid_on_a_dst_change_day():
        rule = _equipment_rule(start_time=time(10, 0), end_time=time(18, 0))
        ordinary = _local_times(rule.carve_starts(date(2026, 11, 8)))
        assert _local_times(rule.carve_starts(date(2026, 11, 1))) == ordinary  # fall back
        assert _local_times(rule.carve_starts(date(2026, 3, 8))) == ordinary  # spring forward


def describe_generate_slots_for_equipment():
    def it_carves_guildless_orienterless_slots_from_the_rule():
        rule = _equipment_rule()
        created = orientations.generate_slots(equipment=_tool(rule))
        # 8 weekly occurrences inside the 8 week window, 2 hourly slots each.
        assert created == 16
        slots = list(rule.slots.order_by("starts_at"))
        assert len(slots) == 16
        first, second = slots[0], slots[1]
        assert first.guild is None
        assert first.orienter is None
        assert first.source == OrientationSlot.Source.GENERATED
        assert first.seats == 1
        assert _local_times([first.starts_at, first.ends_at]) == ["10:00", "11:00"]
        assert _local_times([second.starts_at, second.ends_at]) == ["11:00", "12:00"]

    def it_uses_the_types_default_location():
        rule = _equipment_rule()
        rule.orientation_type.default_location = "By the big saw"
        rule.orientation_type.save(update_fields=["default_location"])
        orientations.generate_slots(equipment=_tool(rule))
        assert rule.slots.first().location == "By the big saw"

    def it_is_idempotent():
        rule = _equipment_rule()
        orientations.generate_slots(equipment=_tool(rule))
        assert orientations.generate_slots(equipment=_tool(rule)) == 0

    def it_scopes_to_one_tool():
        mine = _equipment_rule()
        other = _equipment_rule()
        orientations.generate_slots(equipment=_tool(mine))
        assert mine.slots.exists()
        assert not other.slots.exists()

    def it_refuses_both_scopes():
        rule = _equipment_rule()
        with pytest.raises(ValueError, match="not both"):
            orientations.generate_slots(guild=GuildFactory(), equipment=_tool(rule))

    def it_generates_nothing_for_a_closed_retired_or_inactive_type_tool():
        closed = _equipment_rule()
        _tool(closed).is_closed = True
        _tool(closed).save(update_fields=["is_closed"])
        retired = _equipment_rule()
        _tool(retired).is_active = False
        _tool(retired).save(update_fields=["is_active"])
        inactive_type = _equipment_rule()
        inactive_type.orientation_type.is_active = False
        inactive_type.orientation_type.save(update_fields=["is_active"])
        assert orientations.generate_slots() == 0

    def it_skips_a_personal_rule_whose_manager_no_longer_manages_the_tool():
        from tests.membership.factories import EquipmentStaffMembershipFactory

        stranger = MemberFactory()
        rule = _equipment_rule(orienter=stranger)
        assert orientations.generate_slots(equipment=_tool(rule)) == 0
        EquipmentStaffMembershipFactory(equipment=_tool(rule), member=stranger)
        assert orientations.generate_slots(equipment=_tool(rule)) == 16
        assert rule.slots.first().orienter == stranger

    def it_never_regenerates_a_cancelled_slot_open():
        rule = _equipment_rule()
        orientations.generate_slots(equipment=_tool(rule))
        slot = rule.slots.get(starts_at=_at(_tool_day(), 10))
        slot.mark_cancelled(reason="Machine down")
        rule.slot_minutes = 30
        rule.save(update_fields=["slot_minutes"])
        assert orientations.retire_open_slots(rule) == (15, 0)
        orientations.generate_slots(equipment=_tool(rule))
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.cancelled_reason == "Machine down"
        assert slot.availability == rule  # still attached: the manager's cancel owns the time
        assert rule.slots.filter(starts_at=slot.starts_at).count() == 1
        assert not OrientationSlot.objects.filter(starts_at=slot.starts_at, is_cancelled=False).exists()

    def it_skips_a_plain_admins_personal_rule_until_they_hold_the_capability():
        from membership.models import AdminCapability

        admin = MemberFactory(fog_role="admin")
        rule = _equipment_rule(orienter=admin)
        assert orientations.generate_slots(equipment=_tool(rule)) == 0
        admin.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert orientations.generate_slots(equipment=_tool(rule)) == 16

    def it_looks_a_tools_runners_up_once_per_run():
        from tests.membership.factories import EquipmentStaffMembershipFactory

        first = _equipment_rule(slot_minutes=None)
        tool = _tool(first)
        dana = MemberFactory()
        EquipmentStaffMembershipFactory(equipment=tool, member=dana)
        first.orienter = dana
        first.save(update_fields=["orienter"])
        # Every candidate is "past" against this reference, so no slot is inserted and the
        # remaining queries are the rules, one off-grid cleanup per equipment rule, the
        # runner set, and the occupied set.
        far_future = timezone.now() + timedelta(days=400)

        def query_count() -> int:
            with CaptureQueriesContext(connection) as ctx:
                orientations.generate_slots(equipment=tool, now=far_future)
            return len(ctx.captured_queries)

        one_rule = query_count()
        for offset in range(3, 8):
            _equipment_rule(
                orientation_type=first.orientation_type,
                orienter=dana,
                weekday=_tool_day(offset).weekday(),
                slot_minutes=None,
            )
        # Each extra rule costs exactly its own off-grid cleanup query; the runner set
        # (staff rows + capability holders) is never looked up again per rule.
        assert query_count() - one_rule == 5

    def it_covers_both_owners_with_no_scope():
        guild, _staff = _staffed_guild()
        guild_rule = OrientationAvailabilityFactory(guild=guild)
        tool_rule = _equipment_rule()
        orientations.generate_slots()
        assert guild_rule.slots.exists()
        assert tool_rule.slots.exists()


def describe_slot_layer_overlap():
    def it_skips_candidates_overlapping_a_booked_old_grid_slot():
        rule = _equipment_rule()
        day = _tool_day()
        kept = _generated_slot(rule, hour=10, minute=30, seats=1)
        OrientationBookingFactory(slot=kept)
        created = orientations.generate_slots(equipment=_tool(rule))
        assert created == 14  # both hourly candidates on that day overlap the kept 10:30 slot
        assert list(rule.slots.filter(starts_at__gte=_at(day, 0), starts_at__lt=_at(day + timedelta(days=1), 0))) == [
            kept
        ]

    def it_skips_a_candidate_overlapping_a_one_time_slot():
        rule = _equipment_rule()
        day = _tool_day()
        manual = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=rule.orientation_type,
            starts_at=_at(day, 10),
            ends_at=_at(day, 10, 30),
        )
        assert orientations.generate_slots(equipment=_tool(rule)) == 15
        assert not rule.slots.filter(starts_at=_at(day, 10)).exists()
        assert rule.slots.filter(starts_at=_at(day, 11)).exists()
        assert OrientationSlot.objects.filter(pk=manual.pk).exists()

    def it_skips_a_candidate_overlapping_a_sibling_types_slot():
        rule = _equipment_rule()
        day = _tool_day()
        sibling = OrientationTypeFactory(equipment_owned=True, equipment=_tool(rule), name="Advanced")
        OrientationSlotFactory(
            equipment_owned=True, orientation_type=sibling, starts_at=_at(day, 11), ends_at=_at(day, 12)
        )
        assert orientations.generate_slots(equipment=_tool(rule)) == 15
        assert rule.slots.filter(starts_at=_at(day, 10)).exists()
        assert not rule.slots.filter(starts_at=_at(day, 11)).exists()

    def it_ignores_a_cancelled_slot():
        rule = _equipment_rule()
        day = _tool_day()
        OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=rule.orientation_type,
            starts_at=_at(day, 10),
            ends_at=_at(day, 11),
            is_cancelled=True,
        )
        assert orientations.generate_slots(equipment=_tool(rule)) == 16

    def it_lets_a_later_rule_see_slots_carved_earlier_in_the_run():
        first = _equipment_rule()
        sibling = OrientationTypeFactory(equipment_owned=True, equipment=_tool(first), name="Advanced")
        second = _equipment_rule(orientation_type=sibling, start_time=time(11, 0), end_time=time(13, 0))
        assert orientations.generate_slots(equipment=_tool(first)) == 24
        assert first.slots.count() == 16
        assert sorted(set(_local_times(second.slots.values_list("starts_at", flat=True)))) == ["12:00"]

    def it_still_lets_guild_rules_generate_over_each_other():
        guild, _staff = _staffed_guild()
        weekday = _tool_day().weekday()
        first = OrientationAvailabilityFactory(
            guild=guild, weekday=weekday, start_time=time(18, 0), end_time=time(19, 0)
        )
        second = OrientationAvailabilityFactory(
            guild=guild, weekday=weekday, start_time=time(18, 30), end_time=time(19, 30)
        )
        orientations.generate_slots(guild=guild)
        assert first.slots.count() == 8
        assert second.slots.count() == 8

    def it_loads_the_overlap_set_once_per_run():
        rule = _equipment_rule()
        tool = _tool(rule)
        # Every candidate is "past" against this reference, so no slot is inserted and the
        # remaining queries are exactly: the rules, one off-grid cleanup per rule, the set.
        far_future = timezone.now() + timedelta(days=400)

        def query_count() -> int:
            with CaptureQueriesContext(connection) as ctx:
                orientations.generate_slots(equipment=tool, now=far_future)
            return len(ctx.captured_queries)

        one_rule = query_count()
        _equipment_rule(orientation_type=rule.orientation_type, weekday=_tool_day(3).weekday())
        _equipment_rule(orientation_type=rule.orientation_type, weekday=_tool_day(4).weekday())
        assert query_count() - one_rule == 2


def describe_off_grid_cleanup():
    def it_deletes_an_open_old_grid_slot_before_carving():
        rule = _equipment_rule()
        stale = _generated_slot(rule, hour=10, minute=30, seats=1)
        assert orientations.generate_slots(equipment=_tool(rule)) == 16
        assert not OrientationSlot.objects.filter(pk=stale.pk).exists()

    def it_reseats_an_on_grid_slot_to_the_rules_count():
        # A seats mismatch alone is not off-grid: the slot stays and follows the rule.
        rule = _equipment_rule(seats=2)
        capped = _generated_slot(rule, hour=10, seats=1)
        assert orientations.generate_slots(equipment=_tool(rule)) == 15
        capped.refresh_from_db()
        assert capped.seats == 2

    def it_keeps_a_booked_on_grid_slot_at_its_taken_seats_when_the_rule_is_lower():
        rule = _equipment_rule(seats=1)
        booked = _generated_slot(rule, hour=10, seats=2)
        OrientationBookingFactory(slot=booked)
        OrientationBookingFactory(slot=booked)
        orientations.generate_slots(equipment=_tool(rule))
        booked.refresh_from_db()
        assert booked.seats == 2

    def it_keeps_a_booked_old_grid_slot():
        rule = _equipment_rule()
        booked = _generated_slot(rule, hour=10, minute=30, seats=1)
        OrientationBookingFactory(slot=booked)
        orientations.generate_slots(equipment=_tool(rule))
        assert OrientationSlot.objects.filter(pk=booked.pk).exists()

    def it_never_touches_manual_slots():
        rule = _equipment_rule()
        other_day = _tool_day(3)
        manual = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=rule.orientation_type,
            starts_at=_at(other_day, 9),
            ends_at=_at(other_day, 9, 30),
        )
        orientations.generate_slots(equipment=_tool(rule))
        assert OrientationSlot.objects.filter(pk=manual.pk).exists()


def describe_retire_open_slots():
    def it_removes_open_future_generated_slots_but_keeps_the_rule():
        rule = _equipment_rule()
        open_slot = _generated_slot(rule, hour=10)
        booked = _generated_slot(rule, hour=11)
        OrientationBookingFactory(slot=booked)
        manual = OrientationSlotFactory(equipment_owned=True, orientation_type=rule.orientation_type, availability=rule)
        assert orientations.retire_open_slots(rule) == (1, 1)
        assert OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        assert OrientationSlot.objects.filter(pk=booked.pk).exists()
        assert OrientationSlot.objects.filter(pk=manual.pk).exists()

    def it_caps_a_kept_slot_to_its_taken_seats():
        rule = _equipment_rule()
        slot = _generated_slot(rule, seats=4)
        OrientationBookingFactory(slot=slot)
        assert orientations.retire_open_slots(rule) == (0, 1)
        slot.refresh_from_db()
        assert slot.seats == 1
        assert slot.is_full
        with pytest.raises(OrientationError):
            slot.book(MemberFactory())

    def it_caps_a_slot_held_only_by_a_checkout_hold_to_one():
        rule = _equipment_rule()
        slot = _generated_slot(rule, seats=4)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT)
        assert orientations.retire_open_slots(rule) == (0, 1)
        slot.refresh_from_db()
        assert slot.seats == 1

    def it_leaves_an_already_capped_slot_alone():
        rule = _equipment_rule()
        slot = _generated_slot(rule, seats=1)
        OrientationBookingFactory(slot=slot)
        assert orientations.retire_open_slots(rule) == (0, 1)
        slot.refresh_from_db()
        assert slot.seats == 1

    def it_leaves_a_cancelled_slot_alone():
        rule = _equipment_rule()
        cancelled = _generated_slot(rule)
        cancelled.mark_cancelled(reason="Machine down")
        assert orientations.retire_open_slots(rule) == (0, 0)
        cancelled.refresh_from_db()
        assert cancelled.is_cancelled is True
        assert cancelled.cancelled_reason == "Machine down"
        assert cancelled.availability == rule  # a deliberate cancel stays attached and dead

    def it_cancels_rather_than_deletes_a_slot_with_booking_history():
        rule = _equipment_rule()
        slot = _generated_slot(rule)
        declined = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.DECLINED)
        assert orientations.retire_open_slots(rule) == (1, 0)
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.cancelled_reason == "The hours this time came from are no longer posted."
        assert slot.availability is None  # detached, so the time can regenerate
        assert OrientationBooking.objects.filter(pk=declined.pk, slot=slot).exists()
        assert slot not in OrientationSlot.objects.bookable()

    def it_cancels_a_slot_with_a_refunded_booking_and_keeps_the_refund_history():
        rule = _equipment_rule()
        slot = _generated_slot(rule)
        refunded = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.CANCELLED, amount_paid_cents=1500
        )
        refund = PaymentRefund.objects.create(
            orientation_booking=refunded, amount_cents=1500, status=PaymentRefund.Status.SUCCEEDED
        )
        assert orientations.retire_open_slots(rule) == (1, 0)
        assert OrientationSlot.objects.filter(pk=slot.pk, is_cancelled=True, availability__isnull=True).exists()
        assert PaymentRefund.objects.filter(pk=refund.pk, orientation_booking=refunded).exists()
        refunded.refresh_from_db()
        assert refunded.refund_state == "full"

    def it_deletes_the_rule_only_through_retire_rule():
        rule = _equipment_rule()
        open_slot = _generated_slot(rule, hour=10)
        booked = _generated_slot(rule, hour=11)
        OrientationBookingFactory(slot=booked)
        assert orientations.retire_rule(rule) == (1, 1)
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        booked.refresh_from_db()
        assert booked.availability is None
        assert booked.seats == 1


def describe_hold_expiry_recap():
    def _held(rule, *, seats: int = 4):
        slot = _generated_slot(rule, seats=seats)
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        return slot, hold

    def it_cancels_a_kept_slot_when_its_only_hold_is_released_after_the_rule_is_gone():
        rule = _equipment_rule()
        slot, hold = _held(rule)
        orientations.retire_rule(rule)
        assert orientations.release_hold_if_unpaid(hold) == "released"
        slot.refresh_from_db()
        assert slot.availability is None
        assert slot.is_cancelled is True
        assert slot.cancelled_reason == "The hours this time came from are no longer posted."

    def it_cancels_a_kept_slot_under_a_paused_rule():
        rule = _equipment_rule()
        slot, hold = _held(rule)
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        orientations.retire_open_slots(rule)
        orientations.release_hold_if_unpaid(hold)
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.availability is None

    def it_recaps_to_the_confirmed_booking_when_a_hold_expires():
        rule = _equipment_rule()
        slot, hold = _held(rule)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        assert orientations.retire_open_slots(rule) == (0, 1)
        slot.refresh_from_db()
        assert slot.seats == 2
        orientations.release_hold_if_unpaid(hold)
        slot.refresh_from_db()
        assert slot.seats == 1
        assert slot.is_cancelled is False

    def it_leaves_a_recapped_slot_alone_when_the_cap_already_matches():
        rule = _equipment_rule()
        slot, hold = _held(rule, seats=1)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        orientations.release_hold_if_unpaid(hold)
        slot.refresh_from_db()
        assert slot.seats == 1
        assert slot.is_cancelled is False

    def it_leaves_a_slot_under_a_live_rule_alone():
        rule = _equipment_rule()
        slot, hold = _held(rule)
        orientations.release_hold_if_unpaid(hold)
        slot.refresh_from_db()
        assert slot.seats == 4
        assert slot.is_cancelled is False

    def it_deletes_an_orphan_one_seat_manual_slot_instead_of_recapping():
        slot = OrientationSlotFactory(equipment_owned=True, seats=1)
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        orientations.release_hold_if_unpaid(hold)
        assert not OrientationSlot.objects.filter(pk=slot.pk).exists()

    def it_leaves_everything_alone_when_a_webhook_finalized_the_hold_first():
        rule = _equipment_rule()
        slot, hold = _held(rule)
        hold.status = OrientationBooking.Status.REQUESTED
        hold.save(update_fields=["status"])
        assert orientations.release_hold_if_unpaid(hold) == "released"
        slot.refresh_from_db()
        assert slot.seats == 4
        assert slot.is_cancelled is False
        assert OrientationBooking.objects.filter(pk=hold.pk).exists()

    def it_ignores_a_one_time_slot():
        slot = OrientationSlotFactory(equipment_owned=True, seats=4)
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        orientations.release_hold_if_unpaid(hold)
        slot.refresh_from_db()
        assert slot.seats == 4
        assert slot.is_cancelled is False


def describe_cancel_and_decline_recap():
    """A freed seat on a slot whose hours are gone or paused must not reopen (review finding 2)."""

    def _booked(rule, *, paid: bool = False, status: str | None = None):
        slot = _generated_slot(rule, seats=4)
        extra: dict = {"amount_paid_cents": 1500, "stripe_payment_id": "pi_recap"} if paid else {}
        if status is not None:
            extra["status"] = status
        return slot, OrientationBookingFactory(slot=slot, **extra)

    def _deleted_window(rule) -> None:
        orientations.retire_rule(rule)

    def _paused_window(rule) -> None:
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        orientations.retire_open_slots(rule)

    def _assert_closed(slot) -> None:
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.cancelled_reason == "The hours this time came from are no longer posted."
        assert slot.availability is None  # detached: the time regenerates once the rule is live again
        assert slot not in OrientationSlot.objects.bookable()

    def it_cancels_a_deleted_windows_slot_when_its_free_booking_is_cancelled():
        rule = _equipment_rule()
        slot, booking = _booked(rule, status=OrientationBooking.Status.CONFIRMED)
        _deleted_window(rule)
        slot.refresh_from_db()
        assert slot.seats == 1
        orientations.cancel_orientation(booking, actor_label="Sam")
        _assert_closed(slot)

    @patch("billing.refunds.issue_refund")
    def it_cancels_a_deleted_windows_slot_when_its_paid_booking_is_cancelled(mock_issue):
        rule = _equipment_rule()
        slot, booking = _booked(rule, paid=True, status=OrientationBooking.Status.CONFIRMED)
        _deleted_window(rule)
        orientations.cancel_orientation(booking, actor_label="Sam")
        mock_issue.assert_called_once()
        _assert_closed(slot)

    def it_cancels_a_paused_windows_slot_when_its_free_request_is_declined():
        rule = _equipment_rule()
        slot, booking = _booked(rule)
        _paused_window(rule)
        orientations.decline_orientation(booking, note="Sorry")
        _assert_closed(slot)

    @patch("billing.refunds.issue_refund")
    def it_cancels_a_paused_windows_slot_when_its_paid_request_is_declined(mock_issue):
        rule = _equipment_rule()
        slot, booking = _booked(rule, paid=True)
        _paused_window(rule)
        orientations.decline_orientation(booking, note="Sorry")
        mock_issue.assert_called_once()
        _assert_closed(slot)

    def it_recaps_to_the_remaining_booking_instead_of_cancelling():
        rule = _equipment_rule()
        slot, booking = _booked(rule)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        _deleted_window(rule)
        slot.refresh_from_db()
        assert slot.seats == 2
        orientations.cancel_orientation(booking, actor_label="Sam")
        slot.refresh_from_db()
        assert slot.seats == 1
        assert slot.is_cancelled is False
        assert slot.is_full

    def it_leaves_a_live_rules_slot_alone():
        rule = _equipment_rule()
        slot, booking = _booked(rule)
        orientations.cancel_orientation(booking, actor_label="Sam")
        slot.refresh_from_db()
        assert slot.seats == 4
        assert slot.is_cancelled is False

    def it_keeps_a_managers_cancel_reason_through_the_fan_out():
        rule = _equipment_rule()
        slot, _booking = _booked(rule, status=OrientationBooking.Status.CONFIRMED)
        _deleted_window(rule)
        slot.refresh_from_db()  # the manage panel fetches the slot fresh; drop the deleted rule from the cache
        orientations.cancel_slot(slot, reason="Machine down")
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.cancelled_reason == "Machine down"


def describe_retired_slots_regenerate():
    """A retired or recap-cancelled slot detaches from its rule, so its time comes back (second review round)."""

    def _ten(rule):
        return rule.slots.get(starts_at=_at(_tool_day(), 10))

    def _pause(rule) -> tuple[int, int]:
        rule.is_active = False
        rule.save(update_fields=["is_active"])
        return orientations.retire_open_slots(rule)

    def _unpause(rule) -> int:
        rule.is_active = True
        rule.save(update_fields=["is_active"])
        return orientations.generate_slots(equipment=_tool(rule))

    def it_brings_the_time_back_after_a_pause_when_the_member_cancels():
        rule = _equipment_rule()
        orientations.generate_slots(equipment=_tool(rule))
        slot = _ten(rule)
        booking = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        assert _pause(rule) == (15, 1)
        orientations.cancel_orientation(booking, actor_label="Sam")
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.availability is None
        assert _unpause(rule) == 16
        assert rule.slots.filter(is_cancelled=False).count() == 16
        fresh = _ten(rule)
        assert fresh.pk != slot.pk
        assert fresh in OrientationSlot.objects.bookable()
        # The detached row still carries its history.
        assert OrientationBooking.objects.filter(pk=booking.pk, slot=slot).exists()

    def it_brings_the_time_back_after_a_pause_with_a_declined_request():
        rule = _equipment_rule()
        orientations.generate_slots(equipment=_tool(rule))
        slot = _ten(rule)
        booking = OrientationBookingFactory(slot=slot)
        orientations.decline_orientation(booking, note="Sorry")
        slot.refresh_from_db()
        assert slot.is_cancelled is False  # a live rule's slot stays open after a decline
        assert _pause(rule) == (16, 0)  # 15 deleted, the history-bearing one retired
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.availability is None
        assert _unpause(rule) == 16
        fresh = _ten(rule)
        assert fresh.pk != slot.pk
        assert fresh in OrientationSlot.objects.bookable()
        assert OrientationBooking.objects.filter(pk=booking.pk, slot=slot).exists()

    def it_regenerates_the_time_across_a_break_round_trip():
        rule = _equipment_rule()
        orientations.generate_slots(equipment=_tool(rule))
        slot = _ten(rule)
        booking = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.DECLINED)
        rule.buffer_minutes = 15
        rule.save(update_fields=["buffer_minutes"])
        assert orientations.retire_open_slots(rule) == (16, 0)
        assert orientations.generate_slots(equipment=_tool(rule)) == 8  # 10:00 only: 11:15 no longer fits
        after_break = _ten(rule)
        assert after_break.pk != slot.pk
        assert after_break.is_cancelled is False
        rule.buffer_minutes = 0
        rule.save(update_fields=["buffer_minutes"])
        assert orientations.retire_open_slots(rule) == (8, 0)
        assert orientations.generate_slots(equipment=_tool(rule)) == 16
        assert rule.slots.filter(is_cancelled=False).count() == 16
        assert _ten(rule) in OrientationSlot.objects.bookable()
        slot.refresh_from_db()
        assert slot.is_cancelled is True
        assert slot.availability is None
        assert OrientationBooking.objects.filter(pk=booking.pk, slot=slot).exists()

    def it_leaves_a_manager_cancelled_slot_attached_and_unregenerated():
        rule = _equipment_rule()
        orientations.generate_slots(equipment=_tool(rule))
        slot = _ten(rule)
        orientations.cancel_slot(slot, reason="Machine down")
        assert _pause(rule) == (15, 0)
        assert _unpause(rule) == 15
        slot.refresh_from_db()
        assert slot.availability == rule
        assert slot.is_cancelled is True
        assert not OrientationSlot.objects.filter(starts_at=slot.starts_at, is_cancelled=False).exists()


def describe_retire_equipment_orienter():
    def it_sweeps_only_the_members_rules_on_that_tool_and_counts_booked_slots():
        from tests.membership.factories import EquipmentStaffMembershipFactory

        rule = _equipment_rule()
        tool = _tool(rule)
        dana = MemberFactory(full_legal_name="Dana Reyes")
        EquipmentStaffMembershipFactory(equipment=tool, member=dana)
        mine = _equipment_rule(orientation_type=rule.orientation_type, orienter=dana)
        shared = rule  # orienter-less, must survive
        elsewhere = _equipment_rule(orienter=dana)  # another tool's rule, must survive
        open_slot = _generated_slot(mine, hour=10)
        booked = _generated_slot(mine, hour=11)
        OrientationBookingFactory(slot=booked)
        for slot in (open_slot, booked):
            slot.orienter = dana
            slot.save(update_fields=["orienter"])

        removed, booked_remaining = orientations.retire_equipment_orienter(tool, dana)

        assert (removed, booked_remaining) == (1, 1)
        assert not OrientationAvailability.objects.filter(pk=mine.pk).exists()
        assert OrientationAvailability.objects.filter(pk=shared.pk).exists()
        assert OrientationAvailability.objects.filter(pk=elsewhere.pk).exists()
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        booked.refresh_from_db()
        assert booked.availability is None
        assert booked.orienter == dana

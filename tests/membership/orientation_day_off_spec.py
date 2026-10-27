"""BDD specs for days off (#767): the ``OrientationDayOff`` model, generation skipping a day the
rule's orientor has off (``_horizon_spans``, in any guild or on any equipment, with a shared rule
unaffected, however far ahead), ``times_on_day_off`` and ``cancel_day_off`` (fixed slots through
``cancel_slot`` with the member's email and a hold released, open windows cancelled with their
booked segments, times with no orientor and other people's and other days' times untouched, the
local day boundary), and what the next generation run recreates once a day off is deleted."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.core import mail
from django.db import IntegrityError
from django.utils import timezone

from membership import orientations
from membership.models import (
    GuildStaffMembership,
    OrientationAvailability,
    OrientationAvailabilityBlock,
    OrientationBooking,
    OrientationDayOff,
    OrientationSlot,
)
from tests.membership.factories import (
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityBlockFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _day(offset: int) -> date:
    """A local calendar day ``offset`` days from today."""
    return timezone.localdate() + timedelta(days=offset)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    """An aware local instant on ``day``."""
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _staffed_guild(*names: str) -> tuple[object, list[object]]:
    """An orientation guild with a lead, one active type, and one ORIENTER staffer per name."""
    MembershipPlanFactory()
    guild = GuildFactory(guild_lead=MemberFactory(full_legal_name="Lead Person"))
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    OrientationTypeFactory(guild=guild)
    staffers = []
    for name in names:
        member = MemberFactory(full_legal_name=name)
        GuildStaffMembershipFactory(guild=guild, member=member, role=GuildStaffMembership.Role.ORIENTER)
        staffers.append(member)
    return guild, staffers


def _rule(guild: object, orienter: object, day: date, **overrides: object) -> OrientationAvailability:
    """A weekly fixed rule of ``orienter``'s on ``day``'s weekday, 6 to 7 PM unless told otherwise."""
    fields: dict[str, object] = {"guild": guild, "orienter": orienter, "weekday": day.weekday()}
    fields.update(overrides)
    return OrientationAvailabilityFactory(**fields)


def _open_rule(guild: object, orienter: object, day: date) -> OrientationAvailability:
    """An Any orientation open row of ``orienter``'s on ``day``'s weekday, 11 AM to 1 PM."""
    return OrientationAvailabilityFactory(
        guild=guild,
        orienter=orienter,
        booking_style=OrientationAvailability.BookingStyle.OPEN,
        orientation_type=None,
        weekday=day.weekday(),
        start_time=time(11, 0),
        end_time=time(13, 0),
    )


def _slot(guild: object, orienter: object | None, day: date, hour: int, **overrides: object) -> OrientationSlot:
    """A one hour slot at ``hour`` local on ``day``, a one off of ``orienter``'s unless told otherwise."""
    fields: dict[str, object] = {
        "guild": guild,
        "orientation_type": guild.orientation_types.first(),
        "orienter": orienter,
        "starts_at": _at(day, hour),
        "ends_at": _at(day, hour) + timedelta(hours=1),
    }
    fields.update(overrides)
    return OrientationSlotFactory(**fields)


def _equipment_slot(orienter: object, day: date, hour: int) -> OrientationSlot:
    """A one hour equipment owned slot of ``orienter``'s at ``hour`` local on ``day``."""
    return OrientationSlotFactory(
        equipment_owned=True, orienter=orienter, starts_at=_at(day, hour), ends_at=_at(day, hour) + timedelta(hours=1)
    )


def _local_days(rows: object) -> set[date]:
    """The local calendar days the uncancelled rows in ``rows`` start on."""
    return {timezone.localtime(row.starts_at).date() for row in rows.filter(is_cancelled=False)}


def _day_off(orienter: object, day: date) -> OrientationDayOff:
    return OrientationDayOff.objects.create(orienter=orienter, date=day)


def describe_the_model():
    def it_reads_as_the_person_and_the_day():
        bob = MemberFactory(full_legal_name="Bob Placeholder")
        assert str(_day_off(bob, date(2026, 11, 26))) == "Bob Placeholder off 2026-11-26"

    def it_keeps_one_row_per_person_and_day():
        bob = MemberFactory()
        _day_off(bob, date(2026, 11, 26))
        with pytest.raises(IntegrityError):
            _day_off(bob, date(2026, 11, 26))

    def it_lists_only_upcoming_days_soonest_first():
        bob = MemberFactory()
        later, today, gone = _day_off(bob, _day(40)), _day_off(bob, _day(0)), _day_off(bob, _day(-1))
        assert list(OrientationDayOff.objects.upcoming()) == [today, later]
        assert gone not in OrientationDayOff.objects.upcoming()

    def it_finds_the_rows_of_the_listed_people_inside_a_horizon():
        bob, alice = MemberFactory(), MemberFactory()
        inside, past_the_edge, hers = _day_off(bob, _day(10)), _day_off(bob, _day(56)), _day_off(alice, _day(10))
        assert list(OrientationDayOff.objects.for_orienters([bob])) == [inside, past_the_edge]
        assert hers not in OrientationDayOff.objects.for_orienters([bob])
        assert list(OrientationDayOff.objects.within(_day(0), _day(56))) == [inside, hers]


def describe_generation_skips_a_day_off():
    def it_drops_the_day_from_a_rules_horizon_and_leaves_a_shared_rule_alone():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(14)
        rule, shared = _rule(guild, bob, day), _rule(guild, None, day)
        with_day_off = orientations._horizon_spans(rule, today=_day(0), window_weeks=4, days_off={day})
        without = orientations._horizon_spans(rule, today=_day(0), window_weeks=4)
        assert len(without) == 4 and len(with_day_off) == 3
        assert _at(day, 18) in [start for start, _end in without]
        assert _at(day, 18) not in [start for start, _end in with_day_off]
        # A shared rule has no orientor, so generation hands it nothing to skip.
        assert len(orientations._horizon_spans(shared, today=_day(0), window_weeks=4)) == 4

    def it_creates_nothing_of_theirs_that_day_in_any_guild_or_on_any_equipment_however_far_ahead():
        guild, (bob, alice) = _staffed_guild("Bob Placeholder", "Alice Ash")
        other_guild, _ = _staffed_guild()
        GuildStaffMembershipFactory(guild=other_guild, member=bob, role=GuildStaffMembership.Role.ORIENTER)
        far = _day(70)  # ten weeks out: past the 8 week window the Upcoming Times card reaches
        here, there, hers, shared = (
            _rule(guild, bob, far),
            _rule(other_guild, bob, far),
            _rule(guild, alice, far),
            _rule(guild, None, far),
        )
        on_tool = OrientationAvailabilityFactory(equipment_owned=True, orienter=bob, weekday=far.weekday())
        EquipmentStaffMembershipFactory(equipment=on_tool.orientation_type.equipment, member=bob)
        _day_off(bob, far)

        orientations.generate_slots(window_weeks=12)

        for his in (here, there, on_tool):
            days = _local_days(his.slots)
            assert far not in days, his
            assert {far - timedelta(days=7), far + timedelta(days=7)} <= days, his
        assert far in _local_days(hers.slots)
        assert far in _local_days(shared.slots)

    def it_creates_no_open_window_of_theirs_that_day_either():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        far = _day(70)
        rule = _open_rule(guild, bob, far)
        _day_off(bob, far)
        orientations.generate_slots(guild=guild, window_weeks=12)
        days = _local_days(rule.windows)
        assert far not in days
        assert far + timedelta(days=7) in days


def describe_times_on_day_off():
    def it_keeps_the_day_to_the_local_calendar():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        first_thing = _slot(guild, bob, day, 0)
        late = _slot(guild, bob, day, 23, ends_at=_at(day, 23) + timedelta(minutes=45))
        after_midnight = _slot(guild, bob, day + timedelta(days=1), 0, starts_at=_at(day + timedelta(days=1), 0, 30))
        the_day_before = _slot(guild, bob, day - timedelta(days=1), 23)
        times = orientations.times_on_day_off(bob, day)
        assert [slot.pk for slot in times.slots] == [first_thing.pk, late.pk]
        assert after_midnight.pk not in [slot.pk for slot in times.slots]
        assert the_day_before.pk not in [slot.pk for slot in times.slots]

    def it_counts_the_members_who_will_be_emailed_not_every_booking_row():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        slot = _slot(guild, bob, day, 10)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.REQUESTED)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CANCELLED)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id="")
        times = orientations.times_on_day_off(bob, day)
        assert (times.count, times.emailed) == (1, 2)

    def it_lists_a_live_windows_booked_segment_with_the_window_not_as_a_slot():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        window = OrientationAvailabilityBlockFactory(
            guild=guild, orienter=bob, starts_at=_at(day, 9), ends_at=_at(day, 12)
        )
        booking = orientations.request_block_orientation(
            window, MemberFactory(), _at(day, 10), orientation_type=guild.orientation_types.first()
        )
        times = orientations.times_on_day_off(bob, day)
        assert [window.pk for window in times.windows] == [window.pk]
        assert [segment.pk for segment in times.segments] == [booking.slot.pk]
        assert times.slots == []
        assert (times.count, times.emailed) == (1, 1)


def describe_cancel_day_off():
    def it_cancels_every_time_of_theirs_that_day_through_the_existing_paths():
        guild, (bob, alice) = _staffed_guild("Bob Placeholder", "Alice Ash")
        day = _day(3)
        booked = _slot(guild, bob, day, 10)
        booking = OrientationBookingFactory(
            slot=booked, status=OrientationBooking.Status.CONFIRMED, member=MemberFactory(full_legal_name="Rae Member")
        )
        held = _slot(guild, bob, day, 12)
        hold = OrientationBookingFactory(
            slot=held, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        on_tool = _equipment_slot(bob, day, 16)
        shared, hers, tomorrow = (
            _slot(guild, None, day, 14),
            _slot(guild, alice, day, 10),
            _slot(guild, bob, day + timedelta(days=1), 10),
        )
        mail.outbox.clear()

        assert orientations.cancel_day_off(bob, day) == (3, 1)

        for slot in (booked, held, on_tool):
            slot.refresh_from_db()
            assert slot.is_cancelled is True, slot
            assert slot.cancelled_reason == "Day off"
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert any("cancelled" in message.subject.lower() for message in mail.outbox)
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()  # the unpaid hold was released
        for slot in (shared, hers, tomorrow):
            slot.refresh_from_db()
            assert slot.is_cancelled is False, slot

    def it_detaches_an_unbooked_generated_slot_and_keeps_a_booked_one_cancelled_at_its_key():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        rule = _rule(guild, bob, day, slot_minutes=30)
        orientations.generate_slots(guild=guild)
        empty = rule.slots.get(starts_at=_at(day, 18))
        booked = rule.slots.get(starts_at=_at(day, 18, 30))
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)

        assert orientations.cancel_day_off(bob, day) == (2, 1)

        empty.refresh_from_db()
        booked.refresh_from_db()
        assert (empty.is_cancelled, empty.availability_id, empty.cancelled_reason) == (True, None, "Day off")
        assert (booked.is_cancelled, booked.availability_id) == (True, rule.pk)

    def it_cancels_a_window_with_its_booked_segment_and_detaches_an_empty_generated_one():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        rule = _open_rule(guild, bob, day)
        orientations.generate_slots(guild=guild)
        empty = rule.windows.get(starts_at=_at(day, 11))
        booked = OrientationAvailabilityBlockFactory(
            guild=guild, orienter=bob, starts_at=_at(day, 8), ends_at=_at(day, 10)
        )
        booking = orientations.request_block_orientation(
            booked, MemberFactory(), _at(day, 8), orientation_type=guild.orientation_types.first()
        )
        mail.outbox.clear()

        assert orientations.cancel_day_off(bob, day) == (2, 1)

        empty.refresh_from_db()
        booked.refresh_from_db()
        booking.refresh_from_db()
        assert (empty.is_cancelled, empty.availability_id) == (True, None)
        assert booked.is_cancelled is True
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert booking.slot.is_cancelled is True
        assert any("cancelled" in message.subject.lower() for message in mail.outbox)

    def it_retires_a_generated_window_whose_only_segment_is_a_checkout_hold_like_a_hold_only_slot():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        rule = _open_rule(guild, bob, day)
        orientations.generate_slots(guild=guild)
        window = rule.windows.get(starts_at=_at(day, 11))
        # A member mid checkout on a segment of the window: a seat holding hold, nobody emailed.
        segment = _slot(
            guild, bob, day, 11, block=window, source=OrientationSlot.Source.FROM_BLOCK, seats=1, enabled_settings=False
        )
        hold = OrientationBookingFactory(
            slot=segment, status=OrientationBooking.Status.PENDING_PAYMENT, stripe_session_id=""
        )
        mail.outbox.clear()
        day_off = _day_off(bob, day)

        assert orientations.cancel_day_off(bob, day) == (1, 0)

        window.refresh_from_db()
        assert (window.is_cancelled, window.availability_id) == (True, None)
        # The unpaid hold is released, and its orphan carved segment goes with it (``_delete_hold``).
        assert not OrientationBooking.objects.filter(pk=hold.pk).exists()
        assert not OrientationSlot.objects.filter(pk=segment.pk).exists()
        assert mail.outbox == []

        day_off.delete()
        orientations.generate_slots(guild=guild)
        assert rule.windows.filter(is_cancelled=False, starts_at=_at(day, 11)).exists()

    def it_touches_nothing_when_they_have_no_times_that_day():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        assert orientations.cancel_day_off(bob, _day(3)) == (0, 0)


def describe_deleting_a_day_off():
    def it_lets_the_next_run_recreate_the_unbooked_times_and_keeps_the_booked_one_cancelled():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        rule = _rule(guild, bob, day, slot_minutes=30)
        orientations.generate_slots(guild=guild)
        booked = rule.slots.get(starts_at=_at(day, 18, 30))
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)
        day_off = _day_off(bob, day)
        orientations.cancel_day_off(bob, day)

        orientations.generate_slots(guild=guild)
        assert not OrientationSlot.objects.filter(availability=rule, starts_at__date=day, is_cancelled=False).exists()

        day_off.delete()
        orientations.generate_slots(guild=guild)

        fresh = OrientationSlot.objects.filter(
            orienter=bob, is_cancelled=False, starts_at__gte=_at(day, 0), starts_at__lt=_at(day + timedelta(days=1), 0)
        )
        assert [slot.starts_at for slot in fresh] == [_at(day, 18)]
        booked.refresh_from_db()
        assert (booked.is_cancelled, booked.availability_id) == (True, rule.pk)

    def it_recreates_an_empty_open_window_the_same_way():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        rule = _open_rule(guild, bob, day)
        orientations.generate_slots(guild=guild)
        day_off = _day_off(bob, day)
        orientations.cancel_day_off(bob, day)
        orientations.generate_slots(guild=guild)
        assert not rule.windows.filter(is_cancelled=False, starts_at=_at(day, 11)).exists()

        day_off.delete()
        orientations.generate_slots(guild=guild)
        assert rule.windows.filter(is_cancelled=False, starts_at=_at(day, 11)).exists()
        assert OrientationAvailabilityBlock.objects.filter(orienter=bob, starts_at=_at(day, 11)).count() == 2

"""BDD specs for days off (#767, #1300): the ``OrientationDayOff`` model, generation skipping a day
the rule's orientor has off (``_horizon_spans``, in any guild or on any equipment, with a shared
rule unaffected, however far ahead, a tour day off too), a day off hiding only the unbooked times
and cancelling nothing (#1300), and what the next generation run recreates once a day off is deleted."""

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


def _bookable(*slots: OrientationSlot) -> list[bool]:
    """Whether each slot is offered to members (``bookable()``, the list every page reads)."""
    offered = set(OrientationSlot.objects.bookable().values_list("pk", flat=True))
    return [slot.pk in offered for slot in slots]


def describe_a_day_off_cancels_nothing():
    def it_hides_their_unbooked_times_that_day_and_keeps_every_booking():
        guild, (bob, alice) = _staffed_guild("Bob Placeholder", "Alice Ash")
        day = _day(3)
        empty, booked = _slot(guild, bob, day, 10), _slot(guild, bob, day, 12, seats=2)
        booking = OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)
        shared, hers, tomorrow = (
            _slot(guild, None, day, 14),
            _slot(guild, alice, day, 10),
            _slot(guild, bob, day + timedelta(days=1), 10),
        )
        mail.outbox.clear()

        _day_off(bob, day)

        assert _bookable(empty, booked, shared, hers, tomorrow) == [False, False, True, True, True]
        booked.refresh_from_db()
        booking.refresh_from_db()
        assert (booked.is_cancelled, booking.status) == (False, OrientationBooking.Status.CONFIRMED)
        assert mail.outbox == []

    def it_leaves_no_free_time_in_their_window_that_day():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        window = OrientationAvailabilityBlockFactory(
            guild=guild, orienter=bob, starts_at=_at(day, 9), ends_at=_at(day, 12)
        )
        _day_off(bob, day)
        assert window.valid_starts_for(guild.orientation_types.first()) == []
        assert OrientationAvailabilityBlock.free_intervals_by_block([window]) == {window.pk: []}

    def it_keeps_a_booked_generated_slot_through_the_next_run_and_offers_the_day_again_once_deleted():
        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        rule = _rule(guild, bob, day, slot_minutes=30)
        orientations.generate_slots(guild=guild)
        booked = rule.slots.get(starts_at=_at(day, 18, 30))
        OrientationBookingFactory(slot=booked, status=OrientationBooking.Status.CONFIRMED)
        day_off = _day_off(bob, day)

        orientations.generate_slots(guild=guild)
        assert list(OrientationSlot.objects.filter(availability=rule, starts_at__date=day, is_cancelled=False)) == [
            booked
        ]

        day_off.delete()
        orientations.generate_slots(guild=guild)
        fresh = OrientationSlot.objects.filter(
            orienter=bob, is_cancelled=False, starts_at__gte=_at(day, 0), starts_at__lt=_at(day + timedelta(days=1), 0)
        )
        assert [slot.starts_at for slot in fresh] == [_at(day, 18), _at(day, 18, 30)]

    def it_skips_the_day_of_a_tour_day_off_too():
        from tests.tours.factories import TourDayOffFactory

        guild, (bob,) = _staffed_guild("Bob Placeholder")
        day = _day(3)
        _rule(guild, bob, day)
        TourDayOffFactory(guide=bob, starts_on=day, ends_on=day + timedelta(days=1))
        orientations.generate_slots(guild=guild)
        assert day not in _local_days(OrientationSlot.objects.filter(orienter=bob))
        assert day + timedelta(days=7) in _local_days(OrientationSlot.objects.filter(orienter=bob))

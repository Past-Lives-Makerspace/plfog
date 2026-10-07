"""BDD specs for "Equipment it uses" on an orientation type (#658).

A slot with an active booking blocks every item its type lists, through
``OrientationSlotQuerySet.holding_seats_on``: the day's busy spans, the "Reserved until"
line and the ``ensure_reservable`` refusal. An open, unbooked slot blocks nothing. An
equipment owned type always lists its owner (the model's save and migration 0211), and
the owner FK still decides who runs the orientation.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from importlib import import_module
from typing import Any

import pytest
from django.apps import apps
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from classes.factories import UserFactory
from hub.view_as import ROLE_MEMBER, ViewAs
from membership import equipment as equipment_service
from membership.models import (
    Equipment,
    EquipmentError,
    Guild,
    Member,
    OrientationAvailability,
    OrientationBooking,
    OrientationSlot,
    OrientationType,
)
from membership.permissions import manageable_orientation_bookings
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

_migration = import_module("membership.migrations.0211_orientationtype_uses_equipment")


def _day() -> Any:
    return timezone.localdate() + timedelta(days=2)


def _at(hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(_day(), time(hour, minute)))


def _open_tool(name: str) -> Equipment:
    """A standalone tool open 9 to 5 on the test day's weekday."""
    equipment = EquipmentFactory(name=name)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _member() -> Member:
    MembershipPlanFactory()
    member = UserFactory().member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member


def _printmaking(*uses: Equipment) -> OrientationType:
    """A guild owned orientation that lists ``uses``, like Printmaking on the etching press."""
    orientation_type = OrientationTypeFactory(guild=GuildFactory(name="Printmaking Guild"), name="Etching Basics")
    orientation_type.uses_equipment.set(uses)
    return orientation_type


def _slot(orientation_type: OrientationType, hour: int, *, starts_at: datetime | None = None) -> OrientationSlot:
    start = starts_at if starts_at is not None else _at(hour)
    return OrientationSlotFactory(
        guild=orientation_type.guild,
        orientation_type=orientation_type,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
    )


def describe_listing_the_owner():
    def it_lists_an_equipment_owned_types_own_equipment_when_created():
        laser = EquipmentFactory(name="Laser Engraver")
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")
        assert list(orientation_type.uses_equipment.all()) == [laser]

    def it_puts_the_owner_back_on_the_next_save_after_it_was_removed():
        laser = EquipmentFactory(name="Laser Engraver")
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")
        orientation_type.uses_equipment.clear()
        orientation_type.save()
        assert list(orientation_type.uses_equipment.all()) == [laser]

    def it_lists_nothing_for_a_new_guild_owned_type():
        orientation_type = OrientationTypeFactory(guild=GuildFactory(), name="Shop Basics")
        assert not orientation_type.uses_equipment.exists()


def describe_migration_0211_list_owner_equipment():
    def it_adds_each_equipment_owned_types_owner_and_leaves_guild_types_alone():
        cnc = EquipmentFactory(name="CNC Machine")
        laser = EquipmentFactory(name="Laser Engraver Mira 9")
        cnc_type = OrientationTypeFactory(equipment_owned=True, equipment=cnc, name="CNC Basics")
        laser_type = OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")
        guild_type = OrientationTypeFactory(guild=GuildFactory(), name="Shop Basics")
        OrientationType.uses_equipment.through.objects.all().delete()

        _migration.list_owner_equipment(apps, None)

        assert list(cnc_type.uses_equipment.all()) == [cnc]
        assert list(laser_type.uses_equipment.all()) == [laser]
        assert not guild_type.uses_equipment.exists()

    def it_is_safe_to_run_over_an_owner_already_listed():
        laser = EquipmentFactory(name="Laser Engraver Mira 9")
        laser_type = OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")

        _migration.list_owner_equipment(apps, None)

        assert list(laser_type.uses_equipment.all()) == [laser]

    def it_reverses_by_removing_only_the_owner_rows():
        laser = EquipmentFactory(name="Laser Engraver Mira 9")
        press = EquipmentFactory(name="Etching Press")
        laser_type = OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")
        laser_type.uses_equipment.add(press)
        guild_type = _printmaking(press)

        _migration.unlist_owner_equipment(apps, None)

        assert list(laser_type.uses_equipment.all()) == [press]
        assert list(guild_type.uses_equipment.all()) == [press]


def describe_a_booked_slot_blocks_every_listed_item():
    def it_returns_the_slot_for_each_listed_item_and_not_for_others():
        press = _open_tool("Etching Press")
        roller = _open_tool("Brayer Station")
        other = _open_tool("Lathe")
        slot = _slot(_printmaking(press, roller), 11)
        OrientationBookingFactory(slot=slot)
        assert list(OrientationSlot.objects.holding_seats_on(press, _at(11), _at(12))) == [slot]
        assert list(OrientationSlot.objects.holding_seats_on(roller, _at(11), _at(12))) == [slot]
        assert not OrientationSlot.objects.holding_seats_on(other, _at(11), _at(12)).exists()

    def it_marks_the_span_busy_on_each_listed_item():
        press = _open_tool("Etching Press")
        roller = _open_tool("Brayer Station")
        OrientationBookingFactory(slot=_slot(_printmaking(press, roller), 11))
        assert press.busy_spans_for_day(_day()) == [(_at(11), _at(12))]
        assert roller.busy_spans_for_day(_day()) == [(_at(11), _at(12))]
        assert _at(11) not in press.free_starts_for_day(_day())

    def it_shows_reserved_until_while_the_orientation_runs():
        press = EquipmentFactory(name="Etching Press")
        EquipmentHoursFactory(
            equipment=press, weekday=timezone.localtime().weekday(), start_time=time(0, 0), end_time=time(23, 59)
        )
        now = timezone.now()
        slot = _slot(_printmaking(press), 0, starts_at=now - timedelta(minutes=30))
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        ends_local = timezone.localtime(slot.ends_at)
        hour = ends_local.hour % 12 or 12
        suffix = "AM" if ends_local.hour < 12 else "PM"
        assert press.availability_line() == ("busy", f"Reserved until {hour}:{ends_local.minute:02d} {suffix}")

    def it_refuses_a_reservation_over_the_booked_slot_on_every_listed_item():
        press = _open_tool("Etching Press")
        roller = _open_tool("Brayer Station")
        OrientationBookingFactory(slot=_slot(_printmaking(press, roller), 11))
        for item in (press, roller):
            with pytest.raises(EquipmentError, match="overlaps a booked orientation"):
                equipment_service.reserve(item, _member(), _at(11), 60)
        assert not press.reservations.exists()
        assert not roller.reservations.exists()

    def it_still_blocks_an_equipment_owned_types_own_machine():
        laser = _open_tool("Laser Engraver")
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")
        slot = OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(11), ends_at=_at(12)
        )
        OrientationBookingFactory(slot=slot)
        assert laser.busy_spans_for_day(_day()) == [(_at(11), _at(12))]

    def it_does_not_block_the_owner_through_the_fk_once_the_list_lacks_it():
        laser = _open_tool("Laser Engraver")
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")
        slot = OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(11), ends_at=_at(12)
        )
        OrientationBookingFactory(slot=slot)
        orientation_type.uses_equipment.clear()
        assert laser.busy_spans_for_day(_day()) == []


def describe_an_unbooked_slot_blocks_nothing():
    def it_leaves_every_listed_item_free():
        press = _open_tool("Etching Press")
        _slot(_printmaking(press), 11)
        assert press.busy_spans_for_day(_day()) == []
        assert _at(11) in press.free_starts_for_day(_day())

    def it_lets_a_member_reserve_over_it():
        press = _open_tool("Etching Press")
        _slot(_printmaking(press), 11)
        reservation = equipment_service.reserve(press, _member(), _at(11), 60)
        assert reservation.equipment == press

    def it_frees_the_item_once_the_only_booking_is_cancelled():
        press = _open_tool("Etching Press")
        slot = _slot(_printmaking(press), 11)
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CANCELLED)
        assert press.busy_spans_for_day(_day()) == []


def describe_ownership_is_unchanged():
    def it_keeps_a_guild_type_that_uses_equipment_owned_by_its_guild():
        press = EquipmentFactory(name="Etching Press")
        orientation_type = _printmaking(press)
        assert orientation_type.is_equipment_owned is False
        assert isinstance(orientation_type.owner, Guild)
        assert orientation_type.owner_page_path() == reverse("hub_guild_detail", args=[orientation_type.guild.slug])
        assert not press.owned_orientation_types.exists()

    def it_keeps_the_guild_types_hours_off_the_equipment():
        press = EquipmentFactory(name="Etching Press")
        orientation_type = _printmaking(press)
        OrientationAvailabilityFactory(guild=orientation_type.guild, orientation_type=orientation_type)
        assert not OrientationAvailability.objects.for_equipment(press).exists()

    def it_lets_the_guild_lead_run_the_booking_and_not_the_equipments_staff():
        press = EquipmentFactory(name="Etching Press")
        orientation_type = _printmaking(press)
        booking = OrientationBookingFactory(slot=_slot(orientation_type, 11))
        lead = _member()
        guild = orientation_type.guild
        guild.guild_lead = lead
        guild.save()
        press_staff = _member()
        EquipmentStaffMembershipFactory(equipment=press, member=press_staff)

        def _runs(member: Member) -> bool:
            request = RequestFactory().get("/")
            request.user = member.user
            request.view_as = ViewAs(actual=frozenset({ROLE_MEMBER}), picked=None)
            return (
                manageable_orientation_bookings(request, OrientationBooking.objects.all())
                .filter(pk=booking.pk)
                .exists()
            )

        assert _runs(lead) is True
        assert _runs(press_staff) is False

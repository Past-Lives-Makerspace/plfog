"""BDD specs for managers' blocks on equipment (#657).

A block is an ``EquipmentReservation`` of kind BLOCK: ``block_time()`` holds it under the
Equipment row lock, skipping the member limits but never landing on a reservation, another
block or a booked orientation. Members cannot book over it, it never counts toward a cap,
it never charges a late fee, and nothing about it is emailed.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from billing.models import LateCancellationFee
from core.models import Notification, SiteConfiguration
from membership import equipment as equipment_service
from membership.models import Equipment, EquipmentError, EquipmentReservation, Member, OrientationBooking
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _day(offset: int = 2):
    return timezone.localdate() + timedelta(days=offset)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _linked_member(username: str) -> Member:
    """An ACTIVE member with a linked User, so any notification would have somewhere to land."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member


def _tool(**kwargs) -> Equipment:
    """A tool open 9 to 5 on ``_day()``'s weekday, with tight member limits."""
    defaults = {"min_duration_minutes": 60, "max_duration_minutes": 120, "max_active_reservations_per_member": 1}
    equipment = EquipmentFactory(**{**defaults, **kwargs})
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _manager(equipment: Equipment, username: str = "blocker") -> Member:
    member = _linked_member(username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=member)
    return member


def _block(equipment: Equipment, manager: Member, start: datetime, end: datetime, reason: str = "Orientation"):
    return equipment_service.block_time(equipment, manager, start, end, reason=reason)


def describe_block_time():
    def it_holds_the_span_as_a_block_held_by_the_manager():
        equipment = _tool()
        manager = _manager(equipment)
        block = _block(equipment, manager, _at(_day(), 10), _at(_day(), 12), reason="  Press maintenance  ")
        assert block.kind == EquipmentReservation.Kind.BLOCK
        assert block.is_block
        assert block.member == manager
        assert block.purpose == "Press maintenance"
        assert block.status == EquipmentReservation.Status.CONFIRMED

    def it_skips_the_duration_bounds_and_the_open_hours():
        equipment = _tool()
        manager = _manager(equipment)
        # 6 AM to 10 PM: outside 9 to 5 and far past the 2 hour maximum.
        block = _block(equipment, manager, _at(_day(), 6), _at(_day(), 22))
        assert block.ends_at - block.starts_at == timedelta(hours=16)

    def it_skips_the_booking_horizon_and_a_closure():
        equipment = _tool(max_advance_days=7, is_closed=True)
        manager = _manager(equipment)
        far = _day(30)
        assert _block(equipment, manager, _at(far, 10), _at(far, 11)).is_block

    def it_skips_the_managers_own_reservation_cap():
        equipment = _tool()
        manager = _manager(equipment)
        EquipmentReservationFactory(
            equipment=equipment, member=manager, starts_at=_at(_day(), 9), ends_at=_at(_day(), 10)
        )
        _block(equipment, manager, _at(_day(), 11), _at(_day(), 12))
        _block(equipment, manager, _at(_day(), 13), _at(_day(), 14))
        assert equipment.reservations.blocks().count() == 2

    def it_allows_a_block_that_meets_a_reservation_end_to_end():
        equipment = _tool()
        manager = _manager(equipment)
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(_day(), 10), ends_at=_at(_day(), 12))
        assert _block(equipment, manager, _at(_day(), 12), _at(_day(), 13)).is_block

    def it_refuses_a_span_over_a_reservation_naming_the_member_and_the_time():
        equipment = _tool()
        manager = _manager(equipment)
        sam = MemberFactory(full_legal_name="Sam Reyes", preferred_name="")
        EquipmentReservationFactory(equipment=equipment, member=sam, starts_at=_at(_day(), 14), ends_at=_at(_day(), 16))
        with pytest.raises(EquipmentError) as exc:
            _block(equipment, manager, _at(_day(), 13), _at(_day(), 15))
        assert str(exc.value) == "Overlaps Sam R.'s reservation, 2:00 PM to 4:00 PM."
        assert not equipment.reservations.blocks().exists()

    def it_names_the_earliest_reservation_when_several_overlap():
        equipment = _tool()
        manager = _manager(equipment)
        late = MemberFactory(full_legal_name="Zed Late", preferred_name="")
        early = MemberFactory(full_legal_name="Amy Early", preferred_name="")
        EquipmentReservationFactory(
            equipment=equipment, member=late, starts_at=_at(_day(), 13), ends_at=_at(_day(), 14)
        )
        EquipmentReservationFactory(
            equipment=equipment, member=early, starts_at=_at(_day(), 10), ends_at=_at(_day(), 11)
        )
        with pytest.raises(EquipmentError, match=r"^Overlaps Amy E\.'s reservation, 10:00 AM to 11:00 AM\.$"):
            _block(equipment, manager, _at(_day(), 9), _at(_day(), 17))

    def it_refuses_a_span_over_another_block_naming_its_reason():
        equipment = _tool()
        manager = _manager(equipment)
        _block(equipment, manager, _at(_day(), 10), _at(_day(), 11, 30), reason="Workshop")
        with pytest.raises(EquipmentError) as exc:
            _block(equipment, manager, _at(_day(), 11), _at(_day(), 12))
        assert str(exc.value) == "Overlaps time already held for Workshop, 10:00 AM to 11:30 AM."

    def it_refuses_a_span_over_a_booked_orientation():
        equipment = _tool()
        manager = _manager(equipment)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        slot = OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(_day(), 15), ends_at=_at(_day(), 16)
        )
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        with pytest.raises(EquipmentError) as exc:
            _block(equipment, manager, _at(_day(), 14), _at(_day(), 15, 30))
        assert str(exc.value) == "Overlaps a booked orientation, 3:00 PM to 4:00 PM."

    def it_ignores_an_open_unbooked_orientation_slot():
        equipment = _tool()
        manager = _manager(equipment)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(_day(), 15), ends_at=_at(_day(), 16)
        )
        assert _block(equipment, manager, _at(_day(), 14), _at(_day(), 16)).is_block

    def it_refuses_someone_who_does_not_manage_the_item():
        equipment = _tool()
        outsider = _linked_member("outsider")
        with pytest.raises(EquipmentError, match="Only a manager"):
            _block(equipment, outsider, _at(_day(), 10), _at(_day(), 11))

    def it_refuses_an_end_at_or_before_the_start():
        equipment = _tool()
        manager = _manager(equipment)
        with pytest.raises(EquipmentError, match="end time must be after"):
            _block(equipment, manager, _at(_day(), 10), _at(_day(), 10))

    def it_refuses_a_span_that_already_started():
        equipment = _tool()
        manager = _manager(equipment)
        with pytest.raises(EquipmentError, match="already past"):
            _block(equipment, manager, _at(_day(-1), 10), _at(_day(-1), 11))

    def it_refuses_a_start_or_end_off_the_half_hour_grid():
        equipment = _tool()
        manager = _manager(equipment)
        with pytest.raises(EquipmentError, match="half hour marks"):
            _block(equipment, manager, _at(_day(), 10, 15), _at(_day(), 11))
        with pytest.raises(EquipmentError, match="half hour marks"):
            _block(equipment, manager, _at(_day(), 10), _at(_day(), 11, 10))
        with pytest.raises(EquipmentError, match="half hour marks"):
            _block(equipment, manager, _at(_day(), 10), _at(_day(), 11) + timedelta(seconds=1))

    def it_refuses_a_blank_reason():
        equipment = _tool()
        manager = _manager(equipment)
        with pytest.raises(EquipmentError, match="give a reason"):
            _block(equipment, manager, _at(_day(), 10), _at(_day(), 11), reason="   ")

    def it_takes_a_reason_of_eighty_characters_and_refuses_eighty_one():
        equipment = _tool()
        manager = _manager(equipment)
        assert _block(equipment, manager, _at(_day(), 10), _at(_day(), 11), reason="x" * 80).purpose == "x" * 80
        with pytest.raises(EquipmentError, match="80 characters"):
            _block(equipment, manager, _at(_day(), 12), _at(_day(), 13), reason="x" * 81)

    def it_sends_no_email_and_no_notification():
        equipment = _tool()
        manager = _manager(equipment)
        _manager(equipment, "other_manager")
        mail.outbox.clear()
        _block(equipment, manager, _at(_day(), 10), _at(_day(), 11))
        assert mail.outbox == []
        assert not Notification.objects.exists()


def describe_members_and_a_block():
    def it_refuses_a_member_booking_over_a_block():
        equipment = _tool(max_duration_minutes=180)
        manager = _manager(equipment)
        _block(equipment, manager, _at(_day(), 11), _at(_day(), 12))
        member = _linked_member("booker")
        with pytest.raises(EquipmentError, match="just taken"):
            equipment_service.reserve(equipment, member, _at(_day(), 10), 120)

    def it_offers_no_start_inside_a_block():
        equipment = _tool()
        manager = _manager(equipment)
        _block(equipment, manager, _at(_day(), 10), _at(_day(), 16))
        starts = equipment.free_starts_for_day(_day())
        assert all(not (_at(_day(), 10) <= start < _at(_day(), 16)) for start in starts)
        assert _at(_day(), 9) in starts

    def it_frees_the_span_again_once_removed():
        equipment = _tool()
        manager = _manager(equipment)
        block = _block(equipment, manager, _at(_day(), 10), _at(_day(), 12))
        block.remove_block(manager)
        member = _linked_member("after")
        assert equipment_service.reserve(equipment, member, _at(_day(), 10), 60).kind == "reservation"


def describe_active_count_for():
    def it_counts_reservations_and_never_blocks():
        equipment = _tool()
        manager = _manager(equipment)
        _block(equipment, manager, _at(_day(), 9), _at(_day(), 10))
        assert EquipmentReservation.objects.active_count_for(manager, equipment) == 0
        # The cap is 1, and the block does not use it up.
        reservation = equipment_service.reserve(equipment, manager, _at(_day(), 11), 60)
        assert EquipmentReservation.objects.active_count_for(manager, equipment) == 1
        assert reservation.kind == EquipmentReservation.Kind.RESERVATION


def describe_remove_block():
    def _late_fee_site() -> None:
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()

    def it_releases_the_block_without_a_reason_an_email_or_a_fee():
        _late_fee_site()
        equipment = _tool(late_cancel_fee_cents=2500)
        manager = _manager(equipment)
        soon = timezone.now() + timedelta(hours=2)
        start = soon.replace(minute=0 if soon.minute < 30 else 30, second=0, microsecond=0)
        block = _block(equipment, manager, start, start + timedelta(hours=1))
        mail.outbox.clear()
        block.remove_block(manager)
        block.refresh_from_db()
        assert block.status == EquipmentReservation.Status.CANCELLED
        assert block.cancelled_by == manager
        assert block.cancelled_reason == ""
        assert block.cancelled_as_manager is True
        assert block.cancelled_at is not None
        assert not LateCancellationFee.objects.exists()
        assert mail.outbox == []
        assert not Notification.objects.exists()

    def it_lets_any_manager_of_the_item_remove_it():
        equipment = _tool()
        block = _block(equipment, _manager(equipment), _at(_day(), 10), _at(_day(), 11))
        block.remove_block(_manager(equipment, "co_manager"))
        assert block.status == EquipmentReservation.Status.CANCELLED

    def it_refuses_someone_who_does_not_manage_the_item():
        equipment = _tool()
        block = _block(equipment, _manager(equipment), _at(_day(), 10), _at(_day(), 11))
        with pytest.raises(EquipmentError, match="Only a manager"):
            block.remove_block(_linked_member("outsider"))
        block.refresh_from_db()
        assert block.status == EquipmentReservation.Status.CONFIRMED

    def it_refuses_a_member_reservation():
        equipment = _tool()
        manager = _manager(equipment)
        reservation = EquipmentReservationFactory(equipment=equipment)
        with pytest.raises(EquipmentError, match="Only a block"):
            reservation.remove_block(manager)

    def it_removes_once_and_refuses_the_second_time():
        equipment = _tool()
        manager = _manager(equipment)
        block = _block(equipment, manager, _at(_day(), 10), _at(_day(), 11))
        stale = EquipmentReservation.objects.get(pk=block.pk)
        block.remove_block(manager)
        with pytest.raises(EquipmentError, match="already removed"):
            stale.remove_block(manager)


def describe_cancel_on_a_block():
    def it_refuses_so_no_reason_email_or_fee_path_can_run():
        equipment = _tool(late_cancel_fee_cents=2500)
        manager = _manager(equipment)
        block = _block(equipment, manager, _at(_day(), 10), _at(_day(), 11))
        with pytest.raises(EquipmentError, match="This is a block"):
            block.cancel(manager)
        with pytest.raises(EquipmentError, match="This is a block"):
            block.cancel(manager, reason="Done", as_manager=True)
        block.refresh_from_db()
        assert block.status == EquipmentReservation.Status.CONFIRMED
        assert not LateCancellationFee.objects.exists()


def describe_queryset_kinds():
    def it_splits_reservations_from_blocks():
        equipment = _tool()
        reservation = EquipmentReservationFactory(equipment=equipment)
        block = EquipmentReservationFactory(equipment=equipment, kind=EquipmentReservation.Kind.BLOCK)
        assert list(EquipmentReservation.objects.reservations()) == [reservation]
        assert list(EquipmentReservation.objects.blocks()) == [block]


def describe_kind_database_default():
    def it_files_a_row_inserted_without_kind_as_a_reservation():
        """The old release's reserve() inserts without ``kind`` while the migration runs; it must not fail."""
        from django.db import connection

        equipment = _tool()
        member = MemberFactory()
        table = EquipmentReservation._meta.db_table
        now = timezone.now()
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} (equipment_id, member_id, starts_at, ends_at, purpose, status, "  # noqa: S608
                "cancelled_reason, cancelled_as_manager, late_fee_waived, created_at) "
                "VALUES (%s, %s, %s, %s, '', 'confirmed', '', %s, %s, %s)",
                [equipment.pk, member.pk, _at(_day(), 10), _at(_day(), 11), False, False, now],
            )
        row = EquipmentReservation.objects.get(equipment=equipment)
        assert row.kind == EquipmentReservation.Kind.RESERVATION


def describe_availability_line_with_a_block():
    def _running(equipment: Equipment, minutes: int, **fields) -> EquipmentReservation:
        return EquipmentReservationFactory(
            equipment=equipment,
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=minutes),
            **fields,
        )

    def _booked_orientation(equipment: Equipment, minutes: int) -> None:
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        slot = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=minutes),
        )
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)

    def _clock(value: datetime) -> str:
        local = timezone.localtime(value)
        return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"

    def it_says_held_until_the_block_ends():
        equipment = _tool()
        block = _running(equipment, 60, kind=EquipmentReservation.Kind.BLOCK)
        assert equipment.availability_line() == ("busy", f"Held until {_clock(block.ends_at)}")

    def it_still_says_reserved_for_a_member_reservation():
        equipment = _tool()
        reservation = _running(equipment, 60)
        assert equipment.availability_line() == ("busy", f"Reserved until {_clock(reservation.ends_at)}")

    def it_takes_the_orientations_word_when_it_ends_after_the_block():
        equipment = _tool()
        _running(equipment, 30, kind=EquipmentReservation.Kind.BLOCK)
        _booked_orientation(equipment, 90)
        tone, text = equipment.availability_line()
        assert (tone, text.split(" until ")[0]) == ("busy", "Reserved")

    def it_takes_the_blocks_word_when_it_ends_after_the_orientation():
        equipment = _tool()
        block = _running(equipment, 90, kind=EquipmentReservation.Kind.BLOCK)
        _booked_orientation(equipment, 30)
        assert equipment.availability_line() == ("busy", f"Held until {_clock(block.ends_at)}")

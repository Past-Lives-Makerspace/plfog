"""BDD specs for the equipment reservation engine + service (spec §4/§5/§8, PR 2).

Covers the overlap predicate, ``free_starts_for_day`` / ``durations_for``,
``reserve()`` (every guard branch plus the sequential race shape the lock
serializes), ``cancel()`` with the manager-reason guard, the three spine events
(registry defaults, resolver, placeholder/context parity), and the ``.ics``.
All window math asserted in local time at ``now + 2 days``.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core import mail
from django.utils import timezone

from core.events import resolvers
from core.events.copy import COPY_CHANNELS, default_copy_for, placeholders_for
from core.events.registry import Channel, ChannelDefault, Recipients, get_event
from core.events.rendering import render_text
from core.models import Notification
from membership import equipment as equipment_service
from membership import orientations
from membership.models import (
    AdminCapability,
    Equipment,
    EquipmentError,
    EquipmentReservation,
    Member,
    OrientationBooking,
    OrientationError,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
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


def _open_tool(*, day=None, start=time(9, 0), end=time(17, 0), **kwargs) -> Equipment:
    """A standalone tool with one active window on ``day``'s weekday (default: +2 days, 9 to 5)."""
    equipment = EquipmentFactory(**kwargs)
    target = day if day is not None else _day()
    EquipmentHoursFactory(equipment=equipment, weekday=target.weekday(), start_time=start, end_time=end)
    return equipment


def _linked_member(username: str) -> Member:
    """An ACTIVE member with a linked User (so spine channels can deliver)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    return member


def describe_overlapping():
    def it_detects_containment_and_partial_overlap():
        equipment = EquipmentFactory()
        day = _day()
        held = EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 12))
        assert list(EquipmentReservation.objects.overlapping(equipment, _at(day, 10, 30), _at(day, 11))) == [held]
        assert list(EquipmentReservation.objects.overlapping(equipment, _at(day, 9), _at(day, 13))) == [held]
        assert list(EquipmentReservation.objects.overlapping(equipment, _at(day, 11), _at(day, 13))) == [held]

    def it_treats_adjacent_bookings_as_no_conflict():
        equipment = EquipmentFactory()
        day = _day()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 12))
        assert not EquipmentReservation.objects.overlapping(equipment, _at(day, 12), _at(day, 13)).exists()
        assert not EquipmentReservation.objects.overlapping(equipment, _at(day, 9), _at(day, 10)).exists()

    def it_never_conflicts_with_cancelled_rows():
        equipment = EquipmentFactory()
        day = _day()
        EquipmentReservationFactory(
            equipment=equipment,
            starts_at=_at(day, 10),
            ends_at=_at(day, 12),
            status=EquipmentReservation.Status.CANCELLED,
        )
        assert not EquipmentReservation.objects.overlapping(equipment, _at(day, 10), _at(day, 12)).exists()

    def it_scopes_to_the_given_equipment():
        day = _day()
        EquipmentReservationFactory(starts_at=_at(day, 10), ends_at=_at(day, 12))
        other = EquipmentFactory()
        assert not EquipmentReservation.objects.overlapping(other, _at(day, 10), _at(day, 12)).exists()


def describe_free_starts_for_day():
    def it_offers_half_hour_starts_that_fit_the_minimum():
        equipment = _open_tool(start=time(9, 0), end=time(11, 0))
        starts = equipment.free_starts_for_day(_day())
        locals_ = [timezone.localtime(s) for s in starts]
        assert [(dt.hour, dt.minute) for dt in locals_] == [(9, 0), (9, 30), (10, 0), (10, 30)]

    def it_excludes_starts_blocked_by_a_reservation():
        equipment = _open_tool(start=time(9, 0), end=time(12, 0))
        day = _day()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 11))
        locals_ = [
            (timezone.localtime(s).hour, timezone.localtime(s).minute) for s in equipment.free_starts_for_day(day)
        ]
        assert (10, 0) not in locals_
        assert (10, 30) not in locals_
        assert (9, 0) in locals_
        assert (11, 0) in locals_

    def it_excludes_a_tail_too_short_for_the_minimum():
        equipment = _open_tool(start=time(9, 0), end=time(10, 0))
        equipment.min_duration_minutes = 60
        equipment.save(update_fields=["min_duration_minutes"])
        locals_ = [
            (timezone.localtime(s).hour, timezone.localtime(s).minute) for s in equipment.free_starts_for_day(_day())
        ]
        assert locals_ == [(9, 0)]

    def it_handles_multiple_windows_in_one_day():
        equipment = _open_tool(start=time(9, 0), end=time(10, 0))
        day = _day()
        EquipmentHoursFactory(equipment=equipment, weekday=day.weekday(), start_time=time(14, 0), end_time=time(15, 0))
        locals_ = [
            (timezone.localtime(s).hour, timezone.localtime(s).minute) for s in equipment.free_starts_for_day(day)
        ]
        assert locals_ == [(9, 0), (9, 30), (14, 0), (14, 30)]

    def it_ignores_inactive_rules():
        equipment = EquipmentFactory()
        day = _day()
        EquipmentHoursFactory(equipment=equipment, weekday=day.weekday(), is_active=False)
        assert equipment.free_starts_for_day(day) == []

    def it_returns_nothing_for_past_days_and_beyond_the_horizon():
        equipment = _open_tool()
        yesterday = timezone.localdate() - timedelta(days=1)
        beyond = timezone.localdate() + timedelta(days=equipment.max_advance_days + 1)
        assert equipment.free_starts_for_day(yesterday) == []
        assert equipment.free_starts_for_day(beyond) == []

    def it_offers_late_evening_starts_from_a_full_day_window():
        # Hours can now run to 23:30 — a 23:00 start fits the 30 minute minimum.
        equipment = _open_tool(start=time(6, 0), end=time(23, 30))
        locals_ = [
            (timezone.localtime(s).hour, timezone.localtime(s).minute) for s in equipment.free_starts_for_day(_day())
        ]
        assert (23, 0) in locals_
        assert (23, 30) not in locals_

    def it_books_a_2300_start():
        equipment = _open_tool(start=time(6, 0), end=time(23, 30))
        member = _linked_member("res_latenight")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 23), 30)
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_aligns_every_start_to_the_half_hour_grid():
        equipment = _open_tool(start=time(9, 0), end=time(16, 0))
        for start in equipment.free_starts_for_day(_day()):
            assert timezone.localtime(start).minute in (0, 30)


def describe_retired_equipment_fails_closed():
    def it_blocks_everyone_at_the_engine_level():
        equipment = EquipmentFactory(is_active=False)
        member = MemberFactory()
        assert equipment.booking_blockers(member) == ["This equipment is retired and not taking reservations."]
        # Even a manager cannot book retired gear — reserve() reads the same blockers.
        admin = _linked_member("retired_admin")
        admin.fog_role = Member.FogRole.ADMIN
        admin.save(update_fields=["fog_role"])
        EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday())
        with pytest.raises(EquipmentError, match="retired"):
            equipment_service.reserve(equipment, admin, _at(_day(), 10), 60)


def describe_equipment_hours_grid_guard():
    def it_rejects_off_grid_times_in_full_clean():
        from django.core.exceptions import ValidationError

        rule = EquipmentHoursFactory.build(equipment=EquipmentFactory(), start_time=time(9, 15), end_time=time(11, 15))
        with pytest.raises(ValidationError, match="half hour marks"):
            rule.full_clean()

    def it_accepts_grid_times_in_full_clean():
        rule = EquipmentHoursFactory.build(equipment=EquipmentFactory(), start_time=time(9, 30), end_time=time(11, 0))
        rule.full_clean()  # must not raise

    def it_snaps_starts_from_a_legacy_off_grid_window():
        # A pre-guard 9:15 to 11:15 row degrades to offering only starts the engine
        # accepts: 9:30, 10:00, 10:30 (11:00 leaves no room for the 30 minute minimum).
        equipment = EquipmentFactory()
        day = _day()
        EquipmentHoursFactory(equipment=equipment, weekday=day.weekday(), start_time=time(9, 15), end_time=time(11, 15))
        locals_ = [
            (timezone.localtime(s).hour, timezone.localtime(s).minute) for s in equipment.free_starts_for_day(day)
        ]
        assert locals_ == [(9, 30), (10, 0), (10, 30)]


def describe_window_and_span_merging():
    def it_merges_overlapping_hour_windows():
        equipment = _open_tool(start=time(9, 0), end=time(12, 0))
        day = _day()
        EquipmentHoursFactory(equipment=equipment, weekday=day.weekday(), start_time=time(11, 0), end_time=time(14, 0))
        assert equipment.open_intervals_for_day(day) == [(_at(day, 9), _at(day, 14))]

    def it_merges_overlapping_busy_spans_from_drifted_data():
        # reserve() can never create these, but a merged view of drifted rows keeps
        # the free math honest.
        equipment = _open_tool(start=time(9, 0), end=time(17, 0))
        day = _day()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 12))
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 11), ends_at=_at(day, 13))
        assert equipment.free_intervals_for_day(day) == [(_at(day, 9), _at(day, 10)), (_at(day, 13), _at(day, 17))]


def describe_availability_line():
    def it_reports_busy_without_a_prefetch():
        equipment = EquipmentFactory()
        EquipmentHoursFactory(
            equipment=equipment,
            weekday=timezone.localtime().weekday(),
            start_time=time(0, 0),
            end_time=time(23, 59),
        )
        EquipmentReservationFactory(
            equipment=equipment,
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=30),
        )
        tone, text = equipment.availability_line()
        assert tone == "busy"
        assert text.startswith("Reserved until ")

    def it_reports_open_and_after_hours_states():
        equipment = EquipmentFactory()
        local = timezone.localtime()
        EquipmentHoursFactory(
            equipment=equipment, weekday=local.weekday(), start_time=time(0, 0), end_time=time(23, 59)
        )
        assert equipment.availability_line() == ("free", "Available now")
        # Move the only window to another weekday: has hours, but not open right now.
        equipment.hours_rules.update(weekday=(local.weekday() + 3) % 7)
        equipment = Equipment.objects.get(pk=equipment.pk)
        assert equipment.availability_line() == ("muted", "Not open right now")


def describe_model_strings():
    def it_stringifies_hours_and_reservations():
        equipment = EquipmentFactory(name="CNC Router")
        rule = EquipmentHoursFactory(equipment=equipment, start_time=time(9, 0), end_time=time(17, 0))
        assert str(rule) == "CNC Router: Tuesday 09:00-17:00"
        reservation = EquipmentReservationFactory(equipment=equipment)
        assert "CNC Router:" in str(reservation)


def describe_durations_for():
    def it_steps_from_the_minimum_to_the_cap():
        equipment = _open_tool(start=time(9, 0), end=time(17, 0))
        durations = equipment.durations_for(_at(_day(), 9))
        assert durations[0] == 30
        assert durations[-1] == 240
        assert all(minutes % 30 == 0 for minutes in durations)

    def it_shrinks_before_the_next_reservation():
        equipment = _open_tool(start=time(9, 0), end=time(17, 0))
        day = _day()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 11))
        assert equipment.durations_for(_at(day, 9)) == [30, 60]

    def it_shrinks_before_closing_time():
        equipment = _open_tool(start=time(9, 0), end=time(10, 0))
        assert equipment.durations_for(_at(_day(), 9, 30)) == [30]

    def it_offers_nothing_for_a_start_outside_free_time():
        equipment = _open_tool(start=time(9, 0), end=time(12, 0))
        day = _day()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 11))
        assert equipment.durations_for(_at(day, 10)) == []
        assert equipment.durations_for(_at(day, 7)) == []


def describe_reserve():
    def it_creates_a_confirmed_reservation_and_notifies():
        equipment = _open_tool()
        member = _linked_member("res_happy")
        mail.outbox.clear()
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 90, purpose="Longarm quilting")
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        assert reservation.ends_at == reservation.starts_at + timedelta(minutes=90)
        assert reservation.purpose == "Longarm quilting"
        # The member's forced confirmation email carries the calendar invite.
        confirmation = next(m for m in mail.outbox if "Reserved" in m.subject)
        assert equipment.name in confirmation.subject
        names = [attachment[0] for attachment in confirmation.attachments]
        assert "reservation.ics" in names
        # The member's in-app bell row landed too.
        assert Notification.objects.filter(user=member.user, trigger="equipment.reservation_confirmed").exists()

    def it_pings_the_equipment_managers_in_app():
        equipment = _open_tool()
        manager = _linked_member("res_mgr")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        member = _linked_member("res_mgr_booker")
        equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        row = Notification.objects.get(user=manager.user, trigger="equipment.reservation_made")
        assert member.display_name in row.body

    def it_rejects_a_blocked_member_first():
        orientation_type = OrientationTypeFactory(name="Lathe")
        equipment = _open_tool(required_orientation=orientation_type)
        member = _linked_member("res_blocked")
        with pytest.raises(EquipmentError, match="Lathe orientation"):
            equipment_service.reserve(equipment, member, _at(_day(), 10), 60)

    def it_rejects_when_closed_but_leaves_existing_reservations_standing():
        equipment = _open_tool()
        member = _linked_member("res_closed")
        existing = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        equipment.is_closed = True
        equipment.closed_message = "Down for maintenance. Back Tuesday."
        equipment.save(update_fields=["is_closed", "closed_message"])
        with pytest.raises(EquipmentError, match="Down for maintenance"):
            equipment_service.reserve(equipment, member, _at(_day(), 12), 60)
        existing.refresh_from_db()
        assert existing.status == EquipmentReservation.Status.CONFIRMED

    def it_rejects_a_past_start():
        equipment = _open_tool(day=timezone.localdate())
        member = _linked_member("res_past")
        with pytest.raises(EquipmentError, match="already past"):
            equipment_service.reserve(equipment, member, timezone.now() - timedelta(hours=1), 60)

    def it_rejects_a_start_beyond_the_horizon():
        far_day = timezone.localdate() + timedelta(days=40)
        equipment = _open_tool(day=far_day)
        member = _linked_member("res_far")
        with pytest.raises(EquipmentError, match="30 days ahead"):
            equipment_service.reserve(equipment, member, _at(far_day, 10), 60)

    def it_rejects_an_off_grid_start():
        equipment = _open_tool()
        member = _linked_member("res_grid")
        with pytest.raises(EquipmentError, match="half hour marks"):
            equipment_service.reserve(equipment, member, _at(_day(), 10, 15), 60)

    def it_rejects_off_grid_and_out_of_bounds_durations():
        equipment = _open_tool()
        member = _linked_member("res_dur")
        with pytest.raises(EquipmentError, match="half hour steps"):
            equipment_service.reserve(equipment, member, _at(_day(), 10), 45)
        with pytest.raises(EquipmentError, match="at least 30"):
            equipment_service.reserve(equipment, member, _at(_day(), 10), 0)
        with pytest.raises(EquipmentError, match="at most 240"):
            equipment_service.reserve(equipment, member, _at(_day(), 10), 270)

    def it_rejects_a_time_outside_open_hours():
        equipment = _open_tool(start=time(9, 0), end=time(12, 0))
        member = _linked_member("res_hours")
        with pytest.raises(EquipmentError, match="outside this equipment's open hours"):
            equipment_service.reserve(equipment, member, _at(_day(), 13), 60)
        # Fits the start but runs past closing — also out of hours.
        with pytest.raises(EquipmentError, match="outside this equipment's open hours"):
            equipment_service.reserve(equipment, member, _at(_day(), 11, 30), 60)

    def it_enforces_the_per_member_cap():
        equipment = _open_tool()
        member = _linked_member("res_cap")
        equipment_service.reserve(equipment, member, _at(_day(), 9), 30)
        equipment_service.reserve(equipment, member, _at(_day(), 10), 30)
        with pytest.raises(EquipmentError, match="2 upcoming reservations"):
            equipment_service.reserve(equipment, member, _at(_day(), 11), 30)

    def it_loses_the_race_with_friendly_copy():
        # The sequential shape the select_for_update lock serializes: the second
        # competing booking re-validates under the lock and hits the overlap guard.
        equipment = _open_tool()
        first = _linked_member("res_race1")
        second = _linked_member("res_race2")
        winner = equipment_service.reserve(equipment, first, _at(_day(), 10), 120)
        assert winner.status == EquipmentReservation.Status.CONFIRMED
        with pytest.raises(EquipmentError, match="just taken"):
            equipment_service.reserve(equipment, second, _at(_day(), 11), 60)
        assert equipment.reservations.confirmed().count() == 1

    def it_allows_an_adjacent_booking():
        equipment = _open_tool()
        first = _linked_member("res_adj1")
        second = _linked_member("res_adj2")
        equipment_service.reserve(equipment, first, _at(_day(), 10), 60)
        adjacent = equipment_service.reserve(equipment, second, _at(_day(), 11), 60)
        assert adjacent.status == EquipmentReservation.Status.CONFIRMED


def describe_cancel():
    def it_lets_the_member_cancel_a_future_reservation_quietly():
        equipment = _open_tool()
        member = _linked_member("cx_self")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        Notification.objects.all().delete()
        mail.outbox.clear()
        reservation.cancel(member)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.cancelled_by == member
        assert reservation.cancelled_at is not None
        assert reservation.is_cancelled_by_manager is False
        # Self cancel notifies nobody.
        assert not Notification.objects.exists()
        assert mail.outbox == []

    def it_blocks_a_self_cancel_once_started():
        member = MemberFactory()
        reservation = EquipmentReservationFactory(
            member=member,
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=30),
        )
        with pytest.raises(EquipmentError, match="already started"):
            reservation.cancel(member)

    def it_requires_a_manager_reason():
        equipment = _open_tool()
        member = _linked_member("cx_reason")
        manager = _linked_member("cx_reason_mgr")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        with pytest.raises(ValueError, match="needs a reason"):
            reservation.cancel(manager, reason="   ")

    def it_lets_a_manager_cancel_with_the_reason_reaching_the_member():
        equipment = _open_tool()
        member = _linked_member("cx_mgr")
        manager = _linked_member("cx_mgr_actor")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        mail.outbox.clear()
        reservation.cancel(manager, reason="The router is down for repair.")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.is_cancelled_by_manager is True
        assert reservation.cancelled_reason == "The router is down for repair."
        cancel_email = next(m for m in mail.outbox if "cancelled" in m.subject)
        assert "The router is down for repair." in cancel_email.body
        row = Notification.objects.get(user=member.user, trigger="equipment.reservation_cancelled_by_manager")
        assert "The router is down for repair." in row.body

    def it_lets_a_manager_cancel_an_in_progress_reservation():
        equipment = EquipmentFactory()
        manager = _linked_member("cx_now_mgr")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        reservation = EquipmentReservationFactory(
            equipment=equipment,
            member=_linked_member("cx_now_member"),
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=30),
        )
        reservation.cancel(manager, reason="Emergency maintenance.")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED

    def it_blocks_a_manager_cancel_after_the_end():
        equipment = EquipmentFactory()
        manager = MemberFactory(fog_role=Member.FogRole.ADMIN)
        reservation = EquipmentReservationFactory(
            equipment=equipment,
            starts_at=timezone.now() - timedelta(hours=2),
            ends_at=timezone.now() - timedelta(hours=1),
        )
        with pytest.raises(EquipmentError, match="already ended"):
            reservation.cancel(manager, reason="Too late.")

    def it_blocks_a_non_manager_stranger():
        reservation = EquipmentReservationFactory()
        stranger = MemberFactory()
        with pytest.raises(EquipmentError, match="equipment manager"):
            reservation.cancel(stranger, reason="Nope.")

    def it_lets_a_manager_cancel_their_own_row_as_manager_without_notifying_themselves():
        equipment = _open_tool()
        manager = _linked_member("cx_own_mgr")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        reservation = EquipmentReservationFactory(
            equipment=equipment,
            member=manager,
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=30),
        )
        Notification.objects.all().delete()
        mail.outbox.clear()
        # In progress — the self path would refuse; the manager path allows it.
        reservation.cancel(manager, reason="Freeing my own slot.", as_manager=True)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.cancelled_reason == "Freeing my own slot."
        # The member IS the actor — nobody to notify.
        assert not Notification.objects.exists()
        assert mail.outbox == []

    def it_requires_a_reason_even_for_an_own_row_manager_cancel():
        equipment = _open_tool()
        manager = _linked_member("cx_own_mgr_blank")
        EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
        reservation = EquipmentReservationFactory(
            equipment=equipment, member=manager, starts_at=_at(_day(), 10), ends_at=_at(_day(), 11)
        )
        with pytest.raises(ValueError, match="needs a reason"):
            reservation.cancel(manager, as_manager=True)

    def it_blocks_a_double_cancel():
        member = MemberFactory()
        reservation = EquipmentReservationFactory(member=member)
        reservation.cancel(member)
        with pytest.raises(EquipmentError, match="already cancelled"):
            reservation.cancel(member)


def describe_equipment_events():
    def it_registers_the_three_events_with_the_specified_channel_defaults():
        confirmed = get_event("equipment.reservation_confirmed")
        assert confirmed.recipient is Recipients.SINGLE_USER
        assert confirmed.channel(Channel.EMAIL).default is ChannelDefault.FORCED
        assert confirmed.channel(Channel.PUSH).default is ChannelDefault.ON
        cancelled = get_event("equipment.reservation_cancelled_by_manager")
        assert cancelled.recipient is Recipients.SINGLE_USER
        assert cancelled.channel(Channel.EMAIL).default is ChannelDefault.FORCED
        made = get_event("equipment.reservation_made")
        assert made.recipient is Recipients.EQUIPMENT_MANAGERS
        assert made.channel(Channel.EMAIL).default is ChannelDefault.OFF
        # The managers' ping ALSO broadcasts to the #reservations Discord channel;
        # the two personal events stay Discord-free (a booking receipt is not news).
        assert made.channel(Channel.DISCORD).default is ChannelDefault.ON
        for event in (confirmed, cancelled):
            assert not event.has_channel(Channel.DISCORD)
        for event in (confirmed, cancelled, made):
            assert event.category == "Spaces & Equipment"

    def it_resolves_equipment_managers_across_all_three_tiers_deduped():
        lead = _linked_member("ev_lead")
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=_linked_member("ev_staff"))
        equipment = EquipmentFactory(guild=guild)
        row_manager = _linked_member("ev_row")
        EquipmentStaffMembershipFactory(equipment=equipment, member=row_manager)
        holder = _linked_member("ev_cap")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        # The row manager ALSO holds the capability — must resolve once.
        row_manager.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        recipients = resolvers.resolve(Recipients.EQUIPMENT_MANAGERS, {"equipment": equipment})
        usernames = sorted(user.username for user, _reason in recipients)
        assert usernames == ["ev_cap", "ev_lead", "ev_row", "ev_staff"]

    def it_supplies_every_documented_placeholder_in_each_emit_context():
        # The context each emit builds must cover every placeholder any channel's copy
        # uses, or a member's email ships a literal [missing: …] hole.
        equipment = _open_tool(name="CNC Router")
        member = _linked_member("ev_ctx")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        base = equipment_service._placeholder_context(reservation)
        contexts = {
            "equipment.reservation_confirmed": {"user": member.user, **base},
            "equipment.reservation_made": {"equipment": equipment, **base},
            "equipment.reservation_cancelled_by_manager": {"user": member.user, "cancel_reason": "x", **base},
        }
        for event_key, context in contexts.items():
            for name in placeholders_for(event_key):
                assert name in context, f"{event_key} copy documents {{{{ {name} }}}} but the emit omits it"
            # And render every channel's default copy against that real context — no
            # unresolved {{ … }} may survive into a member's message.
            for channel in COPY_CHANNELS:
                copy = default_copy_for(event_key, channel)
                for fragment in (copy.subject, copy.body_text, copy.body_html):
                    rendered = render_text(fragment, context)
                    assert "[missing:" not in rendered, f"{event_key}/{channel.value} renders a hole: {rendered}"

    def it_renders_the_confirmation_email_without_missing_markers():
        equipment = _open_tool(name="CNC Router")
        member = _linked_member("ev_render")
        mail.outbox.clear()
        equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        confirmation = next(m for m in mail.outbox if "Reserved" in m.subject)
        assert "[missing:" not in confirmation.body
        assert "[missing:" not in confirmation.subject

    def describe_reservations_discord_post():
        _WEBHOOK = "https://discord.com/api/webhooks/900/reservations"

        def _configure_webhook(url: str = _WEBHOOK) -> None:
            from core.models import SiteConfiguration

            config = SiteConfiguration.load()
            config.discord_reservations_webhook_url = url
            config.save()

        @respx.mock
        def it_posts_a_greeting_free_embed_to_the_reservations_webhook():
            _configure_webhook()
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            equipment = _open_tool(name="CNC Router")
            member = _linked_member("disc_post")
            equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
            assert route.call_count == 1
            payload = json.loads(route.calls[0].request.content)
            embed = payload["embeds"][0]
            assert embed["title"] == "New reservation"
            assert member.display_name in embed["description"]
            assert "CNC Router" in embed["description"]
            assert "Hi " not in embed["description"]
            assert "[missing:" not in embed["description"]
            # Discord rejects relative embed URLs with a silent 400 — the url must be absolute.
            assert embed["url"].startswith("http")

        @respx.mock
        def it_is_a_silent_no_op_with_a_blank_webhook(settings):
            # A blank pin silences Discord for the event — it must NOT fall back to
            # the central notify webhook.
            settings.DISCORD_NOTIFY_WEBHOOK_URL = "https://discord.com/api/webhooks/901/central"
            central = respx.post(settings.DISCORD_NOTIFY_WEBHOOK_URL).mock(return_value=httpx.Response(204))
            equipment = _open_tool()
            equipment_service.reserve(equipment, _linked_member("disc_blank"), _at(_day(), 10), 60)
            assert not central.called

        @respx.mock
        def it_posts_only_to_reservations_for_guild_owned_equipment():
            # The owner asked for #reservations only — the guild's own channel must
            # never also hear it (the emit context carries no "guild" key).
            _configure_webhook()
            guild_webhook = "https://discord.com/api/webhooks/902/guildchannel"
            reservations_route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            guild_route = respx.post(guild_webhook).mock(return_value=httpx.Response(204))
            guild = GuildFactory(discord_webhook_url=guild_webhook, discord_post_enabled=True)
            equipment = _open_tool(guild=guild)
            equipment_service.reserve(equipment, _linked_member("disc_guild"), _at(_day(), 10), 60)
            assert reservations_route.call_count == 1
            assert not guild_route.called

        @respx.mock
        def it_dedupes_a_re_emit_of_the_same_reservation():
            _configure_webhook()
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
            equipment = _open_tool()
            reservation = equipment_service.reserve(equipment, _linked_member("disc_dedupe"), _at(_day(), 10), 60)
            assert route.call_count == 1
            equipment_service._notify_managers(reservation)  # a scheduler-style re-emit
            assert route.call_count == 1

    def describe_build_ics():
        def it_builds_a_confirmed_invite():
            equipment = EquipmentFactory(name="CNC Router", location_note="Back corner of the wood shop.")
            reservation = EquipmentReservationFactory(equipment=equipment)
            payload = equipment_service.build_ics(reservation, method="REQUEST", status="CONFIRMED").decode()
            assert "BEGIN:VEVENT" in payload
            assert "METHOD:REQUEST" in payload
            assert "STATUS:CONFIRMED" in payload
            assert "CNC Router" in payload
            assert "Back corner of the wood shop." in payload

        def it_can_retract_with_cancel():
            payload = equipment_service.build_ics(
                EquipmentReservationFactory(), method="CANCEL", status="CANCELLED"
            ).decode()
            assert "METHOD:CANCEL" in payload
            assert "STATUS:CANCELLED" in payload


# ── Orientations and reservations stay out of each other's way (equipment-orientation-hours PR 2) ──


def _tool_slot(equipment: Equipment, hour: int, *, length: int = 60, day=None):
    """An equipment-owned orientation slot on ``day`` (default +2) from ``hour`` for ``length`` minutes."""
    orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
    start = _at(day if day is not None else _day(), hour)
    return OrientationSlotFactory(
        equipment_owned=True,
        orientation_type=orientation_type,
        starts_at=start,
        ends_at=start + timedelta(minutes=length),
        seats=2,
    )


def _booked(slot, **overrides):
    OrientationBookingFactory(slot=slot, **overrides)
    return slot


def describe_busy_spans_for_day():
    def it_is_empty_when_every_slot_is_open_and_unbooked():
        equipment = _open_tool()
        _tool_slot(equipment, 10)
        assert equipment.busy_spans_for_day(_day()) == []

    def it_counts_a_requested_confirmed_or_held_seat_as_busy():
        equipment = _open_tool()
        _booked(_tool_slot(equipment, 10))
        _booked(_tool_slot(equipment, 12), status=OrientationBooking.Status.CONFIRMED)
        _booked(_tool_slot(equipment, 14), status=OrientationBooking.Status.PENDING_PAYMENT)
        assert equipment.busy_spans_for_day(_day()) == [
            (_at(_day(), 10), _at(_day(), 11)),
            (_at(_day(), 12), _at(_day(), 13)),
            (_at(_day(), 14), _at(_day(), 15)),
        ]

    def it_frees_the_span_when_the_slot_is_cancelled():
        equipment = _open_tool()
        slot = _booked(_tool_slot(equipment, 10), status=OrientationBooking.Status.CONFIRMED)
        slot.mark_cancelled(reason="Machine down")
        assert equipment.busy_spans_for_day(_day()) == []

    def it_frees_the_span_when_the_booking_is_resolved():
        equipment = _open_tool()
        _booked(_tool_slot(equipment, 10), status=OrientationBooking.Status.DECLINED)
        _booked(_tool_slot(equipment, 12), status=OrientationBooking.Status.CANCELLED)
        assert equipment.busy_spans_for_day(_day()) == []

    def it_merges_reservations_with_booked_orientations():
        equipment = _open_tool()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(_day(), 10), ends_at=_at(_day(), 12))
        _booked(_tool_slot(equipment, 11, length=120))
        assert equipment.busy_spans_for_day(_day()) == [(_at(_day(), 10), _at(_day(), 13))]

    def it_ignores_another_tools_booked_slot():
        equipment = _open_tool()
        _booked(_tool_slot(_open_tool(), 10))
        assert equipment.busy_spans_for_day(_day()) == []


def describe_free_time_around_a_booked_orientation():
    def it_omits_starts_that_would_run_into_the_orientation():
        equipment = _open_tool()
        _booked(_tool_slot(equipment, 11))
        starts = equipment.free_starts_for_day(_day())
        assert _at(_day(), 10, 30) in starts  # 10:30 + the 30 minute minimum ends as the orientation begins
        assert _at(_day(), 11) not in starts
        assert _at(_day(), 11, 30) not in starts
        assert _at(_day(), 12) in starts

    def it_caps_durations_at_the_orientation():
        equipment = _open_tool()
        _booked(_tool_slot(equipment, 11))
        assert equipment.durations_for(_at(_day(), 10)) == [30, 60]

    def it_leaves_every_start_when_the_slot_is_unbooked():
        equipment = _open_tool()
        _tool_slot(equipment, 11)
        assert _at(_day(), 11) in equipment.free_starts_for_day(_day())


def describe_ensure_reservable_orientation_guard():
    def it_refuses_a_crafted_time_over_a_booked_orientation():
        # The UI never offers this start; a stale or crafted POST still hits the guard under the lock.
        equipment = _open_tool()
        _booked(_tool_slot(equipment, 11))
        with pytest.raises(EquipmentError, match="overlaps a booked orientation"):
            equipment_service.reserve(equipment, _linked_member("res_over_orient"), _at(_day(), 11), 60)
        assert not equipment.reservations.exists()

    def it_allows_a_touching_time():
        equipment = _open_tool()
        _booked(_tool_slot(equipment, 11))
        reservation = equipment_service.reserve(equipment, _linked_member("res_touch_orient"), _at(_day(), 12), 60)
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_ignores_an_open_unbooked_slot():
        equipment = _open_tool()
        _tool_slot(equipment, 11)
        reservation = equipment_service.reserve(equipment, _linked_member("res_open_orient"), _at(_day(), 11), 60)
        assert reservation.status == EquipmentReservation.Status.CONFIRMED


def describe_reservation_vs_orientation_race():
    """The sequential shape the shared Equipment row lock serializes: exactly one winner per span."""

    def it_lets_the_reservation_win_when_it_lands_first():
        equipment = _open_tool()
        slot = _tool_slot(equipment, 10)
        winner = equipment_service.reserve(equipment, _linked_member("race_res_first"), _at(_day(), 10), 60)
        with pytest.raises(OrientationError, match="not available to book"):
            orientations.request_orientation(slot, _linked_member("race_orient_second"))
        assert winner.status == EquipmentReservation.Status.CONFIRMED
        assert not slot.bookings.exists()

    def it_lets_the_orientation_win_when_it_lands_first():
        equipment = _open_tool()
        slot = _tool_slot(equipment, 10)
        booking = orientations.request_orientation(slot, _linked_member("race_orient_first"))
        with pytest.raises(EquipmentError, match="overlaps a booked orientation"):
            equipment_service.reserve(equipment, _linked_member("race_res_second"), _at(_day(), 10), 60)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert not equipment.reservations.exists()

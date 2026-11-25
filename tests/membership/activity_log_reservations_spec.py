"""Specs for equipment reservations on the portal activity log (#1330, AC 3 and 4).

Each real happening writes one Activity Feed row, which posts one line: requested, confirmed
(instant, approved or once paid), declined, cancelled by the member or a manager, and
"Reservation happened" once a confirmed reservation's end passes. A reservation awaiting
payment writes nothing until the money lands.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from core.models import SiteActivity, SiteConfiguration
from membership import equipment as equipment_service
from membership.models import Equipment, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_WEBHOOK = "https://discord.com/api/webhooks/1330/activity"
Kind = SiteActivity.Kind
Status = EquipmentReservation.Status


def _day(offset: int = 2):
    return timezone.localdate() + timedelta(days=offset)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _tool(**kwargs) -> Equipment:
    equipment = EquipmentFactory(name="Thimble Lathe", **kwargs)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _member(username: str, name: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.preferred_name = name
    member.save(update_fields=["status", "preferred_name"])
    return member


def _manager(equipment: Equipment) -> Member:
    manager = _member("mgr", "Moss")
    EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
    return manager


def _rows(reservation: EquipmentReservation) -> list[SiteActivity]:
    return [row for row in SiteActivity.objects.order_by("pk") if row.target == reservation]


def _turn_on() -> None:
    config = SiteConfiguration.load()
    config.discord_activity_webhook_url = _WEBHOOK
    config.save()


def describe_making_a_reservation():
    def it_logs_an_instant_booking_as_confirmed_by_the_member():
        member = _member("robin", "Robin")
        reservation = equipment_service.reserve(_tool(), member, _at(_day(), 10), 60)
        [row] = _rows(reservation)
        assert row.kind == Kind.RESERVATION_CONFIRMED
        assert row.actor == member.user
        assert row.line.headline == f"Reservation confirmed: Robin for Thimble Lathe, {reservation.request_span}"

    def it_logs_a_booking_that_needs_approval_as_requested():
        reservation = equipment_service.reserve(
            _tool(requires_approval=True), _member("rue", "Rue"), _at(_day(), 10), 60
        )
        assert [row.kind for row in _rows(reservation)] == [Kind.RESERVATION_REQUESTED]

    def it_logs_nothing_while_a_paid_booking_awaits_payment_then_confirmed_once_paid():
        equipment = _tool(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500)
        hold = EquipmentReservationFactory(
            equipment=equipment,
            member=_member("payer", "Pax"),
            starts_at=_at(_day(), 10),
            ends_at=_at(_day(), 11),
            status=Status.PENDING_PAYMENT,
            stripe_session_id="cs_1",
        )
        assert _rows(hold) == []
        equipment_service.finalize_paid_reservation(hold, payment_intent="pi_1", amount_total=2500)
        equipment_service.finalize_paid_reservation(hold, payment_intent="pi_1", amount_total=2500)
        assert [row.kind for row in _rows(hold)] == [Kind.RESERVATION_CONFIRMED]

    def it_logs_a_paid_request_as_requested_once_paid():
        equipment = _tool(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, requires_approval=True)
        hold = EquipmentReservationFactory(
            equipment=equipment,
            member=_member("payer2", "Pip"),
            starts_at=_at(_day(), 10),
            ends_at=_at(_day(), 11),
            status=Status.PENDING_PAYMENT,
        )
        equipment_service.finalize_paid_reservation(hold, payment_intent="pi_2", amount_total=2500)
        assert [row.kind for row in _rows(hold)] == [Kind.RESERVATION_REQUESTED]


def describe_deciding_a_request():
    def it_logs_an_approval_as_confirmed_by_the_manager():
        equipment = _tool(requires_approval=True)
        reservation = equipment_service.reserve(equipment, _member("ana", "Ana"), _at(_day(), 10), 60)
        manager = _manager(equipment)
        reservation.approve(manager)
        row = _rows(reservation)[-1]
        assert row.kind == Kind.RESERVATION_CONFIRMED
        assert row.actor == manager.user

    def it_logs_a_decline_with_its_reason_as_the_note():
        equipment = _tool(requires_approval=True)
        reservation = equipment_service.reserve(equipment, _member("bo", "Bo"), _at(_day(), 10), 60)
        manager = _manager(equipment)
        reservation.decline(manager, reason="Out for repair.")
        row = _rows(reservation)[-1]
        assert row.kind == Kind.RESERVATION_DECLINED
        assert row.actor == manager.user
        assert row.line.note == "Out for repair."


def describe_cancelling():
    def it_logs_the_members_own_cancel():
        member = _member("cy", "Cy")
        reservation = equipment_service.reserve(_tool(), member, _at(_day(), 10), 60)
        reservation.cancel(member)
        row = _rows(reservation)[-1]
        assert row.kind == Kind.RESERVATION_CANCELLED
        assert row.actor == member.user
        assert row.line.note == ""

    def it_logs_a_managers_cancel_with_its_reason():
        equipment = _tool()
        reservation = equipment_service.reserve(equipment, _member("di", "Di"), _at(_day(), 10), 60)
        manager = _manager(equipment)
        reservation.cancel(manager, reason="Tool is down.")
        row = _rows(reservation)[-1]
        assert (row.kind, row.actor, row.line.note) == (Kind.RESERVATION_CANCELLED, manager.user, "Tool is down.")

    def it_logs_a_manager_cancelling_their_own_row_as_a_manager():
        equipment = _tool()
        manager = _manager(equipment)
        reservation = equipment_service.reserve(equipment, manager, _at(_day(), 10), 60)
        reservation.cancel(manager, reason="Changed plans.", as_manager=True)
        assert [row.kind for row in _rows(reservation)] == [Kind.RESERVATION_CONFIRMED, Kind.RESERVATION_CANCELLED]


def describe_the_link():
    def it_opens_the_equipments_manage_reservations_tab():
        reservation = EquipmentReservationFactory()
        path = reverse("hub_equipment_manage", args=[reservation.equipment.slug])
        assert reservation.activity_url == f"{path}?tab=reservations"


def describe_log_finished_reservations():
    def _ended(minutes_ago: int, *, logged: bool = True, **kwargs) -> EquipmentReservation:
        """A reservation that ended ``minutes_ago``; ``logged`` gives it the confirmed row the log writes."""
        ends = timezone.now() - timedelta(minutes=minutes_ago)
        reservation = EquipmentReservationFactory(starts_at=ends - timedelta(hours=1), ends_at=ends, **kwargs)
        if logged:
            reservation.log_activity(Kind.RESERVATION_CONFIRMED, actor=reservation.member.user)
        return reservation

    def it_logs_a_confirmed_reservation_once_its_end_passes_and_never_twice():
        reservation = _ended(10)
        assert equipment_service.log_finished_reservations() == 1
        assert equipment_service.log_finished_reservations() == 0
        row = _rows(reservation)[-1]
        assert row.kind == Kind.RESERVATION_HAPPENED
        assert row.actor is None
        assert row.by == "System"

    def it_catches_up_on_a_reservation_that_ended_days_ago_during_an_outage():
        reservation = _ended(60 * 24 * 3)
        assert equipment_service.log_finished_reservations() == 1
        assert _rows(reservation)[-1].kind == Kind.RESERVATION_HAPPENED

    def it_never_backfills_a_reservation_confirmed_before_the_log_shipped():
        before = _ended(10, logged=False)
        assert equipment_service.log_finished_reservations() == 0
        assert _rows(before) == []

    def it_skips_what_has_not_ended_or_never_happened():
        upcoming = EquipmentReservationFactory(
            starts_at=timezone.now() + timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=2)
        )
        upcoming.log_activity(Kind.RESERVATION_CONFIRMED, actor=None)
        cancelled = _ended(10, status=Status.CANCELLED)
        declined = _ended(10, status=Status.DECLINED)
        block = _ended(10, kind=EquipmentReservation.Kind.BLOCK)
        assert equipment_service.log_finished_reservations() == 0
        for reservation in (upcoming, cancelled, declined, block):
            assert Kind.RESERVATION_HAPPENED not in [row.kind for row in _rows(reservation)]

    def it_lets_the_database_refuse_a_second_happened_row_from_an_overlapping_run():
        reservation = _ended(10)
        assert equipment_service._log_happened_once(reservation) is True
        assert equipment_service._log_happened_once(reservation) is False
        assert SiteActivity.objects.filter(kind=Kind.RESERVATION_HAPPENED).count() == 1
        with pytest.raises(IntegrityError), transaction.atomic():
            reservation.log_activity(Kind.RESERVATION_HAPPENED, actor=None)

    @respx.mock
    def it_runs_from_the_tick_and_posts_one_line(django_capture_on_commit_callbacks, settings):
        settings.MEMBER_BASE_URL = "https://members.example"
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        reservation = _ended(10)
        with django_capture_on_commit_callbacks(execute=True):
            call_command("log_finished_reservations")
        with django_capture_on_commit_callbacks(execute=True):
            call_command("log_finished_reservations")
        contents = [json.loads(call.request.content)["content"] for call in route.calls]
        assert [content.split(":")[0] for content in contents] == ["Reservation happened"]
        content = contents[0]
        assert content.endswith(f" · System · <https://members.example{reservation.activity_url}>")


def describe_posting():
    @respx.mock
    def it_posts_each_happening_once(django_capture_on_commit_callbacks):
        _turn_on()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        equipment = _tool(requires_approval=True)
        member = _member("eve", "Eve")
        with django_capture_on_commit_callbacks(execute=True):
            reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        with django_capture_on_commit_callbacks(execute=True):
            reservation.approve(_manager(equipment))
        with django_capture_on_commit_callbacks(execute=True):
            reservation.cancel(member)
        contents = [json.loads(call.request.content)["content"] for call in route.calls]
        assert [content.split(":")[0] for content in contents] == [
            "Reservation requested",
            "Reservation confirmed",
            "Reservation cancelled",
        ]

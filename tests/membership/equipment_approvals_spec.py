"""BDD specs for equipment that needs a manager's approval (#748): the model and the service.

A request is made PENDING_APPROVAL and holds its time (overlaps, busy spans, the per member
cap, the upcoming list, the orientation overlap and the index's busy line all count it);
``approve`` and ``decline`` are conditional updates keyed on status, gated by
``can_manage_equipment``; a waiting request cancels without a fee. The three new events and
the approval line on the reused confirmation are checked against their copy and audience.
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

from core.events.copy import COPY_CHANNELS, default_copy_for, placeholders_for
from core.events.registry import Channel, ChannelDefault, Recipients, get_event
from core.events.rendering import render_text
from core.models import Notification, SiteConfiguration
from membership import equipment as equipment_service
from membership.models import (
    AdminCapability,
    Equipment,
    EquipmentError,
    EquipmentReservation,
    Member,
    OrientationSlot,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

WAITING = EquipmentReservation.Status.PENDING_APPROVAL


def _day(offset: int = 2):
    return timezone.localdate() + timedelta(days=offset)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _approval_tool(**kwargs) -> Equipment:
    """A tool that needs approval, open 9 to 5 on the day two days out."""
    equipment = EquipmentFactory(requires_approval=True, **kwargs)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _linked_member(username: str, name: str = "") -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="x")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name or f"{username.title()} Quillfeather"
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _with_manager(equipment: Equipment, username: str, name: str = "") -> Member:
    manager = _linked_member(username, name)
    EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
    return manager


def _waiting(
    equipment: Equipment | None = None, *, hours: float = 48, status: str = WAITING, **kwargs
) -> EquipmentReservation:
    starts = timezone.now() + timedelta(hours=hours)
    return EquipmentReservationFactory(
        equipment=equipment or EquipmentFactory(requires_approval=True),
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        status=status,
        **kwargs,
    )


def describe_holding_reads():
    def it_counts_a_waiting_request_as_overlapping_but_never_a_declined_one():
        equipment = EquipmentFactory()
        day = _day()
        held = EquipmentReservationFactory(
            equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 12), status=WAITING
        )
        EquipmentReservationFactory(
            equipment=equipment,
            starts_at=_at(day, 13),
            ends_at=_at(day, 14),
            status=EquipmentReservation.Status.DECLINED,
        )
        assert list(EquipmentReservation.objects.overlapping(equipment, _at(day, 9), _at(day, 15))) == [held]

    def it_lists_holding_rows_and_only_waiting_ones_as_awaiting_approval():
        equipment = EquipmentFactory()
        confirmed = EquipmentReservationFactory(equipment=equipment)
        waiting = _waiting(equipment)
        _waiting(equipment, hours=-3)  # a request whose time already passed
        EquipmentReservationFactory(equipment=equipment, status=EquipmentReservation.Status.CANCELLED)
        assert set(EquipmentReservation.objects.holding()) >= {confirmed, waiting}
        assert list(EquipmentReservation.objects.awaiting_approval()) == [waiting]

    def it_puts_a_waiting_request_on_the_upcoming_list():
        equipment = EquipmentFactory()
        waiting = _waiting(equipment)
        assert list(equipment.reservations.upcoming()) == [waiting]

    def it_leaves_a_waiting_requests_time_out_of_the_free_starts():
        equipment = _approval_tool()
        day = _day()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 11), status=WAITING)
        starts = {timezone.localtime(s).hour for s in equipment.free_starts_for_day(day)}
        assert 10 not in starts
        assert (_at(day, 10), _at(day, 11)) in equipment.busy_spans_for_day(day)

    def it_refuses_a_second_member_the_held_time():
        equipment = _approval_tool()
        first = _linked_member("hold_first")
        equipment_service.reserve(equipment, first, _at(_day(), 10), 60)
        with pytest.raises(EquipmentError, match="just taken"):
            equipment_service.reserve(equipment, _linked_member("hold_second"), _at(_day(), 10), 60)

    def it_counts_a_waiting_request_toward_the_members_cap():
        equipment = _approval_tool(max_active_reservations_per_member=1)
        member = _linked_member("hold_cap")
        equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        assert EquipmentReservation.objects.active_count_for(member, equipment) == 1
        with pytest.raises(EquipmentError, match="already have 1 upcoming reservation here"):
            equipment_service.reserve(equipment, member, _at(_day(), 13), 60)

    def it_holds_the_equipment_against_an_orientation_that_uses_it():
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(guild=None, equipment=equipment)
        day = _day()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(day, 10), ends_at=_at(day, 12), status=WAITING)
        clash = EquipmentReservation.objects.over_orientation(orientation_type, _at(day, 11), _at(day, 13))
        assert clash.exists()

    def it_never_reads_the_tool_as_in_use_for_an_undecided_request():
        # In use now is confirmed rows only: a request is not the member at the machine.
        now = timezone.now()
        span = {"starts_at": now - timedelta(minutes=30), "ends_at": now + timedelta(minutes=30)}
        waiting_tool = _approval_tool()
        EquipmentReservationFactory(equipment=waiting_tool, status=WAITING, **span)
        assert waiting_tool.availability_line()[0] != "busy"
        # The control: the same span confirmed reads in use.
        confirmed_tool = _approval_tool()
        EquipmentReservationFactory(equipment=confirmed_tool, **span)
        tone, text = confirmed_tool.availability_line()
        assert tone == "busy"
        assert text.startswith("Reserved until")
        assert not OrientationSlot.objects.exists()

    def it_refuses_a_block_over_a_waiting_request():
        equipment = _approval_tool()
        manager = _with_manager(equipment, "blk_mgr")
        day = _day()
        EquipmentReservationFactory(
            equipment=equipment,
            member=MemberFactory(full_legal_name="Juniper Wrenhallow"),
            starts_at=_at(day, 10),
            ends_at=_at(day, 11),
            status=WAITING,
        )
        with pytest.raises(EquipmentError, match="Overlaps Juniper W.'s reservation"):
            equipment.ensure_blockable(manager, _at(day, 10), _at(day, 12))


def describe_reserve_on_equipment_that_needs_approval():
    def it_makes_a_waiting_request_and_emails_the_member_and_the_managers():
        equipment = _approval_tool(name="Glimmerforge Lathe")
        manager = _with_manager(equipment, "req_mgr")
        member = _linked_member("req_member")
        mail.outbox.clear()
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60, purpose="Sign blanks")
        assert reservation.status == WAITING
        assert reservation.is_awaiting_approval
        requested = next(m for m in mail.outbox if m.to == [member.primary_email])
        assert requested.subject.startswith("Requested: Glimmerforge Lathe, ")
        assert "We hold the time for you and email you when they decide." in requested.body
        assert "See your reservation:" in requested.body
        assert not requested.attachments  # nothing is booked yet, so no calendar invite
        html = requested.alternatives[0][0]
        assert 'href="http' in html
        assert "See Your Reservation" in html
        needs = next(m for m in mail.outbox if m.to == [manager.primary_email])
        assert needs.subject.startswith("Needs approval: Glimmerforge Lathe, ")
        assert needs.subject.endswith(f"({member.display_name})")
        assert "Purpose: Sign blanks. The time is held until a manager decides." in needs.body
        assert "?tab=reservations" in needs.body
        assert "Approve or Decline" in needs.alternatives[0][0]
        for message in mail.outbox:
            assert "[missing:" not in message.body
            assert "[missing:" not in message.alternatives[0][0]

    def it_sends_neither_the_confirmation_nor_the_awareness_ping():
        equipment = _approval_tool()
        _with_manager(equipment, "req_quiet_mgr")
        member = _linked_member("req_quiet")
        mail.outbox.clear()
        equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        triggers = set(Notification.objects.values_list("trigger", flat=True))
        assert triggers == {"equipment.reservation_requested", "equipment.reservation_needs_approval"}
        assert not any(m.subject.startswith("Reserved:") for m in mail.outbox)

    def it_leaves_the_purpose_out_of_the_managers_email_when_none_was_given():
        equipment = _approval_tool()
        manager = _with_manager(equipment, "req_nopurpose_mgr")
        mail.outbox.clear()
        equipment_service.reserve(equipment, _linked_member("req_nopurpose"), _at(_day(), 10), 60)
        needs = next(m for m in mail.outbox if m.to == [manager.primary_email])
        assert "Purpose:" not in needs.body
        assert "\n\nThe time is held until a manager decides." in needs.body

    def it_asks_only_the_tools_own_managers_or_the_administrators_when_nobody_runs_it():
        run = _approval_tool()
        manager = _with_manager(run, "aud_mgr")
        holder = _linked_member("aud_holder")
        holder.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        equipment_service.reserve(run, _linked_member("aud_booker"), _at(_day(), 10), 60)
        asked = set(
            Notification.objects.filter(trigger="equipment.reservation_needs_approval").values_list("user", flat=True)
        )
        assert asked == {manager.user.pk}
        Notification.objects.all().delete()
        orphan = _approval_tool()
        equipment_service.reserve(orphan, _linked_member("aud_booker2"), _at(_day(), 10), 60)
        asked = set(
            Notification.objects.filter(trigger="equipment.reservation_needs_approval").values_list("user", flat=True)
        )
        assert asked == {holder.user.pk}

    def it_confirms_a_managers_own_booking_at_once():
        # A manager of the equipment never waits on their own approval (same test as the late fee exemption).
        equipment = _approval_tool()
        manager = _with_manager(equipment, "own_mgr")
        _with_manager(equipment, "own_other_mgr")
        mail.outbox.clear()
        reservation = equipment_service.reserve(equipment, manager, _at(_day(), 10), 60)
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        assert any(m.subject.startswith("Reserved:") for m in mail.outbox)
        triggers = set(Notification.objects.values_list("trigger", flat=True))
        assert "equipment.reservation_requested" not in triggers
        assert "equipment.reservation_needs_approval" not in triggers
        assert "equipment.reservation_made" in triggers

    def it_keeps_an_instant_booking_exactly_as_it_was():
        equipment = EquipmentFactory()
        EquipmentHoursFactory(
            equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0)
        )
        member = _linked_member("instant")
        mail.outbox.clear()
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        confirmation = next(m for m in mail.outbox if m.subject.startswith("Reserved:"))
        assert f"Hi {member.display_name},\n\nYour reservation is set.\n\n" in confirmation.body
        html = confirmation.alternatives[0][0]
        assert "Your reservation is set.</p>" in html
        assert "approved your reservation" not in html
        assert "approved" not in confirmation.body


def describe_approve():
    def it_confirms_the_request_and_sends_the_confirmation_with_the_invite_and_who_approved():
        equipment = _approval_tool(name="Glimmerforge Lathe")
        manager = _with_manager(equipment, "ap_mgr", "Sami Brindlewood")
        member = _linked_member("ap_member")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        mail.outbox.clear()
        reservation.approve(manager)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        confirmation = next(m for m in mail.outbox if m.subject.startswith("Reserved:"))
        assert confirmation.to == [member.primary_email]
        assert "Sami Brindlewood approved your reservation. Your reservation is set." in confirmation.body
        assert (
            "Sami Brindlewood approved your reservation. Your reservation is set.</p>"
            in (confirmation.alternatives[0][0])
        )
        assert "reservation.ics" in [attachment[0] for attachment in confirmation.attachments]
        # Approving frees nothing: the time stays taken.
        assert EquipmentReservation.objects.overlapping(equipment, reservation.starts_at, reservation.ends_at).exists()

    def it_refuses_a_member_who_does_not_manage_the_equipment():
        reservation = _waiting()
        with pytest.raises(EquipmentError, match="Only a manager"):
            reservation.approve(MemberFactory())
        reservation.refresh_from_db()
        assert reservation.status == WAITING

    def it_still_approves_a_request_after_the_switch_was_turned_off():
        equipment = EquipmentFactory(requires_approval=True)
        manager = _with_manager(equipment, "ap_off_mgr")
        reservation = _waiting(equipment, member=_linked_member("ap_off_member"))
        equipment.requires_approval = False
        equipment.save(update_fields=["requires_approval"])
        reservation.approve(manager)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_refuses_a_row_that_is_not_waiting():
        equipment = EquipmentFactory()
        manager = _with_manager(equipment, "ap_notwaiting")
        confirmed = EquipmentReservationFactory(equipment=equipment)
        with pytest.raises(EquipmentError, match="isn't waiting for approval any more"):
            confirmed.approve(manager)

    def it_refuses_a_request_whose_time_already_passed():
        equipment = EquipmentFactory()
        manager = _with_manager(equipment, "ap_past")
        reservation = _waiting(equipment, hours=-3)
        with pytest.raises(EquipmentError, match="already passed"):
            reservation.approve(manager)

    def it_decides_once_when_two_managers_approve_at_the_same_time():
        equipment = _approval_tool()
        first = _with_manager(equipment, "ap_race_one")
        second = _with_manager(equipment, "ap_race_two")
        reservation = equipment_service.reserve(equipment, _linked_member("ap_race_member"), _at(_day(), 10), 60)
        stale = EquipmentReservation.objects.get(pk=reservation.pk)
        mail.outbox.clear()
        reservation.approve(first)
        with pytest.raises(EquipmentError, match="isn't waiting for approval any more"):
            stale.approve(second)
        assert sum(1 for m in mail.outbox if m.subject.startswith("Reserved:")) == 1


def describe_decline():
    def it_frees_the_time_keeps_the_reason_and_tells_the_member():
        equipment = _approval_tool(name="Glimmerforge Lathe")
        manager = _with_manager(equipment, "dc_mgr", "Sami Brindlewood")
        member = _linked_member("dc_member")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        mail.outbox.clear()
        reservation.decline(manager, reason="  The spindle is out for repair that week.  ")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.DECLINED
        assert reservation.is_declined
        assert reservation.cancelled_by == manager
        assert reservation.cancelled_reason == "The spindle is out for repair that week."
        assert reservation.cancelled_at is not None
        assert not EquipmentReservation.objects.overlapping(
            equipment, reservation.starts_at, reservation.ends_at
        ).exists()
        declined = mail.outbox[0]
        assert declined.to == [member.primary_email]
        assert declined.subject == "Your Glimmerforge Lathe reservation was declined"
        assert "Sami Brindlewood declined your request for Glimmerforge Lathe, " in declined.body
        assert "Their reason: The spindle is out for repair that week." in declined.body
        assert "Pick another time:" in declined.body
        assert "Pick Another Time" in declined.alternatives[0][0]
        assert "[missing:" not in declined.alternatives[0][0]
        row = Notification.objects.get(user=member.user, trigger="equipment.reservation_declined")
        assert "The spindle is out for repair that week." in row.body
        # The freed time can be booked again.
        assert equipment_service.reserve(equipment, _linked_member("dc_next"), reservation.starts_at, 60)

    def it_still_declines_a_request_after_the_switch_was_turned_off():
        equipment = EquipmentFactory(requires_approval=True)
        manager = _with_manager(equipment, "dc_off_mgr")
        reservation = _waiting(equipment, member=_linked_member("dc_off_member"))
        equipment.requires_approval = False
        equipment.save(update_fields=["requires_approval"])
        reservation.decline(manager, reason="Booked for maintenance.")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.DECLINED

    def it_refuses_a_blank_reason():
        equipment = EquipmentFactory()
        manager = _with_manager(equipment, "dc_blank")
        reservation = _waiting(equipment)
        with pytest.raises(ValueError, match="needs a reason"):
            reservation.decline(manager, reason="   ")
        reservation.refresh_from_db()
        assert reservation.status == WAITING

    def it_refuses_a_member_who_does_not_manage_the_equipment():
        reservation = _waiting()
        with pytest.raises(EquipmentError, match="Only a manager"):
            reservation.decline(MemberFactory(), reason="No.")

    def it_decides_once_when_a_decline_races_an_approve():
        equipment = EquipmentFactory()
        first = _with_manager(equipment, "dc_race_one")
        second = _with_manager(equipment, "dc_race_two")
        reservation = _waiting(equipment, member=_linked_member("dc_race_member"))
        stale = EquipmentReservation.objects.get(pk=reservation.pk)
        reservation.approve(first)
        with pytest.raises(EquipmentError, match="isn't waiting for approval any more"):
            stale.decline(second, reason="Too late.")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        assert reservation.cancelled_reason == ""


def describe_cancelling_a_waiting_request():
    def _late_fee_on(equipment: Equipment) -> None:
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.late_cancel_notice_hours = 24
        config.late_cancel_grace_hours = 0
        config.save()
        equipment.late_cancel_fee_cents = 1500
        equipment.save(update_fields=["late_cancel_fee_cents"])

    def it_lets_the_member_cancel_inside_the_window_without_a_fee():
        equipment = EquipmentFactory(requires_approval=True)
        _late_fee_on(equipment)
        member = _linked_member("cw_self")
        reservation = _waiting(equipment, hours=2, member=member)
        fee = reservation.cancel(member)
        reservation.refresh_from_db()
        assert fee is None
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.late_fee_waived is False
        # The control: a confirmed row cancelled the same way is charged.
        confirmed = _waiting(equipment, hours=2, member=member, status=EquipmentReservation.Status.CONFIRMED)
        assert confirmed.cancel(member) is not None

    def it_lets_a_manager_cancel_a_waiting_request_with_a_reason():
        equipment = EquipmentFactory(requires_approval=True)
        _late_fee_on(equipment)
        manager = _with_manager(equipment, "cw_mgr")
        member = _linked_member("cw_member")
        reservation = _waiting(equipment, hours=2, member=member)
        mail.outbox.clear()
        reservation.cancel(manager, reason="The shop is closed that day.")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.is_cancelled_by_manager
        assert reservation.late_fee_waived is False
        assert "The shop is closed that day." in mail.outbox[0].body

    def it_tells_the_member_a_manager_just_approved_when_the_cancel_loses_that_race():
        equipment = EquipmentFactory(requires_approval=True)
        manager = _with_manager(equipment, "race_ap_mgr")
        member = _linked_member("race_ap_member")
        reservation = _waiting(equipment, member=member)
        stale = EquipmentReservation.objects.get(pk=reservation.pk)
        reservation.approve(manager)
        with pytest.raises(EquipmentError, match="A manager just approved this. Cancel it again if you still want to."):
            stale.cancel(member)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_tells_the_member_a_manager_just_declined_when_the_cancel_loses_that_race():
        equipment = EquipmentFactory(requires_approval=True)
        manager = _with_manager(equipment, "race_dc_mgr")
        member = _linked_member("race_dc_member")
        reservation = _waiting(equipment, member=member)
        stale = EquipmentReservation.objects.get(pk=reservation.pk)
        reservation.decline(manager, reason="Booked for maintenance.")
        with pytest.raises(EquipmentError, match="A manager just declined this request"):
            stale.cancel(member)

    def it_keeps_already_cancelled_when_the_cancel_loses_to_another_cancel():
        member = _linked_member("race_cx_member")
        reservation = _waiting(member=member)
        stale = EquipmentReservation.objects.get(pk=reservation.pk)
        reservation.cancel(member)
        with pytest.raises(EquipmentError, match="This reservation was already cancelled."):
            stale.cancel(member)

    def it_refuses_to_cancel_a_declined_request():
        member = MemberFactory()
        reservation = _waiting(member=member, status=EquipmentReservation.Status.DECLINED)
        with pytest.raises(EquipmentError, match="already declined"):
            reservation.cancel(member)


def describe_approval_events():
    def it_registers_the_three_events_with_their_audiences_and_defaults():
        requested = get_event("equipment.reservation_requested")
        assert requested.recipient is Recipients.SINGLE_USER
        assert requested.channel(Channel.EMAIL).default is ChannelDefault.FORCED
        needs = get_event("equipment.reservation_needs_approval")
        assert needs.recipient is Recipients.EQUIPMENT_MANAGERS
        assert needs.channel(Channel.EMAIL).default is ChannelDefault.ON
        declined = get_event("equipment.reservation_declined")
        assert declined.recipient is Recipients.SINGLE_USER
        assert declined.channel(Channel.EMAIL).default is ChannelDefault.FORCED
        assert declined.channel(Channel.PUSH).default is ChannelDefault.ON
        for event in (requested, needs, declined):
            assert event.category == "Spaces & Equipment"
            assert not event.has_channel(Channel.DISCORD)

    def it_supplies_every_documented_placeholder_in_each_emit_context():
        equipment = _approval_tool(name="Glimmerforge Lathe")
        manager = _with_manager(equipment, "ctx_mgr")
        member = _linked_member("ctx_member")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60, purpose="Sign blanks.")
        base = equipment_service._placeholder_context(reservation)
        contexts = {
            "equipment.reservation_requested": {"user": member.user, **base},
            "equipment.reservation_needs_approval": {
                "equipment": equipment,
                "purpose_line": equipment_service._purpose_line(reservation.purpose),
                "manage_url": equipment_service._manage_reservations_url(reservation),
                **base,
            },
            "equipment.reservation_declined": {
                "user": member.user,
                "manager_name": manager.display_name,
                "decline_reason": "x",
                "refund_line": "",
                **base,
            },
            "equipment.reservation_confirmed": {"user": member.user, "approval_line": "Sami approved. ", **base},
        }
        for event_key, context in contexts.items():
            for name in placeholders_for(event_key):
                assert name in context, f"{event_key} copy documents {{{{ {name} }}}} but the emit omits it"
            for channel in COPY_CHANNELS:
                copy = default_copy_for(event_key, channel)
                for fragment in (copy.subject, copy.body_text, copy.body_html):
                    assert "[missing:" not in render_text(fragment, context), f"{event_key}/{channel.value}"

    def it_gives_each_step_its_own_period_so_a_re_emit_sends_nothing_twice():
        equipment = _approval_tool()
        _with_manager(equipment, "per_mgr")
        member = _linked_member("per_member")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        mail.outbox.clear()
        equipment_service._notify_requested(reservation)
        equipment_service._notify_needs_approval(reservation)
        assert mail.outbox == []

    def it_never_doubles_the_full_stop_a_member_typed_in_the_purpose():
        assert equipment_service._purpose_line("Sign blanks.") == "Purpose: Sign blanks. "
        assert equipment_service._purpose_line("  ") == ""


def describe_the_reservations_discord_feed():
    _WEBHOOK = "https://discord.com/api/webhooks/900/reservations"

    def _configure_webhook() -> None:
        config = SiteConfiguration.load()
        config.discord_reservations_webhook_url = _WEBHOOK
        config.save()

    @respx.mock
    def it_posts_nothing_for_a_request():
        _configure_webhook()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        equipment = _approval_tool()
        _with_manager(equipment, "feed_req_mgr")
        equipment_service.reserve(equipment, _linked_member("feed_req"), _at(_day(), 10), 60)
        assert not route.called

    @respx.mock
    def it_posts_the_reservation_once_a_manager_approves_it_without_pinging_the_managers():
        _configure_webhook()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        equipment = _approval_tool(name="Glimmerforge Lathe")
        manager = _with_manager(equipment, "feed_ap_mgr")
        other = _with_manager(equipment, "feed_ap_other")
        member = _linked_member("feed_ap_member")
        reservation = equipment_service.reserve(equipment, member, _at(_day(), 10), 60)
        assert not route.called
        mail.outbox.clear()
        reservation.approve(manager)
        assert route.call_count == 1
        embed = json.loads(route.calls[0].request.content)["embeds"][0]
        assert embed["title"] == "New reservation"
        assert member.display_name in embed["description"]
        assert "Glimmerforge Lathe" in embed["description"]
        assert embed["url"].startswith("http")
        # The approving managers already know: no bell, and no email to either of them.
        pinged = Notification.objects.filter(trigger="equipment.reservation_made")
        assert not pinged.filter(user__in=[manager.user, other.user]).exists()
        assert all(m.to == [member.primary_email] for m in mail.outbox)

    @respx.mock
    def it_posts_nothing_for_a_decline():
        _configure_webhook()
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
        equipment = _approval_tool()
        manager = _with_manager(equipment, "feed_dc_mgr")
        reservation = equipment_service.reserve(equipment, _linked_member("feed_dc"), _at(_day(), 10), 60)
        reservation.decline(manager, reason="Booked for maintenance.")
        assert not route.called

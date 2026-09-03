"""BDD specs for the PR 2 equipment reservation views (spec §6/§7).

The HTMX schedule partial, the Book a Time POST (with crafted-POST probes for
every guard), self + manager cancels, the Hours & Limits save (formset + closure
+ limits in one Save, real per-row Delete), the manage tabs, the closed banner,
and the index availability line.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Equipment, EquipmentHours, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _member_user(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    if not member.full_legal_name:
        member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    user = _member_user(username, fog_role=fog_role)
    client.login(username=username, password="pass")
    return user


def _day():
    return timezone.localdate() + timedelta(days=2)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _open_tool(**kwargs) -> Equipment:
    equipment = EquipmentFactory(**kwargs)
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _toast(response) -> str:
    return json.loads(response["HX-Trigger"])["showToast"]["message"]


def describe_equipment_schedule():
    def it_shows_reserver_names_and_purpose_to_a_plain_logged_in_member(client: Client):
        _login(client, "sch_names")
        equipment = _open_tool()
        booker = _member_user("sch_names_booker")
        booker.member.full_legal_name = "Sam Reyes"
        booker.member.save(update_fields=["full_legal_name"])
        EquipmentReservationFactory(
            equipment=equipment,
            member=booker.member,
            starts_at=_at(_day(), 10),
            ends_at=_at(_day(), 12),
            purpose="Longarm quilting",
        )
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]), {"day": _day().isoformat()})
        assert response.status_code == 200
        assert b"Sam Reyes" in response.content
        assert b"Longarm quilting" in response.content
        assert b"Open" in response.content

    def it_tells_a_member_the_equipment_is_not_taking_reservations_yet(client: Client):
        _login(client, "sch_nohours")
        equipment = EquipmentFactory()
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]))
        assert b"isn't taking reservations yet" in response.content
        assert b"Add opening hours" not in response.content

    def it_tells_a_manager_to_add_opening_hours(client: Client):
        user = _login(client, "sch_mgr_nohours")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]))
        assert b"Add opening hours to start taking reservations." in response.content

    def it_shows_the_fully_booked_day_state(client: Client):
        user = _login(client, "sch_full")
        equipment = EquipmentFactory()
        EquipmentHoursFactory(
            equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(11, 0)
        )
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(_day(), 9), ends_at=_at(_day(), 11))
        assert user.member.status == Member.Status.ACTIVE
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]), {"day": _day().isoformat()})
        assert b"No open times this day. Try another day." in response.content

    def it_skips_reservations_outside_the_open_window(client: Client):
        # A same-day reservation outside the window (data drift) must not derail the
        # timeline — the window renders fully open.
        _login(client, "sch_outside")
        equipment = EquipmentFactory()
        EquipmentHoursFactory(
            equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(11, 0)
        )
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(_day(), 14), ends_at=_at(_day(), 15))
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]), {"day": _day().isoformat()})
        assert response.status_code == 200
        assert b"9:00 AM to 11:00 AM" in response.content
        assert b"2:00 PM" not in response.content or b"Open" in response.content

    def it_renders_for_a_user_with_no_member(client: Client):
        user = _member_user("sch_no_member")
        user.member.delete()
        client.login(username="sch_no_member", password="pass")
        equipment = _open_tool()
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]))
        assert response.status_code == 200
        assert b"Book a Time" not in response.content

    def it_hides_the_booking_form_from_a_blocked_member(client: Client):
        _login(client, "sch_blocked")
        equipment = _open_tool(required_orientation=OrientationTypeFactory(name="Lathe"))
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]), {"day": _day().isoformat()})
        assert b"Book a Time" not in response.content


def describe_equipment_reserve():
    def _post(client: Client, equipment: Equipment, *, hour: int = 10, duration: int = 60, day=None):
        target = day if day is not None else _day()
        return client.post(
            reverse("hub_equipment_reserve", args=[equipment.slug]),
            {
                "starts_at": _at(target, hour).isoformat(),
                "duration_minutes": duration,
                "purpose": "Testing",
                "day": target.isoformat(),
            },
        )

    def it_books_and_reswaps_the_schedule_with_a_toast(client: Client):
        user = _login(client, "bk_happy")
        equipment = _open_tool()
        response = _post(client, equipment)
        assert response.status_code == 200
        assert _toast(response).startswith("Reserved. See you ")
        reservation = EquipmentReservation.objects.get(equipment=equipment, member=user.member)
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        # The re-swapped partial already lists the new reservation.
        assert b"Your Reservations Here" in response.content

    def it_recovers_from_a_lost_race_with_a_friendly_toast_and_fresh_starts(client: Client):
        _login(client, "bk_race")
        equipment = _open_tool()
        EquipmentReservationFactory(equipment=equipment, starts_at=_at(_day(), 10), ends_at=_at(_day(), 11))
        response = _post(client, equipment, hour=10)
        assert response.status_code == 200
        assert _toast(response) == "That time was just taken. Please pick another time."
        assert b"equipment-schedule" in response.content

    def it_rejects_booking_on_closed_equipment(client: Client):
        user = _login(client, "bk_closed")
        equipment = _open_tool(is_closed=True, closed_message="Down for maintenance.")
        response = _post(client, equipment)
        assert _toast(response) == "Down for maintenance."
        assert not EquipmentReservation.objects.filter(member=user.member).exists()

    def it_rejects_an_unoriented_member(client: Client):
        user = _login(client, "bk_unoriented")
        equipment = _open_tool(required_orientation=OrientationTypeFactory(name="Lathe"))
        response = _post(client, equipment)
        assert "Lathe orientation" in _toast(response)
        assert not EquipmentReservation.objects.filter(member=user.member).exists()

    def it_rejects_a_past_time(client: Client):
        user = _login(client, "bk_past")
        equipment = _open_tool()
        response = client.post(
            reverse("hub_equipment_reserve", args=[equipment.slug]),
            {
                "starts_at": (timezone.now() - timedelta(hours=2)).isoformat(),
                "duration_minutes": 60,
                "purpose": "",
                "day": _day().isoformat(),
            },
        )
        assert "already past" in _toast(response)
        assert not EquipmentReservation.objects.filter(member=user.member).exists()

    def it_rejects_an_over_limit_duration(client: Client):
        user = _login(client, "bk_long")
        equipment = _open_tool()
        response = _post(client, equipment, duration=300)
        assert "at most 240" in _toast(response)
        assert not EquipmentReservation.objects.filter(member=user.member).exists()

    def it_rejects_garbage_datetimes_gracefully(client: Client):
        _login(client, "bk_garbage")
        equipment = _open_tool()
        response = client.post(
            reverse("hub_equipment_reserve", args=[equipment.slug]),
            {"starts_at": "not-a-time", "duration_minutes": 60, "purpose": "", "day": ""},
        )
        assert _toast(response) == "Please pick one of the listed times."

    def it_403s_a_user_with_no_member(client: Client):
        user = _member_user("bk_no_member")
        user.member.delete()
        client.login(username="bk_no_member", password="pass")
        equipment = _open_tool()
        assert _post(client, equipment).status_code == 403


def describe_equipment_reservation_cancel():
    def it_lets_the_member_self_cancel_via_the_schedule(client: Client):
        user = _login(client, "cx_self_view")
        equipment = _open_tool()
        reservation = EquipmentReservationFactory(
            equipment=equipment, member=user.member, starts_at=_at(_day(), 10), ends_at=_at(_day(), 11)
        )
        response = client.post(reverse("hub_equipment_reservation_cancel", args=[equipment.slug, reservation.pk]))
        assert response.status_code == 200
        assert _toast(response) == "Reservation cancelled."
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED

    def it_403s_a_user_with_no_member(client: Client):
        user = _member_user("cx_no_member")
        user.member.delete()
        client.login(username="cx_no_member", password="pass")
        reservation = EquipmentReservationFactory(starts_at=_at(_day(), 10), ends_at=_at(_day(), 11))
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk])
        )
        assert response.status_code == 403

    def it_403s_another_members_crafted_cancel(client: Client):
        _login(client, "cx_stranger")
        reservation = EquipmentReservationFactory(starts_at=_at(_day(), 10), ends_at=_at(_day(), 11))
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk])
        )
        assert response.status_code == 403
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_requires_a_reason_from_a_manager(client: Client):
        _login(client, "cx_mgr_blank", fog_role=Member.FogRole.ADMIN)
        reservation = EquipmentReservationFactory(starts_at=_at(_day(), 10), ends_at=_at(_day(), 11))
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk]),
            {"reason": "   "},
        )
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=reservations")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_lets_a_manager_cancel_with_a_reason(client: Client):
        _login(client, "cx_mgr_ok", fog_role=Member.FogRole.ADMIN)
        booker = _member_user("cx_mgr_target")
        reservation = EquipmentReservationFactory(
            member=booker.member, starts_at=_at(_day(), 10), ends_at=_at(_day(), 11)
        )
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk]),
            {"reason": "Down for repair."},
        )
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=reservations")
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.cancelled_reason == "Down for repair."

    def it_toasts_the_guard_when_self_cancelling_an_in_progress_reservation(client: Client):
        user = _login(client, "cx_started")
        equipment = _open_tool()
        reservation = EquipmentReservationFactory(
            equipment=equipment,
            member=user.member,
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=30),
        )
        response = client.post(reverse("hub_equipment_reservation_cancel", args=[equipment.slug, reservation.pk]))
        assert response.status_code == 200
        assert "already started" in _toast(response)
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_redirects_a_manager_cancel_of_an_ended_reservation_with_the_error(client: Client):
        _login(client, "cx_mgr_ended", fog_role=Member.FogRole.ADMIN)
        reservation = EquipmentReservationFactory(
            starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(hours=1)
        )
        response = client.post(
            reverse("hub_equipment_reservation_cancel", args=[reservation.equipment.slug, reservation.pk]),
            {"reason": "Too late."},
        )
        assert response.status_code == 302
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED

    def it_shows_the_manager_cancelled_row_with_the_reason_on_the_schedule(client: Client):
        user = _login(client, "cx_row")
        equipment = _open_tool()
        admin = _member_user("cx_row_admin", fog_role=Member.FogRole.ADMIN)
        reservation = EquipmentReservationFactory(
            equipment=equipment, member=user.member, starts_at=_at(_day(), 10), ends_at=_at(_day(), 11)
        )
        reservation.cancel(admin.member, reason="Down for repair.")
        response = client.get(reverse("hub_equipment_schedule", args=[equipment.slug]))
        assert b"Cancelled by the manager: Down for repair." in response.content


def describe_equipment_hours_save():
    def _settings_data(**overrides):
        data = {
            "hours-TOTAL_FORMS": "0",
            "hours-INITIAL_FORMS": "0",
            "hours-MIN_NUM_FORMS": "0",
            "hours-MAX_NUM_FORMS": "1000",
            "closed_message": "",
            "min_duration_minutes": "30",
            "max_duration_minutes": "240",
            "max_advance_days": "30",
            "max_active_reservations_per_member": "2",
        }
        data.update(overrides)
        return data

    def it_403s_a_crafted_post_from_a_plain_member(client: Client):
        _login(client, "hrs_plain")
        equipment = EquipmentFactory()
        response = client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), _settings_data())
        assert response.status_code == 403

    def it_saves_a_new_row_plus_closure_and_limits_in_one_post(client: Client):
        user = _login(client, "hrs_save")
        equipment = EquipmentFactory()
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        data = _settings_data(
            **{
                "hours-TOTAL_FORMS": "1",
                "hours-0-id": "",
                "hours-0-weekday": "1",
                "hours-0-start_time": "09:00",
                "hours-0-end_time": "17:00",
                "hours-0-is_active": "on",
                "is_closed": "on",
                "closed_message": "Down for maintenance.",
                "max_advance_days": "14",
            }
        )
        response = client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), data)
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=hours")
        rule = equipment.hours_rules.get()
        assert rule.weekday == 1
        assert rule.start_time == time(9, 0)
        equipment.refresh_from_db()
        assert equipment.is_closed is True
        assert equipment.closed_message == "Down for maintenance."
        assert equipment.max_advance_days == 14

    def it_rejects_an_end_before_the_start_with_the_friendly_message(client: Client):
        _login(client, "hrs_backwards", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        data = _settings_data(
            **{
                "hours-TOTAL_FORMS": "1",
                "hours-0-id": "",
                "hours-0-weekday": "1",
                "hours-0-start_time": "17:00",
                "hours-0-end_time": "09:00",
                "hours-0-is_active": "on",
            }
        )
        response = client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), data)
        assert response.status_code == 200
        assert b"The end time must be after the start time." in response.content
        assert not equipment.hours_rules.exists()

    def it_deletes_a_row_while_preserving_the_other_edits(client: Client):
        _login(client, "hrs_delete", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        rule = EquipmentHoursFactory(equipment=equipment)
        data = _settings_data(
            **{
                "hours-TOTAL_FORMS": "1",
                "hours-INITIAL_FORMS": "1",
                "hours-0-id": str(rule.pk),
                "hours-0-weekday": str(rule.weekday),
                "hours-0-start_time": "09:00",
                "hours-0-end_time": "17:00",
                "hours-0-is_active": "on",
                "hours-0-DELETE": "on",
                "closed_message": "Edited alongside the delete.",
            }
        )
        response = client.post(reverse("hub_equipment_hours_save", args=[equipment.slug]), data)
        assert response.status_code == 302
        assert not EquipmentHours.objects.filter(pk=rule.pk).exists()
        equipment.refresh_from_db()
        assert equipment.closed_message == "Edited alongside the delete."

    def it_rejects_off_grid_limits(client: Client):
        _login(client, "hrs_limits", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        response = client.post(
            reverse("hub_equipment_hours_save", args=[equipment.slug]),
            _settings_data(min_duration_minutes="45"),
        )
        assert response.status_code == 200
        assert b"Use half hour steps, starting at 30." in response.content

    def it_rejects_every_other_bad_limit(client: Client):
        _login(client, "hrs_limits2", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        url = reverse("hub_equipment_hours_save", args=[equipment.slug])
        response = client.post(url, _settings_data(max_duration_minutes="250"))
        assert b"Use half hour steps." in response.content
        response = client.post(url, _settings_data(min_duration_minutes="120", max_duration_minutes="60"))
        assert b"cannot be shorter than the shortest" in response.content
        response = client.post(url, _settings_data(max_advance_days="0"))
        assert b"Use at least 1 day." in response.content
        response = client.post(url, _settings_data(max_active_reservations_per_member="0"))
        assert b"Use at least 1." in response.content


def describe_equipment_manage_tabs():
    def it_renders_the_hours_tab_with_the_add_button_and_empty_state(client: Client):
        _login(client, "tab_hours", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "hours"})
        assert response.status_code == 200
        assert response.context["active_tab"] == "hours"
        assert b"+ Add Hours" in response.content
        assert b"No opening hours yet. Members cannot book until you add some." in response.content
        assert b"Closed for new reservations" in response.content

    def it_renders_the_reservations_tab_with_rows_and_the_reason_modal(client: Client):
        _login(client, "tab_res", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        booker = _member_user("tab_res_booker")
        EquipmentReservationFactory(
            equipment=equipment, member=booker.member, starts_at=_at(_day(), 10), ends_at=_at(_day(), 11)
        )
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "reservations"})
        assert response.context["active_tab"] == "reservations"
        assert booker.member.display_name.encode() in response.content
        assert b"Cancel Reservation" in response.content

    def it_shows_the_reservations_empty_state(client: Client):
        _login(client, "tab_res_empty", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "reservations"})
        assert b"No upcoming reservations." in response.content


def describe_detail_page_reservation_states():
    def it_shows_the_closed_banner_with_the_message(client: Client):
        _login(client, "det_closed")
        equipment = _open_tool(is_closed=True, closed_message="Down for maintenance. Back Tuesday.")
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"Down for maintenance. Back Tuesday." in response.content
        assert b"Book a Time" not in response.content

    def it_appends_pick_a_time_to_the_all_set_banner_when_open(client: Client):
        _login(client, "det_pick")
        equipment = _open_tool()
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        assert b"You're all set. Pick a time below." in response.content
        assert b"Book a Time" in response.content


def describe_index_availability_line():
    def it_shows_not_taking_reservations_yet_without_hours(client: Client):
        _login(client, "idx_nohours")
        EquipmentFactory(name="Bare Tool")
        response = client.get(reverse("hub_equipment_index"))
        assert b"Not taking reservations yet" in response.content

    def it_shows_the_closed_line(client: Client):
        _login(client, "idx_closed")
        equipment = EquipmentFactory(name="Closed Tool", is_closed=True, closed_message="Back Tuesday.")
        EquipmentHoursFactory(equipment=equipment)
        response = client.get(reverse("hub_equipment_index"))
        assert b"Closed. Back Tuesday." in response.content

    def it_shows_available_now_inside_an_open_window(client: Client):
        _login(client, "idx_open")
        equipment = EquipmentFactory(name="Open Tool")
        EquipmentHoursFactory(
            equipment=equipment,
            weekday=timezone.localtime().weekday(),
            start_time=time(0, 0),
            end_time=time(23, 59),
        )
        response = client.get(reverse("hub_equipment_index"))
        assert b"Available now" in response.content

    def it_shows_reserved_until_while_a_reservation_is_running(client: Client):
        _login(client, "idx_busy")
        equipment = EquipmentFactory(name="Busy Tool")
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
        response = client.get(reverse("hub_equipment_index"))
        assert b"Reserved until" in response.content

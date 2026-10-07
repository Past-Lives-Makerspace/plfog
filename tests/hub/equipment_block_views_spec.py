"""BDD specs for the manage tab's Block Time card and how a block reads to members (#657).

The card's add and remove endpoints (manager only, nothing emailed), the timeline's
"Held · reason" segment, and every booking list a block must stay out of: the member's own
reservations, Upcoming Reservations on the schedule and the manage tab, and the Bookings tab.
Assertions anchor on markup and factory strings (STANDARDS.md §8).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Equipment, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username.title()} Tester"
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _day():
    return timezone.localdate() + timedelta(days=2)


def _at(day, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _tool(**kwargs) -> Equipment:
    defaults = {"max_duration_minutes": 120, "max_active_reservations_per_member": 1}
    equipment = EquipmentFactory(**{**defaults, **kwargs})
    EquipmentHoursFactory(equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0))
    return equipment


def _manage_login(client: Client, equipment: Equipment, username: str = "presslead") -> Member:
    member = _login(client, username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=member)
    return member


def _block(equipment: Equipment, member: Member, start: int, end: int, reason: str = "Etching Orientation"):
    return EquipmentReservationFactory(
        equipment=equipment,
        member=member,
        kind=EquipmentReservation.Kind.BLOCK,
        starts_at=_at(_day(), start),
        ends_at=_at(_day(), end),
        purpose=reason,
    )


def _post_block(client: Client, equipment: Equipment, **overrides: str):
    data = {
        "date": _day().isoformat(),
        "start_time": "10:00",
        "end_time": "12:00",
        "reason": "Etching Orientation",
        **overrides,
    }
    return client.post(reverse("hub_equipment_block_add", args=[equipment.slug]), data)


def _manage_url(equipment: Equipment) -> str:
    return f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=reservations"


def describe_block_time_card():
    def it_shows_the_card_with_date_start_end_and_reason_on_the_manage_tab(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        content = client.get(_manage_url(equipment)).content.decode()
        assert "data-block-time-card" in content
        assert f'action="{reverse("hub_equipment_block_add", args=[equipment.slug])}"' in content
        for name in ("date", "start_time", "end_time", "reason"):
            assert f'name="{name}"' in content
        assert 'maxlength="80"' in content
        # The start and end offer the half hour grid, nothing finer.
        assert '<option value="10:30"' in content
        assert '<option value="10:15"' not in content

    def it_blocks_the_span_and_lands_back_on_the_reservations_tab(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        response = _post_block(client, equipment)
        assert response.status_code == 302
        assert response["Location"] == _manage_url(equipment)
        block = EquipmentReservation.objects.blocks().get(equipment=equipment)
        assert (block.member, block.purpose) == (manager, "Etching Orientation")
        assert (block.starts_at, block.ends_at) == (_at(_day(), 10), _at(_day(), 12))

    def it_skips_the_cap_the_duration_bounds_and_the_open_hours(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        EquipmentReservationFactory(
            equipment=equipment, member=manager, starts_at=_at(_day(), 9), ends_at=_at(_day(), 10)
        )
        response = _post_block(client, equipment, start_time="12:00", end_time="22:00")
        assert response.status_code == 302
        assert EquipmentReservation.objects.blocks().filter(equipment=equipment).count() == 1

    def it_sends_no_email(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        mail.outbox.clear()
        _post_block(client, equipment)
        assert mail.outbox == []

    def it_refuses_an_overlap_naming_the_reservation_and_keeps_the_form(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        sam = MemberFactory(full_legal_name="Sammy Quillfeather", preferred_name="")
        EquipmentReservationFactory(equipment=equipment, member=sam, starts_at=_at(_day(), 14), ends_at=_at(_day(), 16))
        response = _post_block(client, equipment, start_time="13:00", end_time="15:00")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Overlaps Sammy Q.&#x27;s reservation, 2:00 PM to 4:00 PM." in content
        assert 'value="Etching Orientation"' in content
        assert not EquipmentReservation.objects.blocks().exists()

    def it_refuses_an_end_at_or_before_the_start(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        response = _post_block(client, equipment, start_time="12:00", end_time="11:00")
        assert response.status_code == 200
        assert response.context["block_form"].errors["end_time"] == ["The end time must be after the start time."]
        assert not EquipmentReservation.objects.blocks().exists()

    def it_refuses_a_reason_over_eighty_characters(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        response = _post_block(client, equipment, reason="x" * 81)
        assert response.status_code == 200
        assert "reason" in response.context["block_form"].errors
        assert not EquipmentReservation.objects.blocks().exists()

    def it_refuses_a_missing_reason(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        response = _post_block(client, equipment, reason="")
        assert response.context["block_form"].errors["reason"] == [
            "Please give a reason members will see on the schedule."
        ]

    def it_forbids_a_member_who_cannot_manage_the_item(client: Client):
        equipment = _tool()
        _login(client, "plainmember")
        assert _post_block(client, equipment).status_code == 403
        assert not EquipmentReservation.objects.blocks().exists()

    def it_requires_a_date(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        response = _post_block(client, equipment, date="")
        assert "date" in response.context["block_form"].errors
        assert not EquipmentReservation.objects.blocks().exists()

    def it_forbids_a_login_with_no_member(client: Client):
        equipment = _tool()
        _login(client, "nomember").delete()
        assert _post_block(client, equipment).status_code == 403

    def it_forbids_a_manager_of_a_different_item(client: Client):
        equipment = _tool()
        _manage_login(client, _tool())
        assert _post_block(client, equipment).status_code == 403

    def it_lists_upcoming_blocks_with_remove_and_keeps_them_out_of_upcoming_reservations(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        block = _block(equipment, manager, 10, 12, reason="Plate Maintenance")
        past = EquipmentReservationFactory(
            equipment=equipment,
            member=manager,
            kind=EquipmentReservation.Kind.BLOCK,
            starts_at=timezone.now() - timedelta(days=1, hours=2),
            ends_at=timezone.now() - timedelta(days=1),
        )
        reservation = EquipmentReservationFactory(
            equipment=equipment, starts_at=_at(_day(), 13), ends_at=_at(_day(), 14)
        )
        response = client.get(_manage_url(equipment))
        assert response.context["manage_blocks"] == [block]
        assert list(response.context["manage_reservations"]) == [reservation]
        content = response.content.decode()
        assert f'data-block-row="{block.pk}"' in content
        assert f'data-block-row="{past.pk}"' not in content
        assert reverse("hub_equipment_block_remove", args=[equipment.slug, block.pk]) in content
        assert f"mgr-cancel-{block.pk}" not in content


def describe_block_remove():
    def _remove(client: Client, equipment: Equipment, pk: int):
        return client.post(reverse("hub_equipment_block_remove", args=[equipment.slug, pk]))

    def it_removes_the_block_with_no_reason_and_no_email(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        block = _block(equipment, manager, 10, 12)
        mail.outbox.clear()
        response = _remove(client, equipment, block.pk)
        assert response.status_code == 302
        assert response["Location"] == _manage_url(equipment)
        block.refresh_from_db()
        assert block.status == EquipmentReservation.Status.CANCELLED
        assert block.cancelled_reason == ""
        assert mail.outbox == []

    def it_lets_another_manager_of_the_item_remove_it(client: Client):
        equipment = _tool()
        block = _block(equipment, MemberFactory(), 10, 12)
        _manage_login(client, equipment)
        _remove(client, equipment, block.pk)
        block.refresh_from_db()
        assert block.status == EquipmentReservation.Status.CANCELLED

    def it_forbids_a_member_who_cannot_manage_the_item(client: Client):
        equipment = _tool()
        block = _block(equipment, MemberFactory(), 10, 12)
        _login(client, "plainmember")
        assert _remove(client, equipment, block.pk).status_code == 403
        block.refresh_from_db()
        assert block.status == EquipmentReservation.Status.CONFIRMED

    def it_forbids_a_login_with_no_member(client: Client):
        equipment = _tool()
        block = _block(equipment, MemberFactory(), 10, 12)
        _login(client, "nomember").delete()
        assert _remove(client, equipment, block.pk).status_code == 403

    def it_is_404_for_a_member_reservation(client: Client):
        equipment = _tool()
        _manage_login(client, equipment)
        reservation = EquipmentReservationFactory(equipment=equipment)
        assert _remove(client, equipment, reservation.pk).status_code == 404

    def it_tells_the_manager_when_it_was_already_removed(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        block = _block(equipment, manager, 10, 12)
        _remove(client, equipment, block.pk)
        response = _remove(client, equipment, block.pk)
        assert response.status_code == 302
        follow = client.get(response["Location"])
        assert "This block was already removed." in [str(m) for m in follow.context["messages"]]

    def it_keeps_the_manager_cancel_route_off_a_block(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        block = _block(equipment, manager, 10, 12)
        client.post(reverse("hub_equipment_reservation_cancel", args=[equipment.slug, block.pk]), {"reason": "x"})
        block.refresh_from_db()
        assert block.status == EquipmentReservation.Status.CONFIRMED


def describe_a_block_on_the_member_schedule():
    def _schedule(client: Client, equipment: Equipment):
        return client.get(reverse("hub_equipment_schedule", args=[equipment.slug]), {"day": _day().isoformat()})

    def it_shows_held_and_the_reason_on_the_day_timeline(client: Client):
        equipment = _tool()
        _block(equipment, MemberFactory(full_legal_name="Quorra Blockwright", preferred_name=""), 10, 12)
        _login(client, "viewer")
        response = _schedule(client, equipment)
        [held] = [segment for segment in response.context["timeline"] if segment.get("kind") == "block"]
        assert held["label"] == "Held · Etching Orientation"
        assert (held["starts_at"], held["ends_at"]) == (_at(_day(), 10), _at(_day(), 12))
        content = response.content.decode()
        assert "data-held-slot" in content
        assert "Held · Etching Orientation" in content
        # Held time, not the manager's booking: their name never shows on it.
        assert "Quorra" not in content

    def it_offers_no_start_inside_the_block(client: Client):
        equipment = _tool()
        _block(equipment, MemberFactory(), 10, 12)
        _login(client, "viewer")
        starts = _schedule(client, equipment).context["starts"]
        assert _at(_day(), 9) in starts
        assert _at(_day(), 10) not in starts
        assert _at(_day(), 11, 30) not in starts

    def it_refuses_a_crafted_booking_over_the_block(client: Client):
        equipment = _tool()
        _block(equipment, MemberFactory(), 10, 12)
        _login(client, "crafty")
        response = client.post(
            reverse("hub_equipment_reserve", args=[equipment.slug]),
            {"starts_at": _at(_day(), 11).isoformat(), "duration_minutes": "60", "day": _day().isoformat()},
        )
        assert json.loads(response["HX-Trigger"])["showToast"]["type"] == "error"
        assert not EquipmentReservation.objects.reservations().exists()

    def it_keeps_the_block_out_of_the_managers_own_reservations_and_upcoming_reservations(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        _block(equipment, manager, 10, 12)
        reservation = EquipmentReservationFactory(
            equipment=equipment, member=manager, starts_at=_at(_day(), 13), ends_at=_at(_day(), 14)
        )
        response = _schedule(client, equipment)
        assert response.context["my_reservations"] == [reservation]
        assert response.context["upcoming_reservations"] == [reservation]

    def it_leaves_the_booking_form_open_to_the_manager_despite_a_block(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        _block(equipment, manager, 10, 11)
        _block(equipment, manager, 14, 15)
        # The cap is 1; two blocks do not use it up.
        assert _schedule(client, equipment).context["can_book"] is True


def describe_a_block_on_the_bookings_tab():
    def it_never_lists_a_block_for_its_manager(client: Client):
        equipment = _tool()
        manager = _manage_login(client, equipment)
        block = _block(equipment, manager, 10, 12)
        reservation = EquipmentReservationFactory(
            equipment=equipment, starts_at=_at(_day(), 13), ends_at=_at(_day(), 14)
        )
        content = client.get(reverse("hub_equipment_index"), {"view": "bookings"}).content.decode()
        assert f'data-reservation-row="{reservation.pk}"' in content
        assert f'data-reservation-row="{block.pk}"' not in content


def describe_a_block_on_the_orientation_tab():
    def it_names_held_time_rather_than_a_members_reservation(client: Client):
        from tests.membership.factories import OrientationSlotFactory, OrientationTypeFactory

        equipment = _tool()
        manager = _manage_login(client, equipment)
        _block(equipment, manager, 10, 12, reason="Roller Repair")
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(_day(), 11), ends_at=_at(_day(), 12)
        )
        content = client.get(
            f"{reverse('hub_equipment_manage', args=[equipment.slug])}?tab=orientation"
        ).content.decode()
        assert "Blocked by held time (Roller Repair) 10:00 AM to 12:00 PM" in content


def describe_a_block_on_the_index_cards():
    def _all_day(name: str) -> Equipment:
        equipment = EquipmentFactory(name=name)
        EquipmentHoursFactory(
            equipment=equipment, weekday=timezone.localtime().weekday(), start_time=time(0, 0), end_time=time(23, 30)
        )
        return equipment

    def _running(equipment: Equipment, **fields) -> None:
        EquipmentReservationFactory(
            equipment=equipment,
            starts_at=timezone.now() - timedelta(minutes=30),
            ends_at=timezone.now() + timedelta(minutes=60),
            **fields,
        )

    def it_says_held_until_on_a_blocked_items_card(client: Client):
        held = _all_day("Quillpress Held")
        reserved = _all_day("Quillpress Booked")
        _running(held, kind=EquipmentReservation.Kind.BLOCK)
        _running(reserved)
        _login(client, "cardviewer")
        cards = {
            card["equipment"].name: card["availability"]
            for card in client.get(reverse("hub_equipment_index")).context["cards"]
        }
        assert cards["Quillpress Held"][1].startswith("Held until ")
        assert cards["Quillpress Booked"][1].startswith("Reserved until ")

    def it_reads_the_block_without_a_query_per_card(client: Client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        tools = [_all_day(f"Quillpress {n}") for n in range(3)]
        _login(client, "cardcounter")
        url = reverse("hub_equipment_index")

        def count_queries() -> int:
            client.get(url)
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(url).status_code == 200
            return len(ctx.captured_queries)

        _running(tools[0])
        one_busy = count_queries()
        for tool in tools[1:]:
            _running(tool, kind=EquipmentReservation.Kind.BLOCK)
        assert count_queries() == one_busy

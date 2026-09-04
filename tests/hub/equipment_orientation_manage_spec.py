"""BDD specs for the equipment manage panel's Orientation tab (equipment-owned orientations).

Types formset (with both delete guards), one-off slot add (bound reveal), slot
cancel fan-out, pending-request list, attendee rows, and the permission edges on
every endpoint including the equipment-gated respond page.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Equipment, Member, OrientationBooking, OrientationSlot, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
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


def _manager_login(client: Client, username: str, equipment: Equipment) -> User:
    user = _login(client, username)
    EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
    return user


def _owned_type(equipment: Equipment, **kwargs) -> OrientationType:
    return OrientationTypeFactory(
        equipment_owned=True, equipment=equipment, name=kwargs.pop("name", "Operator Basics"), **kwargs
    )


def _owned_slot(orientation_type: OrientationType) -> OrientationSlot:
    return OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)


def _types_data(rows: list[dict], initial: int = 0) -> dict:
    data = {
        "otypes-TOTAL_FORMS": str(len(rows)),
        "otypes-INITIAL_FORMS": str(initial),
        "otypes-MIN_NUM_FORMS": "0",
        "otypes-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        defaults = {
            "name": "Operator Basics",
            "description": "",
            "duration_minutes": "60",
            "price": "",
            "default_seats": "4",
            "default_location": "",
            "sort_order": "0",
            "is_active": "on",
        }
        defaults.update(row)
        for field, value in defaults.items():
            data[f"otypes-{i}-{field}"] = value
    return data


def describe_orientation_tab_rendering():
    def it_renders_the_empty_states_and_add_buttons(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_render", equipment)
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        assert response.status_code == 200
        assert response.context["active_tab"] == "orientation"
        content = response.content.decode()
        assert "No pending requests." in content
        assert "No orientation types yet. Add one to start taking bookings on the equipment page." in content
        assert "No upcoming times. Add one so members can book." in content
        assert "+ Add Orientation Type" in content
        assert "+ Add a Time" in content
        assert "{ showAdd: false }" in content

    def it_lists_pending_requests_with_review_links(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_pending", equipment)
        booking = OrientationBookingFactory(slot=_owned_slot(_owned_type(equipment)))
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        content = response.content.decode()
        assert booking.member.display_name in content
        assert reverse("hub_orientation_respond", args=[booking.pk]) in content

    def it_shows_attendee_rows_but_counts_holds_anonymously(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_attendees", equipment)
        slot = _owned_slot(_owned_type(equipment))
        requested = OrientationBookingFactory(slot=slot)
        confirmed = OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        hold = OrientationBookingFactory(
            slot=slot, status=OrientationBooking.Status.PENDING_PAYMENT, amount_paid_cents=1500
        )
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        content = response.content.decode()
        assert requested.member.display_name in content
        assert confirmed.member.display_name in content
        assert ">Requested<" in content
        assert ">Confirmed<" in content
        assert reverse("hub_orientation_respond", args=[confirmed.pk]) in content
        # The hold is a count note, never a named attendee row (no respond link for it).
        assert "1 seat held by a checkout in progress" in content
        assert reverse("hub_orientation_respond", args=[hold.pk]) not in content


def describe_orientation_types_save():
    def it_creates_an_equipment_owned_type(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_create", equipment)
        response = client.post(
            reverse("hub_equipment_orientation_types_save", args=[equipment.slug]),
            _types_data([{"name": "Operator Basics", "price": "15"}]),
        )
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=orientation")
        created = equipment.owned_orientation_types.get()
        assert created.guild is None
        assert created.price_cents == 1500

    def it_blocks_deleting_a_type_with_booking_history(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_del_history", equipment)
        orientation_type = _owned_type(equipment)
        OrientationBookingFactory(slot=_owned_slot(orientation_type))
        data = _types_data([{"name": orientation_type.name}], initial=1)
        data["otypes-0-id"] = str(orientation_type.pk)
        data["otypes-0-DELETE"] = "on"
        response = client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), data)
        assert response.status_code == 200
        assert b"booking history" in response.content
        assert OrientationType.objects.filter(pk=orientation_type.pk).exists()

    def it_blocks_deleting_a_type_some_equipment_requires(client: Client):
        equipment = EquipmentFactory(name="CNC Router")
        _manager_login(client, "ot_del_gated", equipment)
        orientation_type = _owned_type(equipment)
        equipment.required_orientation = orientation_type
        equipment.save(update_fields=["required_orientation"])
        data = _types_data([{"name": orientation_type.name}], initial=1)
        data["otypes-0-id"] = str(orientation_type.pk)
        data["otypes-0-DELETE"] = "on"
        response = client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), data)
        assert response.status_code == 200
        assert b"required by CNC Router" in response.content
        assert OrientationType.objects.filter(pk=orientation_type.pk).exists()

    def it_guards_the_guild_types_editor_against_gated_deletes_too(client: Client):
        # The guard lives on the SHARED base formset — a guild type gating equipment
        # used to 500 on the FK's PROTECT from the guild editor.
        lead = _login(client, "ot_guild_gated")
        guild = GuildFactory(guild_lead=lead.member)
        orientation_type = OrientationTypeFactory(guild=guild, name="Shop Basics")
        EquipmentFactory(name="Gated Saw", required_orientation=orientation_type)
        data = {
            "otypes-TOTAL_FORMS": "1",
            "otypes-INITIAL_FORMS": "1",
            "otypes-MIN_NUM_FORMS": "0",
            "otypes-MAX_NUM_FORMS": "1000",
            "otypes-0-id": str(orientation_type.pk),
            "otypes-0-name": orientation_type.name,
            "otypes-0-description": "",
            "otypes-0-duration_minutes": "60",
            "otypes-0-price": "",
            "otypes-0-default_seats": "4",
            "otypes-0-default_location": "",
            "otypes-0-sort_order": "0",
            "otypes-0-is_active": "on",
            "otypes-0-DELETE": "on",
        }
        response = client.post(reverse("hub_guild_orientation_types_save", args=[guild.pk]), data)
        assert response.status_code == 200
        assert b"required by Gated Saw" in response.content
        assert OrientationType.objects.filter(pk=orientation_type.pk).exists()


def describe_orientation_slot_add():
    def _slot_data(orientation_type: OrientationType, **overrides) -> dict:
        starts = timezone.localtime(timezone.now() + timedelta(days=2))
        data = {
            "orientation_type": str(orientation_type.pk),
            "date": starts.date().isoformat(),
            "start_time": "10:00",
            "duration_minutes": "60",
            "seats": "4",
            "location": "By the big saw",
        }
        data.update(overrides)
        return data

    def it_creates_a_manual_guildless_orienterless_slot(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_slot_add", equipment)
        orientation_type = _owned_type(equipment)
        response = client.post(
            reverse("hub_equipment_orientation_slot_add", args=[equipment.slug]), _slot_data(orientation_type)
        )
        assert response.status_code == 302
        slot = orientation_type.slots.get()
        assert slot.guild is None
        assert slot.orienter is None
        assert slot.source == OrientationSlot.Source.MANUAL
        assert slot.location == "By the big saw"

    def it_rerenders_the_reveal_form_open_with_errors_on_invalid_post(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_slot_bad", equipment)
        orientation_type = _owned_type(equipment)
        past = timezone.localtime(timezone.now() - timedelta(days=1))
        response = client.post(
            reverse("hub_equipment_orientation_slot_add", args=[equipment.slug]),
            _slot_data(orientation_type, date=past.date().isoformat()),
        )
        assert response.status_code == 200
        assert response.context["active_tab"] == "orientation"
        content = response.content.decode()
        assert "Pick a time in the future." in content
        assert "{ showAdd: true }" in content  # the bound form comes back OPEN
        assert not orientation_type.slots.exists()

    def it_rejects_another_equipments_type(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_slot_cross", equipment)
        foreign_type = _owned_type(EquipmentFactory())
        response = client.post(
            reverse("hub_equipment_orientation_slot_add", args=[equipment.slug]), _slot_data(foreign_type)
        )
        assert response.status_code == 200
        assert b"Pick one of this equipment&#x27;s orientations." in response.content
        assert not foreign_type.slots.exists()


def describe_orientation_slot_cancel():
    def it_cancels_with_the_full_fan_out(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_cancel", equipment)
        slot = _owned_slot(_owned_type(equipment))
        booker = _member_user("ot_cancel_booker")
        booking = OrientationBookingFactory(slot=slot, member=booker.member)
        mail.outbox.clear()
        response = client.post(reverse("hub_equipment_orientation_slot_cancel", args=[equipment.slug, slot.pk]))
        assert response.status_code == 302
        slot.refresh_from_db()
        booking.refresh_from_db()
        assert slot.is_cancelled is True
        assert booking.status == OrientationBooking.Status.CANCELLED
        assert any("cancelled" in m.subject for m in mail.outbox)

    def it_404s_a_slot_from_another_equipment(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "ot_cancel_cross", equipment)
        foreign_slot = _owned_slot(_owned_type(EquipmentFactory()))
        response = client.post(reverse("hub_equipment_orientation_slot_cancel", args=[equipment.slug, foreign_slot.pk]))
        assert response.status_code == 404
        foreign_slot.refresh_from_db()
        assert foreign_slot.is_cancelled is False


def describe_permission_edges():
    def _endpoints(equipment: Equipment, slot: OrientationSlot) -> list[tuple[str, dict]]:
        return [
            (reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), _types_data([])),
            (reverse("hub_equipment_orientation_slot_add", args=[equipment.slug]), {}),
            (reverse("hub_equipment_orientation_slot_cancel", args=[equipment.slug, slot.pk]), {}),
        ]

    def it_403s_a_plain_member_and_a_random_guild_lead_everywhere(client: Client):
        equipment = EquipmentFactory()
        slot = _owned_slot(_owned_type(equipment))
        booking = OrientationBookingFactory(slot=slot)
        _login(client, "ot_perm_plain")
        for url, data in _endpoints(equipment, slot):
            assert client.post(url, data).status_code == 403
        assert client.get(reverse("hub_orientation_respond", args=[booking.pk])).status_code == 403

        lead = _login(client, "ot_perm_lead")
        GuildFactory(guild_lead=lead.member)
        for url, data in _endpoints(equipment, slot):
            assert client.post(url, data).status_code == 403
        assert client.get(reverse("hub_orientation_respond", args=[booking.pk])).status_code == 403

    def it_lets_every_manager_tier_confirm_a_request(client: Client):
        from membership.models import AdminCapability

        equipment_guild = GuildFactory()
        equipment = EquipmentFactory(guild=equipment_guild)

        manager = _manager_login(client, "ot_perm_row", equipment)
        booking = OrientationBookingFactory(slot=_owned_slot(_owned_type(equipment, name="Row Type")))
        response = client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"})
        assert response.status_code == 302
        booking.refresh_from_db()
        assert booking.status == OrientationBooking.Status.CONFIRMED
        assert booking.oriented_by == manager.member

        lead = _member_user("ot_perm_glead")
        equipment_guild.guild_lead = lead.member
        equipment_guild.save(update_fields=["guild_lead"])
        client.login(username="ot_perm_glead", password="pass")
        booking = OrientationBookingFactory(slot=_owned_slot(_owned_type(equipment, name="Lead Type")))
        assert (
            client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"}).status_code == 302
        )

        holder = _login(client, "ot_perm_cap")
        holder.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        booking = OrientationBookingFactory(slot=_owned_slot(_owned_type(equipment, name="Cap Type")))
        assert (
            client.post(reverse("hub_orientation_respond", args=[booking.pk]), {"action": "confirm"}).status_code == 302
        )


def describe_review_fixes():
    def it_shows_the_equipment_name_in_the_refund_modal(client: Client):
        # The refund modal's item identity must never render blank for guild=None.
        equipment = EquipmentFactory(name="CNC Router")
        _login(client, "rf_refund", fog_role=Member.FogRole.ADMIN)
        booking = OrientationBookingFactory(
            slot=_owned_slot(_owned_type(equipment)),
            amount_paid_cents=1500,
            stripe_payment_id="pi_refund_x",
        )
        response = client.get(reverse("billing_orientation_refund_form", args=[booking.pk]))
        assert response.status_code == 200
        assert "Orientation — CNC Router".encode() in response.content

    def it_rejects_two_new_rows_sharing_a_name_with_a_friendly_error(client: Client):
        # The conditional unique constraint skips Django's cross-form check — the
        # formset guard must catch it before the DB IntegrityErrors.
        equipment = EquipmentFactory()
        _manager_login(client, "rf_dupe", equipment)
        response = client.post(
            reverse("hub_equipment_orientation_types_save", args=[equipment.slug]),
            _types_data([{"name": "Operator Basics"}, {"name": "operator basics"}]),
        )
        assert response.status_code == 200
        assert b"can&#x27;t share the name" in response.content
        assert not equipment.owned_orientation_types.exists()

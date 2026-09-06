"""BDD specs for the equipment manage panel's Orientation tab (equipment-owned orientations).

Types formset (with both delete guards), one-off slot add (bound reveal), slot
cancel fan-out, pending-request list, attendee rows, and the permission edges on
every endpoint including the equipment-gated respond page.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import (
    AdminCapability,
    Equipment,
    Member,
    OrientationAvailability,
    OrientationBooking,
    OrientationSlot,
    OrientationType,
)
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
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
        assert "No upcoming times. They appear as soon as hours are saved, or add a one time slot here." in content
        assert "Orientation Schedule" in content
        assert "No hours published" in content
        assert "+ Add Orientation Type" in content
        assert "+ Add a Time" in content
        assert "{ showAdd: false }" in content
        # The 1.35.0 window editor and day chips are gone for good (the Hours tab's own
        # "+ Add Hours" for reservation hours is unrelated and stays).
        assert 'id="equip-ohours-form"' not in content
        assert "Orientation Hours</h2>" not in content
        assert "pl-orient-days" not in content
        assert "Show later days" not in content

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

    def it_creates_a_manual_guildless_slot_that_runs_with_the_acting_manager(client: Client):
        equipment = EquipmentFactory()
        manager = _manager_login(client, "ot_slot_add", equipment)
        orientation_type = _owned_type(equipment)
        response = client.post(
            reverse("hub_equipment_orientation_slot_add", args=[equipment.slug]), _slot_data(orientation_type)
        )
        assert response.status_code == 302
        slot = orientation_type.slots.get()
        assert slot.guild is None
        assert slot.orienter == manager.member  # a plain manager's slot always runs with them
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
            (
                reverse("hub_equipment_orientation_hours_save", args=[equipment.slug]),
                {"orienter_scope": "", "formset_prefix": "modal_rules"},
            ),
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


# ── Orientation Hours card (equipment-orientation-hours spec, PR 1) ─────────────────


def _tool_day(offset: int = 2) -> date:
    return timezone.localdate() + timedelta(days=offset)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _tool_rule(orientation_type: OrientationType, **overrides) -> OrientationAvailability:
    defaults = {
        "weekday": _tool_day().weekday(),
        "start_time": time(10, 0),
        "end_time": time(12, 0),
        "slot_minutes": 60,
        "buffer_minutes": 0,
        "seats": 1,
    }
    defaults.update(overrides)
    return OrientationAvailabilityFactory(equipment_owned=True, orientation_type=orientation_type, **defaults)


def _generated_slot(rule: OrientationAvailability, **overrides) -> OrientationSlot:
    return OrientationSlotFactory(
        equipment_owned=True,
        orientation_type=rule.orientation_type,
        availability=rule,
        source=OrientationSlot.Source.GENERATED,
        **overrides,
    )


def describe_blocked_rows():
    """A slot posted over a confirmed reservation renders muted with who holds the machine (PR 2)."""

    def it_marks_the_slot_under_a_reservation_and_leaves_the_rest_alone(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_blocked", equipment)
        orientation_type = _owned_type(equipment)
        day = _tool_day()
        blocked = OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(day, 10), ends_at=_at(day, 11)
        )
        clear = OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(day, 13), ends_at=_at(day, 14)
        )
        touching = OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(day, 12), ends_at=_at(day, 13)
        )
        reserver = _member_user("oh_blocked_sam")
        reserver.member.full_legal_name = "Sam Reyes"
        reserver.member.save(update_fields=["full_legal_name"])
        reservation = EquipmentReservationFactory(
            equipment=equipment, member=reserver.member, starts_at=_at(day, 10), ends_at=_at(day, 12)
        )
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        by_pk = {slot.pk: slot for slot in response.context["orientation_upcoming_slots"]}
        assert by_pk[blocked.pk].blocking_reservation == reservation
        assert by_pk[clear.pk].blocking_reservation is None
        assert by_pk[touching.pk].blocking_reservation is None  # 12:00 start meets the 12:00 end: not blocked
        content = response.content.decode()
        assert "Blocked by Sam Reyes's reservation 10:00 AM to 12:00 PM" in content
        assert content.count("pl-equip-res-row--blocked") == 1
        # The member picker never offers the blocked slot while the reservation stands.
        assert blocked not in OrientationSlot.objects.bookable()
        assert clear in OrientationSlot.objects.bookable()
        assert touching in OrientationSlot.objects.bookable()


# ── The guild pattern on equipment (equipment-orientations-guild-pattern spec) ────────


def _modal_rules(rows: list[dict], *, scope: str, initial: int = 0) -> dict:
    data = {
        "orienter_scope": scope,
        "formset_prefix": "modal_rules",
        "modal_rules-TOTAL_FORMS": str(len(rows)),
        "modal_rules-INITIAL_FORMS": str(initial),
        "modal_rules-MIN_NUM_FORMS": "0",
        "modal_rules-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for field, value in row.items():
            data[f"modal_rules-{i}-{field}"] = value
    return data


def _rule_row(
    orientation_type: OrientationType,
    *,
    weekday: int,
    start: str = "18:00",
    end: str = "20:00",
    seats: str = "1",
    slot: str = "",
    gap: str = "",
    active: bool = True,
    **extra,
) -> dict:
    row = {
        "orientation_type": str(orientation_type.pk),
        "weekday": str(weekday),
        "start_time": start,
        "end_time": end,
        "seats": seats,
        "slot_minutes": slot,
        "buffer_minutes": gap,
    }
    if active:
        row["is_active"] = "on"
    row.update(extra)
    return row


def _hours_form_url(equipment: Equipment, orienter=None) -> str:
    url = reverse("hub_equipment_orientation_hours_form", args=[equipment.slug])
    return f"{url}?orienter={orienter.pk}" if orienter is not None else url


def _hours_save_url(equipment: Equipment) -> str:
    return reverse("hub_equipment_orientation_hours_save", args=[equipment.slug])


def _tab(client: Client, equipment: Equipment):
    return client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})


def _named_manager(client: Client, username: str, equipment: Equipment, name: str) -> User:
    user = _manager_login(client, username, equipment)
    user.member.full_legal_name = name
    user.member.save(update_fields=["full_legal_name"])
    return user


def _personal_rule(orientation_type: OrientationType, orienter, **overrides) -> OrientationAvailability:
    defaults = {
        "weekday": _tool_day().weekday(),
        "start_time": time(18, 0),
        "end_time": time(20, 0),
        "seats": 1,
        "slot_minutes": None,
    }
    defaults.update(overrides)
    return OrientationAvailabilityFactory(
        equipment_owned=True, orientation_type=orientation_type, orienter=orienter, **defaults
    )


def describe_orientation_schedule_card():
    def it_shows_a_lead_level_viewer_every_manager_with_their_rules(client: Client):
        equipment = EquipmentFactory()
        _login(client, "sc_admin", fog_role=Member.FogRole.ADMIN)
        orientation_type = _owned_type(equipment)
        dana = _member_user("sc_dana")
        dana.member.full_legal_name = "Dana Reyes"
        dana.member.save(update_fields=["full_legal_name"])
        EquipmentStaffMembershipFactory(equipment=equipment, member=dana.member)
        quiet = _member_user("sc_quiet")
        EquipmentStaffMembershipFactory(equipment=equipment, member=quiet.member)
        _personal_rule(
            orientation_type, dana.member, weekday=5, start_time=time(10, 0), end_time=time(18, 0), slot_minutes=60
        )
        response = _tab(client, equipment)
        content = response.content.decode()
        assert "Orientation Schedule" in content
        assert "Everyone who manages this equipment, and when they give orientations." in content
        assert "Dana Reyes" in content
        assert "Operator Basics · Saturday · 10:00 a.m. to 6:00 p.m. · 1 seat · 60 min slots" in content
        assert "No hours published" in content  # the quiet manager's group
        assert 'id="edit-hours-modal-body"' in content  # the shared Edit Hours modal shell, loaded per scope
        assert content.count(_hours_form_url(equipment, dana.member)) == 1
        assert response.context["can_edit_others_hours"] is True
        assert "pl-orient-days" not in content

    def it_shows_a_plain_manager_only_their_own_group(client: Client):
        equipment = EquipmentFactory()
        me = _named_manager(client, "sc_me", equipment, "Dana Reyes")
        other = _member_user("sc_other")
        other.member.full_legal_name = "Quinn Other"
        other.member.save(update_fields=["full_legal_name"])
        EquipmentStaffMembershipFactory(equipment=equipment, member=other.member)
        response = _tab(client, equipment)
        content = response.content.decode()
        assert "Your weekly orientation hours. Members book you by name." in content
        assert _hours_form_url(equipment, me.member) in content
        assert _hours_form_url(equipment, other.member) not in content
        assert response.context["can_edit_others_hours"] is False
        assert response.context["slot_form_locked"] is True

    def it_lists_former_managers_orphan_rules_for_the_lead_tier_only(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        stranger = MemberFactory(full_legal_name="Gone Person")
        _personal_rule(orientation_type, stranger)
        _login(client, "sc_former_admin", fog_role=Member.FogRole.ADMIN)
        content = _tab(client, equipment).content.decode()
        assert "Former Managers" in content
        assert "Gone Person" in content
        assert _hours_form_url(equipment, stranger) in content
        _manager_login(client, "sc_former_plain", equipment)
        content = _tab(client, equipment).content.decode()
        assert "Former Managers" not in content

    def it_shows_the_shared_hours_card_only_when_shared_rows_exist(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _manager_login(client, "sc_shared_plain", equipment)
        assert "Shared Hours (Any Manager)" not in _tab(client, equipment).content.decode()
        _tool_rule(orientation_type)  # orienter-less: a row from the old window editor
        content = _tab(client, equipment).content.decode()
        assert "Shared Hours (Any Manager)" in content
        assert "Only an administrator or the owning guild's lead can change shared hours." in content
        assert "Edit Shared Hours" not in content
        _login(client, "sc_shared_admin", fog_role=Member.FogRole.ADMIN)
        content = _tab(client, equipment).content.decode()
        assert "Edit Shared Hours" in content
        assert _hours_form_url(equipment) in content

    def it_treats_an_admin_as_a_former_manager_until_they_are_added_on_the_staff_tab(client: Client):
        """The site-wide EQUIPMENT capability is authority over every tool, not a slot on its roster.

        Listing every capability holder here is what put thirteen council members on the
        Orientation Schedule of a machine with no assigned staff at all.
        """
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        admin = _login(client, "sc_plain_admin", fog_role=Member.FogRole.ADMIN)
        admin.member.full_legal_name = "Ada Admin"
        admin.member.save(update_fields=["full_legal_name"])
        _personal_rule(orientation_type, admin.member)
        response = _tab(client, equipment)
        content = response.content.decode()
        assert "Former Managers" in content
        assert "Ada Admin" in content
        assert [m.pk for m, _rules in response.context["orienter_overview"]] == []
        runs_with = response.context["slot_add_form"].fields["orienter"]
        assert admin.member.pk not in [m.pk for m in runs_with.queryset]
        assert runs_with.initial is None
        # The capability alone changes nothing about the roster.
        admin.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        response = _tab(client, equipment)
        assert "Former Managers" in response.content.decode()
        assert [m.pk for m, _rules in response.context["orienter_overview"]] == []
        # Being added as a manager of this tool does.
        EquipmentStaffMembershipFactory(equipment=equipment, member=admin.member)
        response = _tab(client, equipment)
        assert "Former Managers" not in response.content.decode()
        assert [m.pk for m, _rules in response.context["orienter_overview"]] == [admin.member.pk]
        runs_with = response.context["slot_add_form"].fields["orienter"]
        assert runs_with.initial == admin.member.pk

    def it_names_the_staff_tab_when_nobody_runs_orientations_yet(client: Client):
        equipment = EquipmentFactory()
        _owned_type(equipment)
        _login(client, "sc_empty_roster", fog_role=Member.FogRole.ADMIN)
        content = _tab(client, equipment).content.decode()
        assert "Nobody is set up to give orientations on this equipment yet." in content
        assert "Staff tab" in content

    def it_lists_the_owning_guilds_leadership(client: Client):
        guild = GuildFactory()
        staffer = MemberFactory(full_legal_name="Gwen Guild")
        GuildStaffMembershipFactory(guild=guild, member=staffer)
        equipment = EquipmentFactory(guild=guild)
        _owned_type(equipment)
        _login(client, "sc_guild_roster", fog_role=Member.FogRole.ADMIN)
        response = _tab(client, equipment)
        assert staffer.pk in [m.pk for m, _rules in response.context["orienter_overview"]]

    def it_404s_the_removed_window_endpoint(client: Client):
        equipment = EquipmentFactory()
        _login(client, "sc_old_url", fog_role=Member.FogRole.ADMIN)
        response = client.post(f"/equipment/{equipment.slug}/manage/orientation/hours/", {})
        assert response.status_code == 404


def describe_orientation_hours_form_view():
    def it_serves_a_manager_their_own_modal_with_the_two_new_fields(client: Client):
        equipment = EquipmentFactory()
        _owned_type(equipment)
        me = _named_manager(client, "hf_me", equipment, "Dana Reyes")
        response = client.get(_hours_form_url(equipment, me.member))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Editing Dana Reyes's Hours" in content
        assert "Slot length" in content
        assert "Break between slots" in content
        assert "Whole window" in content
        assert "+ Add hours" in content
        assert _hours_save_url(equipment) in content

    def it_403s_a_plain_manager_opening_someone_elses_or_the_shared_scope(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "hf_plain", equipment)
        other = _member_user("hf_other")
        EquipmentStaffMembershipFactory(equipment=equipment, member=other.member)
        assert client.get(_hours_form_url(equipment, other.member)).status_code == 403
        assert client.get(_hours_form_url(equipment)).status_code == 403

    def it_serves_the_shared_scope_to_the_lead_tier_without_an_add_button(client: Client):
        equipment_guild = GuildFactory()
        equipment = EquipmentFactory(guild=equipment_guild)
        _tool_rule(_owned_type(equipment))
        lead = _member_user("hf_glead")
        equipment_guild.guild_lead = lead.member
        equipment_guild.save(update_fields=["guild_lead"])
        client.login(username="hf_glead", password="pass")
        response = client.get(_hours_form_url(equipment))
        assert response.status_code == 200
        content = response.content.decode()
        assert "Editing Shared Hours" in content
        assert "+ Add hours" not in content
        holder = _login(client, "hf_holder")
        holder.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert client.get(_hours_form_url(equipment)).status_code == 200

    def it_names_the_one_way_door_on_a_shared_rows_delete_confirm(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _tool_rule(orientation_type)
        dana = _member_user("hf_door_dana")
        EquipmentStaffMembershipFactory(equipment=equipment, member=dana.member)
        _personal_rule(orientation_type, dana.member)
        _login(client, "hf_door_admin", fog_role=Member.FogRole.ADMIN)
        shared = client.get(_hours_form_url(equipment)).content.decode()
        assert "Delete these shared hours?" in shared
        assert "Shared recurring hours cannot be recreated." in shared
        personal = client.get(_hours_form_url(equipment, dana.member)).content.decode()
        assert "Delete these hours?" in personal
        assert "Delete these shared hours?" not in personal

    def it_round_trips_an_off_list_slot_length_and_names_the_equipment_on_a_foreign_type(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        shared = _tool_rule(orientation_type, slot_minutes=75)
        _login(client, "hf_75_admin", fog_role=Member.FogRole.ADMIN)
        content = client.get(_hours_form_url(equipment)).content.decode()
        assert '<option value="75" selected>75 minutes</option>' in content
        row = {
            "id": str(shared.pk),
            **_rule_row(orientation_type, weekday=shared.weekday, start="10:00", end="12:00", slot="75"),
        }
        response = client.post(
            _hours_save_url(equipment), _modal_rules([row], scope="", initial=1), HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 204
        shared.refresh_from_db()
        assert shared.slot_minutes == 75
        foreign = _owned_type(EquipmentFactory(), name="Foreign")
        response = client.post(
            _hours_save_url(equipment),
            _modal_rules([{**row, "orientation_type": str(foreign.pk)}], scope="", initial=1),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "Pick one of this equipment&#x27;s orientations." in response.content.decode()
        shared.refresh_from_db()
        assert shared.orientation_type == orientation_type

    def it_404s_garbage_scopes(client: Client):
        equipment = EquipmentFactory()
        _login(client, "hf_garbage", fog_role=Member.FogRole.ADMIN)
        assert client.get(f"{_hours_form_url(equipment)}?orienter=abc").status_code == 404
        assert client.get(f"{_hours_form_url(equipment)}?orienter=999999").status_code == 404


def describe_orientation_hours_save_view():
    def it_lets_a_manager_post_their_own_two_windows(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        dana = _named_manager(client, "hs_dana", equipment, "Dana Reyes")
        friday, saturday = _tool_day(2).weekday(), _tool_day(3).weekday()
        response = client.post(
            _hours_save_url(equipment),
            _modal_rules(
                [
                    _rule_row(orientation_type, weekday=friday, start="18:00", end="20:00"),
                    _rule_row(orientation_type, weekday=saturday, start="12:00", end="16:00"),
                ],
                scope=str(dana.member.pk),
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert response["HX-Redirect"].endswith("?tab=orientation")
        rules = list(OrientationAvailability.objects.for_equipment(equipment).order_by("start_time"))
        assert [(rule.weekday, rule.orienter, rule.guild, rule.slot_minutes) for rule in rules] == [
            (saturday, dana.member, None, None),
            (friday, dana.member, None, None),
        ]
        # Blank slot length: one slot per window, run "with Dana".
        slots = OrientationSlot.objects.filter(orientation_type=orientation_type)
        assert slots.count() == 16
        first = slots.order_by("starts_at").first()
        assert first is not None
        assert first.orienter == dana.member
        assert first.with_label == "with Dana"
        assert first.ends_at - first.starts_at in (timedelta(hours=2), timedelta(hours=4))
        followup = _tab(client, equipment)
        assert "Hours saved." in " ".join(str(m) for m in followup.context["messages"])

    def it_carves_a_window_when_a_slot_length_is_set(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        dana = _manager_login(client, "hs_carve", equipment)
        response = client.post(
            _hours_save_url(equipment),
            _modal_rules(
                [_rule_row(orientation_type, weekday=_tool_day().weekday(), start="18:00", end="20:00", slot="60")],
                scope=str(dana.member.pk),
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        rule = OrientationAvailability.objects.for_equipment(equipment).get()
        assert rule.slot_minutes == 60
        assert rule.slots.count() == 16  # two hourly slots per occurrence

    def it_403s_a_plain_manager_posting_anothers_or_the_shared_scope(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _manager_login(client, "hs_plain", equipment)
        other = _member_user("hs_other")
        EquipmentStaffMembershipFactory(equipment=equipment, member=other.member)
        for scope in (str(other.member.pk), ""):
            response = client.post(
                _hours_save_url(equipment),
                _modal_rules([_rule_row(orientation_type, weekday=5)], scope=scope),
                HTTP_HX_REQUEST="true",
            )
            assert response.status_code == 403
        assert not OrientationAvailability.objects.for_equipment(equipment).exists()

    def it_lets_the_lead_tier_save_on_behalf_and_respects_view_as(client: Client):
        from hub.view_as import ROLE_MEMBER, SESSION_ROLE_KEY

        equipment_guild = GuildFactory()
        equipment = EquipmentFactory(guild=equipment_guild)
        orientation_type = _owned_type(equipment)
        dana = _member_user("hs_target")
        EquipmentStaffMembershipFactory(equipment=equipment, member=dana.member)
        payload = _modal_rules([_rule_row(orientation_type, weekday=5)], scope=str(dana.member.pk))

        _login(client, "hs_admin", fog_role=Member.FogRole.ADMIN)
        session = client.session
        session[SESSION_ROLE_KEY] = ROLE_MEMBER
        session.save()
        assert client.post(_hours_save_url(equipment), payload, HTTP_HX_REQUEST="true").status_code == 403
        session = client.session
        del session[SESSION_ROLE_KEY]
        session.save()
        assert client.post(_hours_save_url(equipment), payload, HTTP_HX_REQUEST="true").status_code == 204
        OrientationAvailability.objects.for_equipment(equipment).delete()

        holder = _login(client, "hs_holder")
        holder.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert client.post(_hours_save_url(equipment), payload, HTTP_HX_REQUEST="true").status_code == 204
        OrientationAvailability.objects.for_equipment(equipment).delete()

        lead = _member_user("hs_glead")
        equipment_guild.guild_lead = lead.member
        equipment_guild.save(update_fields=["guild_lead"])
        client.login(username="hs_glead", password="pass")
        assert client.post(_hours_save_url(equipment), payload, HTTP_HX_REQUEST="true").status_code == 204
        assert OrientationAvailability.objects.for_equipment(equipment).get().orienter == dana.member

    def it_re_renders_the_modal_with_errors_on_an_invalid_post(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        dana = _manager_login(client, "hs_invalid", equipment)
        response = client.post(
            _hours_save_url(equipment),
            _modal_rules(
                [_rule_row(orientation_type, weekday=5, start="18:00", end="18:30", slot="60")],
                scope=str(dana.member.pk),
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "This window is shorter than one slot." in content
        assert 'id="edit-hours-modal-form"' in content
        assert f'hx-post="{_hours_save_url(equipment)}"' in content  # the re-render still posts to the right place
        assert not OrientationAvailability.objects.for_equipment(equipment).exists()

    def it_404s_a_non_htmx_post_and_an_unlisted_prefix(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        dana = _manager_login(client, "hs_prefix", equipment)
        payload = _modal_rules([_rule_row(orientation_type, weekday=5)], scope=str(dana.member.pk))
        assert client.post(_hours_save_url(equipment), payload).status_code == 404
        assert (
            client.post(
                _hours_save_url(equipment), {**payload, "formset_prefix": "rules"}, HTTP_HX_REQUEST="true"
            ).status_code
            == 404
        )

    def it_retires_a_shared_row_with_the_one_way_door_farewell(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        shared = _tool_rule(orientation_type)
        open_slot = _generated_slot(shared)
        _login(client, "hs_shared_admin", fog_role=Member.FogRole.ADMIN)
        response = client.post(
            _hours_save_url(equipment),
            _modal_rules(
                [
                    {
                        "id": str(shared.pk),
                        **_rule_row(orientation_type, weekday=shared.weekday, start="10:00", end="12:00", DELETE="on"),
                    }
                ],
                scope="",
                initial=1,
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert not OrientationAvailability.objects.filter(pk=shared.pk).exists()
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        joined = " ".join(str(m) for m in _tab(client, equipment).context["messages"])
        assert "Shared hours deleted. From now on recurring hours are personal." in joined
        assert "Use an Any manager one time slot for shared coverage." in joined

    def it_lets_the_lead_tier_retire_a_former_managers_rule(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        stranger = MemberFactory(full_legal_name="Gone Person")
        rule = _personal_rule(orientation_type, stranger)
        _login(client, "hs_former", fog_role=Member.FogRole.ADMIN)
        response = client.post(
            _hours_save_url(equipment),
            _modal_rules(
                [{"id": str(rule.pk), **_rule_row(orientation_type, weekday=rule.weekday, DELETE="on")}],
                scope=str(stranger.pk),
                initial=1,
            ),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 204
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()


def describe_upcoming_slots_flat_list():
    def it_lists_slots_with_the_with_chip_attendee_rows_and_source_tags(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "us_flat", equipment)
        orientation_type = _owned_type(equipment)
        dana = MemberFactory(full_legal_name="Dana Reyes")
        EquipmentStaffMembershipFactory(equipment=equipment, member=dana)
        personal = OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            orienter=dana,
            source=OrientationSlot.Source.GENERATED,
        )
        attendee = OrientationBookingFactory(slot=personal)
        OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        response = _tab(client, equipment)
        content = response.content.decode()
        assert "Dana Reyes" in content
        assert "Any manager" in content
        assert ">recurring<" in content
        assert ">one time<" in content
        assert attendee.member.display_name in content
        assert reverse("hub_orientation_respond", args=[attendee.pk]) in content
        assert "{ page: 0, size: 5, total: 2 }" in content
        assert "pl-orient-daygroup" not in content
        assert "Show later days" not in content

    def it_hides_detached_retired_rows(client: Client):
        from membership import orientations

        equipment = EquipmentFactory()
        _manager_login(client, "us_detached", equipment)
        orientation_type = _owned_type(equipment)
        rule = _tool_rule(orientation_type)
        slot = _generated_slot(rule, starts_at=_at(_tool_day(), 10), ends_at=_at(_tool_day(), 11))
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.DECLINED)
        assert orientations.retire_open_slots(rule) == (1, 0)
        response = _tab(client, equipment)
        assert response.context["orientation_upcoming_slots"] == []
        assert "No upcoming times." in response.content.decode()


def describe_add_a_time_runs_with():
    def _slot_data(orientation_type: OrientationType, **overrides) -> dict:
        starts = timezone.localtime(timezone.now() + timedelta(days=2))
        data = {
            "orientation_type": str(orientation_type.pk),
            "date": starts.date().isoformat(),
            "start_time": "10:00",
            "duration_minutes": "60",
            "seats": "4",
            "location": "",
        }
        data.update(overrides)
        return data

    def it_fixes_a_plain_manager_to_themselves_whatever_the_post_says(client: Client):
        equipment = EquipmentFactory()
        me = _manager_login(client, "rw_plain", equipment)
        other = _member_user("rw_other")
        EquipmentStaffMembershipFactory(equipment=equipment, member=other.member)
        orientation_type = _owned_type(equipment)
        content = _tab(client, equipment).content.decode()
        assert "Runs with: you" in content
        response = client.post(
            reverse("hub_equipment_orientation_slot_add", args=[equipment.slug]),
            _slot_data(orientation_type, orienter=str(other.member.pk)),
        )
        assert response.status_code == 302
        assert orientation_type.slots.get().orienter == me.member

    def it_lets_the_lead_tier_pick_any_manager_or_a_manager(client: Client):
        equipment = EquipmentFactory()
        _login(client, "rw_admin", fog_role=Member.FogRole.ADMIN)
        orientation_type = _owned_type(equipment)
        dana = MemberFactory(full_legal_name="Dana Reyes")
        EquipmentStaffMembershipFactory(equipment=equipment, member=dana)
        content = _tab(client, equipment).content.decode()
        assert "Runs with: you" not in content
        assert "Any manager" in content
        url = reverse("hub_equipment_orientation_slot_add", args=[equipment.slug])
        assert client.post(url, _slot_data(orientation_type, orienter="")).status_code == 302
        assert (
            client.post(url, _slot_data(orientation_type, start_time="12:00", orienter=str(dana.pk))).status_code == 302
        )
        assert sorted(slot.orienter_id for slot in orientation_type.slots.all() if slot.orienter_id) == [dana.pk]
        assert orientation_type.slots.filter(orienter=None).count() == 1

    def it_rejects_a_non_manager(client: Client):
        equipment = EquipmentFactory()
        _login(client, "rw_reject", fog_role=Member.FogRole.ADMIN)
        orientation_type = _owned_type(equipment)
        stranger = MemberFactory()
        response = client.post(
            reverse("hub_equipment_orientation_slot_add", args=[equipment.slug]),
            _slot_data(orientation_type, orienter=str(stranger.pk)),
        )
        assert response.status_code == 200
        assert b"Pick someone who manages this equipment." in response.content
        assert not orientation_type.slots.exists()


def describe_one_time_slot_form():
    def _slot_form(equipment: Equipment, orientation_type: OrientationType, start: str):
        from hub.forms import EquipmentOrientationSlotForm

        return EquipmentOrientationSlotForm(
            {
                "orientation_type": str(orientation_type.pk),
                "date": _tool_day().isoformat(),
                "start_time": start,
                "duration_minutes": "60",
                "seats": "1",
                "location": "",
            },
            equipment=equipment,
        )

    def it_rejects_a_time_overlapping_an_existing_slot():
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=_at(_tool_day(), 10),
            ends_at=_at(_tool_day(), 11),
        )
        form = _slot_form(equipment, orientation_type, "10:30")
        assert not form.is_valid()
        assert form.errors["start_time"] == ["That time overlaps another orientation time on this tool."]

    def it_accepts_a_touching_time_and_defaults_to_any_manager():
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=_at(_tool_day(), 10),
            ends_at=_at(_tool_day(), 11),
        )
        form = _slot_form(equipment, orientation_type, "11:00")
        assert form.is_valid()
        assert form.cleaned_data["orienter"] is None

    def it_saves_the_slot_outright_when_asked_to_commit():
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        form = _slot_form(equipment, orientation_type, "10:00")
        assert form.is_valid()
        slot = form.save()
        assert slot.pk is not None
        assert slot.orientation_type == orientation_type
        assert slot.orienter is None

    def it_seeds_the_location_from_the_types_default():
        from hub.forms import EquipmentOrientationSlotForm

        equipment = EquipmentFactory()
        _owned_type(equipment, default_location="Bay 2")
        assert EquipmentOrientationSlotForm(equipment=equipment).fields["location"].initial == "Bay 2"

    def it_skips_the_overlap_check_when_the_fields_are_incomplete():
        from hub.forms import EquipmentOrientationSlotForm

        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        form = EquipmentOrientationSlotForm(
            {
                "orientation_type": str(orientation_type.pk),
                "date": "",
                "start_time": "10:00",
                "duration_minutes": "60",
                "seats": "1",
                "location": "",
            },
            equipment=equipment,
        )
        assert not form.is_valid()
        assert "starts_at" not in form.cleaned_data


def describe_staff_remove_retirement():
    def it_retires_a_removed_managers_rules_and_flags_booked_slots(client: Client):
        equipment = EquipmentFactory()
        _login(client, "sr_admin", fog_role=Member.FogRole.ADMIN)
        orientation_type = _owned_type(equipment)
        dana = MemberFactory(full_legal_name="Dana Reyes")
        row = EquipmentStaffMembershipFactory(equipment=equipment, member=dana)
        rule = _personal_rule(orientation_type, dana)
        open_slot = _generated_slot(rule, orienter=dana)
        booked = _generated_slot(rule, orienter=dana, starts_at=timezone.now() + timedelta(days=3))
        OrientationBookingFactory(slot=booked)
        response = client.post(reverse("hub_equipment_staff_remove", args=[equipment.slug, row.pk]), follow=True)
        assert response.status_code == 200
        joined = " ".join(str(m) for m in response.context["messages"])
        assert "Dana Reyes no longer manages the" in joined
        assert "They still have 1 upcoming booked orientation." in joined
        assert "Upcoming Slots card on the Orientation tab" in joined
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        assert OrientationSlot.objects.filter(pk=booked.pk).exists()

    def it_keeps_the_rules_of_a_manager_who_is_still_owning_guild_staff(client: Client):
        from tests.membership.factories import GuildStaffMembershipFactory

        equipment_guild = GuildFactory()
        equipment = EquipmentFactory(guild=equipment_guild)
        _login(client, "sr_still", fog_role=Member.FogRole.ADMIN)
        orientation_type = _owned_type(equipment)
        dana = MemberFactory()
        GuildStaffMembershipFactory(guild=equipment_guild, member=dana)
        row = EquipmentStaffMembershipFactory(equipment=equipment, member=dana)
        rule = _personal_rule(orientation_type, dana)
        response = client.post(reverse("hub_equipment_staff_remove", args=[equipment.slug, row.pk]), follow=True)
        assert response.status_code == 200
        assert OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert "They still have" not in " ".join(str(m) for m in response.context["messages"])

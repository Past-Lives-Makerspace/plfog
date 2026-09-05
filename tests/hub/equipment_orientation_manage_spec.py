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
    EquipmentStaffMembershipFactory,
    GuildFactory,
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
        assert "No upcoming times. Add hours above, or add a one time slot here." in content
        assert "No orientation hours yet. Add a window and members can start booking." in content
        assert "+ Add Orientation Type" in content
        assert "+ Add Hours" in content
        assert 'id="equip-ohours-empty-template"' in content
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


# ── Orientation Hours card (equipment-orientation-hours spec, PR 1) ─────────────────


def _tool_day(offset: int = 2) -> date:
    return timezone.localdate() + timedelta(days=offset)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def _ohours_data(rows: list[dict], initial: int = 0) -> dict:
    data = {
        "ohours-TOTAL_FORMS": str(len(rows)),
        "ohours-INITIAL_FORMS": str(initial),
        "ohours-MIN_NUM_FORMS": "0",
        "ohours-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for field, value in row.items():
            data[f"ohours-{i}-{field}"] = value
    return data


def _ohours_row(
    orientation_type: OrientationType,
    *,
    start: str = "10:00",
    end: str = "12:00",
    days: list[str] | None = None,
    slot: str = "60",
    gap: str = "0",
    seats: str = "1",
    active: bool = True,
    **extra,
) -> dict:
    row = {
        "orientation_type": str(orientation_type.pk),
        "start_time": start,
        "end_time": end,
        "days": days if days is not None else [str(_tool_day().weekday())],
        "slot_minutes": slot,
        "buffer_minutes": gap,
        "seats": seats,
    }
    if active:
        row["is_active"] = "on"
    row.update(extra)
    return row


def _hours_url(equipment: Equipment) -> str:
    return reverse("hub_equipment_orientation_hours_save", args=[equipment.slug])


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


def describe_orientation_hours_form():
    def _formset(equipment: Equipment, rows: list[dict], initial: int = 0):
        from hub.forms import EquipmentOrientationHoursWindowFormSet

        return EquipmentOrientationHoursWindowFormSet(
            _ohours_data(rows, initial),
            prefix="ohours",
            initial=equipment.orientation_hours_windows(),
            form_kwargs={"equipment": equipment},
        )

    def it_rejects_overlapping_windows_on_a_shared_weekday_across_types():
        equipment = EquipmentFactory()
        first = _owned_type(equipment, name="Basics")
        second = _owned_type(equipment, name="Advanced")
        formset = _formset(
            equipment,
            [_ohours_row(first, days=["5"]), _ohours_row(second, start="11:00", end="13:00", days=["5", "6"])],
        )
        assert not formset.is_valid()
        assert formset.non_form_errors() == ["Those hours overlap on Saturday."]

    def it_accepts_touching_windows():
        equipment = EquipmentFactory()
        first = _owned_type(equipment, name="Basics")
        second = _owned_type(equipment, name="Advanced")
        formset = _formset(
            equipment, [_ohours_row(first, days=["5"]), _ohours_row(second, start="12:00", end="14:00", days=["5"])]
        )
        assert formset.is_valid()

    def it_requires_at_least_one_day():
        equipment = EquipmentFactory()
        formset = _formset(equipment, [_ohours_row(_owned_type(equipment), days=[])])
        assert not formset.is_valid()
        assert formset.forms[0].errors["days"] == ["Pick at least one day."]

    def it_rejects_a_slot_longer_than_the_window():
        equipment = EquipmentFactory()
        formset = _formset(equipment, [_ohours_row(_owned_type(equipment), end="10:30")])
        assert not formset.is_valid()
        assert formset.forms[0].errors["slot_minutes"] == ["This window is shorter than one slot."]

    def it_rejects_another_equipments_type():
        equipment = EquipmentFactory()
        _owned_type(equipment)
        formset = _formset(equipment, [_ohours_row(_owned_type(EquipmentFactory()))])
        assert not formset.is_valid()
        assert formset.forms[0].errors["orientation_type"] == ["Pick one of this equipment's orientations."]

    def it_rejects_an_end_before_the_start():
        equipment = EquipmentFactory()
        formset = _formset(equipment, [_ohours_row(_owned_type(equipment), start="12:00", end="10:00")])
        assert not formset.is_valid()
        assert formset.forms[0].errors["end_time"] == ["The end time must be after the start time."]
        assert "slot_minutes" not in formset.forms[0].errors

    def it_skips_a_removed_clone_row():
        equipment = EquipmentFactory()
        formset = _formset(equipment, [_ohours_row(_owned_type(equipment)), {}])
        assert formset.is_valid()
        assert len([form for form in formset if form.cleaned_data]) == 1

    def it_round_trips_an_off_list_duration_as_its_own_choice():
        from hub.forms import EquipmentOrientationHoursWindowForm

        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment, duration_minutes=75)
        form = EquipmentOrientationHoursWindowForm(
            equipment=equipment, initial={"orientation_type": orientation_type.pk, "slot_minutes": 75}
        )
        assert dict(form.fields["slot_minutes"].choices)["75"] == "75 minutes"
        formset = _formset(equipment, [_ohours_row(orientation_type, slot="75", end="12:30")])
        assert formset.is_valid()
        assert formset.forms[0].cleaned_data["slot_minutes"] == 75

    def it_seeds_data_attributes_on_the_type_options():
        from hub.forms import EquipmentOrientationHoursWindowForm

        equipment = EquipmentFactory()
        _owned_type(equipment, duration_minutes=45, default_seats=2)
        html = str(EquipmentOrientationHoursWindowForm(equipment=equipment)["orientation_type"])
        assert 'data-duration="45"' in html
        assert 'data-seats="2"' in html

    def it_keeps_a_saved_row_under_a_retired_type_valid():
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _tool_rule(orientation_type)
        orientation_type.is_active = False
        orientation_type.save(update_fields=["is_active"])
        formset = _formset(equipment, [_ohours_row(orientation_type)], initial=1)
        assert formset.is_valid()

    def it_rejects_an_unknown_slot_length_without_the_window_check():
        equipment = EquipmentFactory()
        formset = _formset(equipment, [_ohours_row(_owned_type(equipment), slot="999")])
        assert not formset.is_valid()
        assert "slot_minutes" in formset.forms[0].errors
        assert "This window is shorter than one slot." not in formset.forms[0].errors["slot_minutes"]

    def it_leaves_a_plain_option_without_data_attributes():
        from hub.forms import _OrientationTypeSelect

        option = _OrientationTypeSelect().create_option("orientation_type", "", "", False, 0)
        assert "data-duration" not in option["attrs"]
        assert "data-seats" not in option["attrs"]

    def it_appends_a_legacy_off_grid_time():
        from hub.forms import EquipmentOrientationHoursWindowForm

        equipment = EquipmentFactory()
        form = EquipmentOrientationHoursWindowForm(equipment=equipment, initial={"start_time": "09:15"})
        assert dict(form.fields["start_time"].choices)["09:15"] == "9:15 AM"

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

        def it_seeds_the_location_from_the_types_default():
            from hub.forms import EquipmentOrientationSlotForm

            equipment = EquipmentFactory()
            _owned_type(equipment, default_location="Bay 2")
            form = EquipmentOrientationSlotForm(equipment=equipment)
            assert form.fields["location"].initial == "Bay 2"

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
            assert "date" in form.errors
            assert "starts_at" not in form.cleaned_data

        def it_accepts_a_touching_time():
            equipment = EquipmentFactory()
            orientation_type = _owned_type(equipment)
            OrientationSlotFactory(
                equipment_owned=True,
                orientation_type=orientation_type,
                starts_at=_at(_tool_day(), 10),
                ends_at=_at(_tool_day(), 11),
            )
            assert _slot_form(equipment, orientation_type, "11:00").is_valid()


def describe_orientation_hours_save():
    def _messages(client: Client, equipment: Equipment) -> str:
        followup = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        return " ".join(str(message) for message in followup.context["messages"])

    def it_403s_a_plain_member_and_a_random_guild_lead(client: Client):
        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _login(client, "oh_perm_plain")
        assert client.post(_hours_url(equipment), _ohours_data([_ohours_row(orientation_type)])).status_code == 403
        lead = _login(client, "oh_perm_lead")
        GuildFactory(guild_lead=lead.member)
        assert client.post(_hours_url(equipment), _ohours_data([_ohours_row(orientation_type)])).status_code == 403
        assert not OrientationAvailability.objects.for_equipment(equipment).exists()

    def it_saves_for_every_manager_tier(client: Client):
        equipment_guild = GuildFactory()
        equipment = EquipmentFactory(guild=equipment_guild)
        orientation_type = _owned_type(equipment)

        def post() -> int:
            initial = len(equipment.orientation_hours_windows())
            return client.post(
                _hours_url(equipment), _ohours_data([_ohours_row(orientation_type)], initial)
            ).status_code

        _manager_login(client, "oh_tier_row", equipment)
        assert post() == 302
        lead = _member_user("oh_tier_glead")
        equipment_guild.guild_lead = lead.member
        equipment_guild.save(update_fields=["guild_lead"])
        client.login(username="oh_tier_glead", password="pass")
        assert post() == 302
        holder = _login(client, "oh_tier_cap")
        holder.member.admin_capabilities.create(capability=AdminCapability.Capability.EQUIPMENT)
        assert post() == 302
        _login(client, "oh_tier_admin", fog_role=Member.FogRole.ADMIN)
        assert post() == 302

    def it_refuses_an_admin_previewing_as_a_member_but_not_the_real_account(client: Client):
        from hub.view_as import ROLE_MEMBER, SESSION_ROLE_KEY

        equipment = EquipmentFactory()
        orientation_type = _owned_type(equipment)
        _login(client, "oh_preview", fog_role=Member.FogRole.ADMIN)
        session = client.session
        session[SESSION_ROLE_KEY] = ROLE_MEMBER
        session.save()
        assert client.post(_hours_url(equipment), _ohours_data([_ohours_row(orientation_type)])).status_code == 403
        assert not OrientationAvailability.objects.for_equipment(equipment).exists()
        session = client.session
        del session[SESSION_ROLE_KEY]
        session.save()
        assert client.post(_hours_url(equipment), _ohours_data([_ohours_row(orientation_type)])).status_code == 302
        assert OrientationAvailability.objects.for_equipment(equipment).exists()

    def it_creates_rules_and_slots_and_redirects_with_the_flash(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_save", equipment)
        orientation_type = _owned_type(equipment)
        response = client.post(_hours_url(equipment), _ohours_data([_ohours_row(orientation_type)]))
        assert response.status_code == 302
        assert response["Location"].endswith("?tab=orientation")
        rule = OrientationAvailability.objects.for_equipment(equipment).get()
        assert rule.guild is None
        assert rule.orienter is None
        assert rule.slot_minutes == 60
        assert rule.slots.count() == 16  # 8 weekly occurrences, 2 hourly slots each
        assert _messages(client, equipment) == "Hours saved."

    def it_rerenders_the_tab_with_errors_on_an_invalid_post(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_invalid", equipment)
        orientation_type = _owned_type(equipment)
        response = client.post(
            _hours_url(equipment), _ohours_data([_ohours_row(orientation_type, start="12:00", end="10:00")])
        )
        assert response.status_code == 200
        assert response.context["active_tab"] == "orientation"
        assert response.context["orientation_hours_formset"].is_bound
        assert "The end time must be after the start time." in response.content.decode()
        assert not OrientationAvailability.objects.for_equipment(equipment).exists()

    def it_reports_retirement_counts_for_a_delete(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_delete", equipment)
        orientation_type = _owned_type(equipment)
        rule = _tool_rule(orientation_type)
        open_slot = _generated_slot(rule)
        booked = _generated_slot(rule, starts_at=timezone.now() + timedelta(days=3))
        OrientationBookingFactory(slot=booked)
        response = client.post(
            _hours_url(equipment), _ohours_data([_ohours_row(orientation_type, DELETE="on")], initial=1)
        )
        assert response.status_code == 302
        assert not OrientationAvailability.objects.filter(pk=rule.pk).exists()
        assert not OrientationSlot.objects.filter(pk=open_slot.pk).exists()
        assert OrientationSlot.objects.filter(pk=booked.pk).exists()
        assert _messages(client, equipment) == (
            "Hours saved. Removed 1 upcoming open slot. 1 booked slot kept. Cancel it from the Upcoming Slots card."
        )

    def it_reports_retirement_counts_for_a_pause(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_pause", equipment)
        orientation_type = _owned_type(equipment)
        rule = _tool_rule(orientation_type)
        _generated_slot(rule)
        response = client.post(
            _hours_url(equipment), _ohours_data([_ohours_row(orientation_type, active=False)], initial=1)
        )
        assert response.status_code == 302
        rule.refresh_from_db()
        assert rule.is_active is False
        assert not rule.slots.exists()  # paused: nothing regenerated either
        assert _messages(client, equipment) == "Hours saved. Removed 1 upcoming open slot."

    def it_reports_a_kept_slot_without_a_zero_removed_count(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_kept", equipment)
        orientation_type = _owned_type(equipment)
        rule = _tool_rule(orientation_type)
        OrientationBookingFactory(slot=_generated_slot(rule))
        response = client.post(
            _hours_url(equipment), _ohours_data([_ohours_row(orientation_type, active=False)], initial=1)
        )
        assert response.status_code == 302
        assert (
            _messages(client, equipment) == "Hours saved. 1 booked slot kept. Cancel it from the Upcoming Slots card."
        )

    def it_reports_retirement_counts_for_a_regrid(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_regrid", equipment)
        orientation_type = _owned_type(equipment)
        rule = _tool_rule(orientation_type)
        _generated_slot(rule)
        response = client.post(
            _hours_url(equipment), _ohours_data([_ohours_row(orientation_type, slot="30")], initial=1)
        )
        assert response.status_code == 302
        rule.refresh_from_db()
        assert rule.slot_minutes == 30
        assert rule.slots.count() == 32  # regenerated on the new grid: 4 half hour slots x 8 weeks
        assert all(slot.ends_at - slot.starts_at == timedelta(minutes=30) for slot in rule.slots.all())
        assert _messages(client, equipment) == "Hours saved. Removed 1 upcoming open slot."


def describe_orientation_hours_card_rendering():
    def it_groups_existing_rules_into_windows_with_delete_confirms(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_render", equipment)
        orientation_type = _owned_type(equipment)
        _tool_rule(orientation_type, weekday=5)
        _tool_rule(orientation_type, weekday=6)
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        assert [form.initial for form in response.context["orientation_hours_formset"].forms] == [
            {
                "orientation_type": orientation_type.pk,
                "start_time": "10:00",
                "end_time": "12:00",
                "days": [5, 6],
                "slot_minutes": 60,
                "buffer_minutes": 0,
                "seats": 1,
                "is_active": True,
            }
        ]
        content = response.content.decode()
        assert "Delete these hours?" in content
        assert 'id="equip-ohours-del-0"' not in content  # the confirm is dispatched by id, teleported to body
        assert "open-confirm', 'equip-ohours-del-0'" in content
        assert "No orientation hours yet." not in content

    def it_groups_upcoming_slots_by_day(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_days", equipment)
        orientation_type = _owned_type(equipment)
        rule = _tool_rule(orientation_type)
        day = _tool_day()
        booked = _generated_slot(rule, starts_at=_at(day, 10), ends_at=_at(day, 11))
        OrientationBookingFactory(slot=booked)
        OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(day, 11), ends_at=_at(day, 12)
        )
        quiet_day = _tool_day(3)
        OrientationSlotFactory(
            equipment_owned=True,
            orientation_type=orientation_type,
            starts_at=_at(quiet_day, 9),
            ends_at=_at(quiet_day, 10),
        )
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        days = response.context["orientation_slot_days"]
        assert [(d["date"], len(d["slots"]), d["booked_count"], d["open_by_default"]) for d in days] == [
            (day, 2, 1, True),
            (quiet_day, 1, 0, False),
        ]
        content = response.content.decode()
        assert "{ open: true }" in content
        assert "{ open: false }" in content
        assert "· 2 slots · 1 booked" in content
        assert "· 1 slot · 0 booked" in content
        assert ">recurring</span>" in content
        assert ">one time</span>" in content
        assert "Show later days" not in content

    def it_offers_the_later_days_button_beyond_two_weeks(client: Client):
        equipment = EquipmentFactory()
        _manager_login(client, "oh_later", equipment)
        orientation_type = _owned_type(equipment)
        far = _tool_day(20)
        OrientationSlotFactory(
            equipment_owned=True, orientation_type=orientation_type, starts_at=_at(far, 9), ends_at=_at(far, 10)
        )
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        assert response.context["orientation_has_later_days"] is True
        content = response.content.decode()
        assert "Show later days" in content
        assert 'x-show="showLater"' in content

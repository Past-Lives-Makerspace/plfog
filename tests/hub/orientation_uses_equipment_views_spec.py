"""BDD specs for the hub side of "Equipment it uses" (#658, #665).

Both orientation editors (guild and equipment) carry the multi select; the equipment
editor keeps its own item on the list; a booked guild slot shows on each listed item's
day timeline and index card; and the owner still decides where the orientation is run.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import EquipmentForm, OrientationTypeFormSet
from membership.models import Equipment, EquipmentReservation, Guild, Member, OrientationBooking, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.full_legal_name = member.full_legal_name or username.title()
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return member


def _day() -> Any:
    return timezone.localdate() + timedelta(days=2)


def _at(hour: int) -> datetime:
    return timezone.make_aware(datetime.combine(_day(), time(hour, 0)))


def _types_data(rows: list[dict[str, Any]], initial: int) -> dict[str, Any]:
    data: dict[str, Any] = {
        "otypes-TOTAL_FORMS": str(len(rows)),
        "otypes-INITIAL_FORMS": str(initial),
        "otypes-MIN_NUM_FORMS": "0",
        "otypes-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        values = {
            "name": "Etching Basics",
            "description": "",
            "duration_minutes": "60",
            "price": "",
            "default_seats": "4",
            "default_location": "",
            "sort_order": "0",
            "is_active": "on",
            **row,
        }
        for field, value in values.items():
            data[f"otypes-{index}-{field}"] = value
    return data


def _led_guild(client: Client, username: str) -> Guild:
    return GuildFactory(name="Printmaking Guild", guild_lead=_login(client, username))


def _box(content: str, row: str, equipment: Equipment) -> str | None:
    """The row's "Equipment it uses" checkbox for ``equipment``: "checked", "unchecked" or None when absent."""
    match = re.search(
        rf'<input type="checkbox" name="otypes-{row}-uses_equipment" value="{equipment.pk}"[^>]*>', content
    )
    if match is None:
        return None
    return "checked" if " checked" in match.group(0) else "unchecked"


def describe_guild_editor():
    def it_offers_active_equipment_under_equipment_it_uses(client: Client):
        guild = _led_guild(client, "uses_guild_render")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        press = EquipmentFactory(name="Etching Press")
        retired = EquipmentFactory(name="Old Press", is_active=False)
        orientation_type.uses_equipment.add(press)
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "orientations"})
        content = response.content.decode()
        assert "Equipment it uses" in content
        assert _box(content, "0", press) == "checked"
        assert _box(content, "0", retired) is None
        assert _box(content, "__prefix__", press) == "unchecked"

    def it_keeps_offering_a_retired_item_the_type_already_uses(client: Client):
        guild = _led_guild(client, "uses_guild_retired")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        retired = EquipmentFactory(name="Old Press", is_active=False)
        orientation_type.uses_equipment.add(retired)
        response = client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "orientations"})
        assert _box(response.content.decode(), "0", retired) == "checked"

    def it_saves_the_list_and_clears_it(client: Client):
        guild = _led_guild(client, "uses_guild_save")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        press, roller = EquipmentFactory(name="Etching Press"), EquipmentFactory(name="Brayer Station")
        url = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        row = {"id": str(orientation_type.pk), "uses_equipment": [str(press.pk), str(roller.pk)]}
        response = client.post(url, _types_data([row], initial=1))
        assert response.status_code == 302
        assert set(orientation_type.uses_equipment.all()) == {press, roller}
        client.post(url, _types_data([{"id": str(orientation_type.pk)}], initial=1))
        assert not orientation_type.uses_equipment.exists()

    def it_saves_the_list_on_a_new_row(client: Client):
        guild = _led_guild(client, "uses_guild_new")
        press = EquipmentFactory(name="Etching Press")
        url = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        client.post(url, _types_data([{"name": "Relief Basics", "uses_equipment": [str(press.pk)]}], initial=0))
        assert list(OrientationType.objects.get(guild=guild, name="Relief Basics").uses_equipment.all()) == [press]

    def it_refuses_an_unknown_item(client: Client):
        guild = _led_guild(client, "uses_guild_unknown")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        url = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        row = {"id": str(orientation_type.pk), "uses_equipment": ["999999"]}
        response = client.post(url, _types_data([row], initial=1))
        assert response.status_code == 200
        assert not orientation_type.uses_equipment.exists()

    def it_builds_and_renders_the_choices_in_the_same_queries_for_one_row_or_three(django_assert_num_queries):
        EquipmentFactory(name="Etching Press")
        EquipmentFactory(name="Brayer Station")

        def _render(guild: Guild) -> int:
            formset = OrientationTypeFormSet(instance=guild, prefix="otypes")
            rendered = [str(form["uses_equipment"]) for form in formset.forms]
            rendered.append(str(formset.empty_form["uses_equipment"]))
            return len(rendered)

        small, large = GuildFactory(name="Small Guild"), GuildFactory(name="Large Guild")
        OrientationTypeFactory(guild=small, name="One").uses_equipment.add(EquipmentFactory(name="Saw"))
        for name in ("One", "Two", "Three"):
            OrientationTypeFactory(guild=large, name=name).uses_equipment.add(EquipmentFactory(name=f"Saw {name}"))
        with django_assert_num_queries(3):
            assert _render(small) == 2
        with django_assert_num_queries(3):
            assert _render(large) == 4


def describe_equipment_editor():
    def _managed(client: Client, username: str) -> tuple[Equipment, OrientationType]:
        laser = EquipmentFactory(name="Laser Engraver")
        EquipmentStaffMembershipFactory(equipment=laser, member=_login(client, username))
        return laser, OrientationTypeFactory(equipment_owned=True, equipment=laser, name="Laser Basics")

    def it_shows_its_own_item_selected_on_a_saved_row_and_on_the_blank_row(client: Client):
        laser, _orientation_type = _managed(client, "uses_equip_render")
        response = client.get(reverse("hub_equipment_manage", args=[laser.slug]), {"tab": "orientation"})
        content = response.content.decode()
        saved_row, blank_row = content.split('id="equip-otype-empty-template"')
        assert _box(saved_row, "0", laser) == "checked"
        assert _box(blank_row, "__prefix__", laser) == "checked"
        assert "An equipment orientation always holds its own equipment" in content

    def it_keeps_its_own_item_when_the_editor_unselects_it(client: Client):
        laser, orientation_type = _managed(client, "uses_equip_keep")
        bench = EquipmentFactory(name="Finishing Bench")
        url = reverse("hub_equipment_orientation_types_save", args=[laser.slug])
        row = {"id": str(orientation_type.pk), "name": "Laser Basics", "uses_equipment": [str(bench.pk)]}
        response = client.post(url, _types_data([row], initial=1))
        assert response.status_code == 302
        assert set(orientation_type.uses_equipment.all()) == {laser, bench}

    def it_lists_its_own_item_on_a_new_type_saved_with_nothing_selected(client: Client):
        laser, _orientation_type = _managed(client, "uses_equip_new")
        url = reverse("hub_equipment_orientation_types_save", args=[laser.slug])
        rows = [{"id": str(_orientation_type.pk), "name": "Laser Basics"}, {"name": "Rotary Attachment"}]
        client.post(url, _types_data(rows, initial=1))
        added = OrientationType.objects.get(equipment=laser, name="Rotary Attachment")
        assert list(added.uses_equipment.all()) == [laser]

    def it_lists_the_new_equipment_on_the_type_made_with_it(client: Client):
        _login(client, "uses_equip_create", fog_role=Member.FogRole.ADMIN)
        response = client.post(
            reverse("hub_equipment_add"),
            {
                "name": "Etching Press",
                "kind": "tool",
                "unlocking_orientations": EquipmentForm.NEW_TYPE_CHOICE,
                "new_type-name": "Press Basics",
                "new_type-duration_minutes": "60",
                "new_type-default_seats": "2",
                "new_type-price": "",
                "new_type-default_location": "",
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        press = Equipment.objects.get(name="Etching Press")
        assert list(OrientationType.objects.get(equipment=press).uses_equipment.all()) == [press]


def describe_a_booked_guild_slot_on_the_items_it_uses():
    def _open(name: str) -> Equipment:
        equipment = EquipmentFactory(name=name)
        EquipmentHoursFactory(
            equipment=equipment, weekday=_day().weekday(), start_time=time(9, 0), end_time=time(17, 0)
        )
        return equipment

    def it_shows_on_each_listed_items_day_timeline(client: Client):
        _login(client, "uses_timeline")
        press, roller = _open("Etching Press"), _open("Brayer Station")
        guild = GuildFactory(name="Printmaking Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        orientation_type.uses_equipment.set([press, roller])
        slot = OrientationSlotFactory(
            guild=guild, orientation_type=orientation_type, starts_at=_at(11), ends_at=_at(12)
        )
        booker = OrientationBookingFactory(slot=slot).member
        booker.full_legal_name, booker.preferred_name = "Sam Reyes", ""
        booker.save(update_fields=["full_legal_name", "preferred_name"])
        for item in (press, roller):
            response = client.get(reverse("hub_equipment_schedule", args=[item.slug]), {"day": _day().isoformat()})
            segment = next(s for s in response.context["timeline"] if not s["is_free"])
            assert segment["kind"] == "orientation"
            assert (segment["starts_at"], segment["ends_at"]) == (_at(11), _at(12))
            assert "Orientation · Sam R." in response.content.decode()
            assert _at(11) not in response.context["starts"]

    def it_marks_every_listed_card_reserved_on_the_index(client: Client):
        _login(client, "uses_index")
        now = timezone.now()
        cards: dict[str, Equipment] = {}
        for name in ("Etching Press", "Brayer Station", "Lathe"):
            cards[name] = EquipmentFactory(name=name)
            EquipmentHoursFactory(
                equipment=cards[name],
                weekday=timezone.localtime().weekday(),
                start_time=time(0, 0),
                end_time=time(23, 59),
            )
        guild = GuildFactory(name="Printmaking Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        orientation_type.uses_equipment.set([cards["Etching Press"], cards["Brayer Station"]])
        slot = OrientationSlotFactory(
            guild=guild,
            orientation_type=orientation_type,
            starts_at=now - timedelta(minutes=30),
            ends_at=now + timedelta(minutes=30),
        )
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        response = client.get(reverse("hub_equipment_index"))
        availability = {card["equipment"].name: card["availability"] for card in response.context["cards"]}
        assert availability["Etching Press"][1].startswith("Reserved until ")
        assert availability["Brayer Station"][1].startswith("Reserved until ")
        assert availability["Lathe"] == ("free", "Available now")

    def it_leaves_the_cards_free_while_the_running_slot_is_unbooked(client: Client):
        _login(client, "uses_index_open")
        now = timezone.now()
        press = EquipmentFactory(name="Etching Press")
        EquipmentHoursFactory(
            equipment=press, weekday=timezone.localtime().weekday(), start_time=time(0, 0), end_time=time(23, 59)
        )
        guild = GuildFactory(name="Printmaking Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        orientation_type.uses_equipment.add(press)
        OrientationSlotFactory(
            guild=guild,
            orientation_type=orientation_type,
            starts_at=now - timedelta(minutes=30),
            ends_at=now + timedelta(minutes=30),
        )
        response = client.get(reverse("hub_equipment_index"))
        availability = {card["equipment"].name: card["availability"] for card in response.context["cards"]}
        assert availability["Etching Press"] == ("free", "Available now")


def describe_who_runs_it():
    def it_stays_on_the_guild_editor_and_off_the_items_editor(client: Client):
        press = EquipmentFactory(name="Etching Press")
        EquipmentStaffMembershipFactory(equipment=press, member=_login(client, "uses_owner_equipment"))
        guild = GuildFactory(name="Printmaking Guild")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        orientation_type.uses_equipment.add(press)
        response = client.get(reverse("hub_equipment_manage", args=[press.slug]), {"tab": "orientation"})
        assert list(response.context["orientation_types_formset"].queryset) == []
        guild_formset = OrientationTypeFormSet(instance=guild, prefix="otypes")
        assert list(guild_formset.queryset) == [orientation_type]


def describe_the_guild_upcoming_times_flag():
    """#665: a guild slot over a reservation on an item it uses is flagged for its orienters."""

    def _guild_page(client: Client, username: str) -> tuple[Guild, Equipment, Any]:
        lead = _login(client, username)
        guild = GuildFactory(name="Printmaking Guild", guild_lead=lead)
        press = EquipmentFactory(name="Etching Press")
        orientation_type = OrientationTypeFactory(guild=guild, name="Etching Basics")
        orientation_type.uses_equipment.set([press])
        blocked = OrientationSlotFactory(
            guild=guild, orientation_type=orientation_type, starts_at=_at(10), ends_at=_at(11)
        )
        OrientationSlotFactory(guild=guild, orientation_type=orientation_type, starts_at=_at(14), ends_at=_at(15))
        return guild, press, blocked

    def _times(client: Client, guild: Guild) -> Any:
        return client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "orientations"})

    def it_flags_the_slot_under_a_reservation_and_names_the_item(client: Client):
        guild, press, blocked = _guild_page(client, "print_lead")
        reserver = _login(Client(), "print_sam")
        reserver.full_legal_name = "Sam Reyes"
        reserver.save(update_fields=["full_legal_name"])
        reservation = EquipmentReservationFactory(equipment=press, member=reserver, starts_at=_at(10), ends_at=_at(12))
        response = _times(client, guild)
        flags = {
            entry["item"].pk: entry["item"].blocking_reservation for entry in response.context["upcoming_times_admin"]
        }
        assert flags[blocked.pk] == reservation
        assert sum(flag is not None for flag in flags.values()) == 1
        content = response.content.decode()
        assert "Blocked by Sam Reyes's reservation on Etching Press 10:00 AM to 12:00 PM" in content
        assert content.count("pl-equip-res-row--blocked") == 1

    def it_flags_held_time_the_same_way(client: Client):
        guild, press, _blocked = _guild_page(client, "print_lead_block")
        EquipmentReservationFactory(
            equipment=press,
            starts_at=_at(9),
            ends_at=_at(11),
            kind=EquipmentReservation.Kind.BLOCK,
            purpose="Felt replacement",
        )
        content = _times(client, guild).content.decode()
        assert "Blocked by held time (Felt replacement) on Etching Press 9:00 AM to 11:00 AM" in content

    def it_flags_nothing_when_the_items_are_free(client: Client):
        guild, _press, _blocked = _guild_page(client, "print_lead_free")
        EquipmentReservationFactory(equipment=EquipmentFactory(name="Lathe"), starts_at=_at(10), ends_at=_at(11))
        assert "pl-equip-res-row--blocked" not in _times(client, guild).content.decode()

"""BDD specs for the collapsed Orientations rows (#732).

The guild Orientations page and the equipment Orientation tab list each orientation as one
line (photo, name, length and price) with an Edit button over the full form. Saved rows load
collapsed; a new row and a row the server refused load open. The line is written by the
server from the form's bound values, so it reads before Alpine starts and shows what was
typed on a failed save.
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from hub.forms import OrientationTypeForm
from membership.models import Member, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
    tiny_png_bytes,
)

pytestmark = pytest.mark.django_db

TEMPLATE_BLOCK = re.compile(r"<template\b.*?</template>", re.DOTALL)


def _admin(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.status = Member.Status.ACTIVE
    member.full_legal_name = f"{username} Person"
    member.save()
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _rows(content: str) -> list[str]:
    """Each rendered orientation row's markup, outside the clone template."""
    page = TEMPLATE_BLOCK.sub("", content)
    return page.split('class="hub-card pl-otype-row"')[1:]


def _collapsed(row: str) -> bool:
    return "data-otype-body" in row and 'x-show="editing" x-cloak' in row and ">Edit</button>" in row


def _expanded(row: str) -> bool:
    return 'x-show="editing" data-otype-body' in row and ">Done</button>" in row


def _types_post(orientation_type: OrientationType, **fields: str) -> dict[str, str]:
    row = {
        "id": str(orientation_type.pk),
        "name": orientation_type.name,
        "description": "",
        "duration_minutes": "60",
        "price": "",
        "default_seats": "4",
        "default_location": "",
        "sort_order": "0",
        "is_active": "on",
        **fields,
    }
    data = {
        "otypes-TOTAL_FORMS": "1",
        "otypes-INITIAL_FORMS": "1",
        "otypes-MIN_NUM_FORMS": "0",
        "otypes-MAX_NUM_FORMS": "1000",
    }
    data.update({f"otypes-0-{key}": value for key, value in row.items()})
    return data


def describe_the_summary_line():
    def it_reads_length_and_price_from_a_saved_orientation():
        guild = GuildFactory()
        paid = OrientationTypeFactory(guild=guild, name="Lathe", duration_minutes=90, price_cents=1250)
        free = OrientationTypeFactory(guild=guild, name="Shop Basics", duration_minutes=45, price_cents=0)
        whole = OrientationTypeFactory(guild=guild, name="CNC", duration_minutes=60, price_cents=1500)
        assert OrientationTypeForm(instance=paid).summary_name == "Lathe"
        assert OrientationTypeForm(instance=paid).summary_meta == "90 min · $12.50"
        assert OrientationTypeForm(instance=free).summary_meta == "45 min · Free"
        assert OrientationTypeForm(instance=whole).summary_meta == "60 min · $15"

    def it_names_a_donation_and_an_inactive_orientation():
        guild = GuildFactory()
        donation = OrientationTypeFactory(guild=guild, name="Kiln", is_donation=True, price_cents=1500)
        retired = OrientationTypeFactory(guild=guild, name="Old Saw", is_active=False)
        assert OrientationTypeForm(instance=donation).summary_meta == "60 min · Donation based"
        assert OrientationTypeForm(instance=retired).summary_meta == "60 min · Free · Inactive"

    def it_reads_what_was_typed_on_a_bound_form():
        form = OrientationTypeForm(data={"name": "  Welding  ", "duration_minutes": "", "price": "abc"})
        assert form.summary_name == "Welding"
        assert form.summary_meta == "Free · Inactive"

    def it_reads_a_price_that_is_not_a_number_as_free():
        for typed in ("NaN", "Infinity", "-Infinity", "sNaN"):
            assert OrientationTypeForm(data={"duration_minutes": "60", "price": typed}).summary_meta == (
                "60 min · Free · Inactive"
            ), typed

    def it_calls_a_blank_row_a_new_orientation():
        assert OrientationTypeForm(data={"name": ""}).summary_name == "New orientation"


def describe_the_guild_orientations_page():
    def it_lists_saved_orientations_collapsed_with_a_photo_and_an_edit_button(client: Client):
        _admin(client, "orows_guild")
        guild = GuildFactory(name="Wood Guild")
        lathe = OrientationTypeFactory(
            guild=guild,
            name="Lathe",
            duration_minutes=90,
            price_cents=2000,
            photo=SimpleUploadedFile("lathe.png", tiny_png_bytes(), "image/png"),
        )
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        rows = _rows(content)
        assert len(rows) == 1
        assert _collapsed(rows[0])
        assert 'x-data="plOrientationRow(false)"' in rows[0]
        assert '@pl-autosave-error="editing = true"' in rows[0]
        assert ">Lathe</span>" in rows[0]
        assert ">90 min · $20</span>" in rows[0]
        assert 'aria-label="Edit Lathe"' in rows[0]
        assert f'<img src="{lathe.photo.url}" alt="" class="pl-otype-row__thumb" data-otype-thumb>' in rows[0]

    def it_heads_the_card_with_orientations_and_the_add_button(client: Client):
        _admin(client, "orows_head")
        guild = GuildFactory()
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        head = content.split('class="pl-otype-card__head" data-card-head>', 1)[1].split("</div>", 1)[0]
        assert ">Orientations</h2>" in head
        add_page = reverse("hub_orientation_add")
        # The same button style as the equipment card's add.
        assert f'<a href="{add_page}?guild={guild.pk}" class="hub-btn hub-btn--sm" data-add-orientation-type>' in head
        assert ">Add New Orientation +</a>" in head
        assert "No orientations yet." in content
        # The Orientations card and its Booking hint; What's New and old release notes quote the old name.
        card = content.split('id="otypes-form"', 1)[1].split("</form>", 1)[0]
        assert "orientation type" not in card.lower()
        assert "set per orientation in the Orientations card below" in content

    def it_adds_a_row_in_place_on_a_hidden_guild(client: Client):
        _admin(client, "orows_hidden")
        guild = GuildFactory(is_active=False)
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        head = content.split('class="pl-otype-card__head" data-card-head>', 1)[1].split("</div>", 1)[0]
        assert "data-formset-add data-add-orientation-type>Add New Orientation +</button>" in head

    def it_starts_the_cloned_new_row_open(client: Client):
        _admin(client, "orows_clone")
        guild = GuildFactory()
        content = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        template = content.split('<template id="otype-empty-template" data-formset-template>', 1)[1]
        template = template.split("</template>", 1)[0]
        assert 'x-data="plOrientationRow(true)"' in template
        assert _expanded(template)
        assert ">New orientation</span>" in template


def describe_the_equipment_orientation_tab():
    def it_lists_saved_orientations_collapsed(client: Client):
        _admin(client, "orows_equip")
        equipment = EquipmentFactory()
        OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Laser Basics", price_cents=0)
        response = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        content = response.content.decode()
        rows = _rows(content)
        assert len(rows) == 1
        assert _collapsed(rows[0])
        assert ">Laser Basics</span>" in rows[0]
        assert ">60 min · Free</span>" in rows[0]
        head = content.split('class="pl-otype-card__head" data-card-head>', 1)[1].split("</div>", 1)[0]
        assert ">Orientations</h2>" in head
        assert ">Add New Orientation +</button>" in head
        assert '<button type="button" class="hub-btn hub-btn--sm" data-otype-add' in head
        template = content.split('<template id="equip-otype-empty-template">', 1)[1].split("</template>", 1)[0]
        assert _expanded(template)

    def it_reopens_the_row_a_failed_save_refused(client: Client):
        _admin(client, "orows_refused")
        equipment = EquipmentFactory()
        laser = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Laser Basics")
        url = reverse("hub_equipment_orientation_types_save", args=[equipment.slug])
        response = client.post(url, _types_post(laser, name="Laser Basics Two", price="9999"))
        assert response.status_code == 200
        rows = _rows(response.content.decode())
        assert len(rows) == 1
        assert _expanded(rows[0])
        assert "Enter a price between $0 and $500." in rows[0]
        # The line shows what was typed, not what is saved.
        assert ">Laser Basics Two</span>" in rows[0]
        laser.refresh_from_db()
        assert laser.name == "Laser Basics"

    def it_rerenders_a_row_whose_price_is_not_a_number(client: Client):
        _admin(client, "orows_nan")
        equipment = EquipmentFactory()
        laser = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Laser Basics")
        url = reverse("hub_equipment_orientation_types_save", args=[equipment.slug])
        response = client.post(url, _types_post(laser, price="NaN"))
        assert response.status_code == 200
        rows = _rows(response.content.decode())
        assert _expanded(rows[0])
        assert ">60 min · Free</span>" in rows[0]


def describe_the_guild_save_without_autosave():
    def it_rerenders_a_row_whose_price_is_not_a_number(client: Client):
        _admin(client, "orows_guild_nan")
        guild = GuildFactory()
        lathe = OrientationTypeFactory(guild=guild, name="Lathe")
        url = reverse("hub_guild_orientation_types_save", args=[guild.pk])
        response = client.post(url, _types_post(lathe, price="NaN"))
        assert response.status_code == 200
        rows = _rows(response.content.decode())
        assert _expanded(rows[0])
        assert ">Lathe</span>" in rows[0]

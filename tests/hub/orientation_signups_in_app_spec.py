"""BDD specs for orientation signups staying in the app (#502 prelude).

The outside signup link (#368) is gone end to end: neither editor form carries it, a POST
that still sends it is ignored, the guild and equipment pages always render the booking UI,
the booking choke point has no off site refusal left, the custom request picker offers every
active type, and the slash command lists every bookable time.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

import membership.models
from hub.forms import GuildOrientationSettingsForm, OrientationCustomRequestForm, OrientationTypeForm
from membership.discord_commands import _slot_disambiguation
from membership.models import GuildOrientationSettings, Member, OrientationBooking, OrientationType
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

LINK = "https://forms.gle/guild-orientation"
# The custom time block's markup anchor: its copy is free to change, its help key is not.
CUSTOM_REQUEST = 'data-help-key="orientation.request-custom-time"'


def _member_user(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["status"])
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str) -> User:
    user = _member_user(username)
    client.login(username=username, password="pass")
    return user


def _lead_login(client: Client, username: str, guild) -> User:
    user = _login(client, username)
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    return user


def _orientation_section(content: str) -> str:
    """Just the guild page's orientation panel: the changelog renders into every hub page."""
    return content.split('id="guild-orientation"')[1].split("</section>")[0]


def _equipment_section(content: str) -> str:
    return content.split('id="equipment-orientation"')[1].split("</section>")[0]


def _settings_payload(**overrides: str) -> dict[str, str]:
    data = {"is_enabled": "on", "allow_custom_requests": "on", "info": "", "closed_message": ""}
    data.update(overrides)
    return data


def _types_payload(rows: list[dict[str, str]], *, initial: int = 0) -> dict[str, str]:
    data = {
        "otypes-TOTAL_FORMS": str(len(rows)),
        "otypes-INITIAL_FORMS": str(initial),
        "otypes-MIN_NUM_FORMS": "0",
        "otypes-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        fields = {
            "name": "Shop Basics",
            "description": "",
            "duration_minutes": "60",
            "price": "",
            "default_seats": "4",
            "default_location": "",
            "sort_order": "0",
            "is_active": "on",
        }
        fields.update(row)
        for field, value in fields.items():
            data[f"otypes-{index}-{field}"] = value
    return data


def describe_the_editor_forms():
    def it_carries_no_signup_link_on_either_form():
        assert "external_signup_url" not in GuildOrientationSettingsForm().fields
        assert "external_signup_url" not in OrientationTypeForm().fields
        assert not hasattr(GuildOrientationSettings, "external_signup_url")
        assert not hasattr(OrientationType, "external_signup_url")

    def it_ignores_a_posted_link_and_saves_the_rest_of_the_settings(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _lead_login(client, "ia_settings", guild)
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(info="Bring shoes", external_signup_url=LINK),
        )
        assert response.status_code == 302
        settings_obj = GuildOrientationSettings.objects.get(guild=guild)
        assert settings_obj.info == "Bring shoes"
        assert settings_obj.is_enabled is True

    def it_ignores_a_posted_link_on_an_orientation_type_row(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        orientation_type = OrientationTypeFactory(guild=guild, name="Lathe")
        _lead_login(client, "ia_types", guild)
        payload = _types_payload(
            [{"id": str(orientation_type.pk), "name": "Lathe Basics", "external_signup_url": LINK}], initial=1
        )
        response = client.post(reverse("hub_guild_orientation_types_save", args=[guild.pk]), payload)
        assert response.status_code == 302
        orientation_type.refresh_from_db()
        assert orientation_type.name == "Lathe Basics"

    def it_renders_no_link_field_on_the_guild_or_equipment_editors(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        OrientationTypeFactory(guild=guild)
        user = _lead_login(client, "ia_editors", guild)
        content = client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "orientations"}).content.decode()
        assert 'name="external_signup_url"' not in content
        assert "otypes-0-external_signup_url" not in content
        equipment = EquipmentFactory()
        user.member.fog_role = Member.FogRole.ADMIN
        user.member.save(update_fields=["fog_role"])
        user.member.sync_user_permissions()
        OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        manage = client.get(reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"})
        assert manage.status_code == 200
        assert "external_signup_url" not in manage.content.decode()


def describe_the_member_pages():
    def it_always_renders_the_booking_ui_on_the_guild_tab(client: Client):
        _login(client, "ia_guild")
        settings_obj = GuildOrientationSettingsFactory()
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild)
        OrientationSlotFactory(guild=settings_obj.guild, orientation_type=orientation_type)
        section = _orientation_section(
            client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        )
        assert ">Request<" in section
        assert "pl-orient-slots__row" in section
        assert "Sign up for this orientation" not in section
        assert 'target="_blank"' not in section

    def it_always_renders_the_booking_ui_on_the_equipment_page(client: Client):
        _login(client, "ia_equip")
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        section = _equipment_section(
            client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        )
        assert ">Request<" in section
        assert "Sign up for this orientation" not in section

    def it_always_reads_book_the_orientation_on_the_requirements_banner(client: Client):
        _login(client, "ia_banner")
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        equipment.required_orientation = orientation_type
        equipment.save(update_fields=["required_orientation"])
        response = client.get(reverse("hub_equipment_detail", args=[equipment.slug]))
        content = response.content.decode()
        assert "Book the Orientation" in content
        assert "See How to Sign Up" not in content
        assert "required_orientation_is_external" not in response.context


def describe_the_booking_choke_point():
    def it_has_no_off_site_refusal_left():
        assert not hasattr(membership.models, "ExternalSignupRequiredError")
        assert not hasattr(membership.models, "validate_signup_url")
        assert not hasattr(OrientationType, "resolved_external_signup_url")

    def it_books_a_member_on_a_guild_that_used_to_point_outside():
        settings_obj = GuildOrientationSettingsFactory()
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild)
        slot = OrientationSlotFactory(guild=settings_obj.guild, orientation_type=orientation_type)
        member = MemberFactory()
        slot.ensure_bookable_for(member)  # no raise
        booking = slot.book(member)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.orientation_type == orientation_type

    def it_books_through_the_slot_button(client: Client):
        user = _login(client, "ia_button")
        settings_obj = GuildOrientationSettingsFactory()
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild)
        slot = OrientationSlotFactory(guild=settings_obj.guild, orientation_type=orientation_type)
        response = client.post(reverse("hub_orientation_book", args=[slot.pk]))
        assert response.status_code == 302
        assert OrientationBooking.objects.filter(member=user.member, slot=slot).count() == 1


def describe_the_custom_request_form():
    def it_offers_every_active_type(client: Client):
        settings_obj = GuildOrientationSettingsFactory(allow_custom_requests=True)
        lathe = OrientationTypeFactory(guild=settings_obj.guild, name="Lathe")
        basics = OrientationTypeFactory(guild=settings_obj.guild, name="Shop Basics", sort_order=1)
        OrientationTypeFactory(guild=settings_obj.guild, name="Retired", sort_order=2, is_active=False)
        form = OrientationCustomRequestForm(guild=settings_obj.guild)
        assert list(form.fields["orientation_type"].queryset) == [lathe, basics]
        assert form.fields["orientation_type"].initial == lathe.pk
        assert form.has_types is True
        _login(client, "ia_custom")
        section = _orientation_section(
            client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        )
        assert CUSTOM_REQUEST in section

    def it_has_no_types_when_the_guild_offers_none():
        settings_obj = GuildOrientationSettingsFactory()
        OrientationTypeFactory(guild=settings_obj.guild, name="Retired", is_active=False)
        form = OrientationCustomRequestForm(guild=settings_obj.guild)
        assert list(form.fields["orientation_type"].queryset) == []
        assert form.has_types is False

    def it_has_no_types_without_a_guild():
        assert OrientationCustomRequestForm().has_types is False


def describe_the_slash_command_picker():
    def it_lists_every_bookable_slot():
        settings_obj = GuildOrientationSettingsFactory()
        lathe = OrientationTypeFactory(guild=settings_obj.guild, name="Lathe")
        basics = OrientationTypeFactory(guild=settings_obj.guild, name="Shop Basics", sort_order=1)
        first = OrientationSlotFactory(guild=settings_obj.guild, orientation_type=lathe)
        second = OrientationSlotFactory(guild=settings_obj.guild, orientation_type=basics)
        content = _slot_disambiguation(settings_obj.guild, settings_obj, "https://example.test/g")["data"]["content"]
        assert f"`{first.pk}`" in content
        assert f"`{second.pk}`" in content
        assert "Lathe" in content
        assert "Shop Basics" in content

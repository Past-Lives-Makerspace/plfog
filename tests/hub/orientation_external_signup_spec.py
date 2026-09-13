"""BDD specs for arbitrary external orientation signup URLs (issue #368 item 7).

A guild, or one orientation type, can send signups to an outside form instead of the
built-in booking flow. The link replaces the booking UI wherever members would have
booked; the slots and bookings already in the database are left alone, and orientation
completion for those signups becomes a manual step, said so in the editor.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.forms import (
    EXTERNAL_SIGNUP_URL_WARNING,
    GuildOrientationSettingsForm,
    OrientationCustomRequestForm,
    OrientationTypeForm,
)
from membership.models import Guild, GuildOrientationSettings, Member, OrientationBooking, OrientationSlot
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

GUILD_LINK = "https://forms.gle/guild-orientation"
TYPE_LINK = "https://forms.gle/lathe-orientation"


def _member_user(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = username.title()
    member.save()
    member.sync_user_permissions()
    return user


def _login(client: Client, username: str) -> User:
    user = _member_user(username)
    client.login(username=username, password="pass")
    return user


def _lead_login(client: Client, username: str, guild: Guild) -> User:
    user = _login(client, username)
    guild.guild_lead = user.member
    guild.save(update_fields=["guild_lead"])
    return user


def _orientation_section(content: str) -> str:
    """Just the guild page's orientation panel — the changelog renders into every hub page."""
    return content.split('id="guild-orientation"')[1].split("</section>")[0]


def _equipment_section(content: str) -> str:
    return content.split('id="equipment-orientation"')[1].split("</section>")[0]


def _type_block(section: str, pk: int) -> str:
    """One orientation type's block inside a section — up to the next type, or the end."""
    after = section.split(f'id="orientation-type-{pk}"')[1]
    return after.split('id="orientation-type-')[0]


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
            "external_signup_url": "",
        }
        fields.update(row)
        for field, value in fields.items():
            data[f"otypes-{index}-{field}"] = value
    return data


def describe_the_guild_page():
    def it_shows_the_outside_link_where_the_booking_UI_was(client: Client):
        _login(client, "xg1")
        settings_obj = GuildOrientationSettingsFactory(external_signup_url=GUILD_LINK)
        OrientationTypeFactory(guild=settings_obj.guild)
        OrientationSlotFactory(guild=settings_obj.guild)
        section = _orientation_section(
            client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        )
        assert "Signing up for this orientation happens on another site." in section
        assert f'href="{GUILD_LINK}"' in section
        assert "Sign up for this orientation" in section
        # The slot list, its Request button and the pager are all gone.
        assert ">Request<" not in section
        assert "pl-orient-slots__row" not in section

    def it_opens_the_link_in_a_new_tab_without_handing_over_the_opener(client: Client):
        _login(client, "xg2")
        settings_obj = GuildOrientationSettingsFactory(external_signup_url=GUILD_LINK)
        OrientationTypeFactory(guild=settings_obj.guild)
        section = _orientation_section(
            client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        )
        link = section.split(f'href="{GUILD_LINK}"')[1].split(">")[0]
        assert 'target="_blank"' in link
        assert 'rel="noopener"' in link

    def it_leaves_existing_slots_and_bookings_alone(client: Client):
        user = _login(client, "xg3")
        settings_obj = GuildOrientationSettingsFactory(external_signup_url=GUILD_LINK)
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild)
        slot = OrientationSlotFactory(guild=settings_obj.guild, orientation_type=orientation_type)
        other = _member_user("xg3other")
        booking = OrientationBookingFactory(slot=slot, member=other.member)
        client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug]))
        assert OrientationSlot.objects.filter(pk=slot.pk).exists()
        assert OrientationBooking.objects.filter(pk=booking.pk).exists()
        assert user.member.orientation_bookings.count() == 0

    def it_keeps_showing_a_members_live_booking_over_the_link(client: Client):
        user = _login(client, "xg4")
        settings_obj = GuildOrientationSettingsFactory(external_signup_url=GUILD_LINK)
        orientation_type = OrientationTypeFactory(guild=settings_obj.guild)
        slot = OrientationSlotFactory(guild=settings_obj.guild, orientation_type=orientation_type)
        OrientationBookingFactory(slot=slot, member=user.member, status=OrientationBooking.Status.CONFIRMED)
        section = _orientation_section(
            client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        )
        assert "Confirmed" in section
        assert "Sign up for this orientation" not in section

    def it_sends_only_the_type_that_carries_its_own_link_outside(client: Client):
        _login(client, "xg5")
        settings_obj = GuildOrientationSettingsFactory()
        external = OrientationTypeFactory(guild=settings_obj.guild, name="Lathe", external_signup_url=TYPE_LINK)
        internal = OrientationTypeFactory(guild=settings_obj.guild, name="Shop Basics", sort_order=1)
        OrientationSlotFactory(guild=settings_obj.guild, orientation_type=external)
        OrientationSlotFactory(guild=settings_obj.guild, orientation_type=internal)
        content = client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        section = _orientation_section(content)
        outside = _type_block(section, external.pk)
        inside = _type_block(section, internal.pk)
        assert TYPE_LINK in outside
        assert ">Request<" not in outside
        # The other type still books here, untouched.
        assert TYPE_LINK not in inside
        assert ">Request<" in inside

    def it_hides_the_custom_time_request_when_the_guild_link_covers_everything(client: Client):
        _login(client, "xg6")
        settings_obj = GuildOrientationSettingsFactory(external_signup_url=GUILD_LINK, allow_custom_requests=True)
        OrientationTypeFactory(guild=settings_obj.guild)
        section = _orientation_section(
            client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        )
        assert "Request a custom time" not in section

    def it_keeps_the_custom_time_request_when_only_one_type_went_outside(client: Client):
        _login(client, "xg7")
        settings_obj = GuildOrientationSettingsFactory(allow_custom_requests=True)
        OrientationTypeFactory(guild=settings_obj.guild, name="Lathe", external_signup_url=TYPE_LINK)
        OrientationTypeFactory(guild=settings_obj.guild, name="Shop Basics", sort_order=1)
        section = _orientation_section(
            client.get(reverse("hub_guild_detail", args=[settings_obj.guild.slug])).content.decode()
        )
        assert "Request a custom time" in section


def describe_the_equipment_page():
    def it_shows_the_outside_link_where_the_booking_UI_was(client: Client):
        _login(client, "xe1")
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(
            equipment_owned=True, equipment=equipment, external_signup_url=TYPE_LINK
        )
        OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        section = _equipment_section(
            client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        )
        assert "Signing up for this orientation happens on another site." in section
        assert f'href="{TYPE_LINK}"' in section
        assert 'rel="noopener"' in section
        assert ">Request<" not in section

    def it_leaves_a_type_with_no_link_booking_here(client: Client):
        _login(client, "xe2")
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment)
        OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        section = _equipment_section(
            client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        )
        assert "Signing up for this orientation happens on another site." not in section
        assert ">Request<" in section

    def it_keeps_showing_a_members_live_booking_over_the_link(client: Client):
        user = _login(client, "xe3")
        equipment = EquipmentFactory()
        orientation_type = OrientationTypeFactory(
            equipment_owned=True, equipment=equipment, external_signup_url=TYPE_LINK
        )
        slot = OrientationSlotFactory(equipment_owned=True, orientation_type=orientation_type)
        OrientationBookingFactory(slot=slot, member=user.member, guild=None, status=OrientationBooking.Status.CONFIRMED)
        section = _equipment_section(
            client.get(reverse("hub_equipment_detail", args=[equipment.slug])).content.decode()
        )
        assert "Confirmed" in section
        assert "Sign up for this orientation" not in section


def describe_the_custom_time_request_form():
    def it_drops_a_type_whose_signups_went_outside(client: Client):
        settings_obj = GuildOrientationSettingsFactory()
        external = OrientationTypeFactory(guild=settings_obj.guild, name="Lathe", external_signup_url=TYPE_LINK)
        internal = OrientationTypeFactory(guild=settings_obj.guild, name="Shop Basics", sort_order=1)
        form = OrientationCustomRequestForm(guild=settings_obj.guild)
        choices = list(form.fields["orientation_type"].queryset)
        assert choices == [internal]
        assert external not in choices
        assert form.fields["orientation_type"].initial == internal.pk

    def it_offers_nothing_when_the_guild_link_covers_every_type(client: Client):
        settings_obj = GuildOrientationSettingsFactory(external_signup_url=GUILD_LINK)
        OrientationTypeFactory(guild=settings_obj.guild)
        form = OrientationCustomRequestForm(guild=settings_obj.guild)
        assert list(form.fields["orientation_type"].queryset) == []


def describe_the_guild_orientation_settings_editor():
    def it_renders_the_field_and_the_manual_completion_warning(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _lead_login(client, "xs1", guild)
        content = client.get(reverse("hub_guild_edit", args=[guild.pk]), {"tab": "orientations"}).content.decode()
        assert "External signup link" in content
        assert EXTERNAL_SIGNUP_URL_WARNING in content
        assert "mark people oriented by hand on the Orientations dashboard" in content

    def it_saves_a_link(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _lead_login(client, "xs2", guild)
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(external_signup_url=GUILD_LINK),
        )
        assert response.status_code == 302
        assert GuildOrientationSettings.objects.get(guild=guild).external_signup_url == GUILD_LINK

    def it_assumes_https_for_a_link_pasted_without_one(client: Client):
        # Django 6's URLField carries assume_scheme="https", so "forms.gle/x" saves as a
        # working https link rather than being refused. A dangerous scheme is explicit
        # (javascript:, data:) and is never upgraded into one.
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _lead_login(client, "xs3", guild)
        client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(external_signup_url="forms.gle/no-scheme"),
        )
        assert GuildOrientationSettings.objects.get(guild=guild).external_signup_url == "https://forms.gle/no-scheme"

    @pytest.mark.parametrize(
        "url", ["javascript:alert(1)", "javascript://%0aalert(1)", "data:text/html;base64,AAAA", "ftp://example.com"]
    )
    def it_refuses_anything_that_is_not_http_or_https(client: Client, url):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _lead_login(client, "xs4", guild)
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]), _settings_payload(external_signup_url=url)
        )
        assert response.status_code == 200
        assert "external_signup_url" in response.context["orientation_form"].errors
        assert GuildOrientationSettings.objects.get(guild=guild).external_signup_url == ""

    def it_keeps_the_editor_shut_to_a_member_who_does_not_run_orientations(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        _login(client, "xs5")
        response = client.post(
            reverse("hub_guild_orientation_edit", args=[guild.pk]),
            _settings_payload(external_signup_url=GUILD_LINK),
        )
        assert response.status_code == 403
        assert GuildOrientationSettings.objects.get(guild=guild).external_signup_url == ""


def describe_the_orientation_type_editors():
    def it_saves_a_per_type_link_from_the_guild_editor(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        orientation_type = OrientationTypeFactory(guild=guild, name="Lathe")
        _lead_login(client, "xt1", guild)
        payload = _types_payload(
            [{"id": str(orientation_type.pk), "name": "Lathe", "external_signup_url": TYPE_LINK}], initial=1
        )
        response = client.post(reverse("hub_guild_orientation_types_save", args=[guild.pk]), payload)
        assert response.status_code == 302
        orientation_type.refresh_from_db()
        assert orientation_type.external_signup_url == TYPE_LINK

    def it_refuses_a_non_web_scheme_from_the_guild_editor(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild)
        orientation_type = OrientationTypeFactory(guild=guild, name="Lathe")
        _lead_login(client, "xt2", guild)
        payload = _types_payload(
            [{"id": str(orientation_type.pk), "name": "Lathe", "external_signup_url": "javascript:alert(1)"}],
            initial=1,
        )
        response = client.post(reverse("hub_guild_orientation_types_save", args=[guild.pk]), payload)
        assert response.status_code == 200
        assert "external_signup_url" in response.context["orientation_type_formset"].forms[0].errors
        orientation_type.refresh_from_db()
        assert orientation_type.external_signup_url == ""

    def it_saves_a_per_type_link_from_the_equipment_editor(client: Client):
        equipment = EquipmentFactory()
        user = _login(client, "xt3")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        payload = _types_payload(
            [{"id": str(orientation_type.pk), "name": "Operator Basics", "external_signup_url": TYPE_LINK}], initial=1
        )
        response = client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), payload)
        assert response.status_code == 302
        orientation_type.refresh_from_db()
        assert orientation_type.external_signup_url == TYPE_LINK

    def it_states_the_manual_completion_warning_on_the_equipment_tab(client: Client):
        equipment = EquipmentFactory()
        user = _login(client, "xt4")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        content = client.get(
            reverse("hub_equipment_manage", args=[equipment.slug]), {"tab": "orientation"}
        ).content.decode()
        assert "External signup link" in content
        assert EXTERNAL_SIGNUP_URL_WARNING in content

    def it_keeps_the_equipment_editor_shut_to_a_plain_member(client: Client):
        equipment = EquipmentFactory()
        _login(client, "xt5")
        orientation_type = OrientationTypeFactory(equipment_owned=True, equipment=equipment, name="Operator Basics")
        payload = _types_payload(
            [{"id": str(orientation_type.pk), "name": "Operator Basics", "external_signup_url": TYPE_LINK}], initial=1
        )
        response = client.post(reverse("hub_equipment_orientation_types_save", args=[equipment.slug]), payload)
        assert response.status_code == 403
        orientation_type.refresh_from_db()
        assert orientation_type.external_signup_url == ""


def describe_marking_someone_oriented_by_hand():
    """An outside form cannot write back, so the existing manual path has to still work.

    It does, and nothing here is new: add a one-off slot from the guild editor
    (``hub_guild_orientation_slot_add``), add the member to it from the orientations
    dashboard (``hub_orientation_add_member``), then flip Completed
    (``hub_orientation_toggle_completed``). That third step is what
    ``Member.is_oriented_for`` reads, so guild join gating comes back on.
    """

    def it_still_runs_end_to_end_for_a_guild_that_sends_signups_outside(client: Client):
        guild = GuildFactory()
        GuildOrientationSettingsFactory(guild=guild, external_signup_url=GUILD_LINK)
        orientation_type = OrientationTypeFactory(guild=guild)
        lead = _lead_login(client, "xm1", guild)
        attendee = _member_user("xm1attendee")
        assert attendee.member.is_oriented_for(guild) is False

        starts = timezone.localtime(timezone.now() + timedelta(days=3))
        added = client.post(
            reverse("hub_guild_orientation_slot_add", args=[guild.pk]),
            {
                "date": starts.date().isoformat(),
                "start_time": "10:00",
                "duration_minutes": "60",
                "orientation_type": str(orientation_type.pk),
                "orienter": str(lead.member.pk),
                "seats": "4",
                "location": "",
            },
        )
        assert added.status_code == 302
        slot = OrientationSlot.objects.get(guild=guild)

        client.post(
            reverse("hub_orientation_add_member"),
            {"member": str(attendee.member.pk), "slot": str(slot.pk)},
        )
        booking = OrientationBooking.objects.get(member=attendee.member, slot=slot)

        client.post(reverse("hub_orientation_toggle_completed", args=[booking.pk]))
        booking.refresh_from_db()
        assert booking.is_completed is True
        assert attendee.member.is_oriented_for(guild) is True


def describe_the_forms_themselves():
    def it_trims_surrounding_whitespace_on_the_guild_link():
        form = GuildOrientationSettingsForm(
            data={"is_enabled": "on", "info": "", "closed_message": "", "external_signup_url": f"  {GUILD_LINK}  "}
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["external_signup_url"] == GUILD_LINK

    def it_leaves_a_blank_link_blank_on_the_type_form():
        form = OrientationTypeForm(
            data={
                "name": "Shop Basics",
                "description": "",
                "duration_minutes": "60",
                "price": "",
                "default_seats": "4",
                "default_location": "",
                "sort_order": "0",
                "is_active": "on",
                "external_signup_url": "",
            }
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["external_signup_url"] == ""

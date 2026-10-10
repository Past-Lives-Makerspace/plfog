"""Specs for Locations in the hub (#616): the admin page, the pickers on every form, and the pages that show one.

The light itself (which source makes an area red or amber) is specced in
``tests/membership/location_status_spec.py``; here the guild page only has to render what the
service returns, and render nothing for a guild with no location.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from hub.forms import CommunityEventForm, EquipmentForm, LocationForm, OrientationTypeForm
from membership.forms import LocationChoiceField
from membership.models import CommunityEvent, Equipment, Location, Member, OrientationType
from tests.membership.factories import (
    CommunityEventFactory,
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    LocationFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

GUILDS_HOST = "guilds.pastlives.space"
GUILDS_SETTINGS = dict(
    ALLOWED_HOSTS=[GUILDS_HOST, "testserver"],
    GUILDS_HOSTS=[GUILDS_HOST],
    GUILDS_BASE_URL=f"https://{GUILDS_HOST}",
    BOOK_BASE_URL="https://book.pastlives.space",
    MEMBER_BASE_URL="https://members.pastlives.space",
)


def _login(client: Client, username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    member.fog_role = fog_role
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    client.login(username=username, password="pass")
    return user


def _event_payload(**overrides: str) -> dict[str, str]:
    data = {
        "title": "Forge Night",
        "starts_at": "2026-07-11T18:00",
        "ends_at": "2026-07-11T20:00",
        "location": "",
        "description": "",
        "recurrence": "none",
    }
    data.update(overrides)
    return data


def _types_payload(**row: str) -> dict[str, str]:
    data = {
        "otypes-TOTAL_FORMS": "1",
        "otypes-INITIAL_FORMS": "0",
        "otypes-MIN_NUM_FORMS": "0",
        "otypes-MAX_NUM_FORMS": "1000",
    }
    fields = {
        "name": "Lathe Cert",
        "description": "",
        "duration_minutes": "60",
        "price": "",
        "default_seats": "4",
        "default_location": "",
        "sort_order": "0",
        "is_active": "on",
        **row,
    }
    data.update({f"otypes-0-{name}": value for name, value in fields.items()})
    return data


def describe_the_location_picker():
    def it_labels_each_choice_with_its_guild():
        LocationFactory(name="Hot Glass Room", guild=GuildFactory(name="Glass"))
        LocationFactory(name="Loading Dock")
        field = CommunityEventForm(can_choose_audience=True).fields["area"]
        assert isinstance(field, LocationChoiceField)
        assert [label for _value, label in field.choices] == ["No location", "Hot Glass Room (Glass)", "Loading Dock"]

    def it_names_a_location_by_its_name_alone_elsewhere():
        assert str(LocationFactory(name="Garden", guild=GuildFactory(name="Gardeners"))) == "Garden"

    def it_is_called_location_and_never_required():
        field = CommunityEventForm(can_choose_audience=True).fields["area"]
        assert field.label == "Location"
        assert field.required is False

    def it_hides_deactivated_locations_except_the_one_already_set():
        active = LocationFactory(name="Kitchen")
        retired = LocationFactory(name="Old Annex", is_active=False)
        assert list(CommunityEventForm(can_choose_audience=True).fields["area"].queryset) == [active]
        event = CommunityEventFactory(area=retired)
        offered = list(CommunityEventForm(instance=event, can_choose_audience=True).fields["area"].queryset)
        assert offered == [active, retired]

    def it_gives_every_free_text_location_a_label_that_is_not_location():
        assert CommunityEventForm(can_choose_audience=True).fields["location"].label == "Address or room details"
        assert OrientationTypeForm().fields["default_location"].label == "Where to meet"
        assert EquipmentForm().fields["location_note"].label == "Where to find it"


def describe_event_forms():
    def it_sets_and_clears_the_location_from_the_guild_events_tab(client: Client):
        user = _login(client, "loc_lead")
        guild = GuildFactory(guild_lead=user.member)
        location = LocationFactory(name="Jewelry Studio", guild=guild)
        with patch.object(CommunityEvent, "announce"):
            client.post(
                reverse("hub_guild_event_add", args=[guild.pk]),
                _event_payload(area=str(location.pk), location="Back bench"),
            )
        event = CommunityEvent.objects.get(title="Forge Night")
        assert event.area == location
        assert event.location == "Back bench"
        response = client.post(reverse("hub_guild_event_edit", args=[guild.pk, event.pk]), _event_payload())
        assert response.status_code == 302
        event.refresh_from_db()
        assert event.area is None

    def it_sets_and_clears_the_location_from_the_admin_events_tab(client: Client):
        _login(client, "loc_admin_event", fog_role=Member.FogRole.ADMIN)
        location = LocationFactory(name="Events Stage")
        with patch.object(CommunityEvent, "announce"):
            client.post(reverse("hub_event_add"), _event_payload(area=str(location.pk)))
        event = CommunityEvent.objects.get(title="Forge Night")
        assert event.area == location
        client.post(reverse("hub_event_edit", args=[event.pk]), _event_payload())
        event.refresh_from_db()
        assert event.area is None

    def it_sets_and_clears_the_location_on_a_member_proposal(client: Client):
        user = _login(client, "loc_proposer")
        location = LocationFactory(name="Common Area")
        event = CommunityEventFactory(community=True, pending=True, submitted_by=user, area=location)
        page = client.get(reverse("hub_propose_event_edit", args=[event.pk]))
        assert 'name="area"' in page.content.decode()
        client.post(reverse("hub_propose_event_edit", args=[event.pk]), _event_payload())
        event.refresh_from_db()
        assert event.area is None
        client.post(reverse("hub_propose_event_edit", args=[event.pk]), _event_payload(area=str(location.pk)))
        event.refresh_from_db()
        assert event.area == location

    def it_renders_the_picker_beside_the_free_text_on_the_editor(client: Client):
        user = _login(client, "loc_lead_page")
        guild = GuildFactory(guild_lead=user.member)
        html = client.get(reverse("hub_guild_event_add", args=[guild.pk])).content.decode()
        assert html.index('name="area"') < html.index('name="location"')
        assert "Address or room details" in html


def describe_orientation_type_settings():
    def it_sets_and_clears_a_guild_types_location(client: Client):
        user = _login(client, "loc_otype_lead")
        guild = GuildFactory(guild_lead=user.member)
        location = LocationFactory(name="Woodshop", guild=guild)
        client.post(
            reverse("hub_guild_orientation_types_save", args=[guild.pk]),
            _types_payload(area=str(location.pk), default_location="By the lathe"),
        )
        orientation_type = OrientationType.objects.get(guild=guild)
        assert orientation_type.area == location
        assert orientation_type.default_location == "By the lathe"
        client.post(
            reverse("hub_guild_orientation_types_save", args=[guild.pk]),
            _types_payload(**{"id": str(orientation_type.pk)}) | {"otypes-INITIAL_FORMS": "1"},
        )
        orientation_type.refresh_from_db()
        assert orientation_type.area is None

    def it_sets_and_clears_an_equipment_types_location(client: Client):
        user = _login(client, "loc_otype_equipment")
        equipment = EquipmentFactory(name="CNC Machine")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        location = LocationFactory(name="CNC Area")
        save_url = reverse("hub_equipment_orientation_types_save", args=[equipment.slug])
        client.post(save_url, _types_payload(name="Operator Basics", area=str(location.pk)))
        orientation_type = equipment.owned_orientation_types.get()
        assert orientation_type.area == location
        client.post(
            save_url,
            _types_payload(name="Operator Basics", id=str(orientation_type.pk)) | {"otypes-INITIAL_FORMS": "1"},
        )
        orientation_type.refresh_from_db()
        assert orientation_type.area is None

    def it_renders_the_picker_in_each_type_row_and_the_add_template(client: Client):
        user = _login(client, "loc_otype_page", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(guild_lead=user.member)
        OrientationTypeFactory(guild=guild, name="Shop Basics")
        html = client.get(reverse("hub_guild_orientations", args=[guild.pk])).content.decode()
        assert 'name="otypes-0-area"' in html
        assert 'name="otypes-__prefix__-area"' in html


def describe_equipment_details():
    def it_sets_and_clears_the_equipment_location(client: Client):
        user = _login(client, "loc_equipment")
        equipment = EquipmentFactory(name="Laser Engraver")
        EquipmentStaffMembershipFactory(equipment=equipment, member=user.member)
        location = LocationFactory(name="Tech Area")
        save_url = reverse("hub_equipment_details_save", args=[equipment.slug])
        payload = {"name": equipment.name, "kind": Equipment.Kind.TOOL, "is_active": "on"}
        client.post(save_url, payload | {"area": str(location.pk)})
        equipment.refresh_from_db()
        assert equipment.area == location
        client.post(save_url, payload)
        equipment.refresh_from_db()
        assert equipment.area is None

    def it_renders_the_picker_on_the_details_tab(client: Client):
        _login(client, "loc_equipment_page", fog_role=Member.FogRole.ADMIN)
        equipment = EquipmentFactory()
        html = client.get(reverse("hub_equipment_manage", args=[equipment.slug])).content.decode()
        assert 'name="area"' in html
        assert "Where to find it" in html


def describe_a_new_orientation_made_from_the_equipment_form():
    def it_starts_in_the_equipments_location(client: Client):
        _login(client, "loc_new_type_add", fog_role=Member.FogRole.ADMIN)
        location = LocationFactory(name="CNC Area")
        payload = {
            "name": "CNC Machine",
            "kind": Equipment.Kind.TOOL,
            "is_active": "on",
            "area": str(location.pk),
            EquipmentForm.NEW_TYPE_FIELD: "1",
            "new_type-name": "Operator Basics",
            "new_type-duration_minutes": "60",
            "new_type-default_seats": "2",
            "new_type-price": "",
            "new_type-default_location": "",
        }
        assert client.post(reverse("hub_equipment_add"), payload).status_code == 302
        equipment = Equipment.objects.get(name="CNC Machine")
        assert equipment.area == location
        assert equipment.unlocking_orientations.get().area == location

    def it_starts_with_no_location_when_the_equipment_has_none(client: Client):
        _login(client, "loc_new_type_none", fog_role=Member.FogRole.ADMIN)
        payload = {
            "name": "Band Saw",
            "kind": Equipment.Kind.TOOL,
            "is_active": "on",
            EquipmentForm.NEW_TYPE_FIELD: "1",
            "new_type-name": "Saw Basics",
            "new_type-duration_minutes": "60",
            "new_type-default_seats": "2",
            "new_type-price": "",
            "new_type-default_location": "",
        }
        client.post(reverse("hub_equipment_add"), payload)
        equipment = Equipment.objects.get(name="Band Saw")
        assert equipment.unlocking_orientations.get().area is None


def describe_the_admin_locations_page():
    def it_turns_away_a_member(client: Client):
        _login(client, "loc_page_member")
        assert client.get(reverse("hub_admin_locations")).status_code == 403
        location = LocationFactory()
        response = client.post(reverse("hub_admin_location_edit", args=[location.pk]), {"name": "Taken"})
        assert response.status_code == 403
        location.refresh_from_db()
        assert location.name != "Taken"

    def it_sends_a_visitor_to_log_in(client: Client):
        assert client.get(reverse("hub_admin_locations")).status_code == 302

    def it_lists_every_location_with_its_guild_and_links(client: Client):
        _login(client, "loc_page_list", fog_role=Member.FogRole.ADMIN)
        front = LocationFactory(name="Front Studio", guild=GuildFactory(name="Visual Arts"))
        stage = LocationFactory(name="Events Stage")
        front.shares_space_with.add(stage)
        LocationFactory(name="Old Annex", is_active=False)
        ClassOfferingFactory(area=front)
        html = client.get(reverse("hub_admin_locations")).content.decode()
        assert html.index("Front Studio") < html.index("Old Annex")
        assert "Visual Arts · Shares space with Events Stage" in html
        assert "Set on 1 class, 0 events, 0 orientations, 0 equipment" in html
        assert "Inactive" in html
        assert reverse("hub_admin_location_edit", args=[front.pk]) in html

    def it_counts_each_kind_without_multiplying_the_others(client: Client):
        _login(client, "loc_page_counts", fog_role=Member.FogRole.ADMIN)
        location = LocationFactory(name="Busy Room")
        for _ in range(2):
            ClassOfferingFactory(area=location)
        for _ in range(3):
            CommunityEventFactory(area=location)
        OrientationTypeFactory(name="Kiln Basics", area=location)
        OrientationTypeFactory(name="Wheel Basics", area=location)
        EquipmentFactory(area=location)
        html = client.get(reverse("hub_admin_locations")).content.decode()
        assert "Set on 2 classes, 3 events, 2 orientations, 1 equipment" in html

    def it_lists_every_location_in_the_same_number_of_queries(client: Client):
        _login(client, "loc_page_queries", fog_role=Member.FogRole.ADMIN)
        url = reverse("hub_admin_locations")
        LocationFactory(guild=GuildFactory())
        client.get(url)  # warm the per session reads
        with CaptureQueriesContext(connection) as one:
            client.get(url)
        for _ in range(4):
            busy = LocationFactory(guild=GuildFactory())
            busy.shares_space_with.add(LocationFactory())
            ClassOfferingFactory(area=busy)
            CommunityEventFactory(area=busy)
            EquipmentFactory(area=busy)
        with CaptureQueriesContext(connection) as many:
            client.get(url)
        assert len(many.captured_queries) == len(one.captured_queries)

    def it_adds_a_location_with_a_guild_and_a_link(client: Client):
        _login(client, "loc_page_add", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory(name="Glass")
        cold = LocationFactory(name="Cold Glass Room")
        response = client.post(
            reverse("hub_admin_locations"),
            {"name": "Hot Glass Room", "guild": str(guild.pk), "note": "Kilns", "shares_space_with": [str(cold.pk)]},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("hub_admin_locations")
        created = Location.objects.get(name="Hot Glass Room")
        assert (created.guild, created.note, created.is_active) == (guild, "Kilns", True)
        assert list(cold.shares_space_with.all()) == [created]

    def it_refuses_a_duplicate_name(client: Client):
        _login(client, "loc_page_dupe", fog_role=Member.FogRole.ADMIN)
        LocationFactory(name="Kitchen")
        response = client.post(reverse("hub_admin_locations"), {"name": "Kitchen"})
        assert response.status_code == 200
        assert "name" in response.context["form"].errors
        assert Location.objects.filter(name="Kitchen").count() == 1

    def it_edits_the_guild_the_links_and_deactivates(client: Client):
        _login(client, "loc_page_edit", fog_role=Member.FogRole.ADMIN)
        location = LocationFactory(name="Woodshop", guild=GuildFactory())
        sanding = LocationFactory(name="Woodshop Sanding Area")
        location.shares_space_with.add(sanding)
        new_guild = GuildFactory(name="Woodworking")
        edit_url = reverse("hub_admin_location_edit", args=[location.pk])
        assert client.get(edit_url).status_code == 200
        response = client.post(edit_url, {"name": "Woodshop", "guild": str(new_guild.pk), "note": ""})
        assert response.status_code == 302
        location.refresh_from_db()
        assert location.guild == new_guild
        assert location.is_active is False
        assert list(location.shares_space_with.all()) == []

    def it_shows_the_edit_form_again_when_refused(client: Client):
        _login(client, "loc_page_bad_edit", fog_role=Member.FogRole.ADMIN)
        location = LocationFactory(name="Garden")
        response = client.post(reverse("hub_admin_location_edit", args=[location.pk]), {"name": ""})
        assert response.status_code == 200
        assert "name" in response.context["form"].errors

    def it_never_links_a_location_to_itself():
        location = LocationFactory()
        other = LocationFactory()
        assert list(LocationForm(instance=location).fields["shares_space_with"].queryset) == [other]

    def it_starts_a_new_location_active_without_asking():
        assert "is_active" not in LocationForm().fields
        assert "is_active" in LocationForm(instance=LocationFactory()).fields

    def it_keeps_a_deactivated_guild_already_set():
        retired = GuildFactory(name="Retired", is_active=False)
        location = LocationFactory(guild=retired)
        assert retired in LocationForm(instance=location).fields["guild"].queryset
        assert retired not in LocationForm().fields["guild"].queryset

    def it_puts_a_card_on_admin_tools_for_an_admin_only(client: Client):
        _login(client, "loc_tools_admin", fog_role=Member.FogRole.ADMIN)
        assert reverse("hub_admin_locations") in client.get(reverse("hub_admin_tools")).content.decode()
        client.logout()
        user = _login(client, "loc_tools_lead")
        GuildFactory(guild_lead=user.member)
        assert reverse("hub_admin_locations") not in client.get(reverse("hub_admin_tools")).content.decode()


def describe_the_guild_page():
    def it_shows_each_location_with_its_light(client: Client):
        _login(client, "loc_guild_page")
        guild = GuildFactory()
        kiln_room = LocationFactory(name="Kiln Room", guild=guild)
        LocationFactory(name="Bench Room", guild=guild)
        EquipmentReservationFactory(
            equipment=EquipmentFactory(name="Kiln Zq", area=kiln_room),
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(minutes=55),
        )
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        block = html.split("data-area-status")[1].split("</section>")[0]
        assert 'data-area-light="free"' in block
        assert 'data-area-light="in_use"' in block
        assert "In use: Reserved: Kiln Zq until" in block
        assert html.index("pl-guild-statbar") < html.index("data-area-status")

    def it_names_a_private_class_only_as_a_private_class(client: Client):
        _login(client, "loc_guild_private")
        guild = GuildFactory()
        location = LocationFactory(name="Print Studio", guild=guild)
        offering = ClassOfferingFactory(
            title="Secret Workshop Qv", status=ClassOffering.Status.PUBLISHED, is_private=True, area=location
        )
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() - timedelta(minutes=10))
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        block = html.split("data-area-status")[1].split("</section>")[0]
        assert "In use: Private class until" in block
        assert "Secret Workshop Qv" not in html

    def it_renders_the_lights_on_the_public_guilds_site_without_private_titles(client: Client):
        guild = GuildFactory()
        location = LocationFactory(name="Print Studio", guild=guild)
        private = ClassOfferingFactory(
            title="Secret Workshop Qw", status=ClassOffering.Status.PUBLISHED, is_private=True, area=location
        )
        ClassSessionFactory(class_offering=private, starts_at=timezone.now() - timedelta(minutes=10))
        EquipmentReservationFactory(
            equipment=EquipmentFactory(name="Etching Press", area=location),
            starts_at=timezone.now() + timedelta(minutes=20),
            ends_at=timezone.now() + timedelta(minutes=80),
        )
        with override_settings(**GUILDS_SETTINGS):
            response = client.get(f"/guilds/{guild.slug}/", HTTP_HOST=GUILDS_HOST)
        assert response.status_code == 200
        html = response.content.decode()
        assert "css/locations.css" in html
        block = html.split("data-area-status")[1].split("</section>")[0]
        assert 'data-area-light="in_use"' in block
        assert "In use: Private class until" in block
        assert "Secret Workshop Qw" not in html

    def it_renders_nothing_without_a_location(client: Client):
        _login(client, "loc_guild_none")
        guild = GuildFactory()
        LocationFactory(guild=guild, is_active=False)
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "data-area-status" not in html


def describe_detail_pages():
    def it_names_the_location_on_the_event_page(client: Client):
        event = CommunityEventFactory(
            community=True, area=LocationFactory(name="Events Stage Qx"), location="Bring a chair"
        )
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "<span data-event-area>Events Stage Qx</span> · Bring a chair" in html

    def it_names_only_the_location_when_there_is_no_free_text(client: Client):
        event = CommunityEventFactory(community=True, area=LocationFactory(name="Events Stage Qy"))
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "<span data-event-area>Events Stage Qy</span></span>" in html

    def it_leaves_the_event_page_as_it_was_without_one(client: Client):
        event = CommunityEventFactory(community=True, location="Main Studio Qz")
        html = client.get(reverse("hub_event_detail", args=[event.pk])).content.decode()
        assert "data-event-area" not in html
        assert "Main Studio Qz" in html

    def it_names_the_location_on_the_class_page(client: Client):
        offering = ClassOfferingFactory(
            status=ClassOffering.Status.PUBLISHED, area=LocationFactory(name="Hot Glass Qx")
        )
        ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=3))
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert "<strong data-class-area>Hot Glass Qx</strong>" in html

    def it_leaves_the_class_page_as_it_was_without_one(client: Client):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        html = client.get(reverse("classes:public_class_detail", kwargs={"slug": offering.slug})).content.decode()
        assert "data-class-area" not in html

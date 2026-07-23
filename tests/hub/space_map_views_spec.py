"""BDD specs for the space map's read surface: the /info/ map-vs-lightbox branch, the
public detail panel and its CTA states, and the admin gate on every editor endpoint
(including the coordinate JSON endpoint's two-write-path guarantee)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import MapHotspot, Member, Space
from tests.membership.factories import (
    FloorplanFactory,
    GuildFactory,
    MapHotspotFactory,
    MembershipPlanFactory,
    SpaceFactory,
    SpaceRequestFactory,
)


def _user_with_role(username: str, *, fog_role: str = Member.FogRole.MEMBER) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="pass")
    member = user.member
    member.fog_role = fog_role
    member.status = Member.Status.ACTIVE
    member.save(update_fields=["fog_role", "status"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_org_info_map():
    def it_falls_back_to_the_legacy_lightbox_with_no_floors(client: Client):
        response = client.get(reverse("hub_org_info"))
        assert response.status_code == 200
        assert b"pl-map-viewport" not in response.content
        assert b"The facility map is coming soon" in response.content

    def it_renders_the_interactive_map_once_a_floor_is_published(client: Client):
        floor = FloorplanFactory(name="Ground Floor")
        MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="A9"))
        response = client.get(reverse("hub_org_info"))
        assert response.status_code == 200
        assert b"pl-map-viewport" in response.content
        assert b"Ground Floor" in response.content
        assert b"A9" in response.content

    def it_hides_a_draft_floor_from_members(client: Client):
        FloorplanFactory(name="Secret Basement", is_published=False)
        response = client.get(reverse("hub_org_info"))
        assert b"Secret Basement" not in response.content

    def it_notes_a_floor_with_no_markers(client: Client):
        FloorplanFactory(name="Empty Floor")
        response = client.get(reverse("hub_org_info"))
        assert b"No spaces marked on this floor yet." in response.content

    def it_is_public(client: Client):
        FloorplanFactory()
        assert client.get(reverse("hub_org_info")).status_code == 200

    def it_shows_a_reviewer_their_pending_count(client: Client):
        _user_with_role("adm-count", fog_role=Member.FogRole.ADMIN)
        SpaceRequestFactory()
        client.login(username="adm-count", password="pass")
        response = client.get(reverse("hub_org_info"))
        assert b"Space requests (1)" in response.content

    def it_lists_a_members_own_pending_requests_with_a_withdraw_button(client: Client):
        user = _user_with_role("mine")
        SpaceRequestFactory(requester=user.member, space=SpaceFactory(space_id="B4"))
        client.login(username="mine", password="pass")
        response = client.get(reverse("hub_org_info"))
        assert b"Your space requests" in response.content
        assert b"B4" in response.content
        assert b"Withdraw" in response.content


@pytest.mark.django_db
def describe_hotspot_detail():
    def it_is_readable_by_a_guest_who_is_offered_a_login(client: Client):
        hotspot = MapHotspotFactory(space=SpaceFactory(space_id="A9", manual_price=Decimal("420.00")))
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert response.status_code == 200
        assert b"$420.00/mo" in response.content
        assert b"Log in to request" in response.content

    def it_says_price_on_request_when_the_space_has_no_price(client: Client):
        space = SpaceFactory(manual_price=None, size_sqft=None)
        hotspot = MapHotspotFactory(space=space)
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"Price on request" in response.content

    def it_offers_no_cta_on_an_occupied_space(client: Client):
        _user_with_role("m-occ")
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.OCCUPIED))
        client.login(username="m-occ", password="pass")
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"This space is currently taken." in response.content
        assert b"Send request" not in response.content

    def it_explains_maintenance_instead_of_offering_a_cta(client: Client):
        _user_with_role("m-maint")
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.MAINTENANCE))
        client.login(username="m-maint", password="pass")
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"under maintenance" in response.content

    def it_offers_the_request_form_to_an_active_member(client: Client):
        _user_with_role("m-active")
        hotspot = MapHotspotFactory()
        client.login(username="m-active", password="pass")
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"Request to lease" in response.content
        assert b"Send request" in response.content

    def it_blocks_an_inactive_member_with_an_explanation(client: Client):
        user = _user_with_role("m-lapsed")
        user.member.status = Member.Status.FORMER
        user.member.save(update_fields=["status"])
        hotspot = MapHotspotFactory()
        client.login(username="m-lapsed", password="pass")
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"needs an active membership" in response.content

    def it_shows_the_spec_2_placeholder_for_a_meeting_room(client: Client):
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.MEETING_ROOM)
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"coming soon" in response.content

    def it_shows_a_pending_state_with_a_withdraw_button(client: Client):
        user = _user_with_role("m-pending")
        hotspot = MapHotspotFactory()
        SpaceRequestFactory(requester=user.member, space=hotspot.space)
        client.login(username="m-pending", password="pass")
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"Request pending" in response.content
        assert b"Withdraw request" in response.content

    def it_404s_an_unknown_marker(client: Client):
        assert client.get(reverse("hub_map_hotspot_detail", args=[9999])).status_code == 404


@pytest.mark.django_db
def describe_editor_gating():
    @pytest.mark.parametrize(
        ("url_name", "args", "method"),
        [
            ("hub_org_map_edit", [], "get"),
            ("hub_org_map_floors_save", [], "post"),
            ("hub_map_hotspots_save", [], "post"),
        ],
    )
    def it_403s_a_plain_member(client: Client, url_name, args, method):
        _user_with_role("pm-map")
        client.login(username="pm-map", password="pass")
        response = getattr(client, method)(reverse(url_name, args=args))
        assert response.status_code == 403

    def it_redirects_an_anonymous_visitor_to_login(client: Client):
        response = client.get(reverse("hub_org_map_edit"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_shows_an_admin_the_editor(client: Client):
        _user_with_role("adm-map", fog_role=Member.FogRole.ADMIN)
        FloorplanFactory(name="Ground Floor")
        client.login(username="adm-map", password="pass")
        response = client.get(reverse("hub_org_map_edit"))
        assert response.status_code == 200
        assert b"Ground Floor" in response.content

    def it_prompts_for_a_floor_before_placement(client: Client):
        _user_with_role("adm-map2", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm-map2", password="pass")
        response = client.get(reverse("hub_org_map_edit"))
        assert b"Add a floor first" in response.content

    def it_selects_the_floor_named_in_the_query_string(client: Client):
        _user_with_role("adm-map3", fog_role=Member.FogRole.ADMIN)
        FloorplanFactory(name="First", sort_order=1)
        second = FloorplanFactory(name="Second", sort_order=2)
        client.login(username="adm-map3", password="pass")
        response = client.get(f"{reverse('hub_org_map_edit')}?tab=placement&floor={second.pk}")
        assert response.context["selected_floor"] == second


@pytest.mark.django_db
def describe_floor_saving():
    def it_saves_a_renamed_floor(client: Client):
        _user_with_role("adm-fs", fog_role=Member.FogRole.ADMIN)
        floor = FloorplanFactory(name="Old Name")
        client.login(username="adm-fs", password="pass")
        response = client.post(
            reverse("hub_org_map_floors_save"),
            {
                "floors-TOTAL_FORMS": "1",
                "floors-INITIAL_FORMS": "1",
                "floors-MIN_NUM_FORMS": "0",
                "floors-MAX_NUM_FORMS": "1000",
                "floors-0-id": str(floor.pk),
                "floors-0-name": "New Name",
                "floors-0-caption": "",
                "floors-0-sort_order": "0",
                "floors-0-is_published": "on",
            },
        )
        assert response.status_code == 302
        floor.refresh_from_db()
        assert floor.name == "New Name"

    def it_reports_an_invalid_row_rather_than_saving_it(client: Client):
        _user_with_role("adm-fs2", fog_role=Member.FogRole.ADMIN)
        floor = FloorplanFactory(name="Keep Me")
        client.login(username="adm-fs2", password="pass")
        client.post(
            reverse("hub_org_map_floors_save"),
            {
                "floors-TOTAL_FORMS": "1",
                "floors-INITIAL_FORMS": "1",
                "floors-MIN_NUM_FORMS": "0",
                "floors-MAX_NUM_FORMS": "1000",
                "floors-0-id": str(floor.pk),
                "floors-0-name": "",
                "floors-0-caption": "",
                "floors-0-sort_order": "0",
            },
            follow=True,
        )
        floor.refresh_from_db()
        assert floor.name == "Keep Me"

    def it_deletes_a_populated_floor_and_its_markers(client: Client):
        _user_with_role("adm-fd", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-fd", password="pass")
        response = client.post(reverse("hub_org_map_floor_delete", args=[hotspot.floorplan.pk]))
        assert response.status_code == 302
        assert not MapHotspot.objects.filter(pk=hotspot.pk).exists()

    def it_403s_a_plain_member_deleting_a_floor(client: Client):
        _user_with_role("pm-fd")
        floor = FloorplanFactory()
        client.login(username="pm-fd", password="pass")
        assert client.post(reverse("hub_org_map_floor_delete", args=[floor.pk])).status_code == 403


@pytest.mark.django_db
def describe_marker_saving():
    def _marker_payload(floor, hotspot, **overrides):
        data = {
            "floor_id": str(floor.pk),
            "markers-TOTAL_FORMS": "1",
            "markers-INITIAL_FORMS": "1",
            "markers-MIN_NUM_FORMS": "0",
            "markers-MAX_NUM_FORMS": "1000",
            "markers-0-id": str(hotspot.pk),
            "markers-0-floorplan": str(floor.pk),
            "markers-0-kind": hotspot.kind,
            "markers-0-shape": hotspot.shape,
            "markers-0-space": str(hotspot.space_id or ""),
            "markers-0-label": hotspot.label,
            "markers-0-description": "Newly described.",
            "markers-0-sort_order": "0",
        }
        data.update(overrides)
        return data

    def it_saves_structural_fields_without_touching_coordinates(client: Client):
        _user_with_role("adm-ms", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(x=Decimal("12.50"), y=Decimal("33.25"))
        client.login(username="adm-ms", password="pass")
        response = client.post(reverse("hub_map_hotspots_save"), _marker_payload(hotspot.floorplan, hotspot))
        assert response.status_code == 302
        hotspot.refresh_from_db()
        assert hotspot.description == "Newly described."
        # The two-write-path guarantee: a structural save never moves a dragged marker.
        assert (hotspot.x, hotspot.y) == (Decimal("12.50"), Decimal("33.25"))

    def it_rejects_a_studio_marker_with_no_space(client: Client):
        _user_with_role("adm-ms2", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-ms2", password="pass")
        client.post(
            reverse("hub_map_hotspots_save"),
            _marker_payload(hotspot.floorplan, hotspot, **{"markers-0-space": ""}),
            follow=True,
        )
        hotspot.refresh_from_db()
        assert hotspot.description == ""

    def it_gives_a_region_a_starter_box_when_it_has_none(client: Client):
        _user_with_role("adm-ms3", fog_role=Member.FogRole.ADMIN)
        pin = MapHotspotFactory(shape=MapHotspot.Shape.PIN, w=None, h=None)
        client.login(username="adm-ms3", password="pass")
        client.post(
            reverse("hub_map_hotspots_save"),
            _marker_payload(pin.floorplan, pin, **{"markers-0-shape": MapHotspot.Shape.REGION}),
        )
        pin.refresh_from_db()
        assert pin.shape == MapHotspot.Shape.REGION
        assert pin.w is not None and pin.h is not None

    def it_clears_the_box_when_a_region_becomes_a_pin(client: Client):
        _user_with_role("adm-ms4", fog_role=Member.FogRole.ADMIN)
        region = MapHotspotFactory()
        client.login(username="adm-ms4", password="pass")
        client.post(
            reverse("hub_map_hotspots_save"),
            _marker_payload(region.floorplan, region, **{"markers-0-shape": MapHotspot.Shape.PIN}),
        )
        region.refresh_from_db()
        assert (region.w, region.h) == (None, None)

    def it_404s_an_unknown_floor(client: Client):
        _user_with_role("adm-ms5", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm-ms5", password="pass")
        assert client.post(reverse("hub_map_hotspots_save"), {"floor_id": "9999"}).status_code == 404


@pytest.mark.django_db
def describe_marker_position_endpoint():
    def _post(client, hotspot, payload):
        return client.post(
            reverse("hub_map_hotspot_position", args=[hotspot.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def it_403s_a_plain_member(client: Client):
        _user_with_role("pm-pos")
        hotspot = MapHotspotFactory()
        client.login(username="pm-pos", password="pass")
        response = _post(client, hotspot, {"x": 1, "y": 1, "w": 5, "h": 5})
        assert response.status_code == 403
        assert response.json()["error"] == "Forbidden"

    def it_stores_a_dragged_region(client: Client):
        _user_with_role("adm-pos", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-pos", password="pass")
        response = _post(client, hotspot, {"x": 20.5, "y": 30.25, "w": 10, "h": 10})
        assert response.status_code == 200
        hotspot.refresh_from_db()
        assert (hotspot.x, hotspot.y) == (Decimal("20.50"), Decimal("30.25"))

    def it_stores_a_dragged_pin_without_dimensions(client: Client):
        _user_with_role("adm-pos2", fog_role=Member.FogRole.ADMIN)
        pin = MapHotspotFactory(shape=MapHotspot.Shape.PIN, w=None, h=None)
        client.login(username="adm-pos2", password="pass")
        response = _post(client, pin, {"x": 40, "y": 40})
        assert response.status_code == 200
        assert response.json()["w"] is None

    def it_rejects_a_region_that_runs_off_the_edge(client: Client):
        _user_with_role("adm-pos3", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-pos3", password="pass")
        response = _post(client, hotspot, {"x": 95, "y": 10, "w": 20, "h": 10})
        assert response.status_code == 400
        assert "runs off the edge" in response.json()["error"]

    def it_rejects_an_out_of_range_coordinate(client: Client):
        _user_with_role("adm-pos4", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-pos4", password="pass")
        assert _post(client, hotspot, {"x": 140, "y": 10, "w": 5, "h": 5}).status_code == 400

    def it_rejects_a_region_with_no_box(client: Client):
        _user_with_role("adm-pos5", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-pos5", password="pass")
        response = _post(client, hotspot, {"x": 10, "y": 10})
        assert response.status_code == 400
        assert "needs a width and height" in response.json()["error"]

    def it_rejects_a_body_that_is_not_json(client: Client):
        _user_with_role("adm-pos6", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-pos6", password="pass")
        response = client.post(
            reverse("hub_map_hotspot_position", args=[hotspot.pk]),
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def it_leaves_structural_fields_alone(client: Client):
        _user_with_role("adm-pos7", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        hotspot = MapHotspotFactory(space=SpaceFactory(sublet_guild=guild), label="Untouched")
        client.login(username="adm-pos7", password="pass")
        _post(client, hotspot, {"x": 5, "y": 5, "w": 5, "h": 5})
        hotspot.refresh_from_db()
        assert hotspot.label == "Untouched"
        assert hotspot.kind == MapHotspot.Kind.STUDIO

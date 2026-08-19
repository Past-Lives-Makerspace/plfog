"""BDD specs for the space map's read surface: the /spaces/ map-vs-lightbox branch, the
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
        response = client.get(reverse("hub_spaces"))
        assert response.status_code == 200
        assert b"pl-map-viewport" not in response.content
        assert b"The facility map is coming soon" in response.content

    def it_renders_the_interactive_map_once_a_floor_is_published(client: Client):
        floor = FloorplanFactory(name="Ground Floor")
        MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="A9"))
        response = client.get(reverse("hub_spaces"))
        assert response.status_code == 200
        assert b"pl-map-viewport" in response.content
        assert b"Ground Floor" in response.content
        assert b"A9" in response.content

    def it_hides_a_draft_floor_from_members(client: Client):
        FloorplanFactory(name="Secret Basement", is_published=False)
        response = client.get(reverse("hub_spaces"))
        assert b"Secret Basement" not in response.content

    def it_notes_a_floor_with_no_markers(client: Client):
        FloorplanFactory(name="Empty Floor")
        response = client.get(reverse("hub_spaces"))
        assert b"No spaces marked on this floor yet." in response.content

    def it_is_public(client: Client):
        FloorplanFactory()
        assert client.get(reverse("hub_spaces")).status_code == 200

    def describe_the_drawn_canvas():
        def it_draws_a_floor_that_has_no_image_at_all(client: Client):
            floor = FloorplanFactory(name="Drawn Floor", image="", aspect_ratio=Decimal("1.80"))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="D1"))
            response = client.get(reverse("hub_spaces"))
            assert b"pl-map-canvas" in response.content
            assert b"aspect-ratio: 1.80" in response.content
            assert b"pl-map-underlay" not in response.content
            assert b"D1" in response.content

        def it_keeps_an_uploaded_plan_as_a_faint_tracing_underlay(client: Client):
            FloorplanFactory(name="Traced Floor")
            response = client.get(reverse("hub_spaces"))
            assert b"pl-map-underlay" in response.content

        def it_shows_a_legend_with_live_counts(client: Client):
            floor = FloorplanFactory(name="Legend Floor", image="")
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.OCCUPIED))
            response = client.get(reverse("hub_spaces")).content.decode()
            assert "pl-map-legend__swatch--available" in response
            assert "pl-map-legend__swatch--occupied" in response
            assert "pl-map-legend__swatch--maintenance" not in response

        def it_colours_a_room_from_its_live_status(client: Client):
            floor = FloorplanFactory(name="Status Floor", image="")
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.MAINTENANCE))
            response = client.get(reverse("hub_spaces")).content.decode()
            assert "pl-map-marker--maintenance" in response

        def it_drops_the_text_out_of_a_room_too_small_to_hold_it(client: Client):
            floor = FloorplanFactory(name="Tiny Floor", image="")
            MapHotspotFactory(
                floorplan=floor,
                space=SpaceFactory(space_id="TINY"),
                w=Decimal("0.80"),
                h=Decimal("0.80"),
            )
            response = client.get(reverse("hub_spaces")).content.decode()
            assert "pl-map-marker--text-minimal" in response
            # The name still reaches every reader, through the accessible name and the list.
            assert "TINY" in response

    def describe_walls():
        def it_draws_a_wall_but_not_as_a_control(client: Client):
            floor = FloorplanFactory(is_published=True)
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="B1", status=Space.Status.AVAILABLE))
            wall = MapHotspotFactory(floorplan=floor, space=None, kind=MapHotspot.Kind.WALL, label="")
            body = client.get(reverse("hub_spaces")).content.decode()
            # The wall is drawn (its bar class is present)...
            assert "pl-map-marker--wall" in body
            # ...but it is neither a clickable detail control nor a listed row.
            assert reverse("hub_map_hotspot_detail", args=[wall.pk]) not in body
            assert f'id="hotspot-{wall.pk}"' not in body

    def describe_the_accessible_list():
        def it_gives_every_row_what_the_browser_filter_reads(client: Client):
            floor = FloorplanFactory(name="List Floor", image="")
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="A12", status=Space.Status.OCCUPIED))
            response = client.get(reverse("hub_spaces")).content.decode()
            assert 'class="pl-map-list__row"' in response
            assert f'data-floor="{floor.pk}"' in response
            assert 'data-status="occupied"' in response
            assert "data-search=" in response

        def it_offers_a_search_box_and_status_chips_with_live_counts(client: Client):
            floor = FloorplanFactory(name="Chip Floor", image="")
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.OCCUPIED))
            response = client.get(reverse("hub_spaces")).content.decode()
            assert f'id="pl-map-search-{floor.pk}"' in response
            assert 'aria-label="Filter Chip Floor by status"' in response
            assert "setListStatus('available')" in response
            # No maintenance on this floor, so no chip pretending there is.
            assert "setListStatus('maintenance')" not in response

        def it_leads_with_the_spaces_a_member_could_rent(client: Client):
            floor = FloorplanFactory(name="Order Floor", image="")
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="TAKEN", status=Space.Status.OCCUPIED))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="FREE", status=Space.Status.AVAILABLE))
            body = client.get(reverse("hub_spaces")).content.decode()
            rows = body.split('class="pl-map-list__row"')
            assert "FREE" in rows[1] and "TAKEN" in rows[2]

        def it_states_the_whole_list_when_the_browser_cannot_filter_it(client: Client):
            # The server-rendered count is the no-JavaScript truth: every row is on the page.
            floor = FloorplanFactory(name="Plain Floor", image="")
            MapHotspotFactory(floorplan=floor, space=SpaceFactory())
            response = client.get(reverse("hub_spaces")).content.decode()
            assert "Showing all 1 space on Plain Floor." in response

    def it_hides_the_review_queue_entry_point_even_from_a_reviewer(client: Client):
        # The request flow was lightened: the review queue is dormant (view/URL kept) and its
        # entry point on the Spaces page is gone — even an admin no longer sees it there.
        _user_with_role("adm-count", fog_role=Member.FogRole.ADMIN)
        SpaceRequestFactory()
        client.login(username="adm-count", password="pass")
        response = client.get(reverse("hub_spaces"))
        assert b"Space requests (1)" not in response.content
        assert reverse("hub_space_request_review_queue").encode() not in response.content

    def it_lists_a_members_own_pending_requests_with_a_withdraw_button(client: Client):
        user = _user_with_role("mine")
        SpaceRequestFactory(requester=user.member, space=SpaceFactory(space_id="B4"))
        client.login(username="mine", password="pass")
        response = client.get(reverse("hub_spaces"))
        assert b"Your Space Requests" in response.content
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

    def it_links_a_studios_sublet_guild_to_its_page(client: Client):
        guild = GuildFactory(name="Glass Guild")
        hotspot = MapHotspotFactory(space=SpaceFactory(sublet_guild=guild))
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert reverse("hub_guild_detail", args=[guild.slug]).encode() in response.content
        assert b"Glass Guild" in response.content

    def it_links_a_facility_marker_to_its_guild_page(client: Client):
        guild = GuildFactory(name="Ceramics Guild")
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Ceramics", guild=guild)
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert reverse("hub_guild_detail", args=[guild.slug]).encode() in response.content
        assert b"Ceramics Guild" in response.content

    def it_shows_no_guild_link_when_neither_is_set(client: Client):
        hotspot = MapHotspotFactory(space=SpaceFactory(sublet_guild=None))
        response = client.get(reverse("hub_map_hotspot_detail", args=[hotspot.pk]))
        assert b"pl-map-detail__guild-link" not in response.content


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

    def it_offers_a_way_back_to_the_members_map(client: Client):
        _user_with_role("adm-back", fog_role=Member.FogRole.ADMIN)
        FloorplanFactory()
        client.login(username="adm-back", password="pass")
        body = client.get(reverse("hub_org_map_edit")).content.decode()
        assert "view the map" in body
        assert reverse("hub_spaces") in body

    def it_lets_an_admin_place_markers_on_a_floor_with_no_image(client: Client):
        # Placement drags against the drawn canvas now, so an image-less floor is workable.
        _user_with_role("adm-drawn", fog_role=Member.FogRole.ADMIN)
        floor = FloorplanFactory(name="Drawn Floor", image="", aspect_ratio=Decimal("2.40"))
        MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="E1"))
        client.login(username="adm-drawn", password="pass")
        response = client.get(f"{reverse('hub_org_map_edit')}?tab=placement&floor={floor.pk}")
        assert b'data-editor-stage style="aspect-ratio: 2.40' in response.content
        assert b"pl-map-underlay" not in response.content
        assert b"E1" in response.content

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
                "floors-0-aspect_ratio": "1.50",
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
                "floors-0-aspect_ratio": "1.50",
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


@pytest.mark.django_db
def describe_marker_status_endpoint():
    """The edit map's click-to-set-status control: admin-only, writes Space.status, and pushes
    back to Airtable (the system of record) without ever 500ing on an Airtable outage."""

    def _post(client, hotspot, status):
        return client.post(reverse("hub_map_hotspot_status", args=[hotspot.pk]), {"status": status})

    def it_403s_a_plain_member(client: Client):
        _user_with_role("pm-st")
        hotspot = MapHotspotFactory()
        client.login(username="pm-st", password="pass")
        response = _post(client, hotspot, "occupied")
        assert response.status_code == 403
        assert response.json()["error"] == "Forbidden"

    @pytest.mark.parametrize("status", [Space.Status.AVAILABLE, Space.Status.OCCUPIED, Space.Status.MAINTENANCE])
    def it_sets_each_status_and_recolours(client: Client, status):
        _user_with_role("adm-st", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE))
        client.login(username="adm-st", password="pass")
        response = _post(client, hotspot, status)
        assert response.status_code == 200
        assert response.json()["availability_class"] == status
        hotspot.space.refresh_from_db()
        assert hotspot.space.status == status

    def it_pushes_the_change_to_airtable(client: Client, monkeypatch):
        _user_with_role("adm-st2", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE))
        pushed = []
        monkeypatch.setattr(
            "airtable_sync.service.sync_space_to_airtable",
            lambda space: pushed.append(space.pk) or "recPUSHED",
        )
        client.login(username="adm-st2", password="pass")
        response = _post(client, hotspot, Space.Status.OCCUPIED)
        assert response.status_code == 200
        assert pushed == [hotspot.space_id]
        assert "warning" not in response.json()

    def it_keeps_the_local_change_when_the_airtable_push_fails(client: Client, monkeypatch):
        _user_with_role("adm-st3", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE))

        def boom(space):
            raise RuntimeError("Airtable down")

        monkeypatch.setattr("airtable_sync.service.sync_space_to_airtable", boom)
        client.login(username="adm-st3", password="pass")
        response = _post(client, hotspot, Space.Status.MAINTENANCE)
        # No 500 — the local change stands and the admin is warned it may revert.
        assert response.status_code == 200
        assert "warning" in response.json()
        hotspot.space.refresh_from_db()
        assert hotspot.space.status == Space.Status.MAINTENANCE

    def it_warns_when_airtable_is_on_but_the_push_returns_nothing(client: Client, monkeypatch, settings):
        # sync_space_to_airtable swallows its own errors and returns None; with sync enabled
        # that None means the push failed, so the admin is warned (but the local change stands).
        settings.AIRTABLE_SYNC_ENABLED = True
        _user_with_role("adm-st6", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE))
        monkeypatch.setattr("airtable_sync.service.sync_space_to_airtable", lambda space: None)
        client.login(username="adm-st6", password="pass")
        response = _post(client, hotspot, Space.Status.OCCUPIED)
        assert response.status_code == 200
        assert "warning" in response.json()
        hotspot.space.refresh_from_db()
        assert hotspot.space.status == Space.Status.OCCUPIED

    def it_rejects_a_facility_marker_with_no_space(client: Client):
        _user_with_role("adm-st4", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Wood Shop")
        client.login(username="adm-st4", password="pass")
        response = _post(client, hotspot, Space.Status.OCCUPIED)
        assert response.status_code == 400

    def it_rejects_an_unknown_status(client: Client):
        _user_with_role("adm-st5", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-st5", password="pass")
        response = _post(client, hotspot, "nonsense")
        assert response.status_code == 400
        hotspot.space.refresh_from_db()
        assert hotspot.space.status == Space.Status.AVAILABLE


@pytest.mark.django_db
def describe_the_spaces_page_split():
    """The Spaces/Wiki split: the tab shell, the deep link, and the legacy /info/ redirect."""

    def it_offers_a_map_tab_and_a_listings_tab(client: Client):
        FloorplanFactory(is_published=True)
        body = client.get(reverse("hub_spaces")).content.decode()
        assert 'class="pl-space-tabs"' in body
        assert ">Map<" in body
        assert ">Listings<" in body

    def it_starts_on_the_map_tab(client: Client):
        floor = FloorplanFactory(is_published=True)
        body = client.get(reverse("hub_spaces")).content.decode()
        assert f"plMap({floor.pk}, 'map')" in body

    def it_opens_the_listings_tab_from_a_deep_link(client: Client):
        floor = FloorplanFactory(is_published=True)
        body = client.get(reverse("hub_spaces") + "?tab=listings").content.decode()
        assert f"plMap({floor.pk}, 'listings')" in body

    def it_ignores_an_unknown_tab_value(client: Client):
        floor = FloorplanFactory(is_published=True)
        body = client.get(reverse("hub_spaces") + "?tab=nonsense").content.decode()
        assert f"plMap({floor.pk}, 'map')" in body

    def it_keeps_the_wiki_prose_off_the_spaces_page(client: Client):
        """Parking and the code of conduct moved — Spaces must not render them."""
        from membership.models import OrgInfoPage

        page = OrgInfoPage.load()
        page.parking = "Free street parking after 5pm."
        page.code_of_conduct = "Be excellent to each other."
        page.save()
        body = client.get(reverse("hub_spaces")).content.decode()
        assert "Free street parking after 5pm." not in body
        assert "Be excellent to each other." not in body

    def describe_the_legacy_info_url():
        def it_permanently_redirects_to_spaces(client: Client):
            response = client.get("/info/")
            assert response.status_code == 301
            assert response["Location"] == reverse("hub_spaces")

        def it_still_lands_on_a_real_page(client: Client):
            """Space-request emails already carry /info/#hotspot-N links."""
            assert client.get("/info/", follow=True).status_code == 200


@pytest.mark.django_db
def describe_marker_editing():
    """The click-a-tile editor modal: open, save (fields + status), create, and delete — all
    admin-only, all answering with the out-of-band tile so the map stays in sync without a reload."""

    def _edit_payload(hotspot, **overrides):
        data = {
            "kind": hotspot.kind,
            "shape": hotspot.shape,
            "space": str(hotspot.space_id or ""),
            "label": hotspot.label,
            "description": "",
            "guild": "",
            "status": "",
        }
        data.update(overrides)
        return data

    def it_403s_a_plain_member_opening_the_editor(client: Client):
        _user_with_role("pm-ed")
        hotspot = MapHotspotFactory()
        client.login(username="pm-ed", password="pass")
        assert client.get(reverse("hub_map_hotspot_edit", args=[hotspot.pk])).status_code == 403

    def it_403s_a_plain_member_creating(client: Client):
        _user_with_role("pm-cr")
        floor = FloorplanFactory()
        client.login(username="pm-cr", password="pass")
        assert client.post(reverse("hub_map_hotspot_create"), {"floor_id": floor.pk}).status_code == 403

    def it_403s_a_plain_member_deleting(client: Client):
        _user_with_role("pm-del")
        hotspot = MapHotspotFactory()
        client.login(username="pm-del", password="pass")
        assert client.post(reverse("hub_map_hotspot_delete", args=[hotspot.pk])).status_code == 403

    def it_opens_the_editor_for_a_marker(client: Client):
        _user_with_role("adm-ed", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(description="A cosy corner studio.")
        client.login(username="adm-ed", password="pass")
        response = client.get(reverse("hub_map_hotspot_edit", args=[hotspot.pk]))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Save marker" in body
        assert "A cosy corner studio." in body
        assert 'name="status"' in body  # a space-bound marker shows the status chips

    def it_hides_status_for_a_marker_with_no_space(client: Client):
        _user_with_role("adm-ed2", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Wood Shop")
        client.login(username="adm-ed2", password="pass")
        body = client.get(reverse("hub_map_hotspot_edit", args=[hotspot.pk])).content.decode()
        assert 'name="status"' not in body

    def it_saves_the_details_members_see_without_moving_the_tile(client: Client):
        _user_with_role("adm-ed3", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        hotspot = MapHotspotFactory(x=Decimal("12.50"), y=Decimal("33.25"))
        client.login(username="adm-ed3", password="pass")
        response = client.post(
            reverse("hub_map_hotspot_edit", args=[hotspot.pk]),
            _edit_payload(hotspot, description="Now with a kiln.", guild=str(guild.pk), status=hotspot.space.status),
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert f'id="hotspot-{hotspot.pk}"' in body  # the recoloured tile comes back...
        assert 'hx-swap-oob="true"' in body  # ...as an out-of-band replacement
        assert response["HX-Trigger"] == "close-marker-edit"
        hotspot.refresh_from_db()
        assert hotspot.description == "Now with a kiln."
        assert hotspot.guild_id == guild.pk
        assert (hotspot.x, hotspot.y) == (Decimal("12.50"), Decimal("33.25"))

    def it_saves_a_facility_tiles_label_and_guild(client: Client):
        _user_with_role("adm-ed9", fog_role=Member.FogRole.ADMIN)
        guild = GuildFactory()
        hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Wood Shop")
        client.login(username="adm-ed9", password="pass")
        response = client.post(
            reverse("hub_map_hotspot_edit", args=[hotspot.pk]),
            _edit_payload(hotspot, label="Ceramics Studio", guild=str(guild.pk)),
        )
        assert response.status_code == 200
        hotspot.refresh_from_db()
        assert hotspot.label == "Ceramics Studio"
        assert hotspot.guild_id == guild.pk

    def it_sets_the_status_and_pushes_to_airtable(client: Client, monkeypatch):
        _user_with_role("adm-ed4", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE))
        pushed = []
        monkeypatch.setattr(
            "airtable_sync.service.sync_space_to_airtable",
            lambda space: pushed.append(space.pk) or "recX",
        )
        client.login(username="adm-ed4", password="pass")
        response = client.post(
            reverse("hub_map_hotspot_edit", args=[hotspot.pk]),
            _edit_payload(hotspot, status=Space.Status.OCCUPIED),
        )
        assert response.status_code == 200
        hotspot.space.refresh_from_db()
        assert hotspot.space.status == Space.Status.OCCUPIED
        assert pushed == [hotspot.space_id]

    def it_skips_the_airtable_push_when_the_status_is_unchanged(client: Client, monkeypatch):
        _user_with_role("adm-ed8", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE))
        pushed = []
        monkeypatch.setattr("airtable_sync.service.sync_space_to_airtable", lambda space: pushed.append(space.pk))
        client.login(username="adm-ed8", password="pass")
        client.post(
            reverse("hub_map_hotspot_edit", args=[hotspot.pk]), _edit_payload(hotspot, status=Space.Status.AVAILABLE)
        )
        assert pushed == []

    def it_keeps_the_local_change_when_the_airtable_push_fails(client: Client, monkeypatch):
        _user_with_role("adm-ed5", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE))

        def boom(space):
            raise RuntimeError("Airtable down")

        monkeypatch.setattr("airtable_sync.service.sync_space_to_airtable", boom)
        client.login(username="adm-ed5", password="pass")
        response = client.post(
            reverse("hub_map_hotspot_edit", args=[hotspot.pk]),
            _edit_payload(hotspot, status=Space.Status.MAINTENANCE),
        )
        assert response.status_code == 200  # no 500 — the local change stands
        hotspot.space.refresh_from_db()
        assert hotspot.space.status == Space.Status.MAINTENANCE

    def it_rerenders_with_errors_for_a_studio_with_no_space(client: Client):
        _user_with_role("adm-ed6", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory(description="Keep me")
        client.login(username="adm-ed6", password="pass")
        response = client.post(
            reverse("hub_map_hotspot_edit", args=[hotspot.pk]),
            _edit_payload(hotspot, kind=MapHotspot.Kind.STUDIO, space="", description="Changed but invalid"),
        )
        assert response.status_code == 200
        assert "HX-Trigger" not in response  # the modal stays open
        assert "Save marker" in response.content.decode()  # the form is re-rendered
        hotspot.refresh_from_db()
        assert hotspot.description == "Keep me"  # nothing saved

    def it_404s_editing_an_unknown_marker(client: Client):
        _user_with_role("adm-ed7", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm-ed7", password="pass")
        assert client.get(reverse("hub_map_hotspot_edit", args=[99999])).status_code == 404

    def it_creates_a_centred_tile_on_the_floor(client: Client):
        _user_with_role("adm-cr", fog_role=Member.FogRole.ADMIN)
        floor = FloorplanFactory()
        client.login(username="adm-cr", password="pass")
        response = client.post(reverse("hub_map_hotspot_create"), {"floor_id": floor.pk})
        assert response.status_code == 200
        marker = MapHotspot.objects.get(floorplan=floor)
        assert (marker.x, marker.y) == (Decimal("50.00"), Decimal("50.00"))
        body = response.content.decode()
        assert "Save marker" in body  # the editor form for the new tile
        assert 'hx-swap-oob="beforeend:#editor-stage"' in body  # the tile is appended to the map

    def it_404s_creating_on_an_unknown_floor(client: Client):
        _user_with_role("adm-cr2", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm-cr2", password="pass")
        assert client.post(reverse("hub_map_hotspot_create"), {"floor_id": "9999"}).status_code == 404

    def it_deletes_a_marker(client: Client):
        _user_with_role("adm-del", fog_role=Member.FogRole.ADMIN)
        hotspot = MapHotspotFactory()
        client.login(username="adm-del", password="pass")
        response = client.post(reverse("hub_map_hotspot_delete", args=[hotspot.pk]))
        assert response.status_code == 200
        assert not MapHotspot.objects.filter(pk=hotspot.pk).exists()
        body = response.content.decode()
        assert f'id="hotspot-{hotspot.pk}"' in body
        assert 'hx-swap-oob="delete"' in body
        assert response["HX-Trigger"] == "close-marker-edit"

    def it_404s_deleting_an_unknown_marker(client: Client):
        _user_with_role("adm-del2", fog_role=Member.FogRole.ADMIN)
        client.login(username="adm-del2", password="pass")
        assert client.post(reverse("hub_map_hotspot_delete", args=[99999])).status_code == 404

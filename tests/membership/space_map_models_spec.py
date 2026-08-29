"""BDD specs for the space map's data model: Floorplan publishing + image-change
detection, and every derived MapHotspot property the map, detail panel and accessible
list read (status, price, size, occupants, CTA, requestability)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from membership.models import Floorplan, MapHotspot, Space
from tests.membership.factories import (
    FloorplanFactory,
    GuildFactory,
    LeaseFactory,
    MapHotspotFactory,
    MemberFactory,
    SpaceFactory,
    tiny_png_bytes,
)


def _png(name: str = "plan.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, tiny_png_bytes(), content_type="image/png")


@pytest.mark.django_db
def describe_Floorplan():
    def describe_published():
        def it_returns_only_published_floors():
            FloorplanFactory(name="Live", is_published=True)
            FloorplanFactory(name="Draft", is_published=False)
            assert [f.name for f in Floorplan.objects.published()] == ["Live"]

        def it_orders_by_sort_order_then_name():
            FloorplanFactory(name="Second", sort_order=2)
            FloorplanFactory(name="First", sort_order=1)
            assert [f.name for f in Floorplan.objects.published()] == ["First", "Second"]

    def describe_image_changed():
        def it_is_false_for_a_brand_new_floor():
            floor = Floorplan(name="Ground", image=_png())
            floor.save()
            assert floor.image_changed is False

        def it_is_false_when_only_other_fields_are_edited():
            floor = FloorplanFactory()
            floor.name = "Renamed"
            floor.save()
            assert floor.image_changed is False

        def it_treats_a_row_that_no_longer_exists_as_unchanged():
            # Re-saving an object whose row was deleted underneath it re-inserts it —
            # there is no stored image to have moved away from.
            floor = FloorplanFactory()
            Floorplan.objects.filter(pk=floor.pk).delete()
            floor.save()
            assert floor.image_changed is False

        def it_is_true_when_the_image_file_is_replaced():
            floor = FloorplanFactory()
            floor.image = _png("different.png")
            floor.save()
            assert floor.image_changed is True

    def it_uses_its_name_as_its_label():
        assert str(FloorplanFactory(name="2nd Floor")) == "2nd Floor"

    def it_needs_no_image_now_that_the_app_draws_the_map():
        floor = Floorplan.objects.create(name="Drawn only")
        assert not floor.image

    def describe_has_cubbies():
        def it_is_true_when_the_floor_has_a_cubby_marker():
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, kind=MapHotspot.Kind.CUBBY, label="S1-1")
            assert floor.has_cubbies is True

        def it_is_false_when_the_floor_has_no_cubbies():
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, kind=MapHotspot.Kind.STUDIO)
            assert floor.has_cubbies is False

    def describe_legend():
        def it_counts_the_rooms_behind_each_colour():
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.OCCUPIED))
            MapHotspotFactory(floorplan=floor, space=None, kind=MapHotspot.Kind.FACILITY, label="Wood Shop")
            assert floor.legend == [
                ("available", "Available", 2),
                ("occupied", "Occupied", 1),
                ("info", "Shops & facilities", 1),
            ]

        def it_leaves_out_a_colour_no_room_is_wearing():
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.MAINTENANCE))
            assert floor.legend == [("maintenance", "Maintenance", 1)]

        def it_is_empty_for_a_floor_with_nothing_on_it():
            assert FloorplanFactory().legend == []

        def it_leaves_walls_out_of_the_key():
            # A wall is drawn on the map but is not a space, so it is never counted.
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=None, kind=MapHotspot.Kind.WALL, label="")
            assert floor.legend == [("available", "Available", 1)]

    def describe_list_filters():
        def it_leads_with_an_all_chip_over_the_floors_own_colours():
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.OCCUPIED))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(status=Space.Status.OCCUPIED))
            assert floor.list_filters == [
                ("all", "All", 3),
                ("available", "Available", 1),
                ("occupied", "Occupied", 2),
            ]

        def it_offers_no_chips_for_a_floor_with_nothing_to_filter():
            assert FloorplanFactory().list_filters == []

    def describe_list_hotspots():
        def it_sorts_the_spaces_a_member_can_rent_to_the_top():
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, space=None, kind=MapHotspot.Kind.FACILITY, label="Wood Shop")
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="B2", status=Space.Status.OCCUPIED))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="B3", status=Space.Status.MAINTENANCE))
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="B1", status=Space.Status.AVAILABLE))
            assert [h.display_label for h in floor.list_hotspots] == ["B1", "B2", "B3", "Wood Shop"]

        def it_keeps_the_editors_own_order_within_a_status():
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, sort_order=2, space=SpaceFactory(space_id="C2"))
            MapHotspotFactory(floorplan=floor, sort_order=1, space=SpaceFactory(space_id="C1"))
            assert [h.display_label for h in floor.list_hotspots] == ["C1", "C2"]

        def it_is_empty_for_a_floor_with_nothing_on_it():
            assert FloorplanFactory().list_hotspots == []

        def it_leaves_walls_out_of_the_list():
            # Walls are map decoration, not something a member reads or acts on in the list.
            floor = FloorplanFactory()
            MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="B1", status=Space.Status.AVAILABLE))
            MapHotspotFactory(floorplan=floor, space=None, kind=MapHotspot.Kind.WALL, label="")
            assert [h.display_label for h in floor.list_hotspots] == ["B1"]


@pytest.mark.django_db
def describe_MapHotspot():
    def describe_display_label():
        def it_uses_the_linked_space():
            space = SpaceFactory(space_id="A9")
            assert MapHotspotFactory(space=space).display_label == "A9"

        def it_falls_back_to_the_free_text_label():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Wood Shop")
            assert hotspot.display_label == "Wood Shop"

    def describe_is_decorative():
        def it_is_true_for_a_wall():
            assert MapHotspotFactory(space=None, kind=MapHotspot.Kind.WALL, label="").is_decorative is True

        def it_is_false_for_a_studio():
            assert MapHotspotFactory().is_decorative is False

        def it_is_false_for_a_facility():
            wood_shop = MapHotspotFactory(space=None, kind=MapHotspot.Kind.FACILITY, label="Wood Shop")
            assert wood_shop.is_decorative is False

        def it_offers_no_cta_and_carries_no_status():
            wall = MapHotspotFactory(space=None, kind=MapHotspot.Kind.WALL, label="")
            assert wall.cta_kind is None
            assert wall.availability_class is None

    def describe_linked_guild():
        def it_prefers_an_explicit_guild_link():
            explicit = GuildFactory(name="Ceramics")
            sublet = GuildFactory(name="Glass")
            hotspot = MapHotspotFactory(space=SpaceFactory(sublet_guild=sublet), guild=explicit)
            assert hotspot.linked_guild == explicit

        def it_falls_back_to_the_spaces_sublet_guild():
            sublet = GuildFactory(name="Glass")
            hotspot = MapHotspotFactory(space=SpaceFactory(sublet_guild=sublet), guild=None)
            assert hotspot.linked_guild == sublet

        def it_links_a_facility_marker_with_no_space():
            guild = GuildFactory(name="Wood")
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Wood Shop", guild=guild)
            assert hotspot.linked_guild == guild

        def it_is_none_when_neither_is_set():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Gallery", guild=None)
            assert hotspot.linked_guild is None

        def it_is_none_for_a_studio_whose_space_has_no_guild():
            hotspot = MapHotspotFactory(space=SpaceFactory(sublet_guild=None), guild=None)
            assert hotspot.linked_guild is None

    def describe_status_display():
        def it_reads_the_spaces_status():
            hotspot = MapHotspotFactory(space=SpaceFactory(status=Space.Status.MAINTENANCE))
            assert hotspot.status == Space.Status.MAINTENANCE
            assert hotspot.status_display == "Maintenance"
            assert hotspot.availability_class == "maintenance"

        def it_reads_facility_for_a_marker_with_no_space():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.RESTROOM, space=None, label="Restroom 3")
            assert hotspot.status is None
            assert hotspot.status_display == "Facility"
            assert hotspot.availability_class is None

    def describe_price_display():
        def it_formats_a_derivable_price():
            space = SpaceFactory(manual_price=Decimal("420.00"))
            assert MapHotspotFactory(space=space).price_display == "$420.00/mo"

        def it_says_price_on_request_when_the_space_has_none():
            space = SpaceFactory(manual_price=None, size_sqft=None)
            hotspot = MapHotspotFactory(space=space)
            assert hotspot.full_price is None
            assert hotspot.price_display == "Price on request"

        def it_is_blank_for_an_info_marker():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.INFO, space=None, label="Note")
            assert hotspot.full_price is None
            assert hotspot.price_display == ""

    def describe_size_display():
        def it_prefers_square_footage():
            assert MapHotspotFactory(space=SpaceFactory(size_sqft=Decimal("120"))).size_display == "120 sq ft"

        def it_falls_back_to_width_by_depth():
            space = SpaceFactory(size_sqft=None, width=Decimal("10"), depth=Decimal("12"))
            assert MapHotspotFactory(space=space).size_display == "10 × 12 ft"

        def it_is_blank_when_nothing_is_recorded():
            space = SpaceFactory(size_sqft=None, width=None, depth=None)
            assert MapHotspotFactory(space=space).size_display == ""

        def it_is_blank_for_an_info_marker():
            assert MapHotspotFactory(kind=MapHotspot.Kind.EXIT, space=None, label="Exit").size_display == ""

    def describe_occupants():
        def it_lists_the_current_tenants_by_name():
            space = SpaceFactory(status=Space.Status.OCCUPIED)
            member = MemberFactory(preferred_name="Robin Vale")
            LeaseFactory(space=space, tenant_obj=member, start_date=date(2024, 1, 1), end_date=None)
            hotspot = MapHotspotFactory(space=space)
            assert hotspot.occupant_names == ["Robin Vale"]

        def it_is_empty_for_an_info_marker():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Gallery")
            assert hotspot.occupants == []
            assert hotspot.occupant_names == []

    def describe_cta():
        @pytest.mark.parametrize(
            ("kind", "cta", "label"),
            [
                (MapHotspot.Kind.STUDIO, "lease", "Request to lease"),
                (MapHotspot.Kind.CUBBY, "cubby", "Request this space"),
                (MapHotspot.Kind.MEETING_ROOM, "reserve", "Reserve"),
                (MapHotspot.Kind.EVENT_SPACE, "reserve", "Reserve"),
            ],
        )
        def it_maps_each_actionable_kind(kind, cta, label):
            hotspot = MapHotspotFactory(kind=kind)
            assert hotspot.cta_kind == cta
            assert hotspot.cta_label == label

        @pytest.mark.parametrize(
            "kind",
            [MapHotspot.Kind.FACILITY, MapHotspot.Kind.INFO, MapHotspot.Kind.RESTROOM, MapHotspot.Kind.EXIT],
        )
        def it_offers_nothing_for_an_info_kind(kind):
            hotspot = MapHotspotFactory(kind=kind, space=None, label="Thing")
            assert hotspot.cta_kind is None
            assert hotspot.cta_label == ""

    def describe_is_requestable():
        def it_allows_an_available_studio():
            assert MapHotspotFactory(space=SpaceFactory(status=Space.Status.AVAILABLE)).is_requestable

        def it_refuses_an_occupied_studio():
            assert not MapHotspotFactory(space=SpaceFactory(status=Space.Status.OCCUPIED)).is_requestable

        def it_refuses_a_studio_marker_with_no_space():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.STUDIO, space=None, label="TBD")
            assert not hotspot.is_requestable

        def it_refuses_a_reservable_room():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.MEETING_ROOM)
            assert not hotspot.is_requestable

    def describe_search_text():
        def it_finds_a_space_by_its_code_whatever_the_casing():
            hotspot = MapHotspotFactory(space=SpaceFactory(space_id="A12"))
            assert "a12" in hotspot.search_text

        def it_finds_a_facility_by_its_own_name():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.FACILITY, space=None, label="Wood Shop")
            assert "wood shop" in hotspot.search_text

        def it_finds_a_shelf_by_what_kind_of_thing_it_is():
            # The word "cubby" is retired from the member surface; the shelf kind now reads
            # "Shelf" everywhere a member sees it, including the search haystack.
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.CUBBY, space=SpaceFactory(space_id="S1-2"))
            assert "shelf" in hotspot.search_text
            assert "cubby" not in hotspot.search_text

    def describe_aria_label():
        def it_names_the_space_its_status_and_its_price():
            hotspot = MapHotspotFactory(space=SpaceFactory(space_id="A9", manual_price=Decimal("420.00")))
            assert hotspot.aria_label == "A9 — Available, $420.00/mo"

        def it_drops_the_price_for_an_info_marker():
            hotspot = MapHotspotFactory(kind=MapHotspot.Kind.EXIT, space=None, label="North exit")
            assert hotspot.aria_label == "North exit — Facility"

    def describe_shape_constraint():
        def it_rejects_a_region_with_no_dimensions():
            with pytest.raises(IntegrityError), transaction.atomic():
                MapHotspotFactory(shape=MapHotspot.Shape.REGION, w=None, h=None)

        def it_rejects_a_pin_that_carries_dimensions():
            with pytest.raises(IntegrityError), transaction.atomic():
                MapHotspotFactory(shape=MapHotspot.Shape.PIN, w=Decimal("5"), h=Decimal("5"))

        def it_accepts_a_pin_with_no_dimensions():
            pin = MapHotspotFactory(shape=MapHotspot.Shape.PIN, w=None, h=None)
            assert pin.pk is not None

    def it_defaults_a_coordinate_less_marker_to_dead_centre():
        # The "+ Add marker" formset never posts coordinates; the model default has to
        # land the row in the middle of the plan rather than fail NOT NULL.
        hotspot = MapHotspot.objects.create(
            floorplan=FloorplanFactory(),
            shape=MapHotspot.Shape.PIN,
            kind=MapHotspot.Kind.INFO,
            label="Dropped here",
        )
        assert (hotspot.x, hotspot.y) == (Decimal("50.00"), Decimal("50.00"))

    def describe_code_label():
        def it_writes_only_the_bare_code_inside_a_drawn_room():
            space = SpaceFactory(space_id="A9", name="A9 Studio — the corner one")
            hotspot = MapHotspotFactory(space=space)
            assert hotspot.code_label == "A9"
            assert hotspot.display_label != "A9"

        def it_falls_back_to_the_markers_own_label_for_a_facility():
            hotspot = MapHotspotFactory(space=None, kind=MapHotspot.Kind.FACILITY, label="Wood Shop")
            assert hotspot.code_label == "Wood Shop"

    def describe_detail_level():
        def it_lets_a_roomy_shape_carry_its_size_and_price():
            hotspot = MapHotspotFactory(w=Decimal("12.00"), h=Decimal("9.00"))
            assert hotspot.detail_level == "full"

        def it_drops_to_the_name_alone_when_the_room_is_narrow():
            assert MapHotspotFactory(w=Decimal("3.00"), h=Decimal("6.00")).detail_level == "label"

        def it_drops_to_the_name_alone_when_the_room_is_shallow():
            assert MapHotspotFactory(w=Decimal("9.00"), h=Decimal("2.50")).detail_level == "label"

        def it_carries_no_text_at_all_below_a_word_wide():
            assert MapHotspotFactory(w=Decimal("1.00"), h=Decimal("5.00")).detail_level == "minimal"

        def it_carries_no_text_at_all_below_a_line_tall():
            assert MapHotspotFactory(w=Decimal("9.00"), h=Decimal("1.00")).detail_level == "minimal"

        def it_always_labels_a_pin_because_a_pin_sizes_itself_to_its_text():
            pin = MapHotspotFactory(shape=MapHotspot.Shape.PIN, w=None, h=None)
            assert pin.detail_level == "label"

    def it_describes_itself_by_kind_label_and_floor():
        floor = FloorplanFactory(name="Floor 1")
        hotspot = MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="A9"))
        assert str(hotspot) == "Studio (leasable) · A9 (Floor 1)"

    def describe_for_map():
        def it_loads_the_space_and_its_guild_in_one_query(django_assert_num_queries):
            guild = GuildFactory()
            MapHotspotFactory(space=SpaceFactory(sublet_guild=guild))
            MapHotspotFactory(space=SpaceFactory(sublet_guild=guild))
            with django_assert_num_queries(1):
                labels = [(h.display_label, h.space.sublet_guild.name) for h in MapHotspot.objects.for_map()]
            assert len(labels) == 2

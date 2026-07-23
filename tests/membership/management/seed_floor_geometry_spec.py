"""BDD specs for the seed_floor_geometry management command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from membership.floor_geometry import FLOORS
from membership.management.commands.seed_floor_geometry import SEED_SORT_BASE
from membership.models import Floorplan, MapHotspot, Space
from tests.membership.factories import FloorplanFactory, MapHotspotFactory, SpaceFactory


def _run(**options: object) -> str:
    out = StringIO()
    call_command("seed_floor_geometry", stdout=out, **options)
    return out.getvalue()


@pytest.fixture
def a_real_studio(db) -> Space:
    """One Space whose code the drawing actually prints, so binding can be observed."""
    return SpaceFactory(space_id="A12", status=Space.Status.AVAILABLE)


def describe_seed_floor_geometry():
    def describe_a_first_run():
        def it_creates_one_floor_per_traced_plan(db):
            _run()
            assert sorted(Floorplan.objects.values_list("name", flat=True)) == sorted(f.name for f in FLOORS)

        def it_publishes_the_floors_so_members_see_them(db):
            _run()
            assert Floorplan.objects.filter(is_published=False).count() == 0

        def it_gives_each_floor_the_drawings_proportions(db):
            _run()
            floor = Floorplan.objects.get(name="Floor 1")
            expected = next(f for f in FLOORS if f.name == "Floor 1").aspect_ratio
            assert floor.aspect_ratio == expected

        def it_leaves_the_underlay_image_empty_because_the_map_is_drawn(db):
            _run()
            assert all(not floor.image for floor in Floorplan.objects.all())

        def it_draws_every_traced_room(db):
            _run()
            assert MapHotspot.objects.count() == sum(len(f.rooms) for f in FLOORS)

        def it_positions_rooms_inside_the_canvas(db):
            _run()
            for hotspot in MapHotspot.objects.all():
                assert hotspot.x + hotspot.w <= 100
                assert hotspot.y + hotspot.h <= 100

        def it_draws_every_room_as_a_region_not_a_pin(db):
            _run()
            assert set(MapHotspot.objects.values_list("shape", flat=True)) == {MapHotspot.Shape.REGION}

    def describe_binding_to_real_spaces():
        def it_links_a_room_to_the_space_that_shares_its_code(db, a_real_studio):
            _run()
            hotspot = MapHotspot.objects.get(space=a_real_studio)
            assert hotspot.kind == MapHotspot.Kind.STUDIO
            assert hotspot.label == ""
            assert hotspot.display_label == "A12"

        def it_links_both_halves_of_a_split_room_to_the_one_space(db):
            space = SpaceFactory(space_id="C15")
            _run()
            assert MapHotspot.objects.filter(space=space).count() == 2

        def it_draws_an_unmatched_room_as_a_labelled_facility_instead_of_dropping_it(db):
            _run()
            orphan = MapHotspot.objects.get(label="A12")
            assert orphan.kind == MapHotspot.Kind.FACILITY
            assert orphan.space_id is None

        def it_names_every_unmatched_room_in_the_report(db, a_real_studio):
            output = _run()
            assert "had no matching Space" in output
            assert "A12" not in output

        def it_says_nothing_about_unmatched_rooms_when_every_code_binds(db):
            for floor in FLOORS:
                for room in floor.rooms:
                    if room.space_code:
                        Space.objects.get_or_create(
                            space_id=room.space_code,
                            defaults={"space_type": Space.SpaceType.STUDIO, "status": Space.Status.AVAILABLE},
                        )
            output = _run()
            assert "had no matching Space" not in output

        def it_keeps_the_shelves_requestable_as_cubbies(db):
            shelf = SpaceFactory(space_id="S1-4", space_type=Space.SpaceType.STORAGE)
            _run()
            assert MapHotspot.objects.get(space=shelf).kind == MapHotspot.Kind.CUBBY

        def it_keeps_a_facility_marker_on_its_own_kind(db):
            _run()
            assert MapHotspot.objects.get(label="Wood Shop").kind == MapHotspot.Kind.FACILITY

        def it_keeps_a_restroom_on_its_own_kind(db):
            _run()
            assert MapHotspot.objects.get(label="Bathroom 3").kind == MapHotspot.Kind.RESTROOM

        def it_leaves_the_drawings_working_notes_off_the_map(db):
            _run()
            drawn = set(MapHotspot.objects.values_list("label", flat=True))
            for noise in ("Key:", "Frame 1", "Frame 3", "Past Lives Floor 1", "Available/upcoming"):
                assert noise not in drawn

        def it_never_bakes_an_occupants_name_into_the_geometry(db):
            _run()
            drawn = set(MapHotspot.objects.values_list("label", flat=True))
            for person in ("Billy S.", "Ryan P", "Rachel McKenna", "Alex Lawson", "Zack", "Micah"):
                assert person not in drawn

    def describe_re_running():
        def it_adds_no_duplicate_markers(db):
            _run()
            first = MapHotspot.objects.count()
            _run()
            assert MapHotspot.objects.count() == first

        def it_reports_the_second_pass_as_refreshed_not_added(db):
            _run()
            output = _run()
            assert "0 marker(s) added" in output

        def it_picks_up_a_room_that_gained_a_space_since_the_last_run(db):
            _run()
            space = SpaceFactory(space_id="A12")
            _run()
            hotspot = MapHotspot.objects.get(space=space)
            assert hotspot.kind == MapHotspot.Kind.STUDIO

        def it_corrects_a_marker_someone_dragged_off_its_traced_position(db):
            _run()
            hotspot = MapHotspot.objects.filter(sort_order=SEED_SORT_BASE).first()
            assert hotspot is not None
            traced_x = hotspot.x
            hotspot.x = 99
            hotspot.save(update_fields=["x"])
            _run()
            hotspot.refresh_from_db()
            assert hotspot.x == traced_x

        def it_never_touches_a_marker_an_admin_placed_by_hand(db):
            _run()
            floor = Floorplan.objects.get(name="Floor 1")
            handmade = MapHotspotFactory(floorplan=floor, sort_order=0, x=7, y=7)
            _run()
            handmade.refresh_from_db()
            assert (handmade.x, handmade.y) == (7, 7)

        def it_leaves_a_floor_an_admin_unpublished_alone(db):
            _run()
            Floorplan.objects.filter(name="Floor 1").update(is_published=False)
            _run()
            assert Floorplan.objects.get(name="Floor 1").is_published is False

        def it_adopts_a_floor_that_already_exists_under_the_same_name(db):
            existing = FloorplanFactory(name="Floor 1", caption="")
            _run()
            existing.refresh_from_db()
            assert Floorplan.objects.filter(name="Floor 1").count() == 1
            assert existing.caption != ""

    def describe_dry_run():
        def it_writes_nothing_at_all(db):
            _run(dry_run=True)
            assert Floorplan.objects.count() == 0
            assert MapHotspot.objects.count() == 0

        def it_still_reports_everything_it_would_draw(db):
            output = _run(dry_run=True)
            assert output.startswith("Would seed 2 floor(s)")
            assert f"{sum(len(f.rooms) for f in FLOORS)} marker(s) added" in output

        def it_reports_no_changes_against_an_already_seeded_map(db):
            _run()
            output = _run(dry_run=True)
            assert "0 marker(s) added" in output

        def it_writes_nothing_onto_a_floor_that_already_exists_but_is_bare(db):
            FloorplanFactory(name="Floor 1")
            output = _run(dry_run=True)
            assert MapHotspot.objects.count() == 0
            assert "marker(s) added" in output

        def it_leaves_an_already_seeded_marker_untouched(db):
            _run()
            hotspot = MapHotspot.objects.filter(sort_order=SEED_SORT_BASE).first()
            assert hotspot is not None
            hotspot.x = 99
            hotspot.save(update_fields=["x"])
            _run(dry_run=True)
            hotspot.refresh_from_db()
            assert hotspot.x == 99

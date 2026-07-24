"""BDD specs for the traced floor geometry that the app draws the space map from."""

from __future__ import annotations

from decimal import Decimal

from membership.floor_geometry import FLOORS, SeedFloor, SeedRoom, _shelf_rooms


def describe_SeedRoom():
    def describe_space_code():
        def it_falls_back_to_the_printed_label_for_a_leasable_room():
            assert SeedRoom("A12", 0, 0, 10, 10).space_code == "A12"

        def it_uses_an_explicit_space_id_when_the_drawing_splits_one_space():
            room = SeedRoom("C15a (North)", 0, 0, 10, 10, space_id="C15")
            assert room.space_code == "C15"

        def it_is_blank_for_a_facility_so_nothing_tries_to_bind_it():
            assert SeedRoom("Wood Shop", 0, 0, 10, 10, kind="facility").space_code == ""

        def it_is_blank_for_an_info_marker():
            assert SeedRoom("8 x 25", 0, 0, 10, 10, kind="info").space_code == ""


def describe_SeedFloor():
    floor = SeedFloor(
        name="Test floor",
        sort_order=1,
        origin_x=100,
        origin_y=200,
        width=1000,
        height=500,
        caption="",
        rooms=(),
    )

    def describe_aspect_ratio():
        def it_is_the_crop_width_over_its_height():
            assert floor.aspect_ratio == Decimal("2.00")

        def it_rounds_to_two_places():
            tall = SeedFloor("T", 1, 0, 0, 830, 905, "")
            assert tall.aspect_ratio == Decimal("0.92")

    def describe_percent_box():
        def it_converts_drawing_pixels_to_canvas_percentages():
            room = SeedRoom("A1", 350, 325, 450, 375)
            assert floor.percent_box(room) == (
                Decimal("25.00"),
                Decimal("25.00"),
                Decimal("10.00"),
                Decimal("10.00"),
            )

        def it_clamps_a_rectangle_traced_past_the_crop_back_inside_it():
            room = SeedRoom("A1", 1050, 650, 1300, 900)
            x, y, w, h = floor.percent_box(room)
            assert x + w <= Decimal("100")
            assert y + h <= Decimal("100")

        def it_never_stores_a_zero_width_room():
            room = SeedRoom("A1", 350, 325, 350, 325)
            _, _, w, h = floor.percent_box(room)
            assert w > 0
            assert h > 0

        def it_treats_a_rectangle_traced_above_the_crop_as_the_top_edge():
            room = SeedRoom("A1", 50, 100, 150, 250)
            x, y, _, _ = floor.percent_box(room)
            assert (x, y) == (Decimal("0.00"), Decimal("0.00"))


def describe_shelf_rooms():
    def it_lays_out_every_storage_shelf():
        rooms = _shelf_rooms()
        assert len(rooms) == 27
        assert [r.label for r in rooms[:3]] == ["S1-1", "S1-2", "S1-3"]
        assert rooms[-1].label == "S1-27"

    def it_marks_them_all_as_cubbies_so_members_can_ask_for_one():
        assert {r.kind for r in _shelf_rooms()} == {"cubby"}

    def it_wraps_onto_a_new_row_every_nine_shelves():
        rooms = _shelf_rooms()
        assert rooms[0].left == rooms[9].left
        assert rooms[9].top > rooms[0].top


def describe_FLOORS():
    def it_carries_both_real_floors_in_switcher_order():
        assert [f.name for f in FLOORS] == ["Floor 1", "2nd Floor"]

    def it_traces_every_room_inside_its_own_canvas():
        for floor in FLOORS:
            for room in floor.rooms:
                x, y, w, h = floor.percent_box(room)
                assert x + w <= Decimal("100"), f"{floor.name}/{room.label} runs off the right edge"
                assert y + h <= Decimal("100"), f"{floor.name}/{room.label} runs off the bottom edge"

    def it_gives_every_room_a_label():
        for floor in FLOORS:
            for room in floor.rooms:
                assert room.label.strip(), f"{floor.name} has an unlabelled room"

    def it_never_lists_the_same_leasable_room_twice_on_a_floor():
        for floor in FLOORS:
            codes = [r.label for r in floor.rooms if r.space_code]
            assert len(codes) == len(set(codes)), f"{floor.name} lists a room twice"

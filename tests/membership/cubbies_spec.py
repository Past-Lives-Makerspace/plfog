"""BDD specs for guild cubbies' records and rules (#977): Shelf Unit, Cubby, the guild's price and payee.

Covers where a cubby may sit (inside its unit, never over another active cubby, a label unique in
its guild), shrinking a unit, archive and restore, the price rules, Who gets paid being cleared
when that person leaves the guild's staff, and the read models that draw the shelves.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership import cubbies
from membership.models import (
    Cubby,
    GuildStaffMembership,
    Member,
    ShelfUnit,
    cubby_dollars,
    cubby_price_problems,
)
from tests.membership.factories import CubbyFactory, GuildFactory, MembershipPlanFactory, ShelfUnitFactory

pytestmark = pytest.mark.django_db


def _member(name: str) -> Member:
    MembershipPlanFactory()
    member = User.objects.create_user(username=name).member
    member.full_legal_name = name.title()
    member.save(update_fields=["full_legal_name"])
    return member


def describe_cubby_dollars():
    def it_drops_cents_on_whole_dollars():
        assert cubby_dollars(1500) == "$15"
        assert cubby_dollars(150000) == "$1,500"

    def it_keeps_cents_otherwise():
        assert cubby_dollars(1550) == "$15.50"


def describe_cubby_price_problems():
    def it_accepts_a_floor_of_a_dollar_and_a_suggestion_at_the_floor():
        assert cubby_price_problems(100, 100) == {}

    def it_refuses_a_floor_under_a_dollar():
        assert "at least $1" in cubby_price_problems(99, 500)["floor"]

    def it_refuses_a_floor_over_500():
        problems = cubby_price_problems(50001, 50001)
        assert "at most $500" in problems["floor"]
        assert "at most $500" in problems["suggested"]

    def it_refuses_a_suggestion_under_the_floor():
        assert "at least the minimum, $15" in cubby_price_problems(1500, 1000)["suggested"]


def describe_ShelfUnit():
    def it_reads_active_and_archived():
        live = ShelfUnitFactory()
        gone = ShelfUnitFactory(guild=live.guild, archived_at=timezone.now())
        assert list(ShelfUnit.objects.active()) == [live]
        assert list(ShelfUnit.objects.archived()) == [gone]
        assert gone.is_archived and not live.is_archived

    def it_names_itself_with_its_guild():
        unit = ShelfUnitFactory(name="Glaze Wall", guild=GuildFactory(name="Ceramics Guild"))
        assert str(unit) == "Glaze Wall (Ceramics Guild)"

    def it_refuses_more_than_six_across_in_the_database():
        with pytest.raises(IntegrityError), transaction.atomic():
            ShelfUnitFactory(columns=7)

    def describe_resize_problem():
        def it_names_the_cubby_cut_off_by_fewer_rows():
            unit = ShelfUnitFactory(rows=3)
            CubbyFactory(unit=unit, label="Q7", row=2, height=2)
            assert unit.resize_problem(2, 4) == ("rows", "Cubby Q7 sits in row 3, so keep at least 3 rows.")

        def it_names_the_cubby_cut_off_by_fewer_columns():
            unit = ShelfUnitFactory(columns=4)
            CubbyFactory(unit=unit, label="Q8", column=3, width=2)
            field, message = unit.resize_problem(3, 3)
            assert field == "columns"
            assert "Cubby Q8 reaches spot 4 across" in message

        def it_ignores_archived_cubbies_and_allows_what_fits():
            unit = ShelfUnitFactory()
            CubbyFactory(unit=unit, row=3, archived_at=timezone.now())
            CubbyFactory(unit=unit, row=1, column=1)
            assert unit.resize_problem(1, 1) is None

    def describe_restore_problem():
        def it_names_a_label_now_used_elsewhere_in_the_guild():
            unit = ShelfUnitFactory(archived_at=timezone.now())
            CubbyFactory(unit=unit, label="A1")
            CubbyFactory(unit=ShelfUnitFactory(guild=unit.guild), label="a1")
            assert "already called A1" in unit.restore_problem()

        def it_is_blank_when_nothing_clashes():
            unit = ShelfUnitFactory(archived_at=timezone.now())
            CubbyFactory(unit=unit, label="A1")
            CubbyFactory(unit=ShelfUnitFactory(), label="A1")  # another guild's
            assert unit.restore_problem() == ""


def describe_Cubby():
    def it_names_itself_with_its_unit():
        cubby = CubbyFactory(label="B3", unit=ShelfUnitFactory(name="Glaze Wall"))
        assert str(cubby) == "B3 on Glaze Wall"

    def describe_price():
        def it_uses_the_guild_price_without_its_own():
            cubby = CubbyFactory(unit=ShelfUnitFactory(guild=GuildFactory(cubby_floor_cents=1200)))
            assert cubby.uses_guild_price
            assert cubby.floor == 1200
            assert cubby.suggested == 2000
            assert cubby.floor_display == "$12"
            assert cubby.suggested_display == "$20"

        def it_uses_its_own_price_when_set():
            cubby = CubbyFactory(floor_cents=2500, suggested_cents=3050)
            assert not cubby.uses_guild_price
            assert (cubby.floor, cubby.suggested) == (2500, 3050)
            assert cubby.suggested_display == "$30.50"

        def it_refuses_half_a_price_or_a_suggestion_under_the_floor_in_the_database():
            with pytest.raises(IntegrityError), transaction.atomic():
                CubbyFactory(floor_cents=2500)
            with pytest.raises(IntegrityError), transaction.atomic():
                CubbyFactory(floor_cents=2500, suggested_cents=2000)

    def describe_state():
        def it_is_open_or_out_of_service():
            assert CubbyFactory().state == Cubby.State.OPEN
            out = CubbyFactory(out_of_service=True)
            assert out.state == Cubby.State.OUT_OF_SERVICE
            assert out.state_label == "Out of Service"

    def describe_shape():
        @pytest.mark.parametrize(
            ("width", "height", "shape"),
            [(1, 1, "one"), (2, 1, "wide"), (1, 2, "tall"), (2, 2, "big")],
        )
        def it_reads_the_shape_from_its_squares(width: int, height: int, shape: str):
            assert CubbyFactory.build(width=width, height=height).shape == shape

        def it_fails_loudly_on_a_size_no_shape_has():
            with pytest.raises(ValueError, match="no shape matches"):
                _ = CubbyFactory.build(width=3, height=1).shape

    def describe_position_text():
        @pytest.mark.parametrize(
            ("rows", "columns", "row", "column", "width", "height", "text"),
            [
                (3, 4, 1, 1, 1, 1, "Top row, far left"),
                (3, 4, 3, 4, 1, 1, "Bottom row, far right"),
                (3, 4, 2, 2, 1, 1, "Middle row, second from the left"),
                (5, 6, 3, 3, 1, 1, "Row 3, third from the left"),
                (3, 4, 3, 1, 2, 1, "Bottom row, far left, wide"),
                (2, 4, 1, 1, 1, 2, "Top row, far left, tall"),
                (1, 1, 1, 1, 1, 1, "The only spot"),
            ],
        )
        def it_says_where_the_cubby_sits(rows, columns, row, column, width, height, text):
            unit = ShelfUnitFactory(rows=rows, columns=columns)
            assert CubbyFactory(unit=unit, row=row, column=column, width=width, height=height).position_text == text

    def describe_placement_problems():
        def it_refuses_a_cubby_past_the_bottom_row():
            cubby = CubbyFactory.build(unit=ShelfUnitFactory(name="Rack", rows=2), row=2, height=2)
            assert cubby.placement_problems() == {"row": "Rack has 2 rows, so it does not fit there."}

        def it_names_a_wide_cubby_running_past_the_side():
            cubby = CubbyFactory.build(unit=ShelfUnitFactory(name="Rack", columns=4), column=4, width=2)
            assert cubby.placement_problems() == {"column": "Rack is 4 across, so it does not fit there."}

        def it_refuses_overlapping_another_active_cubby():
            unit = ShelfUnitFactory()
            CubbyFactory(unit=unit, label="T1", row=1, column=2, height=2)
            cubby = CubbyFactory.build(unit=unit, label="N1", row=2, column=1, width=2)
            assert cubby.placement_problems() == {"column": "That spot overlaps cubby T1."}

        def it_lets_a_square_an_archived_cubby_left_be_used():
            unit = ShelfUnitFactory()
            CubbyFactory(unit=unit, label="T1", archived_at=timezone.now())
            assert CubbyFactory.build(unit=unit, label="N1").placement_problems() == {}

        def it_refuses_a_label_another_active_cubby_in_the_guild_has_in_any_case():
            unit = ShelfUnitFactory()
            CubbyFactory(unit=ShelfUnitFactory(guild=unit.guild), label="a1")
            assert CubbyFactory.build(unit=unit, label="A1").placement_problems() == {
                "label": "Another cubby in the guild is already called A1."
            }

        def it_allows_a_label_used_only_by_another_guild_or_by_itself():
            CubbyFactory(label="A1")
            cubby = CubbyFactory(label="A1")
            assert cubby.placement_problems() == {}

    def describe_photo():
        def it_deletes_the_old_file_when_the_photo_is_replaced(settings, tmp_path):
            settings.MEDIA_ROOT = tmp_path
            cubby = CubbyFactory(photo=SimpleUploadedFile("a.png", b"one", content_type="image/png"))
            old = cubby.photo.path
            cubby.photo = SimpleUploadedFile("b.png", b"two", content_type="image/png")
            cubby.save()
            assert not (tmp_path / old).exists()


def describe_cubby_payee():
    def it_offers_only_the_guild_lead_and_staff():
        lead, staff, stranger = _member("lead one"), _member("staff one"), _member("stranger one")
        guild = GuildFactory(guild_lead=lead)
        GuildStaffMembership.objects.create(guild=guild, member=staff, role="treasurer")
        GuildStaffMembership.objects.create(guild=guild, member=staff, role="secretary")
        assert list(guild.cubby_payee_choices()) == [lead, staff]
        assert stranger not in guild.cubby_payee_choices()

    def it_clears_when_the_payee_leaves_the_staff():
        lead, staff = _member("lead two"), _member("staff two")
        guild = GuildFactory(guild_lead=lead)
        row = GuildStaffMembership.objects.create(guild=guild, member=staff, role="treasurer")
        guild.cubby_payee = staff
        guild.save()
        guild.remove_staff(row)
        guild.refresh_from_db()
        assert guild.cubby_payee is None

    def it_keeps_a_payee_who_still_holds_another_role():
        staff = _member("staff three")
        guild = GuildFactory()
        first = GuildStaffMembership.objects.create(guild=guild, member=staff, role="treasurer")
        GuildStaffMembership.objects.create(guild=guild, member=staff, role="secretary")
        guild.cubby_payee = staff
        guild.save()
        guild.remove_staff(first)
        guild.refresh_from_db()
        assert guild.cubby_payee == staff

    def it_keeps_the_payee_when_someone_else_leaves():
        staff, other = _member("staff four"), _member("other four")
        guild = GuildFactory(cubby_payee=staff)
        GuildStaffMembership.objects.create(guild=guild, member=staff, role="treasurer")
        row = GuildStaffMembership.objects.create(guild=guild, member=other, role="secretary")
        guild.remove_staff(row)
        guild.refresh_from_db()
        assert guild.cubby_payee == staff

    def it_clears_when_the_lead_paid_is_replaced_or_cleared():
        old, new = _member("old lead"), _member("new lead")
        guild = GuildFactory(guild_lead=old, cubby_payee=old)
        guild.assign_lead(new)
        guild.refresh_from_db()
        assert guild.cubby_payee is None
        guild.cubby_payee = new
        guild.save()
        guild.clear_lead()
        guild.refresh_from_db()
        assert guild.cubby_payee is None

    def it_assigns_and_clears_a_lead_on_a_guild_with_none():
        guild = GuildFactory()
        assert guild.clear_lead() is None
        guild.assign_lead(_member("first lead"))
        assert guild.guild_lead is not None


def describe_service():
    def describe_save_cubby():
        def it_saves_a_cubby_that_fits():
            unit = ShelfUnitFactory()
            cubby = Cubby(unit=unit, label="A1", row=1, column=1)
            cubbies.save_cubby(cubby)
            assert cubby.pk is not None

        def it_refuses_one_whose_square_was_taken_meanwhile():
            unit = ShelfUnitFactory()
            CubbyFactory(unit=unit, label="A1")
            with pytest.raises(ValidationError) as refusal:
                cubbies.save_cubby(Cubby(unit=unit, label="A2", row=1, column=1))
            assert "overlaps cubby A1" in refusal.value.message_dict["column"][0]

    def describe_resize_unit():
        def it_renames_and_resizes():
            unit = ShelfUnitFactory()
            cubbies.resize_unit(unit, name="Kiln Shelf", rows=5, columns=6)
            unit.refresh_from_db()
            assert (unit.name, unit.rows, unit.columns) == ("Kiln Shelf", 5, 6)

        def it_refuses_a_size_that_cuts_off_a_cubby():
            unit = ShelfUnitFactory()
            CubbyFactory(unit=unit, label="Z9", row=3)
            with pytest.raises(ValidationError) as refusal:
                cubbies.resize_unit(unit, name="X", rows=2, columns=4)
            assert "Cubby Z9" in refusal.value.message_dict["rows"][0]
            unit.refresh_from_db()
            assert unit.rows == 3

    def describe_archive_and_restore():
        def it_archives_and_restores_a_unit():
            unit = ShelfUnitFactory()
            cubbies.archive_unit(unit)
            assert unit.is_archived
            cubbies.restore_unit(unit)
            unit.refresh_from_db()
            assert not unit.is_archived

        def it_refuses_restoring_a_unit_whose_label_is_taken():
            unit = ShelfUnitFactory(archived_at=timezone.now())
            CubbyFactory(unit=unit, label="A1")
            CubbyFactory(unit=ShelfUnitFactory(guild=unit.guild), label="A1")
            with pytest.raises(ValidationError):
                cubbies.restore_unit(unit)

        def it_archives_and_restores_a_cubby():
            cubby = CubbyFactory()
            cubbies.archive_cubby(cubby)
            assert cubby.is_archived
            assert not Cubby.objects.active().exists()
            cubbies.restore_cubby(cubby)
            assert Cubby.objects.active().get() == cubby

        def it_refuses_restoring_a_cubby_whose_square_was_filled():
            unit = ShelfUnitFactory()
            cubby = CubbyFactory(unit=unit, label="A1", archived_at=timezone.now())
            CubbyFactory(unit=unit, label="A2")
            with pytest.raises(ValidationError, match="overlaps cubby A2"):
                cubbies.restore_cubby(cubby)

    def describe_unit_cells():
        def it_draws_every_square_once_in_row_order_with_spans():
            unit = ShelfUnitFactory(rows=2, columns=3)
            wide = CubbyFactory(unit=unit, label="W", row=1, column=1, width=2)
            tall = CubbyFactory(unit=unit, label="T", row=1, column=3, height=2)
            cells = cubbies.unit_cells(unit, [wide, tall])
            assert [(c.row, c.column, c.cubby) for c in cells] == [
                (1, 1, wide),
                (1, 3, tall),
                (2, 1, None),
                (2, 2, None),
            ]
            assert [c.span_class for c in cells] == ["pl-cubby-cell--wide", "pl-cubby-cell--tall", "", ""]

        def it_gives_a_one_square_cubby_no_span():
            cubby = CubbyFactory()
            assert cubbies.Cell(row=1, column=1, cubby=cubby).span_class == ""

    def describe_CubbyTab():
        def it_shows_units_with_cubbies_and_counts_what_is_open():
            guild = GuildFactory()
            glaze = ShelfUnitFactory(guild=guild, columns=4)
            ShelfUnitFactory(guild=guild)  # empty: hidden from members
            ShelfUnitFactory(guild=guild, archived_at=timezone.now())
            CubbyFactory(unit=glaze, label="A1", floor_cents=2500, suggested_cents=2500)
            CubbyFactory(unit=glaze, label="A2", column=2)
            CubbyFactory(unit=glaze, label="A3", column=3, out_of_service=True, floor_cents=500, suggested_cents=500)
            CubbyFactory(unit=glaze, label="A4", column=4, archived_at=timezone.now())
            tab = cubbies.CubbyTab.load(guild)
            assert [view.unit for view in tab.units] == [glaze]
            assert tab.total == 3
            assert tab.open_count == 2
            assert tab.units[0].open_count == 2
            assert tab.units[0].grid_class == ""
            assert tab.from_price == "$15"

        def it_prices_from_every_cubby_when_none_is_open_and_says_nothing_when_empty():
            unit = ShelfUnitFactory(columns=2)
            CubbyFactory(unit=unit, out_of_service=True)
            tab = cubbies.CubbyTab.load(unit.guild)
            assert tab.from_price == "$15"
            assert tab.units[0].grid_class == "pl-cubby-grid--cols-2"
            assert cubbies.CubbyTab.load(GuildFactory()).from_price == ""

    def describe_CubbyLayout():
        def it_loads_units_and_what_is_archived():
            guild = GuildFactory()
            unit = ShelfUnitFactory(guild=guild, rows=10, columns=6)
            gone = ShelfUnitFactory(guild=guild, archived_at=timezone.now())
            CubbyFactory(unit=unit, label="A1")
            CubbyFactory(unit=unit, label="A2", column=2, out_of_service=True)
            old = CubbyFactory(unit=unit, label="A3", column=3, archived_at=timezone.now())
            CubbyFactory(unit=gone, label="G1", archived_at=timezone.now())  # under an archived unit: not listed
            layout = cubbies.CubbyLayout.load(guild)
            view = layout.unit_view(unit.pk)
            assert not view.can_add_row and not view.can_add_column
            assert view.archive_confirm_id == f"cubby-archive-unit-{unit.pk}"
            assert layout.archived_units == [gone]
            assert layout.archived_cubbies == [old]
            assert layout.archived_count == 2
            assert (layout.open_count, layout.cubby_count) == (1, 2)

        def it_refuses_a_unit_it_does_not_hold():
            layout = cubbies.CubbyLayout.load(GuildFactory())
            with pytest.raises(KeyError):
                layout.unit_view(999)

        def it_lets_a_small_unit_grow():
            first = ShelfUnitFactory()
            unit = ShelfUnitFactory(guild=first.guild)
            view = cubbies.CubbyLayout.load(unit.guild).unit_view(unit.pk)
            assert view.can_add_row and view.can_add_column

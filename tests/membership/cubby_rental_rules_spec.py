"""BDD specs for what renting changes in part 1's cubby rules (#978): membership/cubbies.py.

Staff cannot archive, or put out of service, a cubby someone rents (or holds while paying), nor
archive its shelf; the guild page's tab carries the viewer's live rentals and, with the switch off,
shows only those; a tab with nothing open says so.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from membership import cubbies
from membership.models import CubbyRental
from tests.membership.factories import CubbyFactory, CubbyRentalFactory, GuildFactory, ShelfUnitFactory

pytestmark = pytest.mark.django_db

Status = CubbyRental.Status


def describe_closing_a_rented_cubby():
    @pytest.mark.parametrize("status", [Status.ACTIVE, Status.PAST_DUE, Status.PENDING_PAYMENT])
    def it_refuses_archiving_a_cubby_someone_holds(status: str):
        rental = CubbyRentalFactory(cubby=CubbyFactory(label="R2"), status=status)
        with pytest.raises(ValidationError, match="Someone rents cubby R2, so it stays until their rental ends."):
            cubbies.archive_cubby(rental.cubby)
        rental.cubby.refresh_from_db()
        assert not rental.cubby.is_archived

    def it_archives_a_cubby_whose_rentals_all_ended():
        rental = CubbyRentalFactory(status=Status.ENDED)
        cubbies.archive_cubby(rental.cubby)
        assert rental.cubby.is_archived

    def it_refuses_archiving_a_shelf_with_a_rented_cubby_naming_the_first():
        unit = ShelfUnitFactory()
        CubbyFactory(unit=unit, label="S1")
        CubbyRentalFactory(cubby=CubbyFactory(unit=unit, label="S3", column=3))
        CubbyRentalFactory(cubby=CubbyFactory(unit=unit, label="S2", column=2))
        with pytest.raises(ValidationError, match="Someone rents cubby S2 on this shelf"):
            cubbies.archive_unit(unit)
        unit.refresh_from_db()
        assert not unit.is_archived

    def it_refuses_putting_a_rented_cubby_out_of_service_but_saves_other_edits():
        rental = CubbyRentalFactory(cubby=CubbyFactory(label="R4"))
        cubby = rental.cubby
        cubby.note = "Moved the sign"
        cubbies.save_cubby(cubby)
        cubby.out_of_service = True
        with pytest.raises(ValidationError) as refusal:
            cubbies.save_cubby(cubby)
        assert refusal.value.messages == [cubbies.RENTED_CUBBY_STAYS_OPEN]


def describe_the_tab_for_a_viewer():
    def _rented_guild(*, show: bool) -> tuple[CubbyRental, CubbyRental]:
        guild = GuildFactory(show_cubbies_tab=show)
        unit = ShelfUnitFactory(guild=guild)
        mine = CubbyRentalFactory(cubby=CubbyFactory(unit=unit, label="M1"))
        hold = CubbyRentalFactory(
            cubby=CubbyFactory(unit=unit, label="M2", column=2), member=mine.member, status=Status.PENDING_PAYMENT
        )
        return mine, hold

    def it_carries_the_viewers_billing_rentals_not_their_holds():
        mine, _hold = _rented_guild(show=True)
        tab = cubbies.CubbyTab.for_page(mine.cubby.unit.guild, mine.member)
        assert tab is not None
        assert (tab.rentals, tab.viewer_pk, tab.shelves) == ([mine], mine.member.pk, True)
        assert tab.is_full  # both cubbies are held
        assert tab.open_count == 0

    def it_shows_only_the_viewers_cards_with_the_switch_off():
        mine, _hold = _rented_guild(show=False)
        tab = cubbies.CubbyTab.for_page(mine.cubby.unit.guild, mine.member)
        assert tab is not None
        assert (tab.rentals, tab.units, tab.shelves) == ([mine], [], False)
        assert not tab.is_full

    def it_shows_nothing_with_the_switch_off_to_someone_renting_nothing_there():
        mine, _hold = _rented_guild(show=False)
        other = CubbyRentalFactory().member
        assert cubbies.CubbyTab.for_page(mine.cubby.unit.guild, other) is None

    def it_reads_no_rentals_without_a_viewer():
        mine, _hold = _rented_guild(show=True)
        tab = cubbies.CubbyTab.load(mine.cubby.unit.guild)
        assert (tab.rentals, tab.viewer_pk) == ([], None)

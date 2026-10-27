"""Guild cubbies (#976 part 1, #977): placing, archiving and drawing a guild's shelves.

Every change that can clash with another cubby (placing, resizing a unit, restoring) runs
under a lock on the guild row, so two staff saving at once cannot both take the same square
or the same label. The checks themselves live on the models (``Cubby.placement_problems``,
``ShelfUnit.resize_problem``, ``ShelfUnit.restore_problem``); this module orders them.

The read models draw a unit as its grid: one cell per square in row order, a cubby taking
the squares it spans. CSS grid places the cells in that order, so no cell needs a position
of its own (``.pl-cubby-grid`` in ``static/css/cubbies.css``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from membership.models import Cubby, Guild, ShelfUnit, cubby_dollars

#: The span class each shape's cell carries on the shelf.
_SPAN_CLASSES: dict[str, str] = {
    Cubby.Shape.ONE: "",
    Cubby.Shape.WIDE: "pl-cubby-cell--wide",
    Cubby.Shape.TALL: "pl-cubby-cell--tall",
    Cubby.Shape.BIG: "pl-cubby-cell--big",
}


def _lock_guild(guild_id: int) -> None:
    """Hold the guild row for the rest of the transaction, the one lock every cubby change takes."""
    Guild.objects.select_for_update().filter(pk=guild_id).first()


#: The refusal for placing or restoring a cubby on a shelf unit that is archived.
RESTORE_SHELF_FIRST = "Restore its shelf first."


def _fresh_unit(cubby: Cubby) -> None:
    """Re-read the cubby's unit size and archive state under the lock.

    A form or a list read the unit before the lock; another staff member may have shrunk or
    archived it since, and the placement check must see the unit as it is now.
    """
    cubby.unit.refresh_from_db(fields=["rows", "columns", "archived_at"])


def save_cubby(cubby: Cubby) -> None:
    """Place or update ``cubby`` after checking its spot and label again under the guild lock.

    Raises:
        ValidationError: keyed by field, when the spot or the label was taken meanwhile or the
            unit shrank; on the whole form when the unit was archived meanwhile.
    """
    with transaction.atomic():
        _lock_guild(cubby.unit.guild_id)
        _fresh_unit(cubby)
        if cubby.unit.is_archived:
            raise ValidationError(RESTORE_SHELF_FIRST)
        problems = cubby.placement_problems()
        if problems:
            raise ValidationError(problems)
        cubby.save()


def resize_unit(unit: ShelfUnit, *, name: str, rows: int, columns: int) -> None:
    """Rename and resize ``unit``, refusing a size that would cut off a cubby.

    Raises:
        ValidationError: on ``rows`` (or ``columns``), naming the cubby in the way.
    """
    with transaction.atomic():
        _lock_guild(unit.guild_id)
        problem = unit.resize_problem(rows, columns)
        if problem is not None:
            key, message = problem
            raise ValidationError({key: message})
        unit.name = name
        unit.rows = rows
        unit.columns = columns
        unit.save(update_fields=["name", "rows", "columns"])


def archive_unit(unit: ShelfUnit) -> None:
    """Take the unit and its cubbies off the guild page."""
    unit.archived_at = timezone.now()
    unit.save(update_fields=["archived_at"])


def restore_unit(unit: ShelfUnit) -> None:
    """Bring an archived unit back.

    Raises:
        ValidationError: when one of its cubby labels is now used elsewhere in the guild.
    """
    with transaction.atomic():
        _lock_guild(unit.guild_id)
        problem = unit.restore_problem()
        if problem:
            raise ValidationError(problem)
        unit.archived_at = None
        unit.save(update_fields=["archived_at"])


def archive_cubby(cubby: Cubby) -> None:
    """Take one cubby off the shelf; its square opens for a new one."""
    cubby.archived_at = timezone.now()
    cubby.save(update_fields=["archived_at"])


def restore_cubby(cubby: Cubby) -> None:
    """Put an archived cubby back where it was.

    Raises:
        ValidationError: when its unit is archived ("Restore its shelf first."), or its square
            or its label was taken meanwhile, or its unit shrank.
    """
    with transaction.atomic():
        _lock_guild(cubby.unit.guild_id)
        _fresh_unit(cubby)
        if cubby.unit.is_archived:
            raise ValidationError(RESTORE_SHELF_FIRST)
        problems = cubby.placement_problems()
        if problems:
            raise ValidationError(" ".join(problems.values()))
        cubby.archived_at = None
        cubby.save(update_fields=["archived_at"])


@dataclass
class Cell:
    """One square of a unit's grid as drawn: a cubby (with the squares it spans) or an empty square."""

    row: int
    column: int
    cubby: Cubby | None = None

    @property
    def span_class(self) -> str:
        """The class that lets a wide, tall or big cubby take its extra squares."""
        return _SPAN_CLASSES[self.cubby.shape] if self.cubby is not None else ""


def unit_cells(unit: ShelfUnit, cubbies: list[Cubby]) -> list[Cell]:
    """Every square of ``unit`` in row order, a cubby once at its top left and empty squares as they are."""
    by_corner = {(c.row, c.column): c for c in cubbies}
    covered: set[tuple[int, int]] = set()
    for cubby in cubbies:
        covered |= cubby.squares()
    cells: list[Cell] = []
    for row in range(1, unit.rows + 1):
        for column in range(1, unit.columns + 1):
            corner = by_corner.get((row, column))
            if corner is not None:
                cells.append(Cell(row=row, column=column, cubby=corner))
            elif (row, column) not in covered:
                cells.append(Cell(row=row, column=column))
    return cells


@dataclass
class UnitView:
    """One shelf unit as a page draws it: its cells and its counts."""

    unit: ShelfUnit
    cubbies: list[Cubby]
    cells: list[Cell] = field(default_factory=list)

    @property
    def open_count(self) -> int:
        """How many of its cubbies are open."""
        return sum(1 for c in self.cubbies if c.state == Cubby.State.OPEN)

    @property
    def can_add_row(self) -> bool:
        """Whether the Rows stepper's plus still has room."""
        return self.unit.rows < ShelfUnit.MAX_ROWS

    @property
    def can_add_column(self) -> bool:
        """Whether the Across stepper's plus still has room."""
        return self.unit.columns < ShelfUnit.MAX_COLUMNS

    @property
    def archive_confirm_id(self) -> str:
        """The DOM id of this unit's Archive Shelf confirm."""
        return f"cubby-archive-unit-{self.unit.pk}"

    @property
    def grid_class(self) -> str:
        """The column count class for anything but the default four across."""
        return "" if self.unit.columns == 4 else f"pl-cubby-grid--cols-{self.unit.columns}"


def _unit_views(units: list[ShelfUnit], cubbies: list[Cubby]) -> list[UnitView]:
    by_unit: dict[int, list[Cubby]] = {}
    for cubby in cubbies:
        by_unit.setdefault(cubby.unit_id, []).append(cubby)
    views = []
    for unit in units:
        mine = by_unit.get(unit.pk, [])
        views.append(UnitView(unit=unit, cubbies=mine, cells=unit_cells(unit, mine)))
    return views


@dataclass
class CubbyTab:
    """The guild page's Cubbies tab (#977): the units members see and the summary line."""

    guild: Guild
    units: list[UnitView]

    @classmethod
    def load(cls, guild: Guild) -> CubbyTab:
        """Active units that hold at least one active cubby, in two queries."""
        units = list(guild.shelf_units.active())
        cubbies = list(
            Cubby.objects.active().filter(unit__guild=guild).select_related("unit__guild").order_by("row", "column")
        )
        views = [view for view in _unit_views(units, cubbies) if view.cubbies]
        return cls(guild=guild, units=views)

    @property
    def cubbies(self) -> list[Cubby]:
        """Every cubby on the tab, unit by unit."""
        return [cubby for view in self.units for cubby in view.cubbies]

    @property
    def total(self) -> int:
        """How many cubbies the tab shows."""
        return len(self.cubbies)

    @property
    def open_count(self) -> int:
        """How many are open."""
        return sum(view.open_count for view in self.units)

    @property
    def from_price(self) -> str:
        """The lowest floor among open cubbies (or all, when none is open), as "$15"; "" with none."""
        open_floors = [c.floor for c in self.cubbies if c.state == Cubby.State.OPEN]
        floors = open_floors or [c.floor for c in self.cubbies]
        return cubby_dollars(min(floors)) if floors else ""


@dataclass
class CubbyLayout:
    """The staff Layout tab (#977): every active unit with its squares, and what is archived."""

    guild: Guild
    units: list[UnitView]
    archived_units: list[ShelfUnit]
    archived_cubbies: list[Cubby]

    @classmethod
    def load(cls, guild: Guild) -> CubbyLayout:
        """Four queries: active units, their cubbies, archived units, archived cubbies on active units."""
        units = list(guild.shelf_units.active())
        cubbies = list(
            Cubby.objects.active().filter(unit__guild=guild).select_related("unit__guild").order_by("row", "column")
        )
        archived_cubbies = list(
            Cubby.objects.filter(unit__guild=guild, unit__archived_at__isnull=True, archived_at__isnull=False)
            .select_related("unit")
            .order_by("-archived_at", "-pk")
        )
        return cls(
            guild=guild,
            units=_unit_views(units, cubbies),
            archived_units=list(guild.shelf_units.archived()),
            archived_cubbies=archived_cubbies,
        )

    def unit_view(self, unit_pk: int) -> UnitView:
        """The one unit an edit page draws.

        Raises:
            KeyError: the unit is archived or another guild's.
        """
        for view in self.units:
            if view.unit.pk == unit_pk:
                return view
        raise KeyError(unit_pk)

    @property
    def archived_count(self) -> int:
        """Archived units and cubbies together, for the Show Archived fold."""
        return len(self.archived_units) + len(self.archived_cubbies)

    @property
    def open_count(self) -> int:
        """Open cubbies across the guild, for the Guild Tools card."""
        return sum(view.open_count for view in self.units)

    @property
    def cubby_count(self) -> int:
        """Active cubbies across the guild."""
        return sum(len(view.cubbies) for view in self.units)

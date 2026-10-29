"""Guild cubbies (#976 part 1, #977): placing, archiving and drawing a guild's shelves.

Every change that can clash with another cubby (placing, resizing a unit, restoring) runs
under a lock on the guild row, so two staff saving at once cannot both take the same square
or the same label. The checks themselves live on the models (``Cubby.placement_problems``,
``ShelfUnit.resize_problem``, ``ShelfUnit.restore_problem``); this module orders them.

A change that opens a cubby (a new one, Out of Service turned off, a cubby or its shelf restored)
tells the guild's waitlist once it commits (``membership.cubby_waitlist.open_for_waitlist``, #980).
Resizing never opens one: it refuses to cut a cubby off, and growing adds empty squares only.

The read models draw a unit as its grid: one cell per square in row order, a cubby taking
the squares it spans. CSS grid places the cells in that order, so no cell needs a position
of its own (``.pl-cubby-grid`` in ``static/css/cubbies.css``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from membership import cubby_waitlist
from membership.models import Cubby, CubbyRental, CubbyWaitlistEntry, Guild, Member, ShelfUnit, cubby_dollars

if TYPE_CHECKING:
    from django.db.models import QuerySet

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
    """Re-read the cubby's unit name, size and archive state under the lock.

    A form or a list read the unit before the lock; another staff member may have shrunk or
    archived it since, and the placement check must see the unit as it is now.
    """
    cubby.unit.refresh_from_db(fields=["name", "rows", "columns", "archived_at"])


#: The refusals for closing a cubby or a shelf someone rents (#978): staff end a rental in Part 3 (#979).
RENTED_CUBBY_STAYS_OPEN = "Someone rents this cubby, so it stays in service until their rental ends."
RENTED_CUBBY_STAYS = "Someone rents cubby {label}, so it stays until their rental ends."
RENTED_SHELF_STAYS = "Someone rents cubby {label} on this shelf, so the shelf stays until their rental ends."


def _rented_label(cubbies: Iterable[Cubby]) -> str:
    """The label of the first of ``cubbies`` a live rental (or a hold awaiting payment) holds, or ""."""
    pks = [cubby.pk for cubby in cubbies]
    rented = (
        CubbyRental.objects.live()
        .filter(cubby_id__in=pks)
        .select_related("cubby")
        .order_by("cubby__row", "cubby__column", "cubby__pk")
        .first()
    )
    return rented.cubby.label if rented is not None else ""


def save_cubby(cubby: Cubby) -> None:
    """Place or update ``cubby`` after checking its spot and label again under the guild lock.

    A rented cubby (or one held while someone pays) cannot be put out of service (#978). A new
    cubby in service, or one coming back into service, opens for the waitlist (#980).

    Raises:
        ValidationError: keyed by field, when the spot or the label was taken meanwhile or the
            unit shrank; on the whole form when the unit was archived meanwhile or a rented cubby
            would go out of service.
    """
    with transaction.atomic():
        _lock_guild(cubby.unit.guild_id)
        _fresh_unit(cubby)
        if cubby.unit.is_archived:
            raise ValidationError(RESTORE_SHELF_FIRST)
        if cubby.pk is not None and cubby.out_of_service and _rented_label([cubby]):
            # On the whole form: the Out of Service switch draws no error of its own.
            raise ValidationError(RENTED_CUBBY_STAYS_OPEN)
        problems = cubby.placement_problems()
        if problems:
            raise ValidationError(problems)
        was_closed = cubby.pk is None or Cubby.objects.filter(pk=cubby.pk, out_of_service=True).exists()
        cubby.save()
        if was_closed and not cubby.out_of_service and not _rented_label([cubby]):
            cubby_waitlist.open_for_waitlist(cubby)


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
    """Take the unit and its cubbies off the guild page, unless someone rents one of them (#978).

    Raises:
        ValidationError: naming the first rented (or held) cubby on the unit.
    """
    with transaction.atomic():
        _lock_guild(unit.guild_id)
        label = _rented_label(unit.cubbies.filter(archived_at__isnull=True))
        if label:
            raise ValidationError(RENTED_SHELF_STAYS.format(label=label))
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
        for cubby in unit.cubbies.filter(archived_at__isnull=True, out_of_service=False):
            cubby_waitlist.open_for_waitlist(cubby)


def archive_cubby(cubby: Cubby) -> None:
    """Take one cubby off the shelf; its square opens for a new one. Not while someone rents it (#978).

    Raises:
        ValidationError: the cubby is rented, or held while someone pays.
    """
    with transaction.atomic():
        _lock_guild(cubby.unit.guild_id)
        if _rented_label([cubby]):
            raise ValidationError(RENTED_CUBBY_STAYS.format(label=cubby.label))
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
        if not cubby.out_of_service:
            cubby_waitlist.open_for_waitlist(cubby)


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
    """The guild page's Cubbies tab (#977): the units members see, the summary line, and the
    viewer's own live rentals (#978), drawn as Your Cubby cards above the shelves.

    ``shelves`` is False when the guild's switch is off but the viewer still rents here: the
    tab then shows only their Your Cubby cards, so they can always cancel or fix their card.

    ``waitlist_entry`` is the viewer's place on the guild's waitlist (#980), with ``waitlist_place``
    counting from 1: shown as You're on the List whenever they wait, full or not, so they can leave.
    """

    guild: Guild
    units: list[UnitView]
    rentals: list[CubbyRental] = field(default_factory=list)
    viewer_pk: int | None = None
    shelves: bool = True
    waitlist_entry: CubbyWaitlistEntry | None = None
    waitlist_place: int = 0

    @classmethod
    def load(cls, guild: Guild, member: Member | None = None) -> CubbyTab:
        """The whole tab: the viewer's live rentals here, then active units with their active cubbies and renters.

        Four queries with a member (their waitlist entry too, and a fifth for its place when they
        wait), two without.
        """
        rentals = cls._rentals(guild, member)
        entry = cubby_waitlist.entry_for(guild, member) if member is not None else None
        units = list(guild.shelf_units.active())
        cubbies = list(
            Cubby.objects.active()
            .with_renter()
            .filter(unit__guild=guild)
            .select_related("unit__guild")
            .order_by("row", "column")
        )
        views = [view for view in _unit_views(units, cubbies) if view.cubbies]
        return cls(
            guild=guild,
            units=views,
            rentals=rentals,
            viewer_pk=member.pk if member is not None else None,
            waitlist_entry=entry,
            waitlist_place=cubby_waitlist.place_in_line(entry) if entry is not None else 0,
        )

    @classmethod
    def for_page(cls, guild: Guild, member: Member) -> CubbyTab | None:
        """What the guild page shows ``member``: the whole tab while the switch is on; with it off,
        only their Your Cubby cards and, while they wait, their place with Leave the List (#980), or
        None when they neither rent nor wait here."""
        if guild.show_cubbies_tab:
            return cls.load(guild, member)
        rentals = cls._rentals(guild, member)
        entry = cubby_waitlist.entry_for(guild, member)
        if not rentals and entry is None:
            return None
        return cls(
            guild=guild,
            units=[],
            rentals=rentals,
            viewer_pk=member.pk,
            shelves=False,
            waitlist_entry=entry,
            waitlist_place=cubby_waitlist.place_in_line(entry) if entry is not None else 0,
        )

    @staticmethod
    def _rentals(guild: Guild, member: Member | None) -> list[CubbyRental]:
        """The member's rentals holding a cubby in ``guild`` and billing (a hold awaiting payment is not theirs yet)."""
        if member is None:
            return []
        return list(
            CubbyRental.objects.live()
            .filter(member=member, cubby__unit__guild=guild)
            .exclude(status=CubbyRental.Status.PENDING_PAYMENT)
            .select_related("cubby__unit__guild")
            .order_by("created_at", "pk")
        )

    @property
    def is_full(self) -> bool:
        """Every cubby on the tab is rented or out of service (the full card, #978)."""
        return bool(self.units) and self.open_count == 0

    @property
    def offers_waitlist(self) -> bool:
        """Notify Me When One Opens shows: every cubby is taken and the viewer is not waiting yet (#980)."""
        return self.is_full and self.waitlist_entry is None

    @property
    def all_rented(self) -> bool:
        """Every cubby on the tab is rented (none out of service), for the full card's wording."""
        return all(cubby.state == Cubby.State.RENTED for cubby in self.cubbies)

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
            Cubby.objects.active()
            .with_renter()
            .filter(unit__guild=guild)
            .select_related("unit__guild")
            .order_by("row", "column")
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


# ── The staff Rentals tab (#979) ─────────────────────────────────────────────

#: The Rentals tab's filter chips, in order: ``?show=`` value and label. Current is the default.
RENTAL_FILTERS: tuple[tuple[str, str], ...] = (
    ("current", "Current"),
    ("past_due", "Past Due"),
    ("ended", "Ended"),
    ("all", "All"),
)
_FILTER_STATUSES: dict[str, tuple[str, ...]] = {
    "current": (CubbyRental.Status.ACTIVE, CubbyRental.Status.PAST_DUE),
    "past_due": (CubbyRental.Status.PAST_DUE,),
    "ended": (CubbyRental.Status.ENDED,),
    "all": (CubbyRental.Status.ACTIVE, CubbyRental.Status.PAST_DUE, CubbyRental.Status.ENDED),
}


def rental_filter(value: str) -> str:
    """The chip ``?show=`` picks, Current for anything it does not name."""
    return value if value in _FILTER_STATUSES else "current"


def staff_rentals(guild: Guild, show: str) -> QuerySet[CubbyRental]:
    """The guild's rentals one chip shows, with what a row draws loaded (a hold awaiting payment is not a rental yet).

    Each renter's primary email comes prefetched, so the table asks a fixed number of queries.
    """
    from allauth.account.models import EmailAddress

    return (
        CubbyRental.objects.filter(cubby__unit__guild=guild, status__in=_FILTER_STATUSES[rental_filter(show)])
        .select_related("member__user", "cubby__unit")
        .prefetch_related(
            Prefetch(
                "member__user__emailaddress_set",
                queryset=EmailAddress.objects.filter(primary=True),
                to_attr="_primary_emailaddresses",
            )
        )
    )


def initials(name: str) -> str:
    """ "Priya Nair" -> "PN": the avatar letters a rental row shows."""
    parts = name.split()
    if not parts:
        return ""
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


@dataclass(frozen=True)
class RentalsSummary:
    """The Rentals tab's tiles and chip counts (#979, mockup view manage-rentals), in three queries.

    Monthly Revenue is what the live rentals bring in each month. Of it, the guild gets its share
    of the Stripe rentals once it has picked who gets paid (``BillingSettings.cubby_guild_percent``),
    and all of a Paid Another Way rental's amount, which is paid to the guild directly.
    """

    monthly_cents: int
    to_guild_cents: int
    open_count: int
    cubby_count: int
    rented_count: int
    comped_count: int
    outside_count: int
    ending_count: int
    past_due: list[CubbyRental]
    chip_counts: dict[str, int]

    @classmethod
    def load(cls, guild: Guild) -> RentalsSummary:
        from billing.models import BillingSettings
        from billing.reconciliation import _cubby_percents, split_cents

        rentals = list(
            CubbyRental.objects.filter(cubby__unit__guild=guild)
            .exclude(status=CubbyRental.Status.PENDING_PAYMENT)
            .select_related("member", "cubby")
            .order_by("cubby__label", "pk")
        )
        cubbies = list(Cubby.objects.active().with_renter().filter(unit__guild=guild))
        live = [rental for rental in rentals if rental.is_live]
        stripe_cents = sum(rental.amount_cents for rental in live if not rental.is_assigned)
        outside_cents = sum(rental.amount_cents for rental in live if rental.source == CubbyRental.Source.OUTSIDE)
        to_guild = outside_cents
        if guild.cubby_payee_id is not None and stripe_cents:
            to_guild += split_cents(stripe_cents, _cubby_percents(BillingSettings.load()))["cubby_payee"]
        return cls(
            monthly_cents=stripe_cents + outside_cents,
            to_guild_cents=to_guild,
            open_count=sum(1 for cubby in cubbies if cubby.state == Cubby.State.OPEN),
            cubby_count=len(cubbies),
            rented_count=len(live),
            comped_count=sum(1 for rental in live if rental.is_comped),
            outside_count=sum(1 for rental in live if rental.source == CubbyRental.Source.OUTSIDE),
            ending_count=sum(1 for rental in live if rental.is_cancelling),
            past_due=[rental for rental in live if rental.is_past_due],
            chip_counts={
                key: sum(1 for rental in rentals if rental.status in _FILTER_STATUSES[key])
                for key, _label in RENTAL_FILTERS
            },
        )

    @property
    def monthly_display(self) -> str:
        """Monthly Revenue as "$232"."""
        return cubby_dollars(self.monthly_cents)

    @property
    def to_guild_display(self) -> str:
        """The guild's part of it as "$197.20"."""
        return cubby_dollars(self.to_guild_cents)

    @property
    def rented_sub(self) -> str:
        """Under Rented: "1 comped, 1 ending", or how they are all paid."""
        parts = [
            f"{count} {words}"
            for count, words in (
                (self.comped_count, "comped"),
                (self.outside_count, "paid another way"),
                (self.ending_count, "ending"),
            )
            if count
        ]
        if parts:
            return ", ".join(parts)
        return "All paid by card" if self.rented_count else "None yet"

    @property
    def past_due_sub(self) -> str:
        """Under Past Due: who and which cubby, "Priya Nair, A2", or that nobody is."""
        if not self.past_due:
            return "Every card went through"
        first = self.past_due[0]
        words = f"{first.member.display_name}, {first.cubby.label}"
        more = len(self.past_due) - 1
        return f"{words} and {more} more" if more else words

    @property
    def filters(self) -> list[tuple[str, str, int]]:
        """Each chip as (``show`` value, label, count)."""
        return [(key, label, self.chip_counts[key]) for key, label in RENTAL_FILTERS]


def _short_day(value: date | datetime | None) -> str:
    """ "Nov 27": the Rentals tab's dates."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = timezone.localtime(value).date()
    return f"{value:%b} {value.day}"


@dataclass(frozen=True)
class RentalRow:
    """One rental as the Rentals table and its phone card draw it (#979): the same words in both."""

    rental: CubbyRental
    name: str
    initials: str
    email: str
    amount: str
    amount_note: str
    since: str
    next_main: str
    next_note: str
    card_label: str
    card_value: str

    @classmethod
    def build(cls, rental: CubbyRental) -> RentalRow:
        """``rental`` needs its member (with the primary email prefetched) and cubby loaded."""
        name = rental.member.display_name
        if rental.is_comped:
            amount, amount_note = "$0", ""
        else:
            amount = f"{rental.amount_display}/mo"
            amount_note = "Paid another way" if rental.is_assigned else ""
        period_end = _short_day(rental.current_period_end)
        state = rental.staff_state
        if state == "ended":
            next_main, next_note = "None", f"Ended {_short_day(rental.end_date)}"
            card_label, card_value = "Ended", _short_day(rental.end_date)
        elif state == "past_due":
            retry = _short_day(rental.next_payment_attempt)
            next_main, next_note = "Retrying", f"Next try {retry}" if retry else "No more tries"
            card_label, card_value = "Next Try", retry or "None"
        elif state == "cancelling":
            next_main, next_note = "None", f"Ends {period_end}"
            card_label, card_value = "Ends", period_end
        elif rental.is_assigned:
            next_main, next_note = "None", "Never charged"
            card_label, card_value = "Next Charge", "None"
        else:
            next_main, next_note = period_end, ""
            card_label, card_value = "Next Charge", period_end
        return cls(
            rental=rental,
            name=name,
            initials=initials(name),
            email=rental.member.primary_email,
            amount=amount,
            amount_note=amount_note,
            since=_short_day(rental.start_date),
            next_main=next_main,
            next_note=next_note,
            card_label=card_label,
            card_value=card_value,
        )

    @property
    def end_message(self) -> str:
        """End Rental's confirm sentence: what happens, and that nothing is refunded."""
        rental = self.rental
        cubby = rental.cubby
        first = self.name.split()[0] if self.name.split() else self.name
        sentence = f"{cubby.label} on the {cubby.unit.name} opens right away"
        sentence += "." if rental.is_assigned else f" and {first} stops being charged. Nothing is refunded."
        if rental.is_cancelling:
            sentence += f" {first} was already cancelling on {_short_day(rental.current_period_end)}."
        return f"{sentence} We'll email {first} your reason, so check the shelf is clear first."


# ── The staff Waitlist (#980) ────────────────────────────────────────────────


@dataclass(frozen=True)
class WaitlistRow:
    """One waiting member as the Rentals tab's Waitlist chip draws them: the table row and the phone card."""

    entry: CubbyWaitlistEntry
    place: int
    name: str
    initials: str
    joined: str

    @classmethod
    def for_guild(cls, guild: Guild) -> list[WaitlistRow]:
        """The guild's line, first told first, in one query."""
        rows = []
        for place, entry in enumerate(cubby_waitlist.line(guild), start=1):
            name = entry.member.display_name
            rows.append(
                cls(entry=entry, place=place, name=name, initials=initials(name), joined=_short_day(entry.joined_at))
            )
        return rows

    @property
    def remove_message(self) -> str:
        """Remove's confirm sentence: who moves up, and that the member is not told."""
        first = self.name.split()[0] if self.name.split() else self.name
        return f"{first} leaves the waitlist and everyone behind them moves up one. We won't tell {first}."

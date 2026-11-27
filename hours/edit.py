"""Changing one set of a person's hours, every kind together (#1300, part 4).

A set of hours (:class:`hours.models.Hours`) is never written directly: its kind rows are, and the
set follows them (``hours.follow``). :class:`HoursEdit` is one edit of one set, new or saved: its
time once (``TimeKey``), whether it is open to Tours and with what tour length and seats, and each
orientation it is open to with that row's own settings. :meth:`HoursEdit.problems` checks it the
way the old editors check their rows (each row's own ``clean``, a guide's tour rows never
overlapping, an open window sharing its hours with nothing); :meth:`HoursEdit.save` writes it
through the services those editors use, so slots and windows regenerate as they always have: a
restyled orientation retires its old shape first, an orientation ticked off is paused (its open
times retired, booked slots stay, its settings kept for when it is ticked on again: the old editors'
Active switch), and every owner touched generates again. A tour ticked off
deletes its row; tours already booked stay booked, as on the Tours page.

Whoever calls :meth:`HoursEdit.save` or :func:`remove_hours` holds the person's lock
(``hours.follow.lock_person``) across the check and the write, as the Tours page's save does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db.models import Q

from hours.models import Hours, TimeKey
from membership.models import Equipment, Guild, OrientationAvailability, OrientationType
from tours.models import TourHours, guide_filter

if TYPE_CHECKING:
    from membership.models import Member

TOURS = "tours"
#: Where a problem with the set's own time lands, apart from any one kind.
TIME = "time"
#: Fields of a kind row that are the set's time: an error on one of them is the time's.
_TIME_FIELDS = frozenset({"weekday", "date", "cadence", "anchor_date", "last_of_month", "start_time", "end_time"})


def owner_key(guild_id: int | None, equipment_id: int | None) -> str:
    """``equipment-<pk>`` for a tool's orientations, else ``guild-<pk>``: the kind an orientation row is."""
    if equipment_id is not None:
        return f"equipment-{equipment_id}"
    return f"guild-{guild_id}"


def rule_key(rule: OrientationAvailability) -> str:
    """The kind a saved orientation row is: its type's tool or guild, or its own guild for Any orientation."""
    orientation_type = rule.orientation_type
    if orientation_type is not None:
        return owner_key(orientation_type.guild_id, orientation_type.equipment_id)
    return owner_key(rule.guild_id, None)


@dataclass(frozen=True)
class Owner:
    """A guild or tool whose orientations a person gives: one kind on My Hours."""

    key: str
    guild: Guild | None
    equipment: Equipment | None

    @property
    def name(self) -> str:
        return self.equipment.name if self.equipment is not None else self.guild.name if self.guild else ""

    @property
    def label(self) -> str:
        """``Ceramics Guild orientations``: the Open To toggle's words, as the week's legend says them."""
        return f"{self.name} orientations"

    def types(self) -> Any:
        """The orientations this owner offers now, as the Edit Hours editors scope them."""
        if self.equipment is not None:
            return self.equipment.owned_orientation_types.active()
        return OrientationType.objects.filter(guild=self.guild).active()

    @classmethod
    def of(cls, rule: OrientationAvailability) -> Owner:
        """The owner a saved orientation row belongs to."""
        orientation_type = rule.orientation_type
        if orientation_type is not None and orientation_type.equipment_id is not None:
            return cls(rule_key(rule), None, orientation_type.equipment)
        guild = orientation_type.guild if orientation_type is not None else rule.guild
        return cls(rule_key(rule), guild, None)


def gives_tours(member: Member) -> bool:
    """Whether ``member`` may offer tours: a Tour Guide or an admin (``tours.models.guide_filter``)."""
    from membership.models import Member

    return Member.objects.filter(pk=member.pk).filter(guide_filter()).exists()


def held_owners(member: Member) -> list[Owner]:
    """Each guild and tool ``member`` is a current orientor of and that offers an orientation, by name (#1300, Felix 7).

    A guild: its lead or staff, while an Active member (``Guild.orientor_ids``). A tool: its own
    staff or its guild's leadership, likewise (``Equipment.orientor_ids``). Only these are offered
    in Open To; a saved row of any other owner still shows, so it can be ticked off.
    """
    from membership.models import Member

    if member.status != Member.Status.ACTIVE:
        return []
    guilds = list(
        Guild.objects.filter(Q(guild_lead=member) | Q(staff_memberships__member=member))
        .filter(orientation_types__is_active=True, orientation_types__equipment__isnull=True)
        .distinct()
    )
    led = Guild.objects.filter(Q(guild_lead=member) | Q(staff_memberships__member=member))
    tools = list(
        Equipment.objects.filter(Q(staff_memberships__member=member) | Q(guild__in=led))
        .filter(owned_orientation_types__is_active=True)
        .distinct()
    )
    owners = [Owner(owner_key(guild.pk, None), guild, None) for guild in guilds]
    owners += [Owner(owner_key(None, tool.pk), None, tool) for tool in tools]
    return sorted(owners, key=lambda owner: owner.name.casefold())


@dataclass
class TourOffer:
    """Tours in this set of hours, with each tour's length and seats."""

    tour_minutes: int
    seats: int


@dataclass
class OrientationOffer:
    """One orientation row in this set of hours: a saved one (``rule``) or a new one for ``owner``.

    ``on`` False on a saved active row pauses it, and leaves a paused one paused; a new row that is off is nothing.
    """

    owner: Owner
    on: bool
    rule: OrientationAvailability | None = None
    orientation_type: OrientationType | None = None
    booking_style: str = OrientationAvailability.BookingStyle.FIXED
    seats: int = 4
    slot_minutes: int | None = None
    buffer_minutes: int = 0

    @property
    def key(self) -> str:
        """The offer's own name in a problem list: ``rule-<pk>`` for a saved row, the owner's key for a new one."""
        return f"rule-{self.rule.pk}" if self.rule is not None else self.owner.key


@dataclass
class HoursEdit:
    """One edit of one set of ``member``'s hours: a new set when ``hours`` is None."""

    member: Member
    hours: Hours | None
    key: TimeKey
    tours: TourOffer | None
    orientations: list[OrientationOffer] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._tour_row = self.hours.tour_hours.first() if self.hours is not None else None
        self._built: list[tuple[OrientationOffer, OrientationAvailability]] = []

    # --- Reading --------------------------------------------------------------------------------

    def _time_of(self, row: Any) -> None:
        for name, value in self.key._asdict().items():
            setattr(row, name, value)

    def _tour_row_built(self) -> TourHours | None:
        if self.tours is None:
            return None
        row = self._tour_row or TourHours(guide=self.member)
        self._time_of(row)
        row.tour_minutes = self.tours.tour_minutes
        row.seats = self.tours.seats
        return row

    def _rule_built(self, offer: OrientationOffer) -> OrientationAvailability:
        rule = offer.rule or OrientationAvailability(orienter=self.member)
        self._time_of(rule)
        rule.guild = offer.owner.guild
        rule.orientation_type = offer.orientation_type
        rule.booking_style = offer.booking_style
        is_open = offer.booking_style == OrientationAvailability.BookingStyle.OPEN
        rule.seats = offer.seats
        rule.slot_minutes = None if is_open else offer.slot_minutes
        rule.buffer_minutes = 0 if is_open else offer.buffer_minutes
        rule.is_active = True
        return rule

    # --- Checking -------------------------------------------------------------------------------

    def problems(self) -> dict[str, dict[str, list[str]]]:
        """Everything wrong with this edit, by where it belongs: ``time``, ``tours`` or an offer's key, then field.

        A field of ``None`` is the kind's own, not one of its fields. Empty when it may be saved.
        """
        found: dict[str, dict[str, list[str]]] = {}

        def add(where: str, name: str | None, message: str) -> None:
            found.setdefault(where, {}).setdefault(name or "__all__", []).append(message)

        self._check_time(add)
        on = [offer for offer in self.orientations if offer.on]
        if self.tours is None and not on:
            add(TIME, None, "Tick at least one thing these hours are open to, or delete them.")
        if found:
            return found
        self._check_tours(add)
        self._check_orientations(on, add)
        return found

    def _check_tours(self, add: Any) -> None:
        tour = self._tour_row_built()
        if tour is None:
            return
        self._check_row(tour, TOURS, add, exclude=["guide", "hours"])
        for other in TourHours.objects.filter(guide=self.member).exclude(pk=tour.pk):
            if tour.overlaps(other):
                add(TOURS, None, f"These hours overlap your tours {_when(other)}. You give one tour at a time.")

    def _check_orientations(self, on: list[OrientationOffer], add: Any) -> None:
        siblings = frozenset(offer.rule.pk for offer in on if offer.rule is not None)
        self._built = []
        for offer in on:
            rule = self._rule_built(offer)
            rule.overlap_skip_pks = siblings
            self._check_row(rule, offer.key, add, exclude=["hours", "orienter", "guild", "location"])
            self._built.append((offer, rule))
        if len(on) > 1:
            for offer, rule in self._built:
                if rule.is_open:
                    add(offer.key, "booking_style", "Any time in the window needs these hours to itself.")

    def _check_time(self, add: Any) -> None:
        key = self.key
        if key.end_time <= key.start_time:
            add(TIME, "end_time", "Until must be after From.")
        for name in ("start_time", "end_time"):
            value: time = getattr(key, name)
            if value.minute % 30 or value.second or value.microsecond:
                add(TIME, name, "Hours line up on half hour marks, for example 4:00 or 4:30.")
        for name, message in OrientationAvailability.repeat_errors(SimpleNamespace(**key._asdict())).items():
            add(TIME, name, message)
        same = Hours.objects.filter(member=self.member, **key._asdict())
        if self.hours is not None:
            same = same.exclude(pk=self.hours.pk)
        if same.exists():
            add(TIME, None, "You already have hours at exactly this time. Edit those instead.")

    def _check_row(self, row: Any, where: str, add: Any, *, exclude: list[str]) -> None:
        try:
            row.full_clean(exclude=exclude, validate_unique=False, validate_constraints=False)
        except ValidationError as error:
            for name, messages in error.message_dict.items():
                for message in messages:
                    add(TIME if name in _TIME_FIELDS else where, None if name == "__all__" else name, message)

    # --- Writing --------------------------------------------------------------------------------

    def save(self) -> Hours:
        """Write the edit (after :meth:`problems` came back empty, under the person's lock) and return its set.

        Raises:
            ValueError: when :meth:`problems` was not asked first, or found something.
        """
        from membership import orientations

        if self.problems():
            raise ValueError("HoursEdit.save needs an edit with no problems.")
        touched: dict[str, Owner] = {}
        # What stays on is written before what goes, so the set always holds a row and keeps its key.
        tour = self._write_kept(touched)
        if tour is None and self._tour_row is not None:
            self._tour_row.delete()
        for offer in self.orientations:
            if not offer.on and offer.rule is not None and offer.rule.is_active:
                # Paused, as the old Active switch did: its open times go, its settings stay for a tick back on.
                rule = OrientationAvailability.objects.get(pk=offer.rule.pk)
                orientations.retire_open_slots(rule)
                self._time_of(rule)
                rule.is_active = False
                rule.save()
                touched[offer.owner.key] = offer.owner
        for owner in touched.values():
            orientations.generate_slots(guild=owner.guild, equipment=owner.equipment)
        return Hours.objects.get(member=self.member, **self.key._asdict())

    def _write_kept(self, touched: dict[str, Owner]) -> TourHours | None:
        """Save the tour row and every orientation row left on, and move the set's paused rows with it."""
        from membership import orientations

        tour = self._tour_row_built()
        if tour is not None:
            tour.save()
        for offer, rule in self._built:
            if offer.rule is not None:
                stored = OrientationAvailability.objects.get(pk=offer.rule.pk)
                if stored.booking_style != rule.booking_style:
                    # Generation only tidies the current shape (as the Edit Hours save does, #532).
                    orientations.retire_open_slots(stored)
            rule.save()
            touched[offer.owner.key] = offer.owner
        if self.hours is not None:
            # A paused row of this set (no Open To tick) keeps its pause but follows the new time.
            for paused in self.hours.orientation_rules.filter(is_active=False):
                self._time_of(paused)
                paused.save()
        return tour


def remove_hours(hours: Hours) -> tuple[int, int]:
    """Delete one set of hours (after the confirm, under the person's lock): every kind's row goes the way its old Delete did.

    Its tour rows are deleted (tours already booked stay booked); each orientation row is retired
    (``retire_rule``: open times go, booked ones stay). The set itself goes with its last row.

    Returns:
        ``(open_times_removed, kept_with_bookings)`` across its orientation rows.
    """
    from membership import orientations

    removed = kept = 0
    for row in list(hours.tour_hours.all()):
        row.delete()
    for rule in list(hours.orientation_rules.all()):
        rule_removed, rule_kept = orientations.retire_rule(rule)
        removed += rule_removed
        kept += rule_kept
    return removed, kept


def _when(row: TourHours) -> str:
    """``on Mondays, 11:00 AM to 6:00 PM`` or ``on Sat, Nov 29, ...``: an overlapping tour row, in words."""
    if row.date is not None:
        day: date = row.date
        return f"on {day:%a, %b} {day.day}, {row.times_label}"
    return f"on {row.get_weekday_display()}s, {row.times_label}"

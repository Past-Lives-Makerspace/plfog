"""Plan, then apply, the one-off Location backfill (#616, part 2).

:func:`plan_backfill` reads the database and returns a :class:`BackfillPlan` without writing
anything: the locations to create or fill in (by name), the shares space link, and a proposed
location for every record whose Location is blank. It only issues SELECTs, so it runs on a read
only connection (it is run against production through a read only door before anything is
applied). Locations are referenced by name, because on a first run they do not exist yet.

:func:`apply_backfill` writes a plan in one transaction. It creates missing locations, fills a
blank guild or note on an existing one (a set value is kept and reported), links the locations
that share space, and sets ``area`` only where it is still blank, through ``queryset.update`` so
no save side effects fire. Running it twice changes nothing the second time.

The rules are data, kept as the module constants below. Keyword rules match case insensitively
at the start of a word ("forg" matches "Forging", "tig" does not match "Vestige"); the first
matching rule wins. Demo classes and everything belonging to the example guild are skipped.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from django.apps import apps
from django.db import transaction

from classes.models import ClassOffering
from membership.models import EXAMPLE_GUILD_SLUG, CommunityEvent, Equipment, Guild, Location, OrientationType


@dataclass(frozen=True)
class LocationSpec:
    """One location the backfill makes sure exists."""

    name: str
    guild_name: str | None
    note: str = ""


#: The locations Felix confirmed on 2026-10-04, each with its guild matched by exact ``Guild.name``.
LOCATIONS: tuple[LocationSpec, ...] = (
    LocationSpec("Art Framing Studio", "Art Framing Guild"),
    LocationSpec("Ceramics Studio", "Ceramics Guild"),
    LocationSpec("Cold Glass Room", "Glass Guild"),
    LocationSpec("Hot Glass Room", "Glass Guild"),
    LocationSpec("Front Studio", "Visual Arts/Gallery Guild"),
    LocationSpec("Events Stage", "Events Guild"),
    LocationSpec("Jewelry Studio", "Jewelry Guild"),
    LocationSpec("Leather Area", "Leatherwork Guild"),
    LocationSpec("Metal Shop", "Metalworkers Guild"),
    LocationSpec("Print Studio", "Printmaking Guild"),
    LocationSpec("Tech Area", "Tech Guild"),
    LocationSpec("Resin Printing Room", "Tech Guild"),
    LocationSpec("Miniatures Desk", "Tech Guild"),
    LocationSpec("Audio Desk", "Tech Guild"),
    LocationSpec("Textiles Studio", "Textiles Guild"),
    LocationSpec("Woodshop", "Woodworking Guild"),
    LocationSpec("Woodshop Sanding Area", "Woodworking Guild"),
    LocationSpec("Garden", "Gardeners Guild"),
    LocationSpec("Kitchen", "Food Independence Guild"),
    LocationSpec("Common Area", None, "Upstairs next to the Kitchen"),
    LocationSpec("Loading Dock", None),
    LocationSpec("CNC Area", None),
)

#: Pairs of locations on the same floor space (the link is symmetric).
SHARED_SPACE: tuple[tuple[str, str], ...] = (("Front Studio", "Events Stage"),)

#: Where a guild's record goes when nothing more specific matches. Writers and Prison Outreach have none.
GUILD_PRIMARY_LOCATION: dict[str, str] = {
    "Art Framing Guild": "Art Framing Studio",
    "Ceramics Guild": "Ceramics Studio",
    "Glass Guild": "Hot Glass Room",
    "Jewelry Guild": "Jewelry Studio",
    "Leatherwork Guild": "Leather Area",
    "Metalworkers Guild": "Metal Shop",
    "Printmaking Guild": "Print Studio",
    "Tech Guild": "Tech Area",
    "Textiles Guild": "Textiles Studio",
    "Visual Arts/Gallery Guild": "Front Studio",
    "Woodworking Guild": "Woodshop",
    "Gardeners Guild": "Garden",
    "Food Independence Guild": "Kitchen",
    "Events Guild": "Events Stage",
}

#: Where a class goes when its category has no guild.
GUILDLESS_CLASS_LOCATION = "Common Area"

#: Class title keywords, checked in order before the category's guild.
CLASS_TITLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Cold Glass Room", ("stained",)),
    (
        "Hot Glass Room",
        (
            "lampwork",
            "boro",
            "torch",
            "frit",
            "marble",
            "mushroom",
            "bead",
            "glassblowing",
            "glass blowing",
            "soft glass",
            "fused",
            "mandrel",
        ),
    ),
    ("Metal Shop", ("tig", "welding", "damascus", "forg", "blacksmith", "brooch", "barrette", "chain")),
    ("Front Studio", ("figure drawing", "draw models")),
    ("Common Area", ("coworking",)),
)

#: Classes that stay blank whatever their title or category, by id, with the reason the dry run lists.
CLASS_IDS_LEFT_BLANK: dict[int, str] = {
    672: "online class",
}

#: Orientation type ``default_location`` keywords, checked after the owning equipment's location.
ORIENTATION_LOCATION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Art Framing Studio", ("art framing",)),
    ("Events Stage", ("gallery/stage", "stage")),
    ("Cold Glass Room", ("cold glass",)),
    ("Hot Glass Room", ("hot glass",)),
    ("Woodshop", ("woodshop",)),
)

#: Equipment named here (exact name, any case) goes to the location given, ahead of its guild.
EQUIPMENT_BY_NAME: dict[str, str] = {
    "laser engraver mira 9": "Tech Area",
    "cnc machine": "CNC Area",
}

#: Event ``location`` text keywords, checked before the event's guild.
EVENT_LOCATION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Tech Area", ("tech",)),
    ("Front Studio", ("visual arts", "gallery")),
    ("Ceramics Studio", ("ceramics",)),
    ("Woodshop", ("woodshop",)),
    ("Leather Area", ("leather",)),
    ("Print Studio", ("print",)),
)

#: The model each assignment kind writes to, as an app label path.
MODEL_FOR_KIND: dict[str, str] = {
    "class": "classes.ClassOffering",
    "orientation type": "membership.OrientationType",
    "equipment": "membership.Equipment",
    "event": "membership.CommunityEvent",
}


@dataclass(frozen=True)
class LocationChange:
    """What the backfill does to one location: ``create``, ``update`` (fill blanks) or ``keep``."""

    name: str
    action: str
    guild_name: str | None
    note: str
    fills: tuple[str, ...] = ()


@dataclass(frozen=True)
class Assignment:
    """A proposed Location for one record, or ``location=None`` with the reason it stays blank."""

    kind: str
    pk: int
    title: str
    location: str | None
    rule: str


@dataclass
class BackfillPlan:
    """Everything :func:`apply_backfill` would write, computed without writing."""

    locations: list[LocationChange] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def proposed(self) -> list[Assignment]:
        """Assignments that set a location."""
        return [a for a in self.assignments if a.location is not None]

    @property
    def blank(self) -> list[Assignment]:
        """Records left blank, each with its reason."""
        return [a for a in self.assignments if a.location is None]

    def counts_by_location(self) -> list[tuple[str, int]]:
        """How many records each location receives, most first, ties by name."""
        counts = Counter(a.location for a in self.assignments if a.location is not None)
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


@dataclass(frozen=True)
class BackfillResult:
    """What :func:`apply_backfill` wrote."""

    created: int
    updated: int
    linked: int
    assigned: int


def _match_keywords(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, str] | None:
    """The first ``(location, keyword)`` whose keyword starts a word in ``text``, any case."""
    for location, keywords in rules:
        for keyword in keywords:
            if re.search(r"\b" + re.escape(keyword), text, re.IGNORECASE):
                return location, keyword
    return None


def _guild_primary(guild: Guild | None, what: str) -> tuple[str | None, str]:
    """The guild's primary location and the rule text, or ``None`` with why it stays blank."""
    if guild is None:
        return None, f"{what} has no guild"
    location = GUILD_PRIMARY_LOCATION.get(guild.name)
    if location is None:
        return None, f"{guild.name} has no primary location"
    return location, f"{what} guild {guild.name}"


def _is_example(guild: Guild | None) -> bool:
    return guild is not None and guild.slug == EXAMPLE_GUILD_SLUG


def _class_location(offering: ClassOffering) -> tuple[str | None, str]:
    match = _match_keywords(offering.title, CLASS_TITLE_KEYWORDS)
    if match is not None:
        return match[0], f'title keyword "{match[1]}"'
    guild = offering.category.guild
    if guild is None:
        return GUILDLESS_CLASS_LOCATION, f"category {offering.category.name} has no guild"
    return _guild_primary(guild, "category")


def _equipment_location(equipment: Equipment) -> tuple[str | None, str]:
    named = EQUIPMENT_BY_NAME.get(equipment.name.strip().lower())
    if named is not None:
        return named, f'equipment name "{equipment.name}"'
    return _guild_primary(equipment.guild, "equipment")


def _orientation_location(orientation: OrientationType) -> tuple[str | None, str]:
    if orientation.equipment is not None:
        if orientation.equipment.area is not None:
            return orientation.equipment.area.name, f"equipment {orientation.equipment.name}'s location"
        location, rule = _equipment_location(orientation.equipment)
        if location is not None:
            return location, f"equipment {orientation.equipment.name}: {rule}"
    match = _match_keywords(orientation.default_location, ORIENTATION_LOCATION_KEYWORDS)
    if match is not None:
        return match[0], f'default location keyword "{match[1]}"'
    return _guild_primary(orientation.guild, "orientation")


def _event_location(event: CommunityEvent) -> tuple[str | None, str]:
    match = _match_keywords(event.location, EVENT_LOCATION_KEYWORDS)
    if match is not None:
        return match[0], f'location text keyword "{match[1]}"'
    return _guild_primary(event.guild, "event")


def _plan_locations(plan: BackfillPlan) -> None:
    guilds = {g.name: g for g in Guild.objects.filter(name__in={s.guild_name for s in LOCATIONS if s.guild_name})}
    existing = {loc.name: loc for loc in Location.objects.select_related("guild").prefetch_related("shares_space_with")}
    for spec in LOCATIONS:
        guild_name = spec.guild_name
        if guild_name is not None and guild_name not in guilds:
            plan.warnings.append(f'No guild named "{guild_name}"; {spec.name} is left without a guild.')
            guild_name = None
        current = existing.get(spec.name)
        if current is None:
            plan.locations.append(LocationChange(spec.name, "create", guild_name, spec.note))
            continue
        fills: list[str] = []
        if guild_name is not None:
            if current.guild is None:
                fills.append("guild")
            elif current.guild.name != guild_name:
                plan.warnings.append(
                    f"{spec.name} already belongs to {current.guild.name}, not {guild_name}; it is kept."
                )
        if spec.note and not current.note:
            fills.append("note")
        plan.locations.append(
            LocationChange(spec.name, "update" if fills else "keep", guild_name, spec.note, tuple(fills))
        )
    for first, second in SHARED_SPACE:
        current = existing.get(first)
        if current is None or second not in {other.name for other in current.shares_space_with.all()}:
            plan.links.append((first, second))


def _class_plan(offering: ClassOffering) -> tuple[str | None, str]:
    if offering.pk in CLASS_IDS_LEFT_BLANK:
        return None, CLASS_IDS_LEFT_BLANK[offering.pk]
    if offering.is_demo:
        return None, "demo class, skipped"
    if _is_example(offering.category.guild):
        return None, "example guild, skipped"
    return _class_location(offering)


def _orientation_plan(orientation: OrientationType) -> tuple[str | None, str]:
    owner = orientation.guild if orientation.equipment is None else orientation.equipment.guild
    if _is_example(owner):
        return None, "example guild, skipped"
    return _orientation_location(orientation)


def _equipment_plan(equipment: Equipment) -> tuple[str | None, str]:
    if _is_example(equipment.guild):
        return None, "example guild, skipped"
    return _equipment_location(equipment)


def _event_plan(event: CommunityEvent) -> tuple[str | None, str]:
    if _is_example(event.guild):
        return None, "example guild, skipped"
    return _event_location(event)


def _plan_assignments(plan: BackfillPlan) -> None:
    def add(kind: str, pk: int, title: str, result: tuple[str | None, str]) -> None:
        plan.assignments.append(Assignment(kind, pk, title, result[0], result[1]))

    for offering in ClassOffering.objects.filter(area__isnull=True).select_related("category__guild").order_by("pk"):
        add("class", offering.pk, offering.title, _class_plan(offering))
    orientations = OrientationType.objects.filter(area__isnull=True).select_related(
        "guild", "equipment__guild", "equipment__area"
    )
    for orientation in orientations.order_by("pk"):
        add("orientation type", orientation.pk, orientation.name, _orientation_plan(orientation))
    for equipment in Equipment.objects.filter(area__isnull=True).select_related("guild").order_by("pk"):
        add("equipment", equipment.pk, equipment.name, _equipment_plan(equipment))
    for event in CommunityEvent.objects.filter(area__isnull=True).select_related("guild").order_by("pk"):
        add("event", event.pk, event.title, _event_plan(event))


def plan_backfill() -> BackfillPlan:
    """Work out every change the backfill would make, issuing only SELECTs.

    Returns:
        The plan: location changes, shares space links, one :class:`Assignment` per record whose
        Location is blank (proposed or left blank with a reason), and warnings for missing guilds
        or a location that already belongs to another guild.
    """
    plan = BackfillPlan()
    _plan_locations(plan)
    _plan_assignments(plan)
    return plan


@transaction.atomic
def apply_backfill(plan: BackfillPlan) -> BackfillResult:
    """Write ``plan`` in one transaction. Idempotent; never replaces a set guild, note or Location.

    Args:
        plan: A plan from :func:`plan_backfill`, ideally computed just before.

    Returns:
        Counts of locations created and filled in, links added and records assigned.
    """
    guilds = {g.name: g for g in Guild.objects.filter(name__in={c.guild_name for c in plan.locations if c.guild_name})}
    created = updated = 0
    for change in plan.locations:
        guild = guilds[change.guild_name] if change.guild_name else None
        location, was_created = Location.objects.get_or_create(
            name=change.name, defaults={"guild": guild, "note": change.note}
        )
        if was_created:
            created += 1
            continue
        fields = []
        if guild is not None and location.guild_id is None:
            location.guild = guild
            fields.append("guild")
        if change.note and not location.note:
            location.note = change.note
            fields.append("note")
        if fields:
            location.save(update_fields=fields)
            updated += 1

    by_name = {loc.name: loc for loc in Location.objects.all()}
    linked = 0
    for first, second in plan.links:
        if not by_name[first].shares_space_with.filter(pk=by_name[second].pk).exists():
            by_name[first].shares_space_with.add(by_name[second])
            linked += 1

    assigned = 0
    for assignment in plan.assignments:
        if assignment.location is None:
            continue
        model = apps.get_model(MODEL_FOR_KIND[assignment.kind])
        assigned += model.objects.filter(pk=assignment.pk, area__isnull=True).update(area=by_name[assignment.location])
    return BackfillResult(created=created, updated=updated, linked=linked, assigned=assigned)

"""What the HQ pages compute across models: the goal band's rows, the volunteer sheet and the wine wall.

Each live goal kind is counted here, so the page and the model never learn where a figure
comes from. The volunteer half holds the Your Info step (:class:`VolunteerInfo`, kept in the
session), the two writes (:func:`sign_up`, :func:`remove`) with their named refusals, the
band over the shifts (:func:`volunteer_band`), the organisers' roster (:func:`roster_rows`)
and the rate key a guest's sign-ups are counted under. The wine wall half holds the band
over the wall (:func:`wine_band`), the wineries grid (:func:`wineries`), the organisers'
donors table (:func:`donor_rows`) and the three writes (:func:`log_bottle`,
:func:`delete_bottle`, :func:`set_winery_logo`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import F, IntegerField, Sum
from django.db.models.functions import Lower
from django.http import HttpRequest

from hq.models import (
    EventHub,
    HubGoal,
    ShiftPhase,
    ShiftSignup,
    VolunteerShift,
    WineBottle,
    WineKind,
    WineryLogo,
    format_money,
)
from membership.models import CommunityEvent, Member


@dataclass(frozen=True)
class GoalRow:
    """One chip in the goal band, ready to render."""

    label: str
    current: int
    target: int
    is_money: bool

    @property
    def percent(self) -> int:
        """How full the meter is, 0 to 100; a goal with no target reads empty."""
        if self.target == 0:
            return 0
        return min(100, round(self.current * 100 / self.target))

    @property
    def current_display(self) -> str:
        return _figure(self.current, self.is_money)

    @property
    def target_display(self) -> str:
        return _figure(self.target, self.is_money)


def _figure(value: int, is_money: bool) -> str:
    return f"${value:,}" if is_money else f"{value:,}"


def rsvp_count(event: CommunityEvent | None) -> int:
    """How many people are going: the event's people count (party sizes summed), or 0 with no event."""
    if event is None:
        return 0
    return event.rsvp_people_count()


def volunteer_spots_filled(hub: EventHub) -> int:
    """Volunteer shift sign-ups across the hub's shifts (the band's "spots filled")."""
    return volunteer_band(hub).filled


_LIVE_COUNTS: dict[str, Callable[[EventHub], int]] = {
    HubGoal.Kind.RSVPS: lambda hub: rsvp_count(hub.event),
    HubGoal.Kind.VOLUNTEER_SPOTS: volunteer_spots_filled,
}

# The two wine wall kinds are read off one band, so a hub showing both pays for it once.
_WINE_KINDS: frozenset[str] = frozenset({HubGoal.Kind.BOTTLES, HubGoal.Kind.BOTTLE_VALUE})


def _live_counts(hub: EventHub, kind: str) -> dict[str, int]:
    """The live figure for ``kind``, with any sibling kind the same queries answer."""
    if kind in _WINE_KINDS:
        band = wine_band(hub)
        return {HubGoal.Kind.BOTTLES: band.bottles, HubGoal.Kind.BOTTLE_VALUE: band.value_cents // 100}
    return {kind: _LIVE_COUNTS[kind](hub)}


def goal_rows(hub: EventHub) -> list[GoalRow]:
    """Every goal on ``hub`` with its live figure, in band order.

    One query for the goals and a fixed number per live kind that appears, however many
    goals share a kind; a manual goal costs nothing beyond its row.
    """
    counts: dict[str, int] = {}
    rows: list[GoalRow] = []
    for goal in hub.goals.all():
        if goal.kind == HubGoal.Kind.MANUAL:
            current = goal.current_value
        else:
            if goal.kind not in counts:
                counts.update(_live_counts(hub, goal.kind))
            current = counts[goal.kind]
        rows.append(GoalRow(label=goal.label, current=current, target=goal.target, is_money=goal.is_money))
    return rows


# ---- Volunteer shifts ----

# The session key the Your Info step is kept under, for guests and members alike.
SESSION_KEY = "hq_volunteer"

# The scope a guest's sign-ups are counted under in core.abuse_limits, and the caps. A whole
# makerspace can share one address, and Lee's sheet has 21 shifts, so the ceiling is a cost
# cap on a flood rather than a tight per-person budget.
RATE_SCOPE = "hq_signup"
RATE_HOURLY_LIMIT = 30
RATE_DAILY_LIMIT = 100

# A forwarded-for entry is an IP, so anything longer is junk; bound it before it becomes a
# cache key.
_RATE_KEY_MAX_CHARS = 64


class ShiftFull(Exception):
    """Every spot on the shift is taken."""


class AlreadySignedUp(Exception):
    """This email is already on the shift."""


class NotSignedUp(Exception):
    """This email is not on the shift, so there is nothing to remove."""


@dataclass(frozen=True)
class VolunteerInfo:
    """The Your Info step: who is signing up, kept in the session until the browser forgets it."""

    name: str
    email: str
    phone: str = ""
    has_olcc: bool = False
    note: str = ""

    @classmethod
    def for_member(cls, member: Member) -> VolunteerInfo:
        """What a signed in member starts with: their display name, primary email and phone."""
        return cls(name=member.display_name, email=member.primary_email, phone=member.phone)

    @classmethod
    def from_session(cls, request: HttpRequest) -> VolunteerInfo | None:
        """The saved step, or None when this browser has not saved one."""
        data = request.session.get(SESSION_KEY)
        if data is None:
            return None
        return cls(**data)

    def save_to(self, request: HttpRequest) -> None:
        request.session[SESSION_KEY] = asdict(self)


def person_for(request: HttpRequest, member: Member | None) -> VolunteerInfo | None:
    """Who the sign-up buttons act for: the saved step first, then a member's own details, else nobody."""
    saved = VolunteerInfo.from_session(request)
    if saved is not None:
        return saved
    if member is not None:
        return VolunteerInfo.for_member(member)
    return None


def sign_up(shift: VolunteerShift, person: VolunteerInfo, *, member: Member | None) -> ShiftSignup:
    """Put ``person`` on ``shift``.

    The shift row is locked for the check so two last-spot sign-ups cannot both land.

    Raises:
        AlreadySignedUp: the email is on the shift already.
        ShiftFull: every spot is taken.
    """
    email = person.email.strip().lower()
    with transaction.atomic():
        locked = VolunteerShift.objects.select_for_update().get(pk=shift.pk)
        if locked.signups.filter(email=email).exists():
            raise AlreadySignedUp(f"{email} is already on {locked.role}.")
        if locked.signups.count() >= locked.need:
            raise ShiftFull(f"{locked.role} is full.")
        return ShiftSignup.objects.create(
            shift=locked,
            member=member,
            name=person.name,
            email=email,
            phone=person.phone,
            has_olcc=person.has_olcc,
            note=person.note,
        )


def remove(shift: VolunteerShift, email: str) -> None:
    """Take ``email`` off ``shift``.

    Raises:
        NotSignedUp: the email is not on the shift.
    """
    deleted, _ = shift.signups.filter(email=email.strip().lower()).delete()
    if not deleted:
        raise NotSignedUp(f"{email} is not on {shift.role}.")


@dataclass(frozen=True)
class VolunteerBand:
    """The strip over the shifts: how many people, spots filled of the total, and the biggest gap."""

    people: int
    filled: int
    total: int
    most_needed: VolunteerShift | None

    @classmethod
    def of(cls, shifts: Sequence[VolunteerShift], people: int) -> VolunteerBand:
        """Sum ``shifts`` (from ``with_counts()``); most needed is the largest open gap, first in page order."""
        most_needed = max(shifts, key=lambda shift: shift.spots_open, default=None)
        if most_needed is not None and most_needed.spots_open == 0:
            most_needed = None
        return cls(
            people=people,
            filled=sum(shift.filled_count for shift in shifts),
            total=sum(shift.need for shift in shifts),
            most_needed=most_needed,
        )


def volunteer_people(hub: EventHub) -> int:
    """How many different people have signed up for anything on ``hub``, by email."""
    return ShiftSignup.objects.filter(shift__hub=hub).values("email").distinct().count()


def volunteer_band(hub: EventHub) -> VolunteerBand:
    """The band for ``hub`` in two queries (the shifts with their counts, and the people)."""
    return VolunteerBand.of(list(hub.shifts.with_counts()), volunteer_people(hub))


@dataclass(frozen=True)
class PhaseGroup:
    """The shifts of one phase, for the Pick Your Shifts section."""

    key: str
    label: str
    shifts: list[VolunteerShift]


def phase_groups(shifts: Sequence[VolunteerShift]) -> list[PhaseGroup]:
    """``shifts`` (already in page order) grouped by phase; a phase with no shifts is left out."""
    groups: list[PhaseGroup] = []
    for phase in ShiftPhase:
        members = [shift for shift in shifts if shift.phase == phase]
        if members:
            groups.append(PhaseGroup(key=phase.value, label=str(phase.label), shifts=members))
    return groups


def signed_up_shift_ids(hub: EventHub, email: str) -> set[int]:
    """The shifts on ``hub`` that ``email`` is on, for the Sign Me Up or Remove Me choice."""
    return set(
        ShiftSignup.objects.filter(shift__hub=hub, email=email.strip().lower()).values_list("shift_id", flat=True)
    )


@dataclass(frozen=True)
class RosterRow:
    """One person on the organisers' roster, with every shift they took."""

    name: str
    email: str
    phone: str
    has_olcc: bool
    note: str
    shifts: list[str]


def roster_rows(hub: EventHub) -> list[RosterRow]:
    """Everyone signed up on ``hub``, one row per email with their shifts in page order, by name.

    A person's name, phone, permit and note are read from their newest sign-up, so what they
    last typed on Your Info is what the organisers see.
    """
    by_email: dict[str, RosterRow] = {}
    signups = ShiftSignup.objects.filter(shift__hub=hub).select_related("shift").order_by("created_at", "pk")
    for signup in signups:
        earlier = by_email.get(signup.email)
        shifts = earlier.shifts if earlier is not None else []
        by_email[signup.email] = RosterRow(
            name=signup.name,
            email=signup.email,
            phone=signup.phone,
            has_olcc=signup.has_olcc,
            note=signup.note,
            shifts=shifts,
        )
        shifts.append(signup.shift.role)
    return sorted(by_email.values(), key=lambda row: (row.name.lower(), row.email))


def rate_key(request: HttpRequest) -> str:
    """The address a guest's sign-ups are counted under.

    The same reading as ``core.views._rate_limit_key`` (which stays private to the biometric
    endpoint): Cloudflare's ``CF-Connecting-IP`` first, then the rightmost ``X-Forwarded-For``
    entry, then ``REMOTE_ADDR``. The leftmost forwarded entry is whatever the client sent, so
    keying on it would let one caller rotate buckets.
    """
    connecting = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
    if connecting:
        return connecting[:_RATE_KEY_MAX_CHARS]
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.rsplit(",", 1)[-1].strip()[:_RATE_KEY_MAX_CHARS]
    return request.META.get("REMOTE_ADDR", "")


def roster_emails(rows: Sequence[RosterRow]) -> str:
    """The roster's addresses as one comma separated line, for Copy emails."""
    return ", ".join(row.email for row in rows)


# ---- Wine wall (part 3) ----


# Guests' bottles are counted under their own scope, so a collector logging a case of ten
# never spends the shift sign-up budget (the caps are the sign-up sheet's).
RATE_SCOPE_BOTTLE = "hq_bottle"


class NotYourBottle(Exception):
    """Only whoever logged a bottle (by member, or by the email it was logged under) or an organiser may take it off the wall."""


@dataclass(frozen=True)
class KindCount:
    """One chip in the band's row: how many bottles of a kind are on the wall."""

    key: str
    label: str
    bottles: int


@dataclass(frozen=True)
class WineBand:
    """The strip over the wall: bottles of the goal, the value on the wall, the supporters and the kinds."""

    bottles: int
    goal: int
    value_cents: int
    supporters: int
    by_kind: list[KindCount]

    @property
    def percent(self) -> int:
        """How full the meter is, 0 to 100; with no goal it reads empty."""
        if self.goal == 0:
            return 0
        return min(100, round(self.bottles * 100 / self.goal))

    @property
    def value_display(self) -> str:
        return format_money(self.value_cents)


def bottles_goal(hub: EventHub) -> int:
    """The target of the hub's first ``bottles`` goal in band order, or 0 while it has none."""
    target = hub.goals.filter(kind=HubGoal.Kind.BOTTLES).values_list("target", flat=True).first()
    return 0 if target is None else target


def wine_supporters(hub: EventHub) -> int:
    """How many different givers are on the wall, by name whatever the capitalisation."""
    return hub.bottles.annotate(giver=Lower("giver_name")).values("giver").distinct().count()


def wine_band(hub: EventHub) -> WineBand:
    """The band for ``hub`` in three queries: the bottles by kind with their value, the supporters, the goal."""
    by_kind = {
        row["kind"]: (row["bottles"], row["value"])
        for row in hub.bottles.values("kind")
        .annotate(bottles=Sum("qty"), value=Sum(F("value_cents") * F("qty"), output_field=IntegerField()))
        .order_by()
    }
    return WineBand(
        bottles=sum(bottles for bottles, _ in by_kind.values()),
        goal=bottles_goal(hub),
        value_cents=sum(value for _, value in by_kind.values()),
        supporters=wine_supporters(hub),
        by_kind=[
            KindCount(key=kind.value, label=str(kind.label), bottles=by_kind[kind.value][0])
            for kind in WineKind
            if kind.value in by_kind
        ],
    )


@dataclass(frozen=True)
class WineryRow:
    """One tile in the Wineries, Cideries and Breweries grid."""

    name: str
    bottles: int
    logo: WineryLogo | None


def wineries(hub: EventHub) -> list[WineryRow]:
    """The wineries on the wall with their bottle totals and logos, alphabetically, in two queries.

    Bottles group by the winery as typed, lowercased; the tile shows the spelling of the
    first bottle logged under it. A logo joins by the same lowercased name, so "Abacela" and
    "abacela" share one.
    """
    names: dict[str, str] = {}
    totals: dict[str, int] = {}
    for winery, qty in hub.bottles.order_by("created_at", "pk").values_list("winery", "qty"):
        key = winery.lower()
        names.setdefault(key, winery)
        totals[key] = totals.get(key, 0) + qty
    logos = {logo.name.lower(): logo for logo in hub.winery_logos.all()}
    return [WineryRow(name=names[key], bottles=totals[key], logo=logos.get(key)) for key in sorted(totals)]


@dataclass(frozen=True)
class DonorRow:
    """One giver on the organisers' Wine Donors to Thank table, across every bottle they gave."""

    name: str
    giver_type: str
    email: str
    listing: str
    bottles: int
    value_cents: int
    logged_by: list[str]
    notes: list[str]

    @property
    def value_display(self) -> str:
        return format_money(self.value_cents)


def donor_rows(hub: EventHub) -> list[DonorRow]:
    """Every giver on ``hub``'s wall, one row per name (whatever the capitalisation), by name.

    Type and listing are read from the giver's newest bottle, as is the email unless the
    newest left it blank, so what a collector last typed is what the organisers see. Logged
    by lists everyone who logged one of their bottles, a guest as "Guest"; notes are every
    bottle's note, oldest first, since the wall itself keeps them to the logger and organisers.
    """
    by_key: dict[str, DonorRow] = {}
    for bottle in hub.bottles.order_by("created_at", "pk"):
        key = bottle.giver_name.strip().lower()
        earlier = by_key.get(key)
        logged_by = earlier.logged_by if earlier is not None else []
        logger = bottle.logged_by_name or "Guest"
        if logger not in logged_by:
            logged_by.append(logger)
        notes = earlier.notes if earlier is not None else []
        if bottle.note:
            notes.append(bottle.note)
        by_key[key] = DonorRow(
            name=bottle.giver_name,
            giver_type=bottle.get_giver_type_display(),
            email=bottle.giver_email or (earlier.email if earlier is not None else ""),
            listing=bottle.get_listing_display(),
            bottles=(earlier.bottles if earlier is not None else 0) + bottle.qty,
            value_cents=(earlier.value_cents if earlier is not None else 0) + bottle.value_total_cents,
            logged_by=logged_by,
            notes=notes,
        )
    return sorted(by_key.values(), key=lambda row: (row.name.lower(), row.email))


def donor_emails(rows: Sequence[DonorRow]) -> str:
    """The donors' addresses as one comma separated line, each once, for Copy donor emails."""
    return ", ".join(dict.fromkeys(row.email for row in rows if row.email))


def log_bottle(
    hub: EventHub,
    fields: Mapping[str, Any],
    *,
    member: Member | None,
    logger_name: str,
    logger_email: str,
) -> WineBottle:
    """Put a bottle on ``hub``'s wall from the form's cleaned fields, stamped with who logged it.

    ``logger_email`` is the identity a guest keeps (the Your Info address), so they can delete
    their own bottle later; a member is also recorded as ``logged_by``.
    """
    return WineBottle.objects.create(
        hub=hub, logged_by=member, logged_by_name=logger_name, logged_by_email=logger_email, **fields
    )


def delete_bottle(bottle: WineBottle, *, member: Member | None, email: str, is_organizer: bool) -> None:
    """Take ``bottle`` off the wall, with its photo (and the winery's logo when it was the last bottle).

    Raises:
        NotYourBottle: neither ``member`` nor ``email`` logged it, and the caller is not an organiser.
    """
    if not bottle.can_be_deleted_by(member, email=email, is_organizer=is_organizer):
        raise NotYourBottle(f"Only the organisers or whoever logged {bottle.name} can delete it.")
    bottle.delete()


def set_winery_logo(hub: EventHub, name: str, logo: UploadedFile) -> WineryLogo:
    """Store ``logo`` for ``name`` on ``hub``, replacing the one under that name whatever its capitalisation."""
    existing = hub.winery_logos.filter(name__iexact=name).first()
    if existing is None:
        return WineryLogo.objects.create(hub=hub, name=name, logo=logo)
    existing.name = name
    existing.logo = logo
    existing.save()
    return existing

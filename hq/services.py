"""What the HQ pages compute across models: the goal band's rows, the volunteer sheet and the wine wall.

Each live goal kind is counted here, so the page and the model never learn where a figure
comes from. The volunteer half holds the Your Info step (:class:`VolunteerInfo`, kept in the
session), the two writes (:func:`sign_up`, :func:`remove`) with their named refusals, the
band over the shifts (:func:`volunteer_band`), the organisers' roster (:func:`roster_rows`)
and the rate key a guest's sign-ups are counted under. The wine wall half holds the band
over the wall (:func:`wine_band`), the wineries grid (:func:`wineries`), the organisers'
donors table (:func:`donor_rows`) and the three writes (:func:`log_bottle`,
:func:`delete_bottle`, :func:`set_winery_logo`). The Marketing Tracker half holds its totals
(:func:`marketing_totals`, the one source of "Businesses with flyers") and its six tiles. The
Sponsorship Tracker half holds the band (:func:`sponsor_band`, the one source of the sponsor money
received and committed), the Follow up list (:func:`follow_ups`), the pipeline's filters and the
three writes (:func:`add_sponsor`, :func:`update_sponsor`, :func:`log_contact`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models import Count, F, IntegerField, Q, QuerySet, Sum
from django.db.models.functions import Coalesce, Lower
from django.http import HttpRequest

from hq.models import (
    EventHub,
    HubGoal,
    MarketingAction,
    MarketingKind,
    ShiftPhase,
    ShiftSignup,
    Sponsor,
    SponsorLogEntry,
    SponsorStatus,
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


# ---- Marketing Tracker (Track D, part 1) ----

# The key of the Total actions tile in :func:`marketing_totals`, beside each kind's value.
MARKETING_ALL = "all"
# The key of the reach summed over every action.
MARKETING_REACH = "reach"

# Lee's six tiles, in her order: the key :func:`marketing_totals` counts under, the heading and the
# line under the figure. The last is every action; its line carries the reach so far.
MARKETING_TILES: tuple[tuple[str, str, str], ...] = (
    (MarketingKind.FLYER, "Businesses with flyers", "Places the flyer is posted"),
    (MarketingKind.REEL, "Instagram videos", "Reels and video posts"),
    (MarketingKind.POST, "Instagram posts", "Posts and stories"),
    (MarketingKind.EMAIL, "Emails sent", "Member and community emails"),
    (MarketingKind.LISTING, "Event listings", "Eventbrite, calendars, the portal"),
    (MARKETING_ALL, "Total actions", "Reach so far: {reach}"),
)


@dataclass(frozen=True)
class MarketingTile:
    """One tile over the marketing log: the heading, the figure and the line under it."""

    key: str
    heading: str
    figure: int
    helper: str


def marketing_totals(hub: EventHub) -> dict[str, int]:
    """The marketing log counted in one aggregate query: one count per kind, ``"all"`` and the summed ``"reach"``.

    ``marketing_totals(hub)["flyer"]`` is the one source of "Businesses with flyers": the ledger
    and the goals read it here rather than keeping a copy.
    """
    counts = {kind.value: Count("pk", filter=Q(kind=kind.value)) for kind in MarketingKind}
    totals: dict[str, int] = MarketingAction.objects.filter(hub=hub).aggregate(
        **counts,
        **{MARKETING_ALL: Count("pk"), MARKETING_REACH: Coalesce(Sum("reach"), 0)},
    )
    return totals


def marketing_tiles(totals: Mapping[str, int]) -> list[MarketingTile]:
    """Lee's six tiles from :func:`marketing_totals`, in her order."""
    reach = f"{totals[MARKETING_REACH]:,}"
    return [
        MarketingTile(key=key, heading=heading, figure=totals[key], helper=helper.format(reach=reach))
        for key, heading, helper in MARKETING_TILES
    ]


def log_marketing_action(hub: EventHub, fields: Mapping[str, Any], *, member: Member | None) -> MarketingAction:
    """Add an action to ``hub``'s marketing log from the form's cleaned fields, stamped with who logged it."""
    return MarketingAction.objects.create(hub=hub, logged_by=member, **fields)


# ---- Sponsorship Tracker (Track D, part 2) ----

# The meter's target when the hub has no Raised goal and no other money goal, in whole dollars (Lee's default).
DEFAULT_SPONSOR_GOAL = 10_000
# The label of the goal the meter reads first.
RAISED_GOAL_LABEL = "Raised"
# How many rows the Follow up list shows.
FOLLOW_UP_LIMIT = 8

# The pipeline's filters, in Lee's order: the query value and the button's words. ``donor2025`` is every
# sponsor with last year's gift filled in; ``all`` shows everyone.
SPONSOR_FILTER_ALL = "all"
SPONSOR_FILTER_DONORS = "donor2025"
SPONSOR_FILTERS: tuple[tuple[str, str], ...] = (
    (SPONSOR_FILTER_ALL, "All"),
    (SponsorStatus.PROSPECT, "Prospects"),
    (SponsorStatus.ASKED, "Asked"),
    (SponsorStatus.COMMITTED, "Committed"),
    (SponsorStatus.RECEIVED, "Received"),
    (SponsorStatus.DECLINED, "Declined"),
    (SPONSOR_FILTER_DONORS, "2025 donors"),
)


@dataclass(frozen=True)
class SponsorBand:
    """The strip over the tracker: the money received, committed and asked for, the prospects, and the goal.

    ``received_cents`` and ``committed_cents`` are the one source of the sponsor money: the goals' Raised and
    the ledger read them here. A declined sponsor counts nowhere. ``goal`` is whole dollars, like every
    ``HubGoal`` money figure.
    """

    received_cents: int
    committed_cents: int
    asked_cents: int
    open_asks: int
    prospects: int
    goal: int

    @property
    def secured_cents(self) -> int:
        """Received plus committed: what is in hand or promised."""
        return self.received_cents + self.committed_cents

    @property
    def goal_cents(self) -> int:
        return self.goal * 100

    @property
    def received_meter_cents(self) -> int:
        """The solid segment's value, capped at the goal."""
        return min(self.received_cents, self.goal_cents)

    @property
    def secured_meter_cents(self) -> int:
        """Where the faded committed segment ends (received plus committed), capped at the goal."""
        return min(self.secured_cents, self.goal_cents)

    @property
    def received_display(self) -> str:
        return format_money(self.received_cents)

    @property
    def committed_display(self) -> str:
        return format_money(self.committed_cents)

    @property
    def asked_display(self) -> str:
        return format_money(self.asked_cents)

    @property
    def secured_display(self) -> str:
        return format_money(self.secured_cents)

    @property
    def goal_display(self) -> str:
        return f"${self.goal:,}"


def sponsor_goal(hub: EventHub) -> int:
    """The meter's target in whole dollars: the hub's first Raised goal, else its first typed in money goal,
    else :data:`DEFAULT_SPONSOR_GOAL`. A goal with no target is passed over. One query."""
    candidates = list(
        hub.goals.filter(
            Q(label__iexact=RAISED_GOAL_LABEL) | Q(kind=HubGoal.Kind.MANUAL, is_money=True), target__gt=0
        ).values_list("label", "target")
    )
    raised = [target for label, target in candidates if label.lower() == RAISED_GOAL_LABEL.lower()]
    if raised:
        return raised[0]
    if candidates:
        return candidates[0][1]
    return DEFAULT_SPONSOR_GOAL


def sponsor_band(hub: EventHub) -> SponsorBand:
    """The band for ``hub`` in two queries: one aggregate over its sponsors, and the goal."""

    def _sum(field: str, status: str) -> Coalesce:
        return Coalesce(Sum(field, filter=Q(status=status)), 0)

    totals = hub.sponsors.aggregate(
        received=_sum("amount_cents", SponsorStatus.RECEIVED),
        committed=_sum("amount_cents", SponsorStatus.COMMITTED),
        asked=_sum("ask_cents", SponsorStatus.ASKED),
        open_asks=Count("pk", filter=Q(status=SponsorStatus.ASKED)),
        prospects=Count("pk", filter=Q(status=SponsorStatus.PROSPECT)),
    )
    return SponsorBand(
        received_cents=totals["received"],
        committed_cents=totals["committed"],
        asked_cents=totals["asked"],
        open_asks=totals["open_asks"],
        prospects=totals["prospects"],
        goal=sponsor_goal(hub),
    )


@dataclass(frozen=True)
class FollowUp:
    """One row on the Follow up list: the sponsor, and "Overdue " or "Today " when it is due."""

    sponsor: Sponsor
    prefix: str

    @property
    def is_due(self) -> bool:
        """Due on or before today: the row is highlighted."""
        return bool(self.prefix)


@dataclass(frozen=True)
class FollowUps:
    """The Follow up list: how many sponsors have an open follow-up date, and the first few, soonest first."""

    count: int
    rows: list[FollowUp]


def follow_ups(hub: EventHub, *, today: date) -> FollowUps:
    """Every sponsor on ``hub`` with a follow-up date whose ask is still open, soonest first, in one query.

    ``today`` is the Portland date (``timezone.localdate()``): a date before it is overdue, on it is today.
    ``rows`` is the first :data:`FOLLOW_UP_LIMIT`; ``count`` is them all.
    """
    open_rows = list(
        hub.sponsors.filter(follow_up_by__isnull=False)
        .exclude(status__in=[SponsorStatus.RECEIVED, SponsorStatus.DECLINED])
        .order_by("follow_up_by", Lower("organization"), "pk")
    )
    rows = []
    for sponsor in open_rows[:FOLLOW_UP_LIMIT]:
        due: date = sponsor.follow_up_by  # type: ignore[assignment]  # the filter above excludes null
        prefix = "Overdue " if due < today else "Today " if due == today else ""
        rows.append(FollowUp(sponsor=sponsor, prefix=prefix))
    return FollowUps(count=len(open_rows), rows=rows)


def sponsor_filter(value: str) -> str:
    """``value`` when it names one of :data:`SPONSOR_FILTERS`, else ``all``: a stale or typed address shows everyone."""
    return value if value in dict(SPONSOR_FILTERS) else SPONSOR_FILTER_ALL


def filtered_sponsors(hub: EventHub, show: str) -> QuerySet[Sponsor]:
    """``hub``'s sponsors in pipeline order under the filter ``show`` (a value from :data:`SPONSOR_FILTERS`)."""
    sponsors = hub.sponsors.all()
    if show == SPONSOR_FILTER_ALL:
        return sponsors
    if show == SPONSOR_FILTER_DONORS:
        return sponsors.exclude(history="")
    return sponsors.filter(status=show)


# The first line in every new sponsor's contact log.
ADDED_LOG_TEXT = "Added to tracker"


def add_sponsor(hub: EventHub, fields: Mapping[str, Any], *, member: Member | None) -> Sponsor:
    """Put a sponsor on ``hub``'s tracker from the form's fields; its log starts with "Added to tracker" by the asker."""
    with transaction.atomic():
        sponsor = Sponsor.objects.create(hub=hub, created_by=member, **fields)
        SponsorLogEntry.objects.create(sponsor=sponsor, text=ADDED_LOG_TEXT, by=sponsor.owner)
    return sponsor


def update_sponsor(sponsor: Sponsor, fields: Mapping[str, Any]) -> Sponsor:
    """Save the inline edit's fields on ``sponsor``; a new status adds "Status changed to {label}" to its log."""
    old_status = sponsor.status
    for name, value in fields.items():
        setattr(sponsor, name, value)
    with transaction.atomic():
        sponsor.save()
        if sponsor.status != old_status:
            label = SponsorStatus(sponsor.status).label
            SponsorLogEntry.objects.create(sponsor=sponsor, text=f"Status changed to {label}")
    return sponsor


def log_contact(sponsor: Sponsor, *, text: str, by: str) -> SponsorLogEntry:
    """Add one line to ``sponsor``'s contact log, dated Portland today."""
    return SponsorLogEntry.objects.create(sponsor=sponsor, text=text, by=by)

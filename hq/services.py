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
three writes (:func:`add_sponsor`, :func:`update_sponsor`, :func:`log_contact`). The Vendor Tables half holds
the band (:func:`vendor_band`, the one source of the confirmed tables the Member tables goal and the ledger read),
the list's filters and the three writes (:func:`add_vendor_table`, :func:`update_vendor_table`,
:func:`set_vendor_floor`). The flyer half holds the page's content (:func:`flyer_page`), the link members share
(:func:`share_link`, :func:`facebook_share_url`) and the three writes (:func:`set_flyer`, :func:`remove_flyer`,
:func:`save_flyer_links`). The ledger half (D6a) holds the budget's figures (:func:`ledger_band`, the one source of
the approval used and left, the sponsored value, each line's totals and the bar's cost), Lee's board summary
(:func:`board_summary`), the ledger's filters and its writes (:func:`save_approval`, the budget lines',
:func:`add_ledger_entry`, :func:`attach_receipt`). The money in (D6b) holds the bar (:func:`bar_totals`, the quick and batch
writes and Undo last), the wine wall tickets (:func:`ticket_totals`), the event cash Raised counts (:func:`event_cash`)
and the donors thanked (:func:`donor_thanks`, :func:`set_donor_thanked`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from urllib.parse import quote

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.db.models import (
    Count,
    DecimalField,
    F,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    QuerySet,
    RestrictedError,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce, Lower, Trim
from django.http import HttpRequest
from django.utils import timezone

from hq.holiday_bizarre import CHECKIN_THANKS, GOALS
from hq.models import (
    PRICED_DRINKS,
    RECEIPT_KINDS,
    BarDrink,
    BarSale,
    BarSaleSource,
    BudgetCategory,
    BudgetLine,
    EventHub,
    FlyerSlot,
    HubFlyer,
    HubGoal,
    LedgerEntry,
    LedgerKind,
    LedgerSettings,
    MarketingAction,
    MarketingKind,
    MarketReport,
    ShiftPhase,
    ShiftSignup,
    Sponsor,
    SponsorLogEntry,
    SponsorStatus,
    VendorFloor,
    VendorTable,
    VendorTableStatus,
    VolunteerHours,
    VolunteerShift,
    WineBottle,
    WineDonorThanks,
    WineKind,
    WineryLogo,
    donor_key,
    format_hours,
    format_money,
)
from membership.models import CommunityEvent, Guild, GuildMembership, Member


@dataclass(frozen=True)
class GoalRow:
    """One chip in the goal band, ready to render."""

    label: str
    current: int
    target: int
    is_money: bool

    @property
    def has_target(self) -> bool:
        """Whether the chip measures against a target; with none it is a plain count, no "of 0" and no meter."""
        return self.target > 0

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
    HubGoal.Kind.MEMBER_TABLES: lambda hub: vendor_band(hub).confirmed,
    # Whole dollars, as every money goal; cents left over never round up to a dollar not yet raised.
    HubGoal.Kind.EVENT_CASH: lambda hub: event_cash(hub).total_cents // 100,
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


@dataclass
class GoalSeed:
    """What :func:`seed_goals` did: the labels of the goals it made, in order, how many the hub already held, and the
    label of a typed in Raised goal it turned into the counted event cash (blank when none)."""

    added: list[str] = field(default_factory=list)
    kept: int = 0
    adopted: str = ""


def _adopt_raised(hub: EventHub, kinds: set[str]) -> str:
    """Turn the hub's typed in Raised goal into the counted event cash, in place, so Lee's Raised is never doubled.

    Only on a hub with no event cash goal: the first manual goal labelled Raised (any case) in band order becomes
    :attr:`HubGoal.Kind.EVENT_CASH`, keeping its pk, label, target and place, and is marked money. Returns its label, or
    "" when there was nothing to adopt.
    """
    if HubGoal.Kind.EVENT_CASH in kinds:
        return ""
    goal = hub.goals.filter(kind=HubGoal.Kind.MANUAL, label__iexact=RAISED_GOAL_LABEL).first()
    if goal is None:
        return ""
    goal.kind = HubGoal.Kind.EVENT_CASH
    goal.is_money = True
    goal.save(update_fields=["kind", "is_money"])
    return goal.label


def seed_goals(hub: EventHub) -> GoalSeed:
    """Lee's goals (:data:`hq.holiday_bizarre.GOALS`) on ``hub``: the whole set on a hub with none, else each
    counted goal whose kind the hub lacks, at the end of the hub's goal order.

    A counted goal is keyed by its kind, so a renamed one is still found and a rerun adds nothing twice; one an
    admin deleted comes back on the next run, which is the accepted price of reaching hubs seeded before it
    existed. A typed in goal has no key, so it is made only on a hub with no goals at all. Before that, a hub's typed
    in Raised goal is adopted as the event cash goal (:func:`_adopt_raised`), so the counted Raised is not added beside
    it. Callers that can race (Update the doors) hold a row lock on the hub.
    """
    held = list(hub.goals.values_list("kind", "sort_order"))
    adopted = ""
    if held:
        kinds = {kind for kind, _order in held}
        adopted = _adopt_raised(hub, kinds)
        if adopted:
            kinds.add(HubGoal.Kind.EVENT_CASH)
        missing = [spec for spec in GOALS if spec.kind != HubGoal.Kind.MANUAL and spec.kind not in kinds]
        start = max(order for _kind, order in held) + 1
    else:
        missing, start = list(GOALS), 0
    HubGoal.objects.bulk_create(
        HubGoal(
            hub=hub,
            label=spec.label,
            kind=spec.kind,
            target=spec.target,
            is_money=spec.is_money,
            sort_order=start + offset,
        )
        for offset, spec in enumerate(missing)
    )
    return GoalSeed(added=[spec.label for spec in missing], kept=len(held), adopted=adopted)


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
    # The hours the member with this primary email logged on the Market Check-in (D4); 0 for anyone else.
    hours: Decimal = Decimal(0)

    @property
    def hours_display(self) -> str:
        """The hours logged, or a dash for none."""
        return format_hours(self.hours) if self.hours else "\u2013"


def roster_rows(hub: EventHub) -> list[RosterRow]:
    """Everyone signed up on ``hub``, one row per email with their shifts in page order, by name, in two queries.

    A person's name, phone, permit and note are read from their newest sign-up, so what they
    last typed on Your Info is what the organisers see. Hours logged is the Market Check-in's figure for the
    member whose email (``Member.primary_email``'s rule, :func:`hours_by_email`) is the sign-up's.
    """
    hours = hours_by_email(hub)
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
            hours=hours.get(signup.email, Decimal(0)),
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

    @property
    def goal_line(self) -> str:
        """Lee's line under Bottles in: how many to go, the goal reached (and how many extra), or no goal yet."""
        if self.goal == 0:
            return "No bottles goal yet"
        if self.bottles >= self.goal:
            extra = self.bottles - self.goal
            return f"Goal reached, {extra} extra" if extra else "Goal reached"
        return f"{self.goal - self.bottles} to go"

    @property
    def half_goal(self) -> int:
        """The meter's middle label, half the goal rounded half up (Lee's ``Math.round(goal / 2)``)."""
        return (self.goal + 1) // 2

    @property
    def average_line(self) -> str:
        """Lee's line under Donated value: the average per bottle to the cent, rounded half up."""
        if not self.bottles:
            return "Retail value of bottles"
        average = (2 * self.value_cents + self.bottles) // (2 * self.bottles)
        return f"Avg {format_money(average)} per bottle"


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
    """One giver on the organisers' Wine Donors to Thank table, across every bottle they gave.

    ``key`` is how the giver is known (:func:`hq.models.donor_key`), ``thanked`` whether an organiser marked them
    thanked, and ``under`` how many of their bottles are worth less than one wine wall ticket (``ticket_cents``).
    """

    key: str
    name: str
    giver_type: str
    email: str
    listing: str
    bottles: int
    value_cents: int
    logged_by: list[str]
    notes: list[str]
    thanked: bool = False
    under: int = 0
    ticket_cents: int = 0

    @property
    def value_display(self) -> str:
        return format_money(self.value_cents)

    @property
    def under_line(self) -> str:
        """Lee's flag for bottles worth less than a ticket: "Under $25" when every bottle is, "2 under $25" when some
        are, blank when none."""
        if not self.under:
            return ""
        price = format_money(self.ticket_cents)
        return f"Under {price}" if self.under == self.bottles else f"{self.under} under {price}"


def ticket_price_cents(hub: EventHub) -> int:
    """One wine wall ticket on ``hub``, in cents: the ledger settings' price (Lee's $25 until an organiser sets it)."""
    return ledger_settings(hub).ticket_cents


def thanked_keys(hub: EventHub) -> set[str]:
    """The donors marked thanked on ``hub``, by :func:`hq.models.donor_key`. One query."""
    return set(hub.wine_thanks.values_list("giver_key", flat=True))


def donor_rows(hub: EventHub) -> list[DonorRow]:
    """Every giver on ``hub``'s wall, one row per name (whatever the capitalisation), by name, in three queries.

    Type and listing are read from the giver's newest bottle, as is the email unless the
    newest left it blank, so what a collector last typed is what the organisers see. Logged
    by lists everyone who logged one of their bottles, a guest as "Guest"; notes are every
    bottle's note, oldest first, since the wall itself keeps them to the logger and organisers.
    Thanked reads the donor's :class:`WineDonorThanks`, and Under counts the bottles worth less than one ticket.
    """
    thanked = thanked_keys(hub)
    ticket = ticket_price_cents(hub)
    by_key: dict[str, DonorRow] = {}
    for bottle in hub.bottles.order_by("created_at", "pk"):
        key = donor_key(bottle.giver_name)
        earlier = by_key.get(key)
        logged_by = earlier.logged_by if earlier is not None else []
        logger = bottle.logged_by_name or "Guest"
        if logger not in logged_by:
            logged_by.append(logger)
        notes = earlier.notes if earlier is not None else []
        if bottle.note:
            notes.append(bottle.note)
        under = bottle.qty if bottle.value_cents < ticket else 0
        by_key[key] = DonorRow(
            key=key,
            name=bottle.giver_name,
            giver_type=bottle.get_giver_type_display(),
            email=bottle.giver_email or (earlier.email if earlier is not None else ""),
            listing=bottle.get_listing_display(),
            bottles=(earlier.bottles if earlier is not None else 0) + bottle.qty,
            value_cents=(earlier.value_cents if earlier is not None else 0) + bottle.value_total_cents,
            logged_by=logged_by,
            notes=notes,
            thanked=key in thanked,
            under=(earlier.under if earlier is not None else 0) + under,
            ticket_cents=ticket,
        )
    return sorted(by_key.values(), key=lambda row: (row.name.lower(), row.email))


def donor_row(hub: EventHub, key: str) -> DonorRow | None:
    """The one donor known by ``key`` on ``hub``'s table, or None when no bottle names them any more."""
    return next((row for row in donor_rows(hub) if row.key == key), None)


@dataclass(frozen=True)
class DonorThanks:
    """How many of the wall's donors are marked thanked: Lee's "N of M donors thanked"."""

    thanked: int
    donors: int

    @property
    def line(self) -> str:
        return f"{self.thanked} of {self.donors} donor{'' if self.donors == 1 else 's'} thanked"


def donor_thanks(hub: EventHub) -> DonorThanks:
    """The ledger's count of donors thanked, in two queries. A thank-you kept for a giver whose bottles were all taken
    off the wall counts nowhere."""
    keys = hub.bottles.annotate(key=Lower(Trim("giver_name"))).values("key").distinct()
    donors = keys.count()
    thanked = hub.wine_thanks.filter(giver_key__in=Subquery(keys.values("key"))).count()
    return DonorThanks(thanked=thanked, donors=donors)


def set_donor_thanked(hub: EventHub, name: str, *, thanked: bool) -> None:
    """Mark the donor named ``name`` thanked on ``hub`` (any capitalisation), or take the mark off. Saying the same
    twice changes nothing."""
    key = donor_key(name)
    if thanked:
        WineDonorThanks.objects.get_or_create(hub=hub, giver_key=key)
    else:
        hub.wine_thanks.filter(giver_key=key).delete()


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
    """The meter's target in whole dollars: the hub's first event cash goal (D6b's counted Raised), else its first
    goal labelled Raised, else its first typed in money goal, else :data:`DEFAULT_SPONSOR_GOAL`. A goal with no target
    is passed over. One query."""
    candidates = list(
        hub.goals.filter(
            Q(kind=HubGoal.Kind.EVENT_CASH)
            | Q(label__iexact=RAISED_GOAL_LABEL)
            | Q(kind=HubGoal.Kind.MANUAL, is_money=True),
            target__gt=0,
        ).values_list("kind", "label", "target")
    )
    for chosen in (
        [target for kind, _label, target in candidates if kind == HubGoal.Kind.EVENT_CASH],
        [target for _kind, label, target in candidates if label.lower() == RAISED_GOAL_LABEL.lower()],
        [target for _kind, _label, target in candidates],
    ):
        if chosen:
            return chosen[0]
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


def filtered_sponsors(hub: EventHub, show: str, *, keep_pk: int | None = None) -> QuerySet[Sponsor]:
    """``hub``'s sponsors in pipeline order under the filter ``show`` (a value from :data:`SPONSOR_FILTERS`).

    ``keep_pk`` is a sponsor that stays in the list whatever the filter: the row just saved or refused, so a
    status change or a form with errors does not vanish from the group it was edited in.
    """
    sponsors = hub.sponsors.all()
    if show == SPONSOR_FILTER_ALL:
        return sponsors
    match = ~Q(history="") if show == SPONSOR_FILTER_DONORS else Q(status=show)
    if keep_pk is not None:
        match |= Q(pk=keep_pk)
    return sponsors.filter(match)


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


# ---- Vendor Tables (Track D, part 3) ----

# The longest capacity the space map form takes: far past any floor, well inside a database integer.
VENDOR_CAPACITY_MAX = 1_000

# The list's filters, in Lee's order: the query value and the button's words; ``all`` shows every request.
VENDOR_FILTER_ALL = "all"
VENDOR_FILTERS: tuple[tuple[str, str], ...] = (
    (VENDOR_FILTER_ALL, "All"),
    *((status.value, str(status.label)) for status in VendorTableStatus),
)

# Who the Requested tile says the requests wait on when the card names nobody.
VENDOR_TEAM_FALLBACK = "the vendor team"


@dataclass(frozen=True)
class VendorBand:
    """The strip over the table requests: confirmed, requested and waitlist counts, and the space map.

    ``confirmed`` is the one source of the confirmed vendor tables: the Member tables goal and the ledger read
    it here. ``capacity`` is None while the space map is not ready; space left is the capacity less the
    confirmed tables and goes below zero when more are confirmed than fit.
    """

    confirmed: int
    requested: int
    waitlist: int
    cancelled: int
    capacity: int | None
    map_note: str

    @property
    def total(self) -> int:
        """Every request on the list, whatever its status."""
        return self.confirmed + self.requested + self.waitlist + self.cancelled

    @property
    def space_left(self) -> int | None:
        """Tables still free on the map (negative when over), or None while it is not mapped."""
        return None if self.capacity is None else self.capacity - self.confirmed

    @property
    def is_full(self) -> bool:
        """Whether a new request goes to the waitlist: the map is in and the confirmed tables fill it."""
        return self.capacity is not None and self.confirmed >= self.capacity

    @property
    def space_left_display(self) -> str:
        """The Space left figure: the number, or a dash while the map is not ready."""
        return "\u2013" if self.space_left is None else str(self.space_left)

    @property
    def space_left_line(self) -> str:
        """The line under Space left, in Lee's words."""
        if self.capacity is None:
            return "Waiting on the space map"
        if self.is_full:
            return "Full: new requests go to the waitlist"
        return f"of {self.capacity} tables on the map"

    @property
    def percent(self) -> int:
        """How much of the map the confirmed tables fill, 0 to 100; empty while not mapped."""
        if self.capacity is None:
            return 0
        if self.capacity == 0:
            return 100
        return min(100, round(self.confirmed * 100 / self.capacity))


def vendor_band(hub: EventHub) -> VendorBand:
    """The band for ``hub`` in two queries: one aggregate over its table requests, and the space map."""
    totals = hub.vendor_tables.aggregate(
        **{status: Count("pk", filter=Q(status=status)) for status in VendorTableStatus.values}
    )
    floor = VendorFloor.objects.filter(hub=hub).values("capacity", "map_note").first()
    return VendorBand(
        confirmed=totals[VendorTableStatus.CONFIRMED],
        requested=totals[VendorTableStatus.REQUESTED],
        waitlist=totals[VendorTableStatus.WAITLIST],
        cancelled=totals[VendorTableStatus.CANCELLED],
        capacity=floor["capacity"] if floor is not None else None,
        map_note=floor["map_note"] if floor is not None else "",
    )


def active_guilds() -> list[Guild]:
    """The guilds a table request may name, by name: every active guild, in one query."""
    return list(Guild.objects.filter(is_active=True).order_by("name"))


def vendor_filter(value: str) -> str:
    """``value`` when it names one of :data:`VENDOR_FILTERS`, else ``all``: a stale or typed address shows everyone."""
    return value if value in dict(VENDOR_FILTERS) else VENDOR_FILTER_ALL


def filtered_vendor_tables(hub: EventHub, show: str, *, keep_pk: int | None = None) -> QuerySet[VendorTable]:
    """``hub``'s table requests in list order under the filter ``show``, with each one's guild.

    ``keep_pk`` is a request kept in view whatever the filter (the one just added), so it never vanishes.
    """
    tables = hub.vendor_tables.select_related("guild")
    if show == VENDOR_FILTER_ALL:
        return tables
    return tables.filter(Q(status=show) | Q(pk=keep_pk)) if keep_pk is not None else tables.filter(status=show)


def add_vendor_table(hub: EventHub, fields: Mapping[str, Any], *, member: Member | None) -> VendorTable:
    """Put a table request on ``hub`` from the form's fields: requested, or waitlisted while the map is full."""
    status = VendorTableStatus.WAITLIST if vendor_band(hub).is_full else VendorTableStatus.REQUESTED
    return VendorTable.objects.create(hub=hub, created_by=member, status=status, **fields)


def update_vendor_table(table: VendorTable, fields: Mapping[str, Any]) -> VendorTable:
    """Save the row's edited fields on ``table``."""
    for name, value in fields.items():
        setattr(table, name, value)
    table.save()
    return table


def set_vendor_floor(hub: EventHub, *, capacity: int | None, map_note: str) -> VendorFloor:
    """Record how many tables the space map fits on ``hub`` (None clears it) and the mapper's note."""
    floor, _created = VendorFloor.objects.update_or_create(
        hub=hub, defaults={"capacity": capacity, "map_note": map_note}
    )
    return floor


# ---- Market Check-in (Track D, part 4) ----

# How many volunteers the thank-you board shows.
BOARD_LIMIT = 15


class NotYourHours(Exception):
    """Only the member who logged an hours entry, or an organiser, may delete it."""


@dataclass(frozen=True)
class CheckinBand:
    """The Market Check-in's figures: the hours and volunteers in the hero, and the reports' totals.

    These are the one source of the check-in figures: the ledger (D6) reads them here rather than keeping a copy.
    ``volunteers`` is every member with hours logged; ``vendors_with_sales`` every report with sales above zero.
    """

    hours: Decimal
    volunteers: int
    reports: int
    sales_cents: int
    items: int
    vendors_with_sales: int

    @property
    def hours_display(self) -> str:
        return format_hours(self.hours)

    @property
    def sales_display(self) -> str:
        return format_money(self.sales_cents)

    @property
    def average_sale_cents(self) -> int:
        """The sales per artist who sold anything, rounded to the cent; 0 while nobody has."""
        if self.vendors_with_sales == 0:
            return 0
        return round(self.sales_cents / self.vendors_with_sales)

    @property
    def average_sale_display(self) -> str:
        return format_money(self.average_sale_cents)


def checkin_band(hub: EventHub) -> CheckinBand:
    """The check-in figures for ``hub`` in two queries: one aggregate over the hours, one over the reports."""
    hours = hub.volunteer_hours.aggregate(
        total=Coalesce(Sum("hours"), Decimal(0), output_field=DecimalField()),
        people=Count("member", distinct=True),
    )
    reports = hub.market_reports.aggregate(
        count=Count("pk"),
        sales=Coalesce(Sum("sales_cents"), 0),
        items=Coalesce(Sum("items_sold"), 0),
        selling=Count("pk", filter=Q(sales_cents__gt=0)),
    )
    return CheckinBand(
        hours=hours["total"],
        volunteers=hours["people"],
        reports=reports["count"],
        sales_cents=reports["sales"],
        items=reports["items"],
        vendors_with_sales=reports["selling"],
    )


def checkin_thanks(hub: EventHub) -> str:
    """Lee's thank-you line in the hero, with the event's day ("on December 4") when the hub has an event."""
    if hub.event is None:
        return CHECKIN_THANKS.format(on_date="")
    day = timezone.localdate(hub.event.starts_at)
    return CHECKIN_THANKS.format(on_date=f" on {day:%B} {day.day}")


@dataclass(frozen=True)
class BoardRow:
    """One volunteer on the thank-you board: their place, who they are and their hours."""

    rank: int
    member_id: int
    name: str
    hours: Decimal

    @property
    def hours_display(self) -> str:
        return format_hours(self.hours)

    @property
    def meter_value(self) -> str:
        """The hours as a plain number for the bar's ``value``, never localised."""
        return str(self.hours)


@dataclass(frozen=True)
class ThankYouBoard:
    """The top volunteers by hours, and the most anyone has given (the bars' full length)."""

    rows: list[BoardRow]

    @property
    def top(self) -> str:
        """The leader's hours as a plain number for the bars' ``max``; 1 on an empty board."""
        return str(self.rows[0].hours) if self.rows else "1"


def thank_you_board(hub: EventHub, *, limit: int = BOARD_LIMIT) -> ThankYouBoard:
    """The ``limit`` members with the most hours on ``hub``, most first (ties by who logged first), in one query."""
    rows = (
        hub.volunteer_hours.values("member", "member__preferred_name", "member__full_legal_name")
        .annotate(total=Sum("hours"), first=Min("pk"))
        .order_by("-total", "first")[:limit]
    )
    return ThankYouBoard(
        rows=[
            BoardRow(
                rank=position,
                member_id=row["member"],
                name=row["member__preferred_name"] or row["member__full_legal_name"],
                hours=row["total"],
            )
            for position, row in enumerate(rows, start=1)
        ]
    )


def member_hours(hub: EventHub, member: Member) -> list[VolunteerHours]:
    """``member``'s own entries on ``hub``, newest date first."""
    return list(hub.volunteer_hours.filter(member=member))


def total_hours(entries: Sequence[VolunteerHours]) -> Decimal:
    """The hours in ``entries`` added up."""
    return sum((entry.hours for entry in entries), Decimal(0))


def log_hours(hub: EventHub, member: Member, fields: Mapping[str, Any]) -> VolunteerHours:
    """Add one hours entry for ``member`` on ``hub`` from the form's cleaned fields."""
    return VolunteerHours.objects.create(hub=hub, member=member, **fields)


def delete_hours(entry: VolunteerHours, *, member: Member, is_organizer: bool) -> None:
    """Take ``entry`` off the hours.

    Raises:
        NotYourHours: ``member`` did not log it and is not an organiser.
    """
    if not is_organizer and entry.member_id != member.pk:
        raise NotYourHours("Only the organisers or whoever logged these hours can delete them.")
    entry.delete()


def report_for(hub: EventHub, member: Member) -> MarketReport | None:
    """``member``'s report on ``hub`` with its guild, or None before they file one."""
    return hub.market_reports.select_related("guild").filter(member=member).first()


def save_report(hub: EventHub, member: Member, fields: Mapping[str, Any]) -> MarketReport:
    """File or update ``member``'s one report on ``hub`` from the form's fields (an upsert on hub and member)."""
    report, _created = MarketReport.objects.update_or_create(hub=hub, member=member, defaults=dict(fields))
    return report


def confirmed_table(hub: EventHub, member: Member) -> VendorTable | None:
    """The confirmed table request on ``hub`` whose email is ``member``'s primary email, with its guild.

    The address is ``Member.primary_email`` (the rule :func:`hours_by_email` keys on): for a signed in member whose
    user is already loaded, one query for the primary ``EmailAddress`` and one for the table. A blank address
    returns None before the table query, so it never matches a request left blank. The first confirmed request
    entered wins if two share the address.
    """
    email = member.primary_email.strip().lower()
    if not email:
        return None
    return (
        hub.vendor_tables.select_related("guild")
        .filter(status=VendorTableStatus.CONFIRMED, email=email)
        .order_by("pk")
        .first()
    )


def report_initial(member: Member, table: VendorTable | None) -> dict[str, Any]:
    """What a member's first report starts with: the confirmed table's name and guild when there is one, else
    their display name and their guild when they belong to exactly one active guild."""
    name = table.name if table is not None else member.display_name
    guild_id = table.guild_id if table is not None else None
    if guild_id is None:
        guilds = list(
            GuildMembership.objects.filter(member=member, guild__is_active=True).values_list("guild_id", flat=True)[:2]
        )
        guild_id = guilds[0] if len(guilds) == 1 else None
    return {"name": name, "guild": guild_id}


@dataclass(frozen=True)
class GuildSales:
    """One line of the organisers' sales by guild."""

    name: str
    reports: int
    sales_cents: int

    @property
    def sales_display(self) -> str:
        return format_money(self.sales_cents)


def sales_by_guild(hub: EventHub) -> list[GuildSales]:
    """The reports on ``hub`` grouped by guild (none is "Not in a guild"), most sales first, in one query."""
    rows = (
        hub.market_reports.values("guild__name")
        .annotate(reports=Count("pk"), sales=Coalesce(Sum("sales_cents"), 0))
        .order_by("-sales", "guild__name")
    )
    return [
        GuildSales(name=row["guild__name"] or "Not in a guild", reports=row["reports"], sales_cents=row["sales"])
        for row in rows
    ]


def hours_by_email(hub: EventHub) -> dict[str, Decimal]:
    """Each member's hours on ``hub`` keyed by their email as ``Member.primary_email`` reads it, lowercased, in one
    query.

    The rule is ``Member.primary_email``'s: the primary ``EmailAddress`` of a linked user, else ``User.email``, and
    the stored pre-signup address for a member with no user; it is the rule :func:`confirmed_table` matches on. The
    volunteer roster reads it to put "Hours logged" beside a sign-up whose email is that address. A member with no
    address under that rule is left out (a blank key would match nothing real).
    """
    from allauth.account.models import EmailAddress

    primary = EmailAddress.objects.filter(user_id=OuterRef("member__user_id"), primary=True).values("email")[:1]
    rows = (
        hub.volunteer_hours.annotate(primary=Subquery(primary))
        .values("member", "member__user_id", "member__user__email", "member___pre_signup_email", "primary")
        .annotate(total=Sum("hours"))
        .order_by()
    )
    totals: dict[str, Decimal] = {}
    for row in rows:
        if row["member__user_id"] is None:
            email = row["member___pre_signup_email"]
        else:
            email = row["primary"] or row["member__user__email"]
        key = (email or "").strip().lower()
        if key:
            totals[key] = totals.get(key, Decimal(0)) + row["total"]
    return totals


# ---- Event flyer and sharing (Track D, part 5) ----

# Facebook's share dialog: the link goes after ``u=``, percent-encoded. A plain link, no SDK.
FACEBOOK_SHARER = "https://www.facebook.com/sharer/sharer.php?u="


def flyer_page(hub: EventHub) -> HubFlyer:
    """``hub``'s flyer page content, or an unsaved one carrying the defaults (no flyer, no links, Lee's message)
    until an organiser first saves. Rendering never writes a row."""
    page = HubFlyer.objects.filter(hub=hub).first()
    return page if page is not None else HubFlyer(hub=hub)


def share_link(hub: EventHub) -> str:
    """The link members copy and share, absolute: the event's public page, or the HQ page with no event.

    It is also the Member portal link under Find the event.
    """
    if hub.event is not None:
        return hub.event.public_url
    return f"{settings.MEMBER_BASE_URL}{hub.get_absolute_url()}"


def facebook_share_url(link: str) -> str:
    """Facebook's share dialog for ``link``, the whole link percent-encoded into ``u``."""
    return f"{FACEBOOK_SHARER}{quote(link, safe='')}"


def share_text(message: str, link: str) -> str:
    """What Copy a message to send friends copies: the message, then the link."""
    return f"{message.strip()} {link}".strip()


def set_flyer(hub: EventHub, slot: str, upload: UploadedFile) -> HubFlyer:
    """Store ``upload`` as ``hub``'s flyer in ``slot`` (a :class:`hq.models.FlyerSlot` value), making the page's row
    on the first upload. The file it replaces is deleted from storage by :meth:`HubFlyer.save`."""
    if slot not in FlyerSlot.values:
        raise ValueError(f"Unknown flyer slot '{slot}'.")
    with transaction.atomic():
        page, _created = HubFlyer.objects.select_for_update().get_or_create(hub=hub)
        setattr(page, slot, upload)
        page.save()
    return page


def remove_flyer(hub: EventHub, slot: str) -> bool:
    """Clear ``hub``'s flyer in ``slot`` and delete its file; False when there was none to remove."""
    if slot not in FlyerSlot.values:
        raise ValueError(f"Unknown flyer slot '{slot}'.")
    page = HubFlyer.objects.filter(hub=hub).first()
    if page is None or not getattr(page, slot):
        return False
    setattr(page, slot, "")
    page.save(update_fields=[slot, "updated_at"])
    return True


def save_flyer_links(hub: EventHub, fields: Mapping[str, Any]) -> HubFlyer:
    """Save the website and Eventbrite links and the share message (the form's cleaned fields) on ``hub``."""
    page, _created = HubFlyer.objects.update_or_create(hub=hub, defaults=dict(fields))
    return page


# ---- PL Ledger, budget (Track D, part 6a) ----

# The ledger's filters, in Lee's order: the query value and the button's words; ``all`` shows every entry. The From
# filter names the hub's approval, so its words are made per page (:func:`ledger_filters`).
LEDGER_FILTER_ALL = "all"

# Lee's short names for each kind (the ledger's Type chip, the filters and the CSV); ``{approval}`` is the hub's.
_LEDGER_KIND_SHORT: dict[str, str] = {
    LedgerKind.SPONSORED: "Sponsored",
    LedgerKind.PAID_APPROVAL: "From {approval}",
    LedgerKind.PAID_OTHER: "Other PLM $",
    LedgerKind.REFUND: "Refund",
}
_LEDGER_FILTER_WORDS: dict[str, str] = {**_LEDGER_KIND_SHORT, LedgerKind.REFUND: "Refunds"}

# Lee's board line before anything is logged.
LEDGER_EMPTY_SUMMARY = "Nothing logged yet. Every sponsor or purchase you add below shows up here."


class LineHasEntries(Exception):
    """A budget line with entries in the ledger cannot be deleted (``LedgerEntry.line`` is RESTRICT)."""


class ReceiptNotTaken(Exception):
    """A sponsored or donated entry takes no receipt: only money spent or refunded does."""


def ledger_settings(hub: EventHub) -> LedgerSettings:
    """``hub``'s ledger settings, or an unsaved one carrying Lee's defaults until an organiser first saves the
    approval. Rendering never writes a row."""
    found = LedgerSettings.objects.filter(hub=hub).first()
    return found if found is not None else LedgerSettings(hub=hub)


def ledger_kind_label(kind: str, approval: str) -> str:
    """A kind's full label with the hub's approval in place of Lee's $900, e.g. "Paid from board $1,200"."""
    return str(LedgerKind(kind).label).replace("$900", approval)


def ledger_kind_short(kind: str, approval: str) -> str:
    """A kind's short name for the Type chip and the CSV: Sponsored, From $900, Other PLM $ or Refund."""
    return _LEDGER_KIND_SHORT[kind].format(approval=approval)


def ledger_filters(approval: str) -> list[tuple[str, str]]:
    """The ledger's filter buttons, in Lee's order, the From filter naming the hub's approval."""
    return [
        (LEDGER_FILTER_ALL, "All"),
        *((kind.value, _LEDGER_FILTER_WORDS[kind].format(approval=approval)) for kind in LedgerKind),
    ]


def ledger_filter(value: str) -> str:
    """``value`` when it names a kind, else ``all``: a stale or typed address shows every entry."""
    return value if value in LedgerKind.values else LEDGER_FILTER_ALL


def signed_money(cents: int) -> str:
    """Dollars with a minus in front when below zero, e.g. "-$50", as Lee's figures read when over the approval."""
    return f"-{format_money(-cents)}" if cents < 0 else format_money(cents)


def _dash_or_money(cents: int) -> str:
    return "\u2013" if cents == 0 else signed_money(cents)


@dataclass(frozen=True)
class LineStatus:
    """How a budget line was covered, by Lee's rules: the chip's words and its colour."""

    label: str
    tone: str


LINE_OPEN = LineStatus("Open", "")
LINE_SPONSORED = LineStatus("Sponsored", "ok")
LINE_PURCHASED = LineStatus("Purchased", "info")
LINE_MIXED = LineStatus("Mixed", "warn")


@dataclass(frozen=True)
class LineTotals:
    """One budget line and what its entries add up to, by kind. ``paid_cents`` is net of refunds."""

    line: BudgetLine
    sponsored_cents: int
    paid_approval_cents: int
    refund_cents: int
    paid_other_cents: int
    entries: int

    @property
    def paid_cents(self) -> int:
        """Paid from the approval less what was refunded back to it."""
        return self.paid_approval_cents - self.refund_cents

    @property
    def covered_cents(self) -> int:
        return self.sponsored_cents + self.paid_cents + self.paid_other_cents

    @property
    def sponsored_display(self) -> str:
        """Each covered column as Lee's table reads it: the dollars, or a dash for nothing."""
        return _dash_or_money(self.sponsored_cents)

    @property
    def paid_display(self) -> str:
        return _dash_or_money(self.paid_cents)

    @property
    def paid_other_display(self) -> str:
        return _dash_or_money(self.paid_other_cents)

    @property
    def status(self) -> LineStatus:
        """Lee's rules: nothing covered is Open; sponsored alone is Sponsored; no sponsorship is Purchased; else
        Mixed."""
        if self.covered_cents <= 0:
            return LINE_OPEN
        if self.sponsored_cents > 0 and self.paid_cents <= 0 and self.paid_other_cents <= 0:
            return LINE_SPONSORED
        if self.sponsored_cents <= 0:
            return LINE_PURCHASED
        return LINE_MIXED


@dataclass(frozen=True)
class LedgerBand:
    """The board summary and the budget lines' figures: the approval, what was used and is left, the sponsored
    value, and every line's totals.

    These are the one source of the ledger's figures: the hub's goals, the running tally (D6c) and the bar (D6b,
    :attr:`bar_cost_cents`) read them here. ``used`` is paid from the approval less refunds, so it goes below zero
    when more came back than went out, and ``left`` goes below zero when the approval is overspent.
    """

    approved_cents: int
    approved_note: str
    approval: str
    sponsored_cents: int
    paid_approval_cents: int
    paid_other_cents: int
    refund_cents: int
    sponsors: int
    entries: int
    lines: tuple[LineTotals, ...]

    @property
    def used_cents(self) -> int:
        return self.paid_approval_cents - self.refund_cents

    @property
    def approved_display(self) -> str:
        return format_money(self.approved_cents)

    @property
    def used_display(self) -> str:
        return signed_money(self.used_cents)

    @property
    def left_display(self) -> str:
        return signed_money(self.left_cents)

    @property
    def sponsored_display(self) -> str:
        return format_money(self.sponsored_cents)

    @property
    def paid_approval_display(self) -> str:
        return format_money(self.paid_approval_cents)

    @property
    def refund_display(self) -> str:
        return format_money(self.refund_cents)

    @property
    def paid_other_display(self) -> str:
        return format_money(self.paid_other_cents)

    @property
    def left_cents(self) -> int:
        return self.approved_cents - self.used_cents

    @property
    def used_percent(self) -> int:
        """How much of the approval is used, 0 to 100, for the meter; a zero approval with anything used is full."""
        if self.approved_cents == 0:
            return 100 if self.used_cents > 0 else 0
        return max(0, min(100, round(self.used_cents * 100 / self.approved_cents)))

    @property
    def used_line(self) -> str:
        """The line under Used from approval, in Lee's words."""
        if self.refund_cents > 0:
            return f"{format_money(self.paid_approval_cents)} spent, {format_money(self.refund_cents)} refunded"
        return "Net of keg deposit refunds"

    @property
    def left_line(self) -> str:
        return "Over the approval" if self.left_cents < 0 else "Not yet spent"

    @property
    def sponsored_line(self) -> str:
        if self.sponsors:
            return f"{self.sponsors} sponsor{'s' if self.sponsors > 1 else ''}, cash + in-kind"
        return "Cash + in-kind gifts"

    @property
    def budget_total_cents(self) -> int:
        return sum(totals.line.budget_cents for totals in self.lines)

    @property
    def lines_sponsored_cents(self) -> int:
        return sum(totals.sponsored_cents for totals in self.lines)

    @property
    def lines_paid_cents(self) -> int:
        return sum(totals.paid_cents for totals in self.lines)

    @property
    def lines_paid_other_cents(self) -> int:
        return sum(totals.paid_other_cents for totals in self.lines)

    @property
    def totals_row(self) -> tuple[str, str, str, str]:
        """The budget lines' Total row: the budget, sponsored, paid from the approval (net) and other funds."""
        return (
            format_money(self.budget_total_cents),
            format_money(self.lines_sponsored_cents),
            signed_money(self.lines_paid_cents),
            format_money(self.lines_paid_other_cents),
        )

    @property
    def bar_cost_cents(self) -> int:
        """What the bar cost: paid from the approval net of refunds plus paid from other funds, on Drinks lines only,
        never below zero. Lee's page summed every line, which would charge decor and marketing to the bar."""
        drinks = [totals for totals in self.lines if totals.line.category == BudgetCategory.DRINKS]
        return max(0, sum(totals.paid_cents + totals.paid_other_cents for totals in drinks))


def _kind_sum(kind: str, prefix: str = "") -> Coalesce:
    return Coalesce(Sum(f"{prefix}amount_cents", filter=Q(**{f"{prefix}kind": kind})), 0)


def ledger_band(hub: EventHub, settings_row: LedgerSettings | None = None) -> LedgerBand:
    """The ledger's figures for ``hub`` in three queries (two when ``settings_row`` is passed): the settings, one
    aggregate over the entries, and the lines with their sums by kind."""
    found = settings_row if settings_row is not None else ledger_settings(hub)
    totals = hub.ledger_entries.aggregate(
        **{kind: _kind_sum(kind) for kind in LedgerKind.values},
        entries=Count("pk"),
        sponsors=Count(
            Lower(Trim("source")),
            filter=Q(kind=LedgerKind.SPONSORED) & ~Q(source=""),
            distinct=True,
        ),
    )
    # The order is spelled out: a GROUP BY query does not take the model's Meta.ordering.
    lines = hub.budget_lines.annotate(
        **{f"sum_{kind}": _kind_sum(kind, "entries__") for kind in LedgerKind.values},
        entry_count=Count("entries"),
    ).order_by("sort_order", Lower("name"), "pk")
    return LedgerBand(
        approved_cents=found.approved_cents,
        approved_note=found.approved_note,
        approval=found.approval_words,
        sponsored_cents=totals[LedgerKind.SPONSORED],
        paid_approval_cents=totals[LedgerKind.PAID_APPROVAL],
        paid_other_cents=totals[LedgerKind.PAID_OTHER],
        refund_cents=totals[LedgerKind.REFUND],
        sponsors=totals["sponsors"],
        entries=totals["entries"],
        lines=tuple(
            LineTotals(
                line=line,
                sponsored_cents=getattr(line, f"sum_{LedgerKind.SPONSORED}"),
                paid_approval_cents=getattr(line, f"sum_{LedgerKind.PAID_APPROVAL}"),
                refund_cents=getattr(line, f"sum_{LedgerKind.REFUND}"),
                paid_other_cents=getattr(line, f"sum_{LedgerKind.PAID_OTHER}"),
                entries=line.entry_count,
            )
            for line in lines
        ),
    )


def bar_cost_cents(hub: EventHub) -> int:
    """What the bar cost on ``hub``: :attr:`LedgerBand.bar_cost_cents`, the Drinks lines only. D6b reads it here."""
    return ledger_band(hub).bar_cost_cents


@dataclass(frozen=True)
class DoorFigures:
    """What the board summary reads from the other doors and the ledger's money in, each from its one source, never a
    copy."""

    sponsors: SponsorBand
    flyers: int
    checkin: CheckinBand
    wine: WineBand
    cash: EventCash
    bar: BarTotals
    tickets: TicketTotals


def door_figures(
    hub: EventHub, band: LedgerBand | None = None, settings_row: LedgerSettings | None = None
) -> DoorFigures:
    """The other doors' figures and the money in the board summary says, in a fixed number of queries (fewer when the
    page passes the ledger's band and settings in)."""
    found = settings_row if settings_row is not None else ledger_settings(hub)
    ledger = band if band is not None else ledger_band(hub, found)
    sponsors = sponsor_band(hub)
    wine = wine_band(hub)
    bar = bar_totals(hub, found, cost_cents=ledger.bar_cost_cents)
    tickets = ticket_totals(found, wine)
    return DoorFigures(
        sponsors=sponsors,
        flyers=marketing_totals(hub)[MarketingKind.FLYER],
        checkin=checkin_band(hub),
        wine=wine,
        cash=EventCash(
            sponsors_cents=sponsors.received_cents,
            committed_cents=sponsors.committed_cents,
            bar_cents=bar.sales_cents,
            tickets_cents=tickets.revenue_cents,
        ),
        bar=bar,
        tickets=tickets,
    )


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def board_summary(band: LedgerBand, doors: DoorFigures) -> str:
    """Lee's board summary paragraph (her ``summaryText``), the sentences in her order.

    The drinks budget comes from the ledger; then, each only when it has something to say, the cash sponsorships
    (the Sponsorship Tracker), the flyers (the Marketing Tracker's businesses with flyers), the volunteers (the Market
    Check-in's hours) and the wine wall. D6b adds the event cash, the drink sales and the wine wall tickets, and D6c
    the guild demos, the staff time and the attendance, each in the place Lee gives it (marked below). D6b's are here:
    the event cash goal and the drink sales after the drinks budget, the tickets sold after the wine wall.
    """
    money = format_money
    parts = [f"Holiday Bizarre drinks: the board approved {money(band.approved_cents)}."]
    by = f" by {_plural(band.sponsors, 'sponsor', 'sponsors')}" if band.sponsors else ""
    parts.append(f"So far {money(band.sponsored_cents)} in drinks and supplies has been sponsored{by}.")
    after = f" (after {money(band.refund_cents)} in refunds)" if band.refund_cents > 0 else ""
    parts.append(
        f"We have used {signed_money(band.used_cents)} of the approval{after}, leaving {signed_money(band.left_cents)}."
    )
    if band.paid_other_cents > 0:
        parts.append(f"Another {money(band.paid_other_cents)} came from other PLM funds.")
    cash = doors.cash
    if cash.total_cents + cash.committed_cents > 0:
        plus = f" (plus {money(cash.committed_cents)} committed)" if cash.committed_cents else ""
        parts.append(f"Event cash goal: {money(cash.total_cents)} raised toward {doors.sponsors.goal_display}{plus}.")
    bar = doors.bar
    if bar.count > 0:
        after = ""
        if bar.cost_cents > 0:
            result = (
                f"a profit of {money(bar.profit_cents)}"
                if bar.profit_cents >= 0
                else f"{money(-bar.profit_cents)} short of break-even"
            )
            after = f", {result} after {money(bar.cost_cents)} in bar costs"
        parts.append(f"Drink sales: {bar.count} drinks sold for {money(bar.sales_cents)}{after}.")
    sponsors = doors.sponsors
    if sponsors.received_cents + sponsors.committed_cents > 0:
        parts.append(
            f"Cash sponsorships: {money(sponsors.received_cents)} received and "
            f"{money(sponsors.committed_cents)} committed."
        )
    # D6c: the guild demos.
    if doors.flyers:
        parts.append(f"Flyers are up at {_plural(doors.flyers, 'location', 'locations')}.")
    # D6c: the staff time.
    checkin = doors.checkin
    if checkin.hours:
        people = _plural(checkin.volunteers, "person", "people")
        parts.append(f"Volunteers: {people} gave {checkin.hours_display} hours to event prep.")
    # D6c: the attendance and the vendors.
    wine = doors.wine
    if wine.bottles > 0:
        of = f" of {wine.goal}" if wine.goal else ""
        parts.append(f"Wine wall: {wine.bottles}{of} bottles donated, worth {money(wine.value_cents)}.")
        tickets = doors.tickets
        if tickets.count > 0:
            parts.append(f"{tickets.count} tickets sold ({tickets.description}) raised {money(tickets.revenue_cents)}.")
    return " ".join(parts)


def board_line(band: LedgerBand, doors: DoorFigures) -> str:
    """The paragraph under the board summary's meter: Lee's empty line until the ledger has an entry."""
    return board_summary(band, doors) if band.entries else LEDGER_EMPTY_SUMMARY


def filtered_entries(hub: EventHub, show: str) -> QuerySet[LedgerEntry]:
    """``hub``'s ledger entries newest first under the filter ``show``, each with its line."""
    entries = hub.ledger_entries.select_related("line")
    return entries if show == LEDGER_FILTER_ALL else entries.filter(kind=show)


def receipt_entries(hub: EventHub) -> QuerySet[LedgerEntry]:
    """Every entry that takes a receipt, oldest first (Lee's receipts report order), each with its line."""
    return hub.ledger_entries.filter(kind__in=RECEIPT_KINDS).select_related("line").order_by("date", "created_at", "pk")


def save_approval(hub: EventHub, *, approved_cents: int, approved_note: str) -> LedgerSettings:
    """Record the board's approval and its note on ``hub``, making the settings row on the first save. Only those two
    fields are written, so D6b's prices and tickets are never overwritten from an older page."""
    with transaction.atomic():
        found, created = LedgerSettings.objects.select_for_update().get_or_create(
            hub=hub, defaults={"approved_cents": approved_cents, "approved_note": approved_note}
        )
        if not created:
            found.approved_cents = approved_cents
            found.approved_note = approved_note
            found.save(update_fields=["approved_cents", "approved_note", "updated_at"])
    return found


def add_budget_line(hub: EventHub, fields: Mapping[str, Any]) -> BudgetLine:
    """Put a budget line on ``hub`` from the form's fields, after the lines it has."""
    last = hub.budget_lines.aggregate(last=Max("sort_order"))["last"]
    return BudgetLine.objects.create(hub=hub, sort_order=0 if last is None else last + 1, **fields)


def update_budget_line(line: BudgetLine, fields: Mapping[str, Any]) -> BudgetLine:
    """Save the line's edited fields."""
    for name, value in fields.items():
        setattr(line, name, value)
    line.save()
    return line


def delete_budget_line(line: BudgetLine) -> None:
    """Take a budget line off the budget.

    The line's row is locked first, so an entry logged against it (its insert takes a key share lock on the line)
    waits for the delete rather than slipping in between the count and the delete. Should one land anyway, the
    database's refusal (Django's ``RestrictedError``, or the foreign key's ``IntegrityError``) is answered by name.

    Raises:
        LineHasEntries: the line has entries in the ledger, named with how many when they were counted.
    """
    with transaction.atomic():
        list(BudgetLine.objects.select_for_update().filter(pk=line.pk))
        count = line.entries.count()
        if count:
            raise LineHasEntries(
                f"A budget line with entries cannot be deleted: {line.name} has "
                f"{_plural(count, 'entry', 'entries')} in the ledger. Delete those entries first."
            )
        try:
            with transaction.atomic():
                line.delete()
        except (RestrictedError, IntegrityError) as exc:  # an entry logged between the count and the delete
            raise LineHasEntries(f"A budget line with entries cannot be deleted: {line.name} has entries.") from exc


def add_ledger_entry(hub: EventHub, fields: Mapping[str, Any], *, member: Member | None) -> LedgerEntry:
    """Log an entry on ``hub`` from the form's cleaned fields, stamped with who logged it.

    Raises:
        ReceiptNotTaken: a receipt came with a sponsored or donated entry.
    """
    if fields.get("receipt") and fields["kind"] not in RECEIPT_KINDS:
        raise ReceiptNotTaken("A sponsored or donated entry takes no receipt.")
    return LedgerEntry.objects.create(hub=hub, created_by=member, **fields)


def attach_receipt(entry: LedgerEntry, upload: UploadedFile) -> LedgerEntry:
    """Store ``upload`` as ``entry``'s receipt, replacing any it had (the old file is deleted once this commits).

    Raises:
        ReceiptNotTaken: the entry is sponsored or donated.
    """
    if not entry.takes_receipt:
        raise ReceiptNotTaken("A sponsored or donated entry takes no receipt.")
    with transaction.atomic():
        entry.receipt = upload  # type: ignore[assignment]  # a FieldFile takes an uploaded file
        entry.save(update_fields=["receipt", "updated_at"])
    return entry


# ---- PL Ledger, money in (Track D, part 6b) ----

# Lee's line under the batch form's two refusals.
BAR_NOTHING_ENTERED = "Enter how many, or a total collected."
BAR_MIXED_NEEDS_TOTAL = "For mixed sales, enter the total collected."
# A settings form posted from a page older than the figures it shows: someone else saved them since.
SETTINGS_STALE_SAVE = "Someone else changed these. Your page has been refreshed; make your change again."

# The settings field that holds each priced drink's price, in Lee's order.
DRINK_PRICE_FIELDS: dict[str, str] = {
    BarDrink.IPA: "ipa_cents",
    BarDrink.PILS: "pils_cents",
    BarDrink.WINE: "wine_cents",
    BarDrink.NA: "na_cents",
}
# Lee's short drink names, for the Drinks sold line ("3 IPA · 2 Pilsner"); a mixed batch is "Mixed".
_DRINK_SHORT: dict[str, str] = {
    **{drink: str(BarDrink(drink).label) for drink in PRICED_DRINKS},
    BarDrink.MIXED: "Mixed",
}


def drink_price_cents(found: LedgerSettings, drink: str) -> int:
    """What one ``drink`` costs at the bar in cents (a mixed batch has no price: 0)."""
    field_name = DRINK_PRICE_FIELDS.get(drink)
    return 0 if field_name is None else getattr(found, field_name)


@dataclass(frozen=True)
class QuickButton:
    """One of the night's +1 buttons: the drink, its words, its price and how many have sold."""

    drink: str
    label: str
    price_cents: int
    sold: int

    @property
    def price_display(self) -> str:
        return format_money(self.price_cents)


def quick_buttons(found: LedgerSettings, bar: BarTotals) -> list[QuickButton]:
    """The four +1 buttons in Lee's order, at the hub's prices, each with how many of that drink have sold."""
    return [
        QuickButton(
            drink=drink,
            label=str(BarDrink(drink).label),
            price_cents=drink_price_cents(found, drink),
            sold=bar.by_drink.get(drink, 0),
        )
        for drink in PRICED_DRINKS
    ]


@dataclass(frozen=True)
class BarTotals:
    """The Drink sales tiles: drinks sold, sales, the bar's cost and profit, and Lee's break-even line.

    ``cost_cents`` is the ledger's (:attr:`LedgerBand.bar_cost_cents`, Drinks lines only), never a copy. These are the
    one source of the bar's sales: the event cash (:func:`event_cash`) and the board summary read them.
    """

    count: int
    sales_cents: int
    cost_cents: int
    last_year_cents: int
    ipa_cents: int
    by_drink: dict[str, int]
    by_pay: dict[str, int]

    @property
    def profit_cents(self) -> int:
        return self.sales_cents - self.cost_cents

    @property
    def sales_display(self) -> str:
        return format_money(self.sales_cents)

    @property
    def cost_display(self) -> str:
        return format_money(self.cost_cents)

    @property
    def profit_display(self) -> str:
        """The profit, with Lee's minus sign when the bar is short."""
        money = format_money(abs(self.profit_cents))
        return f"\u2212{money}" if self.profit_cents < 0 else money

    @property
    def profit_tone(self) -> str:
        """The profit figure's colour: "bad" when short, "ok" when ahead, plain at zero."""
        if self.profit_cents < 0:
            return "bad"
        return "ok" if self.profit_cents > 0 else ""

    @property
    def count_line(self) -> str:
        """Under Drinks sold: the drinks by kind in Lee's order ("3 IPA · 2 Pilsner"), or her empty line."""
        parts = [f"{self.by_drink[drink]} {_DRINK_SHORT[drink]}" for drink in BarDrink if self.by_drink.get(drink)]
        return " · ".join(parts) if parts else "No sales logged yet"

    @property
    def sales_line(self) -> str:
        """Under Sales: "2025: about $500", or ahead of or behind it once there are sales."""
        last_year = format_money(self.last_year_cents)
        if self.sales_cents > 0 and self.last_year_cents:
            side = "Ahead of" if self.sales_cents >= self.last_year_cents else "Behind"
            return f"{side} 2025 (about {last_year})"
        return f"2025: about {last_year}"

    @property
    def profit_line(self) -> str:
        if self.cost_cents == 0:
            return "No bar costs logged yet"
        if self.profit_cents >= 0:
            return "Bar has paid for itself"
        return f"{format_money(-self.profit_cents)} to break even"

    @property
    def percent(self) -> int:
        """The meter: sales toward covering the bar's cost, 0 to 100; full with sales and no cost."""
        if self.cost_cents > 0:
            return min(100, self.sales_cents * 100 // self.cost_cents)
        return 100 if self.sales_cents > 0 else 0

    @property
    def needed_cents(self) -> int:
        """What the bar still has to sell to cover its cost."""
        return max(0, self.cost_cents - self.sales_cents)

    @property
    def needed_display(self) -> str:
        return format_money(self.needed_cents)

    @property
    def beers_to_break_even(self) -> int | None:
        """Lee's break-even: about how many more beers at the IPA price cover the cost (rounded up), or None when the
        IPA is free and no number of beers would."""
        if not self.ipa_cents:
            return None
        return -(-self.needed_cents // self.ipa_cents)

    @property
    def ipa_display(self) -> str:
        return format_money(self.ipa_cents)

    @property
    def meter_line(self) -> str:
        """The meter's right-hand label: "$120 of $300", blank until there is a cost."""
        return f"{self.sales_display} of {self.cost_display}" if self.cost_cents > 0 else ""

    def pay_cents(self, pay: str) -> int:
        return self.by_pay.get(pay, 0)


def bar_totals(hub: EventHub, found: LedgerSettings, *, cost_cents: int) -> BarTotals:
    """The Drink sales tiles for ``hub`` in one query, against ``cost_cents`` (the ledger's bar cost)."""
    by_drink: dict[str, int] = {}
    by_pay: dict[str, int] = {}
    count = sales = 0
    for row in hub.bar_sales.values("drink", "pay").annotate(qty=Sum("qty"), amount=Sum("amount_cents")).order_by():
        by_drink[row["drink"]] = by_drink.get(row["drink"], 0) + row["qty"]
        by_pay[row["pay"]] = by_pay.get(row["pay"], 0) + row["amount"]
        count += row["qty"]
        sales += row["amount"]
    return BarTotals(
        count=count,
        sales_cents=sales,
        cost_cents=cost_cents,
        last_year_cents=found.last_year_bar_cents,
        ipa_cents=found.ipa_cents,
        by_drink=by_drink,
        by_pay=by_pay,
    )


def bar_sales_cents(hub: EventHub) -> int:
    """Everything the bar took in on ``hub``, in cents. One query."""
    total: int = hub.bar_sales.aggregate(total=Coalesce(Sum("amount_cents"), 0))["total"]
    return total


@dataclass(frozen=True)
class TicketTotals:
    """The wine wall's tickets: singles and two-ticket deals sold at the hub's prices, and Lee's revenue at goal.

    ``goal`` and ``bottles`` are the wall's (:func:`wine_band`), read, never copied.
    """

    singles: int
    pairs: int
    ticket_cents: int
    pair_cents: int
    goal: int
    bottles: int

    @property
    def count(self) -> int:
        """Tickets sold: a two-ticket deal is two."""
        return self.singles + 2 * self.pairs

    @property
    def revenue_cents(self) -> int:
        """Lee's ``ticketRev``: singles at the ticket price plus deals at the deal price."""
        return self.singles * self.ticket_cents + self.pairs * self.pair_cents

    @property
    def revenue_display(self) -> str:
        return format_money(self.revenue_cents)

    @property
    def ticket_display(self) -> str:
        return format_money(self.ticket_cents)

    @property
    def pair_display(self) -> str:
        return format_money(self.pair_cents)

    @property
    def at_goal_tickets(self) -> int:
        """The tickets the wall can sell at goal: one per bottle, the goal or the bottles in, whichever is more."""
        return max(self.goal, self.bottles)

    @property
    def at_goal_low_cents(self) -> int:
        """Lee's low end: every ticket sold in two-ticket deals, an odd one out as a single."""
        tickets = self.at_goal_tickets
        return tickets // 2 * self.pair_cents + tickets % 2 * self.ticket_cents

    @property
    def at_goal_high_cents(self) -> int:
        """Lee's high end: every ticket sold as a single."""
        return self.at_goal_tickets * self.ticket_cents

    @property
    def at_goal_display(self) -> str:
        """Lee's Ticket revenue at goal: "$2,000 to $2,500", or one figure when the two ends meet."""
        low, high = self.at_goal_low_cents, self.at_goal_high_cents
        return format_money(high) if low == high else f"{format_money(low)} to {format_money(high)}"

    @property
    def at_goal_line(self) -> str:
        return f"{self.at_goal_tickets} tickets at {self.ticket_display}, or 2 for {self.pair_display}"

    @property
    def description(self) -> str:
        """Lee's ``ticketDesc``: "3 singles at $25 + 1 deal at $40 for 2"."""
        singles = f"{self.singles} single{'' if self.singles == 1 else 's'} at {self.ticket_display}"
        pairs = f"{self.pairs} deal{'' if self.pairs == 1 else 's'} at {self.pair_display} for 2"
        return f"{singles} + {pairs}"

    @property
    def price_line(self) -> str:
        """Lee's line beside the ticket inputs."""
        return f"{self.ticket_display} a ticket or 2 for {self.pair_display}. Each ticket wins a bottle."


def ticket_totals(found: LedgerSettings, wine: WineBand) -> TicketTotals:
    """The wine wall's tickets from the ledger settings and the wall's band. No query."""
    return TicketTotals(
        singles=found.singles_sold,
        pairs=found.pairs_sold,
        ticket_cents=found.ticket_cents,
        pair_cents=found.pair_cents,
        goal=wine.goal,
        bottles=wine.bottles,
    )


@dataclass(frozen=True)
class EventCash:
    """The event cash Raised counts (Felix, 2026-10-20): sponsors received, bar sales and wine wall tickets, each read
    from its source. Committed sponsor money is carried for the board summary's "plus ... committed" but is not cash.
    D6c's running tally reads it here."""

    sponsors_cents: int
    committed_cents: int
    bar_cents: int
    tickets_cents: int

    @property
    def total_cents(self) -> int:
        return self.sponsors_cents + self.bar_cents + self.tickets_cents


def event_cash(hub: EventHub) -> EventCash:
    """The event cash on ``hub`` in four queries: the sponsor band (two), the bar's sales and the ledger settings."""
    sponsors = sponsor_band(hub)
    found = ledger_settings(hub)
    return EventCash(
        sponsors_cents=sponsors.received_cents,
        committed_cents=sponsors.committed_cents,
        bar_cents=bar_sales_cents(hub),
        tickets_cents=found.singles_sold * found.ticket_cents + found.pairs_sold * found.pair_cents,
    )


def log_quick_sale(hub: EventHub, *, drink: str, pay: str, member: Member | None) -> BarSale:
    """One +1 tap: one ``drink`` at the hub's price, paid by ``pay``, stamped with the organiser who tapped."""
    found = ledger_settings(hub)
    return BarSale.objects.create(
        hub=hub,
        drink=drink,
        qty=1,
        amount_cents=drink_price_cents(found, drink),
        pay=pay,
        source=BarSaleSource.QUICK,
        created_by=member,
    )


def undo_quick_sale(hub: EventHub, member: Member | None) -> BarSale | None:
    """Take back the newest quick sale ``member`` tapped on ``hub``, or None when they have none. Never anyone else's,
    and never a batch."""
    if member is None:
        return None
    with transaction.atomic():
        sale = (
            hub.bar_sales.select_for_update()
            .filter(source=BarSaleSource.QUICK, created_by=member)
            .order_by("-created_at", "-pk")
            .first()
        )
        if sale is not None:
            sale.delete()
    return sale


def add_bar_batch(hub: EventHub, fields: Mapping[str, Any], *, member: Member | None) -> BarSale:
    """Log a batch from the form's fields (``amount_cents`` already defaulted to price times how many)."""
    return BarSale.objects.create(hub=hub, source=BarSaleSource.BATCH, created_by=member, **fields)


def save_ledger_figures(
    hub: EventHub, fields: Mapping[str, int], *, seen: Mapping[str, int | None]
) -> LedgerSettings | None:
    """Write ``fields`` on ``hub``'s ledger settings under a row lock, unless the figures the form was rendered with
    (``seen``) are no longer what the database holds: then nothing is written and None comes back (someone else saved
    them since). Only ``fields`` are written, so the approval, the prices and the tickets never overwrite each other."""
    with transaction.atomic():
        found, _created = LedgerSettings.objects.select_for_update().get_or_create(hub=hub)
        if any(getattr(found, name) != value for name, value in seen.items()):
            return None
        for name, value in fields.items():
            setattr(found, name, value)
        found.save(update_fields=[*fields, "updated_at"])
    return found

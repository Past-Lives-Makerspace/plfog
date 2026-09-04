"""Read-time reconciliation engine — who gets paid, and how much, for a window.

No fourth money table. This module walks the same three streams as
``payments_panel`` (paid registrations, paid orientation bookings, succeeded tab
charges), attributes each payment to its recipients under the makerspace's split
rules, and aggregates per recipient. All money is integer cents; the penny
rounding mirrors ``TabEntry.snapshot_splits`` (largest-percent recipient absorbs
the +/-1c drift so every allocation sums exactly to what was collected).

Split percentages come from ``BillingSettings`` (the editable config), falling
back to the module constants only when a value is unset.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterator, cast

from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from billing.models import BillingSettings, ReconciliationSnapshot, TabCharge, TransactionAdjustment
    from billing.payments_panel import PanelWindow
    from membership.models import Guild

_HUNDRED = Decimal("100")
_ONE = Decimal("1")

# Fallback split percentages, used only when the matching BillingSettings value is unset.
# Ordered producer-first, Past Lives last, so a genuine tie on the largest percent lands
# on a producer (mirroring snapshot_splits' "first row wins" spirit).
CLASS_SPLIT: dict[str, Decimal] = {
    "instructor": Decimal("70"),
    "guild": Decimal("10"),
    "pl": Decimal("20"),
}
ORIENTATION_SPLIT: dict[str, Decimal] = {
    "orientator": Decimal("70"),
    "guild": Decimal("15"),
    "pl": Decimal("15"),
}

PL_LABEL = "Past Lives"


class RecipientKind(str, Enum):
    """The four kinds of reconciliation recipient. Values double as the JSON vocabulary."""

    GUILD = "guild"
    INSTRUCTOR = "instructor"
    ORIENTATOR = "orientator"
    PL = "pl"


# Display / iteration order for the grouped table and CSV.
GROUP_ORDER: tuple[RecipientKind, ...] = (
    RecipientKind.GUILD,
    RecipientKind.INSTRUCTOR,
    RecipientKind.ORIENTATOR,
    RecipientKind.PL,
)

GROUP_LABELS: dict[RecipientKind, str] = {
    RecipientKind.GUILD: "Guilds",
    RecipientKind.INSTRUCTOR: "Instructors",
    RecipientKind.ORIENTATOR: "Orientators",
    RecipientKind.PL: "Past Lives",
}

# A recipient key: (kind_value, recipient_id) — recipient_id is None only for PL.
RecipientKey = tuple[str, "int | None"]
# An adjustment lookup key: (source_kind, source_pk) — source_pk is always a real int.
AdjustmentKey = tuple[str, int]


def split_cents(amount_cents: int, percents: dict[str, Decimal]) -> dict[str, int]:
    """Split ``amount_cents`` by ``percents`` (keys->percent), summing exactly to the input.

    Each share is ``round(amount_cents * pct / 100)`` half-up; the largest-percent key
    absorbs the +/-1c remainder. ``percents`` must be ordered producer-first (Past Lives
    last) so a tie on the largest percent lands on the first (producer) key. The percents
    must sum to exactly 100 (fail loud otherwise).
    """
    if sum(percents.values(), Decimal("0")) != _HUNDRED:
        raise ValueError(f"Split percentages must sum to 100, got {sum(percents.values(), Decimal('0'))}.")
    shares: dict[str, int] = {}
    raw_total = 0
    largest_key = next(iter(percents))
    largest_pct = Decimal("-1")
    for key, pct in percents.items():
        amt = int((Decimal(amount_cents) * pct / _HUNDRED).quantize(_ONE, rounding=ROUND_HALF_UP))
        shares[key] = amt
        raw_total += amt
        if pct > largest_pct:
            largest_pct = pct
            largest_key = key
    drift = amount_cents - raw_total
    if drift != 0:
        shares[largest_key] += drift
    return shares


@dataclass(frozen=True)
class TransactionLine:
    """One contributing payment, with its per-recipient split resolved."""

    source_kind: str  # "tab" | "class" | "orientation"
    source_pk: int
    date: Any  # datetime
    payer_name: str
    item: str
    gross_cents: int
    refunded_cents: int
    net_cents: int
    shares: dict[RecipientKey, int]
    labels: dict[RecipientKey, str]
    omitted: bool = False
    overridden: bool = False
    unassigned: bool = False  # an unset producer or guild rolled its share to Past Lives
    note: str = ""


@dataclass
class RecipientAllocation:
    """One output row: a distinct recipient's total for the window."""

    kind: RecipientKind
    recipient_id: int | None
    label: str
    total_cents: int
    transaction_count: int
    voting_cents: int = 0
    voting_projected: bool = False

    @property
    def combined_cents(self) -> int:
        """Direct splits plus voting allocation (the guild "Total" column)."""
        return self.total_cents + self.voting_cents


@dataclass(frozen=True)
class ReconciliationResult:
    """The built allocation for a window."""

    window: PanelWindow
    groups: dict[RecipientKind, list[RecipientAllocation]]
    lines: list[TransactionLine]
    grand_total_cents: int
    unassigned_note_count: int
    omitted_count: int
    class_percents: dict[str, Decimal]
    orientation_percents: dict[str, Decimal]
    voting_projected: bool = True
    voting_snapshotted_on: date | None = None
    is_snapshot: bool = False

    @property
    def producers_total_cents(self) -> int:
        """Everything disbursed to guilds, instructors, and orientators (not Past Lives)."""
        return sum(
            alloc.total_cents
            for kind in (RecipientKind.GUILD, RecipientKind.INSTRUCTOR, RecipientKind.ORIENTATOR)
            for alloc in self.groups[kind]
        )

    @property
    def pl_total_cents(self) -> int:
        return sum(alloc.total_cents for alloc in self.groups[RecipientKind.PL])

    @property
    def voting_total_cents(self) -> int:
        return sum(alloc.voting_cents for alloc in self.groups[RecipientKind.GUILD])

    @property
    def has_any(self) -> bool:
        return any(self.groups[kind] for kind in GROUP_ORDER)

    def ordered_groups(self) -> list[tuple[RecipientKind, str, list[RecipientAllocation]]]:
        """(kind, heading, rows) in display order — for the template to iterate."""
        return [(kind, GROUP_LABELS[kind], self.groups[kind]) for kind in GROUP_ORDER]

    def to_snapshot_dict(self) -> dict[str, Any]:
        """Serialize to the frozen ``results`` JSON stored on a ReconciliationSnapshot."""
        return {
            "period_start": self.window.start.isoformat(),
            "period_end": self.window.end.isoformat(),
            "grand_total_cents": self.grand_total_cents,
            "unassigned_note_count": self.unassigned_note_count,
            "omitted_count": self.omitted_count,
            "voting_total_cents": self.voting_total_cents,
            "class_percents": {k: str(v) for k, v in self.class_percents.items()},
            "orientation_percents": {k: str(v) for k, v in self.orientation_percents.items()},
            "groups": [
                {
                    "kind": kind.value,
                    "rows": [
                        {
                            "recipient_id": alloc.recipient_id,
                            "label": alloc.label,
                            "total_cents": alloc.total_cents,
                            "transaction_count": alloc.transaction_count,
                            "voting_cents": alloc.voting_cents,
                        }
                        for alloc in self.groups[kind]
                    ],
                }
                for kind in GROUP_ORDER
            ],
        }


# ---------------------------------------------------------------------------
# Percent resolution
# ---------------------------------------------------------------------------


def _class_percents(settings_obj: BillingSettings) -> dict[str, Decimal]:
    """Class split from BillingSettings, ordered producer-first, constant fallback if unset."""
    return {
        "instructor": _percent_or(settings_obj, "class_instructor_percent", CLASS_SPLIT["instructor"]),
        "guild": _percent_or(settings_obj, "class_guild_percent", CLASS_SPLIT["guild"]),
        "pl": _percent_or(settings_obj, "class_pl_percent", CLASS_SPLIT["pl"]),
    }


def _orientation_percents(settings_obj: BillingSettings) -> dict[str, Decimal]:
    """Orientation split from BillingSettings, ordered producer-first, constant fallback if unset."""
    return {
        "orientator": _percent_or(settings_obj, "orientation_orientator_percent", ORIENTATION_SPLIT["orientator"]),
        "guild": _percent_or(settings_obj, "orientation_guild_percent", ORIENTATION_SPLIT["guild"]),
        "pl": _percent_or(settings_obj, "orientation_pl_percent", ORIENTATION_SPLIT["pl"]),
    }


def _percent_or(settings_obj: BillingSettings, attr: str, fallback: Decimal) -> Decimal:
    value = getattr(settings_obj, attr, None)
    return value if value is not None else fallback


def _override_percents(raw: dict[str, Any], order: tuple[str, ...]) -> dict[str, Decimal]:
    """Reorder a stored override map into producer-first Decimal percents."""
    return {key: Decimal(str(raw[key])) for key in order}


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------


def _member_share_line(
    *,
    source_kind: str,
    source_pk: int,
    when: Any,
    payer_name: str,
    item: str,
    gross_cents: int,
    refunded_cents: int,
    producer_key: RecipientKey | None,
    producer_label: str | None,
    producer_role: str,
    guild_key: RecipientKey | None,
    guild_label: str | None,
    percents: dict[str, Decimal],
    adjustment: TransactionAdjustment | None,
) -> TransactionLine:
    """Build one class/orientation line, rolling any unset recipient's share to Past Lives.

    ``producer_role`` is "instructor" or "orientator" (its percent key); ``guild_key``
    is the guild recipient or None. Unset producer/guild shares roll to PL with a note.
    """
    net_cents = gross_cents - refunded_cents
    effective = percents
    overridden = False
    notes: list[str] = []
    if adjustment is not None and adjustment.override_percents is not None:
        # The adjustment form guarantees a valid override, but the model is editable in
        # Django admin too. Defend the read path: a malformed override (missing key or a
        # triad that does not sum to 100) falls back to the standard split with a note,
        # rather than raising and 500ing the whole Reconciliation tab.
        try:
            candidate = _override_percents(adjustment.override_percents, (producer_role, "guild", "pl"))
            if sum(candidate.values(), Decimal("0")) != Decimal("100"):
                raise ValueError("override does not sum to 100")
            effective = candidate
            overridden = True
        except (KeyError, ValueError, TypeError, InvalidOperation):
            notes.append("override ignored (malformed)")
    shares_by_role = split_cents(net_cents, effective)

    shares: dict[RecipientKey, int] = {}
    labels: dict[RecipientKey, str] = {}
    pl_key: RecipientKey = ("pl", None)

    def _credit(key: RecipientKey, label: str, cents: int) -> None:
        shares[key] = shares.get(key, 0) + cents
        labels[key] = label

    unassigned = False
    if producer_key is not None and producer_label is not None:
        _credit(producer_key, producer_label, shares_by_role[producer_role])
    else:
        _credit(pl_key, PL_LABEL, shares_by_role[producer_role])
        notes.append(f"{producer_role} unset -> Past Lives")
        unassigned = True

    if guild_key is not None and guild_label is not None:
        _credit(guild_key, guild_label, shares_by_role["guild"])
    else:
        _credit(pl_key, PL_LABEL, shares_by_role["guild"])
        notes.append("guild unset -> Past Lives")
        unassigned = True

    _credit(pl_key, PL_LABEL, shares_by_role["pl"])

    return TransactionLine(
        source_kind=source_kind,
        source_pk=source_pk,
        date=when,
        payer_name=payer_name,
        item=item,
        gross_cents=gross_cents,
        refunded_cents=refunded_cents,
        net_cents=net_cents,
        shares=shares,
        labels=labels,
        omitted=bool(adjustment is not None and adjustment.is_omitted),
        overridden=overridden,
        unassigned=unassigned,
        note="; ".join(notes),
    )


def _class_lines(
    window: PanelWindow,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
) -> list[TransactionLine]:
    from classes.models import Registration

    registrations = (
        Registration.objects.filter(amount_paid_cents__gt=0)
        .filter(confirmed_at__gte=window.start_dt, confirmed_at__lt=window.end_dt)
        .select_related("class_offering__instructor", "class_offering__category__guild", "member")
        .prefetch_related("refunds")
    )
    lines: list[TransactionLine] = []
    for reg in registrations:
        offering = reg.class_offering
        instructor = offering.instructor
        guild = offering.category.guild
        guest_name = f"{reg.first_name} {reg.last_name}".strip()
        payer_name = reg.member.display_name if reg.member is not None else (guest_name or reg.email)
        lines.append(
            _member_share_line(
                source_kind="class",
                source_pk=reg.pk,
                when=reg.confirmed_at,
                payer_name=payer_name,
                item=offering.title,
                gross_cents=reg.amount_paid_cents,
                refunded_cents=reg.amount_refunded_cents,
                producer_key=("instructor", instructor.id) if instructor is not None else None,
                producer_label=instructor.display_name if instructor is not None else None,
                producer_role="instructor",
                guild_key=("guild", guild.id) if guild is not None else None,
                guild_label=guild.name if guild is not None else None,
                percents=percents,
                adjustment=adjustments.get(("class", reg.pk)),
            )
        )
    return lines


def _orientation_lines(
    window: PanelWindow,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
) -> list[TransactionLine]:
    from membership.models import OrientationBooking

    bookings = (
        OrientationBooking.objects.filter(amount_paid_cents__gt=0)
        .exclude(status=OrientationBooking.Status.PENDING_PAYMENT)
        .filter(requested_at__gte=window.start_dt, requested_at__lt=window.end_dt)
        .select_related("guild", "member", "oriented_by", "orientation_type__guild", "orientation_type__equipment")
        .prefetch_related("refunds")
    )
    lines: list[TransactionLine] = []
    for booking in bookings:
        orientator = booking.oriented_by
        lines.append(
            _member_share_line(
                source_kind="orientation",
                source_pk=booking.pk,
                when=booking.requested_at,
                payer_name=booking.member.display_name,
                item=f"Orientation — {booking.orientation_type.owner_name}",
                gross_cents=booking.amount_paid_cents,
                refunded_cents=booking.amount_refunded_cents,
                producer_key=("orientator", orientator.id) if orientator is not None else None,
                producer_label=orientator.display_name if orientator is not None else None,
                producer_role="orientator",
                guild_key=("guild", booking.guild.id) if booking.guild is not None else None,
                guild_label=booking.guild.name if booking.guild is not None else None,
                percents=percents,
                adjustment=adjustments.get(("orientation", booking.pk)),
            )
        )
    return lines


def _tab_lines(
    window: PanelWindow,
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
) -> list[TransactionLine]:
    """One line per succeeded tab charge, re-using the frozen TabEntrySplit shares.

    Admin splits -> Past Lives; guild splits -> that guild. No re-splitting, no netting
    (tab refunds do not exist product-wide yet). Tab adjustments are omit-only.
    """
    from django.db.models.functions import Coalesce

    from billing.models import TabCharge, TabEntrySplit

    splits = (
        TabEntrySplit.objects.filter(
            entry__tab_charge__status=TabCharge.Status.SUCCEEDED,
            entry__voided_at__isnull=True,
        )
        .annotate(eff=Coalesce("entry__tab_charge__charged_at", "entry__tab_charge__created_at"))
        .filter(eff__gte=window.start_dt, eff__lt=window.end_dt)
        .select_related("entry__tab_charge", "entry__tab__member", "guild")
        .order_by("-eff", "entry__tab_charge_id")
    )
    charges: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for split in splits:
        # The SUCCEEDED-status filter guarantees a non-null charge and (for GUILD rows) a guild.
        charge = cast("TabCharge", split.entry.tab_charge)
        bucket = charges.get(charge.pk)
        if bucket is None:
            bucket = {
                "when": split.eff,
                "payer_name": charge.tab.member.display_name,
                "shares": {},
                "labels": {},
                "gross": 0,
            }
            charges[charge.pk] = bucket
            order.append(charge.pk)
        cents = int((split.amount * 100).quantize(_ONE, rounding=ROUND_HALF_UP))
        if split.recipient_type == TabEntrySplit.RecipientType.ADMIN:
            key: RecipientKey = ("pl", None)
            label = PL_LABEL
        else:
            key = ("guild", split.guild_id)
            label = cast("Guild", split.guild).name
        bucket["shares"][key] = bucket["shares"].get(key, 0) + cents
        bucket["labels"][key] = label
        bucket["gross"] += cents

    lines: list[TransactionLine] = []
    for charge_pk in order:
        bucket = charges[charge_pk]
        adjustment = adjustments.get(("tab", charge_pk))
        lines.append(
            TransactionLine(
                source_kind="tab",
                source_pk=charge_pk,
                date=bucket["when"],
                payer_name=bucket["payer_name"],
                item="Tab charge",
                gross_cents=bucket["gross"],
                refunded_cents=0,
                net_cents=bucket["gross"],
                shares=bucket["shares"],
                labels=bucket["labels"],
                omitted=bool(adjustment is not None and adjustment.is_omitted),
            )
        )
    return lines


# ---------------------------------------------------------------------------
# Voting allocation (rolling monthly)
# ---------------------------------------------------------------------------


def _project_voting_cents() -> dict[int, int]:
    """Live per-guild voting allocation in cents, keyed by guild id (0 if no votes)."""
    from membership.models import Guild, VotingSettings
    from membership.vote_analyzer import serialize_live_votes
    from membership.vote_calculator import calculate_results

    raw = serialize_live_votes()
    if not raw:
        return {}
    votes = [
        {"guild_1st": v["guild_1st_name"], "guild_2nd": v["guild_2nd_name"], "guild_3rd": v["guild_3rd_name"]}
        for v in raw
    ]
    paying = sum(1 for v in raw if v["is_paying"])
    calc = calculate_results(votes, paying_voter_count=paying, minimum_pool=VotingSettings.load().minimum_pool_floor)
    cents_by_name = {
        row["guild_name"]: int((Decimal(str(row["funding"])) * 100).quantize(_ONE, rounding=ROUND_HALF_UP))
        for row in calc["results"]
    }
    result: dict[int, int] = {}
    for guild in Guild.objects.filter(name__in=list(cents_by_name.keys())):
        result[guild.id] = cents_by_name[guild.name]
    return result


def resolve_voting_allocation(window: PanelWindow) -> tuple[dict[int, int], bool, date | None]:
    """Voting cents per guild id for the window: frozen if a snapshot exists, else projected.

    Returns ``(cents_by_guild_id, projected, snapshotted_on)``.
    """
    from billing.models import ReconciliationSnapshot

    snapshot = (
        ReconciliationSnapshot.objects.filter(period_start=window.start, period_end=window.end)
        .order_by("-taken_at")
        .first()
    )
    if snapshot is not None:
        frozen: dict[int, int] = {}
        for group in snapshot.results.get("groups", []):
            if group.get("kind") != RecipientKind.GUILD.value:
                continue
            for row in group.get("rows", []):
                if row.get("recipient_id") is not None:
                    frozen[int(row["recipient_id"])] = int(row.get("voting_cents", 0))
        return frozen, False, timezone.localtime(snapshot.taken_at).date()
    return _project_voting_cents(), True, None


# ---------------------------------------------------------------------------
# build_reconciliation
# ---------------------------------------------------------------------------


def build_reconciliation(
    *,
    window: PanelWindow,
    adjustments: dict[AdjustmentKey, TransactionAdjustment] | None = None,
    include_voting: bool = True,
) -> ReconciliationResult:
    """Attribute every payment in ``window`` to its recipients and aggregate.

    ``adjustments`` defaults to the full ``TransactionAdjustment.objects.as_map()`` —
    pass ``{}`` to force the standard splits. Voting allocation (guild rows) is projected
    live unless a snapshot already froze the window.
    """
    from billing.models import BillingSettings, TransactionAdjustment

    if adjustments is None:
        adjustments = TransactionAdjustment.objects.as_map()

    settings_obj = BillingSettings.load()
    class_percents = _class_percents(settings_obj)
    orientation_percents = _orientation_percents(settings_obj)

    lines: list[TransactionLine] = []
    lines.extend(_tab_lines(window, adjustments))
    lines.extend(_class_lines(window, class_percents, adjustments))
    lines.extend(_orientation_lines(window, orientation_percents, adjustments))
    lines.sort(key=lambda line: line.date, reverse=True)

    aggregate = _aggregate_lines(lines)

    voting_by_guild: dict[int, int] = {}
    voting_projected = True
    voting_on: date | None = None
    if include_voting:
        voting_by_guild, voting_projected, voting_on = resolve_voting_allocation(window)

    groups = _build_groups(aggregate, voting_by_guild, voting_projected, include_voting=include_voting)

    return ReconciliationResult(
        window=window,
        groups=groups,
        lines=lines,
        grand_total_cents=aggregate.grand_total_cents,
        unassigned_note_count=aggregate.unassigned_note_count,
        omitted_count=aggregate.omitted_count,
        class_percents=class_percents,
        orientation_percents=orientation_percents,
        voting_projected=voting_projected,
        voting_snapshotted_on=voting_on,
    )


@dataclass(frozen=True)
class _Aggregate:
    totals: dict[RecipientKey, int]
    counts: dict[RecipientKey, int]
    labels: dict[RecipientKey, str]
    grand_total_cents: int
    unassigned_note_count: int
    omitted_count: int


def _aggregate_lines(lines: list[TransactionLine]) -> _Aggregate:
    """Sum non-omitted lines' shares per recipient; tally grand total, notes, and omissions."""
    totals: dict[RecipientKey, int] = {}
    counts: dict[RecipientKey, int] = {}
    labels: dict[RecipientKey, str] = {}
    grand_total = 0
    unassigned_note_count = 0
    omitted_count = 0
    for line in lines:
        if line.omitted:
            omitted_count += 1
            continue
        if line.unassigned:
            unassigned_note_count += 1
        grand_total += line.net_cents
        for key, cents in line.shares.items():
            totals[key] = totals.get(key, 0) + cents
            labels[key] = line.labels[key]
            if cents > 0:
                counts[key] = counts.get(key, 0) + 1
    return _Aggregate(totals, counts, labels, grand_total, unassigned_note_count, omitted_count)


def _build_groups(
    aggregate: _Aggregate,
    voting_by_guild: dict[int, int],
    voting_projected: bool,
    *,
    include_voting: bool,
) -> dict[RecipientKind, list[RecipientAllocation]]:
    """Turn aggregated totals into sorted per-kind allocation rows, folding in voting."""
    groups: dict[RecipientKind, list[RecipientAllocation]] = {kind: [] for kind in GROUP_ORDER}
    seen_guild_ids: set[int] = set()
    for key, total in aggregate.totals.items():
        # Drop only exact-zero recipients. A negative total (an over refund past the
        # payment) must still show so the per recipient rows keep summing to the grand
        # total; hiding it would silently break conservation of money.
        if total == 0:
            continue
        kind = RecipientKind(key[0])
        recipient_id = key[1]
        alloc = RecipientAllocation(
            kind=kind,
            recipient_id=recipient_id,
            label=aggregate.labels[key],
            total_cents=total,
            transaction_count=aggregate.counts.get(key, 0),
        )
        if kind is RecipientKind.GUILD and recipient_id is not None:
            alloc.voting_cents = voting_by_guild.get(recipient_id, 0)
            alloc.voting_projected = voting_projected
            seen_guild_ids.add(recipient_id)
        groups[kind].append(alloc)

    if include_voting:
        missing = {gid: cents for gid, cents in voting_by_guild.items() if cents > 0 and gid not in seen_guild_ids}
        if missing:
            _append_voting_only_guilds(groups, missing, voting_projected)

    for kind in GROUP_ORDER:
        groups[kind].sort(key=lambda alloc: (-alloc.combined_cents, alloc.label))
    return groups


def _append_voting_only_guilds(
    groups: dict[RecipientKind, list[RecipientAllocation]],
    missing: dict[int, int],
    voting_projected: bool,
) -> None:
    from membership.models import Guild

    names = {g.id: g.name for g in Guild.objects.filter(id__in=list(missing.keys()))}
    for guild_id, cents in missing.items():
        groups[RecipientKind.GUILD].append(
            RecipientAllocation(
                kind=RecipientKind.GUILD,
                recipient_id=guild_id,
                label=names.get(guild_id, f"Guild #{guild_id}"),
                total_cents=0,
                transaction_count=0,
                voting_cents=cents,
                voting_projected=voting_projected,
            )
        )


def result_from_snapshot(snapshot: ReconciliationSnapshot) -> ReconciliationResult:
    """Rebuild a read-only ReconciliationResult from a snapshot's frozen ``results`` JSON."""
    from billing.payments_panel import PanelWindow

    data = snapshot.results
    window = PanelWindow(start=snapshot.period_start, end=snapshot.period_end)
    groups: dict[RecipientKind, list[RecipientAllocation]] = {kind: [] for kind in GROUP_ORDER}
    for group in data.get("groups", []):
        kind = RecipientKind(group["kind"])
        for row in group.get("rows", []):
            groups[kind].append(
                RecipientAllocation(
                    kind=kind,
                    recipient_id=row.get("recipient_id"),
                    label=row["label"],
                    total_cents=int(row["total_cents"]),
                    transaction_count=int(row["transaction_count"]),
                    voting_cents=int(row.get("voting_cents", 0)),
                    voting_projected=False,
                )
            )
    class_percents = {k: Decimal(v) for k, v in data.get("class_percents", {}).items()}
    orientation_percents = {k: Decimal(v) for k, v in data.get("orientation_percents", {}).items()}
    return ReconciliationResult(
        window=window,
        groups=groups,
        lines=[],
        grand_total_cents=int(data.get("grand_total_cents", snapshot.grand_total_cents)),
        unassigned_note_count=int(data.get("unassigned_note_count", 0)),
        omitted_count=int(data.get("omitted_count", 0)),
        class_percents=class_percents,
        orientation_percents=orientation_percents,
        voting_projected=False,
        voting_snapshotted_on=timezone.localtime(snapshot.taken_at).date(),
        is_snapshot=True,
    )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


class _Echo:
    """File-like object whose write() returns the value — the streaming CSV trick."""

    def write(self, value: str) -> str:
        return value


CSV_HEADERS = ["Group", "Recipient", "Transactions", "Amount", "Voting", "Total"]


def _csv_lines(result: ReconciliationResult, writer: Any) -> Iterator[str]:
    yield writer.writerow(CSV_HEADERS)
    for kind, heading, rows in result.ordered_groups():
        for alloc in rows:
            voting = f"{alloc.voting_cents / 100:.2f}" if kind is RecipientKind.GUILD else ""
            yield writer.writerow(
                [
                    heading,
                    alloc.label,
                    str(alloc.transaction_count),
                    f"{alloc.total_cents / 100:.2f}",
                    voting,
                    f"{alloc.combined_cents / 100:.2f}",
                ]
            )


def stream_reconciliation_csv(result: ReconciliationResult) -> StreamingHttpResponse:
    """Streaming CSV of the per-recipient allocation, one row per recipient."""
    writer = csv.writer(_Echo())
    response = StreamingHttpResponse(_csv_lines(result, writer), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="plfog-reconciliation-{stamp}.csv"'
    return response

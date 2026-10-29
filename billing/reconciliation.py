"""Read-time reconciliation engine — who gets paid, and how much, for a window.

No fourth money table. This module walks the same streams as
``payments_panel`` (paid registrations, paid orientation bookings, paid equipment
reservations, succeeded tab charges) plus paid cubby invoices (#979), attributes each payment to its recipients under the makerspace's split
rules, and aggregates per recipient. All money is integer cents; the penny
rounding mirrors ``TabEntry.snapshot_splits`` (largest-percent recipient absorbs
the +/-1c drift so every allocation sums exactly to what was collected).

Split percentages come from ``BillingSettings`` (the editable config), falling
back to the module constants only when a value is unset.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Iterator, cast

from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from billing.models import BillingSettings, PayoutAccount, ReconciliationSnapshot, TabCharge, TransactionAdjustment
    from billing.payments_panel import PanelWindow
    from billing.payouts import LinePayout, PayoutBadge
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
RESERVATION_SPLIT: dict[str, Decimal] = {
    "manager": Decimal("70"),
    "guild": Decimal("15"),
    "pl": Decimal("15"),
}
# A cubby payment is a pair (#979): the guild's share goes to the payee the guild picked.
CUBBY_SPLIT: dict[str, Decimal] = {
    "cubby_payee": Decimal("85"),
    "pl": Decimal("15"),
}

PL_LABEL = "Past Lives"


class RecipientKind(str, Enum):
    """The kinds of reconciliation recipient. Values double as the JSON vocabulary."""

    GUILD = "guild"
    INSTRUCTOR = "instructor"
    ORIENTATOR = "orientator"
    MANAGER = "manager"  # an equipment item's picked manager, paid from its reservations (#749)
    CUBBY_PAYEE = "cubby_payee"  # a guild's cubby payee, paid the guild's share of its cubby rent (#979)
    PL = "pl"

    @property
    def is_producer(self) -> bool:
        """True for the people paid a producer share, whose rows split Sent through Stripe / Owed now / Not yet due."""
        return self.value in _PRODUCER_KINDS


# Display / iteration order for the grouped table and CSV.
GROUP_ORDER: tuple[RecipientKind, ...] = (
    RecipientKind.GUILD,
    RecipientKind.INSTRUCTOR,
    RecipientKind.ORIENTATOR,
    RecipientKind.MANAGER,
    RecipientKind.CUBBY_PAYEE,
    RecipientKind.PL,
)

GROUP_LABELS: dict[RecipientKind, str] = {
    RecipientKind.GUILD: "Guilds",
    RecipientKind.INSTRUCTOR: "Instructors",
    RecipientKind.ORIENTATOR: "Orientors",
    RecipientKind.MANAGER: "Equipment Managers",
    RecipientKind.CUBBY_PAYEE: "Cubby Payees",
    RecipientKind.PL: "Past Lives",
}

# The producer recipient kinds' values: each has a percent key of the same name in its split.
_PRODUCER_KINDS = frozenset({"instructor", "orientator", "manager", "cubby_payee"})

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

    source_kind: str  # "tab" | "class" | "orientation" | "reservation" | "cubby"
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
    payee_kept_share: bool = False  # refunded after its share was sent, and the payee kept it (#662)
    payout: LinePayout | None = None  # live tab only: how its producer share is paid (#905)
    stripe_payment_intent_id: str = ""  # blank for money Stripe never saw (a staff Mark as Paid)
    # What Stripe kept and what reached the bank, refunds included (#984). None means unknown:
    # no fee record yet, Stripe not read yet, or not a Stripe payment. Never read None as zero.
    stripe_fee_cents: int | None = None
    stripe_net_cents: int | None = None


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
    stripe_cents: int = 0  # producer rows: the part paid in the window sent (or scheduled) through Stripe (#662)
    # Producer rows (#985): what the treasurer pays by hand now, every unsent share that fell due
    # in the window whenever it was paid, and the part paid in the window whose class, slot or
    # reservation has not happened yet, with the earliest day one of those falls due.
    owed_now_cents: int = 0
    not_due_cents: int = 0
    next_due_at: datetime | None = None
    # Producer rows: transfers Stripe rejected and is still retrying. Never paid by hand (#985).
    retrying_cents: int = 0
    payout_badge: PayoutBadge | None = None  # live producer rows: the payee's automatic payouts state (#905)

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
    reservation_percents: dict[str, Decimal]
    voting_projected: bool = True
    voting_snapshotted_on: date | None = None
    is_snapshot: bool = False
    cubby_percents: dict[str, Decimal] = field(default_factory=dict)
    # Producer lines paid before the window whose share fell due inside it and is still owed (#985).
    due_earlier_lines: list[TransactionLine] = field(default_factory=list)

    @property
    def producers_total_cents(self) -> int:
        """Everything disbursed to guilds and producers (instructors, orientors, managers), not Past Lives."""
        return sum(
            alloc.total_cents for kind in GROUP_ORDER if kind is not RecipientKind.PL for alloc in self.groups[kind]
        )

    @property
    def pl_total_cents(self) -> int:
        return sum(alloc.total_cents for alloc in self.groups[RecipientKind.PL])

    @property
    def voting_total_cents(self) -> int:
        return sum(alloc.voting_cents for alloc in self.groups[RecipientKind.GUILD])

    @property
    def retrying_total_cents(self) -> int:
        """Producer shares whose transfer failed and Stripe is retrying; the tables show their column only then."""
        return sum(alloc.retrying_cents for kind in GROUP_ORDER for alloc in self.groups[kind])

    @property
    def kept_share_count(self) -> int:
        """Refunded payments whose producer kept a share already sent through Stripe (#662)."""
        return sum(1 for line in self.lines if line.payee_kept_share)

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
            "reservation_percents": {k: str(v) for k, v in self.reservation_percents.items()},
            "cubby_percents": {k: str(v) for k, v in self.cubby_percents.items()},
            SPLIT_BY_DUE_KEY: True,
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
                            "stripe_cents": alloc.stripe_cents,
                            "owed_now_cents": alloc.owed_now_cents,
                            "not_due_cents": alloc.not_due_cents,
                            "retrying_cents": alloc.retrying_cents,
                            "next_due_at": None if alloc.next_due_at is None else alloc.next_due_at.isoformat(),
                        }
                        for alloc in self.groups[kind]
                    ],
                }
                for kind in GROUP_ORDER
            ],
        }


SPLIT_BY_DUE_KEY = "owed_by_due"
"""Marks a snapshot taken since #985, whose Owed now counts shares by the window they fell due in.

A snapshot without it counted every unsent share paid in its window as owed manually, due or not.
"""


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


def _reservation_percents(settings_obj: BillingSettings) -> dict[str, Decimal]:
    """Equipment reservation split (#749) from BillingSettings, ordered producer-first, constant fallback if unset."""
    return {
        "manager": _percent_or(settings_obj, "reservation_manager_percent", RESERVATION_SPLIT["manager"]),
        "guild": _percent_or(settings_obj, "reservation_guild_percent", RESERVATION_SPLIT["guild"]),
        "pl": _percent_or(settings_obj, "reservation_pl_percent", RESERVATION_SPLIT["pl"]),
    }


def _cubby_percents(settings_obj: BillingSettings) -> dict[str, Decimal]:
    """Cubby split (#979) from BillingSettings, payee first, constant fallback if unset."""
    return {
        "cubby_payee": _percent_or(settings_obj, "cubby_guild_percent", CUBBY_SPLIT["cubby_payee"]),
        "pl": _percent_or(settings_obj, "cubby_pl_percent", CUBBY_SPLIT["pl"]),
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


def _effective_percents(
    percents: dict[str, Decimal], adjustment: TransactionAdjustment | None, order: tuple[str, ...]
) -> tuple[dict[str, Decimal], bool, list[str]]:
    """The split one line uses: an adjustment's override when it is sound, else ``percents``.

    Returns the percents, whether they are an override, and the notes the line carries.
    """
    if adjustment is None or adjustment.override_percents is None:
        return percents, False, []
    # The adjustment form guarantees a valid override, but the model is editable in
    # Django admin too. Defend the read path: a malformed override (missing key or a
    # split that does not sum to 100) falls back to the standard split with a note,
    # rather than raising and 500ing the whole Reconciliation tab.
    try:
        candidate = _override_percents(adjustment.override_percents, order)
        if sum(candidate.values(), Decimal("0")) != Decimal("100"):
            raise ValueError("override does not sum to 100")
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return percents, False, ["override ignored (malformed)"]
    return candidate, True, []


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
    stripe_payment_intent_id: str,
) -> TransactionLine:
    """Build one class, orientation or reservation line, rolling any unset recipient's share to Past Lives.

    ``producer_role`` is "instructor", "orientator" or "manager" (its percent key); ``guild_key``
    is the guild recipient or None. Unset producer/guild shares roll to PL with a note.
    """
    net_cents = gross_cents - refunded_cents
    effective, overridden, notes = _effective_percents(percents, adjustment, (producer_role, "guild", "pl"))
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
        stripe_payment_intent_id=stripe_payment_intent_id,
    )


def _class_lines(
    window: PanelWindow,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
) -> list[TransactionLine]:
    from classes.models import Registration

    registrations = (
        Registration.objects.without_demo_money()
        .filter(amount_paid_cents__gt=0)
        .filter(confirmed_at__gte=window.start_dt, confirmed_at__lt=window.end_dt)
        .select_related("class_offering__instructor", "class_offering__category__guild", "member")
        .prefetch_related("refunds")
    )
    return [_class_line(reg, percents, adjustments) for reg in registrations]


def _class_line(
    reg: Any,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
    refunded_cents: int | None = None,
) -> TransactionLine:
    """One paid registration's line. ``reg`` needs its offering, instructor, guild, member and refunds loaded."""
    offering = reg.class_offering
    instructor = offering.instructor
    guild = offering.category.guild
    guest_name = f"{reg.first_name} {reg.last_name}".strip()
    payer_name = reg.member.finance_name if reg.member is not None else (guest_name or reg.email)
    return _member_share_line(
        source_kind="class",
        source_pk=reg.pk,
        when=reg.confirmed_at,
        payer_name=payer_name,
        item=offering.title,
        gross_cents=reg.amount_paid_cents,
        refunded_cents=reg.amount_refunded_cents if refunded_cents is None else refunded_cents,
        producer_key=("instructor", instructor.id) if instructor is not None else None,
        producer_label=instructor.finance_name if instructor is not None else None,
        producer_role="instructor",
        guild_key=("guild", guild.id) if guild is not None else None,
        guild_label=guild.name if guild is not None else None,
        percents=percents,
        adjustment=adjustments.get(("class", reg.pk)),
        stripe_payment_intent_id=reg.stripe_payment_id,
    )


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
    return [_orientation_line(booking, percents, adjustments) for booking in bookings]


def _orientation_line(
    booking: Any,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
    refunded_cents: int | None = None,
) -> TransactionLine:
    """One paid orientation booking's line. ``booking`` needs its guild, member, orientor, type and refunds loaded."""
    orientator = booking.oriented_by
    return _member_share_line(
        source_kind="orientation",
        source_pk=booking.pk,
        when=booking.requested_at,
        payer_name=booking.member.finance_name,
        item=f"Orientation — {booking.orientation_type.owner_name}",
        gross_cents=booking.amount_paid_cents,
        refunded_cents=booking.amount_refunded_cents if refunded_cents is None else refunded_cents,
        producer_key=("orientator", orientator.id) if orientator is not None else None,
        producer_label=orientator.finance_name if orientator is not None else None,
        producer_role="orientator",
        guild_key=("guild", booking.guild.id) if booking.guild is not None else None,
        guild_label=booking.guild.name if booking.guild is not None else None,
        percents=percents,
        adjustment=adjustments.get(("orientation", booking.pk)),
        stripe_payment_intent_id=booking.stripe_payment_id,
    )


def _reservation_lines(
    window: PanelWindow,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
) -> list[TransactionLine]:
    """Paid equipment reservations (#749) made in the window; the hold's creation stands in for paid at.

    An unpaid hold (``PENDING_PAYMENT``) has moved no money. A declined or cancelled one did,
    and shows with its refund netted, like a cancelled orientation booking.
    """
    from membership.models import EquipmentReservation

    reservations = (
        EquipmentReservation.objects.filter(amount_paid_cents__gt=0)
        .exclude(status=EquipmentReservation.Status.PENDING_PAYMENT)
        .filter(created_at__gte=window.start_dt, created_at__lt=window.end_dt)
        .select_related("equipment__payee", "equipment__guild", "member", "payout__payee")
        .prefetch_related("refunds")
    )
    return [_reservation_line(reservation, percents, adjustments) for reservation in reservations]


def _reservation_line(
    reservation: Any,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
    refunded_cents: int | None = None,
) -> TransactionLine:
    """One paid reservation's line. ``reservation`` needs its equipment (payee, guild), payout, member and refunds loaded.

    The producer is :attr:`EquipmentReservation.share_payee`: the payee its ``Payout`` recorded,
    else the item's current pick. Nobody picked rolls the share to Past Lives with the
    "manager unset" note.
    """
    equipment = reservation.equipment
    payee = reservation.share_payee
    guild = equipment.guild
    return _member_share_line(
        source_kind="reservation",
        source_pk=reservation.pk,
        when=reservation.created_at,
        payer_name=reservation.member.finance_name,
        item=f"Reservation, {equipment.name}",
        gross_cents=reservation.amount_paid_cents,
        refunded_cents=reservation.amount_refunded_cents if refunded_cents is None else refunded_cents,
        producer_key=("manager", payee.id) if payee is not None else None,
        producer_label=payee.finance_name if payee is not None else None,
        producer_role="manager",
        guild_key=("guild", guild.id) if guild is not None else None,
        guild_label=guild.name if guild is not None else None,
        percents=percents,
        adjustment=adjustments.get(("reservation", reservation.pk)),
        stripe_payment_intent_id=reservation.stripe_payment_id,
    )


def _cubby_lines(
    window: PanelWindow,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
) -> list[TransactionLine]:
    """Paid cubby invoices (#979) in the window, one line per payment, filed under when Stripe took it."""
    from membership.models import CubbyPayment

    payments = CubbyPayment.objects.filter(paid_at__gte=window.start_dt, paid_at__lt=window.end_dt).select_related(
        "rental__cubby__unit__guild", "rental__member", "payee"
    )
    return [_cubby_line(payment, percents, adjustments) for payment in payments]


def _cubby_line(
    payment: Any,
    percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
    refunded_cents: int | None = None,
) -> TransactionLine:
    """One paid cubby invoice's line: the guild's share to its frozen payee, the rest to Past Lives.

    ``payment`` needs its rental (cubby, unit, guild, member) and payee loaded. With no payee the
    whole payment stays with Past Lives, noted like an unset producer. A refunded payment (a cubby
    the member never got) nets to nothing and owes nobody.
    """
    cubby = payment.rental.cubby
    payee = payment.payee
    gross_cents = payment.amount_cents
    refunded = payment.refunded_cents if refunded_cents is None else refunded_cents
    net_cents = gross_cents - refunded
    adjustment = adjustments.get(("cubby", payment.pk))
    effective, overridden, notes = _effective_percents(percents, adjustment, ("cubby_payee", "pl"))
    shares_by_role = split_cents(net_cents, effective)
    pl_key: RecipientKey = ("pl", None)
    shares: dict[RecipientKey, int] = {pl_key: shares_by_role["pl"]}
    labels: dict[RecipientKey, str] = {pl_key: PL_LABEL}
    unassigned = False
    if payee is not None:
        shares[("cubby_payee", payee.id)] = shares_by_role["cubby_payee"]
        labels[("cubby_payee", payee.id)] = payee.finance_name
    else:
        shares[pl_key] += shares_by_role["cubby_payee"]
        if net_cents:
            notes.append("cubby payee unset -> Past Lives")
            unassigned = True
    return TransactionLine(
        source_kind="cubby",
        source_pk=payment.pk,
        date=payment.paid_at,
        payer_name=payment.rental.member.finance_name,
        item=f"Cubby {cubby.label}, {cubby.unit.guild.name}",
        gross_cents=gross_cents,
        refunded_cents=refunded,
        net_cents=net_cents,
        shares=shares,
        labels=labels,
        omitted=bool(adjustment is not None and adjustment.is_omitted),
        overridden=overridden,
        unassigned=unassigned,
        note="; ".join(notes),
        stripe_payment_intent_id=payment.stripe_payment_intent_id,
    )


class ShareSource:
    """Producer shares for single payments: the one share calculation the payouts code uses (#662).

    Loads the split percents and the adjustments once, then answers per registration,
    booking or reservation with the same ``_member_share_line`` the Reconciliation tab sums.
    An omitted payment, or one whose producer is unset, earns its producer nothing.
    """

    def __init__(self) -> None:
        from billing.models import BillingSettings, TransactionAdjustment

        settings_obj = BillingSettings.load()
        self._class_percents = _class_percents(settings_obj)
        self._orientation_percents = _orientation_percents(settings_obj)
        self._reservation_percents = _reservation_percents(settings_obj)
        self._cubby_percents = _cubby_percents(settings_obj)
        self._adjustments = TransactionAdjustment.objects.as_map()

    def for_registration(self, reg: Any, refunded_cents: int | None = None) -> int:
        """The instructor's share of ``reg`` in cents, collected less refunds so far (or less ``refunded_cents``)."""
        line = _class_line(reg, self._class_percents, self._adjustments, refunded_cents)
        instructor = reg.class_offering.instructor
        if line.omitted or instructor is None:
            return 0
        return line.shares[("instructor", instructor.id)]

    def for_booking(self, booking: Any, refunded_cents: int | None = None) -> int:
        """The orientor's share of ``booking`` in cents, collected less refunds so far (or less ``refunded_cents``)."""
        line = _orientation_line(booking, self._orientation_percents, self._adjustments, refunded_cents)
        if line.omitted or booking.oriented_by is None:
            return 0
        return line.shares[("orientator", booking.oriented_by.id)]

    def for_reservation(self, reservation: Any, refunded_cents: int | None = None) -> int:
        """The share of ``reservation`` (#749) for its ``share_payee``, in cents, collected less refunds so far."""
        line = _reservation_line(reservation, self._reservation_percents, self._adjustments, refunded_cents)
        payee = reservation.share_payee
        if line.omitted or payee is None:
            return 0
        return line.shares[("manager", payee.id)]

    def for_cubby_payment(self, payment: Any, refunded_cents: int | None = None) -> int:
        """The guild's share of a paid cubby invoice (#979) for its frozen payee, in cents; 0 with no payee."""
        line = _cubby_line(payment, self._cubby_percents, self._adjustments, refunded_cents)
        payee = payment.payee
        if line.omitted or payee is None:
            return 0
        return line.shares[("cubby_payee", payee.id)]

    def for_source(self, kind: str, source: Any, refunded_cents: int | None = None) -> int:
        """The producer's share of one payment by its source kind: ``"class"``, ``"orientation"``,
        ``"reservation"`` or ``"cubby"``."""
        share = {
            "class": self.for_registration,
            "orientation": self.for_booking,
            "reservation": self.for_reservation,
            "cubby": self.for_cubby_payment,
        }[kind]
        return share(source, refunded_cents)


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
                "payer_name": charge.tab.member.finance_name,
                "shares": {},
                "labels": {},
                "gross": 0,
                "payment_intent": charge.stripe_payment_intent_id,
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
                stripe_payment_intent_id=bucket["payment_intent"],
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

    # A snapshot still being taken has no groups yet (``ReconciliationSnapshot.take`` freezes the
    # ledger before it reads the results), so it must not stand in for the projection.
    snapshot = (
        ReconciliationSnapshot.objects.filter(
            period_start=window.start, period_end=window.end, results__has_key="groups"
        )
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
    now: datetime | None = None,
) -> ReconciliationResult:
    """Attribute every payment in ``window`` to its recipients and aggregate.

    ``adjustments`` defaults to the full ``TransactionAdjustment.objects.as_map()`` —
    pass ``{}`` to force the standard splits. Voting allocation (guild rows) is projected
    live unless a snapshot already froze the window. ``now`` (default: the current time)
    decides which shares have fallen due (#985).
    """
    from billing.models import BillingSettings, TransactionAdjustment

    if adjustments is None:
        adjustments = TransactionAdjustment.objects.as_map()
    now = timezone.now() if now is None else now

    settings_obj = BillingSettings.load()
    class_percents = _class_percents(settings_obj)
    orientation_percents = _orientation_percents(settings_obj)
    reservation_percents = _reservation_percents(settings_obj)
    cubby_percents = _cubby_percents(settings_obj)

    lines: list[TransactionLine] = []
    lines.extend(_tab_lines(window, adjustments))
    lines.extend(_class_lines(window, class_percents, adjustments))
    lines.extend(_orientation_lines(window, orientation_percents, adjustments))
    lines.extend(_reservation_lines(window, reservation_percents, adjustments))
    lines.extend(_cubby_lines(window, cubby_percents, adjustments))
    lines.sort(key=lambda line: line.date, reverse=True)
    lines = attach_stripe_fees(lines)

    from billing.payouts import kept_share_notes

    kept = kept_share_notes(lines)
    if kept:
        lines = [
            replace(
                line,
                payee_kept_share=True,
                note="; ".join(filter(None, [line.note, kept[(line.source_kind, line.source_pk)]])),
            )
            if (line.source_kind, line.source_pk) in kept
            else line
            for line in lines
        ]
    earlier = _due_earlier_lines(
        window, now, class_percents, orientation_percents, reservation_percents, cubby_percents, adjustments
    )
    attached, accounts = _attach_payouts(lines + earlier, now)
    lines = [_in_window(line, window) for line in attached[: len(lines)]]
    due_earlier = [line for line in attached[len(lines) :] if _owed_for(line, window)]
    aggregate = _aggregate_lines(lines, due_earlier, window)

    voting_by_guild: dict[int, int] = {}
    voting_projected = True
    voting_on: date | None = None
    if include_voting:
        voting_by_guild, voting_projected, voting_on = resolve_voting_allocation(window)

    groups = _build_groups(aggregate, voting_by_guild, voting_projected, include_voting=include_voting)
    _badge_producers(groups, accounts, payouts_switched_on=settings_obj.connect_enabled)

    return ReconciliationResult(
        window=window,
        groups=groups,
        lines=lines,
        grand_total_cents=aggregate.grand_total_cents,
        unassigned_note_count=aggregate.unassigned_note_count,
        omitted_count=aggregate.omitted_count,
        class_percents=class_percents,
        orientation_percents=orientation_percents,
        reservation_percents=reservation_percents,
        voting_projected=voting_projected,
        voting_snapshotted_on=voting_on,
        cubby_percents=cubby_percents,
        due_earlier_lines=due_earlier,
    )


def _due_earlier_lines(
    window: PanelWindow,
    now: datetime,
    class_percents: dict[str, Decimal],
    orientation_percents: dict[str, Decimal],
    reservation_percents: dict[str, Decimal],
    cubby_percents: dict[str, Decimal],
    adjustments: dict[AdjustmentKey, TransactionAdjustment],
) -> list[TransactionLine]:
    """Class, orientation, reservation and cubby lines paid before ``window`` whose share may fall due inside it (#985).

    The queries narrow by the recorded ``Payout.due_at``, else the base ``run_payouts`` uses
    (``fell_due_between``), or by a give up or a snapshot that placed the share in this window;
    the test that counts is the line's own ``LinePayout.owed_at`` once its state attaches
    (``_owed_for``). A payment inside a period a
    snapshot froze before #985 is left out: that snapshot already counted its share as owed.
    Seven queries for any number of lines.
    """
    from datetime import timedelta

    from django.db.models import F, Min, Q
    from django.db.models.functions import Greatest

    from billing.models import ReconciliationSnapshot
    from billing.payouts import fell_due_between, in_snapshot
    from classes.models import Registration
    from membership.models import CubbyPayment, EquipmentReservation, OrientationBooking

    # Fell due in the window, became owed by hand in it after a give up, or counted by its snapshot.
    due = (
        fell_due_between(window.start_dt, min(window.end_dt, now + timedelta(microseconds=1)))
        | Q(payout__owed_since__gte=window.start_dt, payout__owed_since__lt=window.end_dt)
        | Q(payout__counted_owed_in__period_end__gte=window.start, payout__counted_owed_in__period_end__lte=window.end)
    )
    registrations = (
        Registration.objects.filter(amount_paid_cents__gt=0, confirmed_at__lt=window.start_dt)
        .annotate(due_base=Greatest(Min("class_offering__sessions__starts_at"), "confirmed_at"))
        .filter(due)
        .select_related("class_offering__instructor", "class_offering__category__guild", "member")
        .prefetch_related("refunds")
    )
    bookings = (
        OrientationBooking.objects.filter(amount_paid_cents__gt=0, requested_at__lt=window.start_dt)
        .exclude(status=OrientationBooking.Status.PENDING_PAYMENT)
        .annotate(due_base=Greatest("slot__starts_at", "requested_at"))
        .filter(due)
        .select_related("guild", "member", "oriented_by", "orientation_type__guild", "orientation_type__equipment")
        .prefetch_related("refunds")
    )
    reservations = (
        EquipmentReservation.objects.filter(amount_paid_cents__gt=0, created_at__lt=window.start_dt)
        .exclude(status=EquipmentReservation.Status.PENDING_PAYMENT)
        .annotate(due_base=Greatest("starts_at", "created_at"))
        .filter(due)
        .select_related("equipment__payee", "equipment__guild", "member", "payout__payee")
        .prefetch_related("refunds")
    )
    # Nothing is taught: a cubby share falls due 48 hours after payment (#979).
    cubby_payments = (
        CubbyPayment.objects.filter(paid_at__lt=window.start_dt)
        .annotate(due_base=F("paid_at"))
        .filter(due)
        .select_related("rental__cubby__unit__guild", "rental__member", "payee")
    )
    lines = [
        *(_class_line(reg, class_percents, adjustments) for reg in registrations),
        *(_orientation_line(booking, orientation_percents, adjustments) for booking in bookings),
        *(_reservation_line(reservation, reservation_percents, adjustments) for reservation in reservations),
        *(_cubby_line(payment, cubby_percents, adjustments) for payment in cubby_payments),
    ]
    if not lines:
        return []
    counted_before = list(
        ReconciliationSnapshot.objects.exclude(results__has_key=SPLIT_BY_DUE_KEY).values_list(
            "period_start", "period_end"
        )
    )
    return [line for line in lines if not in_snapshot(line.date, counted_before)]


def _in_window(line: TransactionLine, window: PanelWindow) -> TransactionLine:
    """``line`` with its payout state as ``window`` shows it (``LinePayout.for_window``)."""
    if line.payout is None:
        return line
    return replace(line, payout=line.payout.for_window(window.start_dt, window.end_dt))


def _owed_for(line: TransactionLine, window: PanelWindow) -> bool:
    """True when ``line``'s producer share counts in ``window``'s Owed now (``LinePayout.owed_in``)."""
    return line.payout is not None and line.payout.owed_in(window.start_dt, window.end_dt)


def owed_lines(result: ReconciliationResult) -> list[TransactionLine]:
    """Every line whose producer share ``result`` counts in Owed now: paid in its window, or paid earlier."""
    return [line for line in result.lines if _owed_for(line, result.window)] + result.due_earlier_lines


def _split_by_weight(total: int, weights: list[int]) -> list[int]:
    """``total`` split across ``weights`` in proportion; the last part takes the rounding so the parts sum exactly."""
    whole = sum(weights)
    parts = [total * weight // whole for weight in weights[:-1]]
    return [*parts, total - sum(parts)]


def attach_stripe_fees(lines: list[TransactionLine]) -> list[TransactionLine]:
    """``lines`` with Stripe's fee and net from each payment's :class:`StripeFeeRecord` (#984), in one query.

    A line with no PaymentIntent, no record or an unread record keeps None (unknown). Lines
    that share one PaymentIntent split its fee and net by their gross, so no fee is counted twice.
    """
    from billing.models import StripeFeeRecord

    indexes_by_pi: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        if line.stripe_payment_intent_id:
            indexes_by_pi.setdefault(line.stripe_payment_intent_id, []).append(index)
    records = StripeFeeRecord.objects.by_payment_intent(list(indexes_by_pi))
    attached = list(lines)
    for payment_intent_id, indexes in indexes_by_pi.items():
        if payment_intent_id not in records:
            continue
        record = records[payment_intent_id]
        fee = record.fee_after_refunds_cents
        net = record.net_after_refunds_cents
        if fee is None or net is None:
            continue
        weights = [max(lines[index].gross_cents, 1) for index in indexes]
        for index, fee_part, net_part in zip(
            indexes, _split_by_weight(fee, weights), _split_by_weight(net, weights), strict=True
        ):
            attached[index] = replace(attached[index], stripe_fee_cents=fee_part, stripe_net_cents=net_part)
    return attached


def _attach_payouts(
    lines: list[TransactionLine], now: datetime
) -> tuple[list[TransactionLine], dict[int, PayoutAccount]]:
    """``lines`` with their payout states at ``now`` (#905, #985), and the producers' accounts.

    The one path from lines to what Transaction Detail shows, shared by ``build_reconciliation``
    and ``registration_payouts``. One query for every producer's account: producer keys always
    carry a Member pk.
    """
    from billing.payouts import line_payouts, producer_accounts

    accounts = producer_accounts(
        cast(int, key[1]) for line in lines for key in line.shares if key[0] in _PRODUCER_KINDS
    )
    payouts_by_line = line_payouts(lines, accounts, now)
    return [_with_payout(line, payouts_by_line) for line in lines], accounts


def registration_payouts(registration_pks: Iterable[int]) -> dict[int, LinePayout]:
    """What Transaction Detail shows for each registration's instructor share, by registration pk (#910).

    Each paid, confirmed registration gets the same line Reconciliation builds for it (split,
    refunds, adjustments) and goes through the same ``_attach_payouts``, so the two always agree.
    A registration with no state (unpaid, free, not confirmed, omitted, or no instructor share)
    is left out. A fixed number of queries for any number of registrations.
    """
    from billing.models import BillingSettings, TransactionAdjustment
    from classes.models import Registration

    pks = list(registration_pks)
    registrations = (
        Registration.objects.filter(pk__in=pks, amount_paid_cents__gt=0)
        .filter(confirmed_at__isnull=False)
        .select_related("class_offering__instructor", "class_offering__category__guild", "member")
        .prefetch_related("refunds")
    )
    percents = _class_percents(BillingSettings.load())
    adjustments = TransactionAdjustment.objects.filter(source_kind="class", source_pk__in=pks).as_map()
    lines, _accounts = _attach_payouts(
        [_class_line(reg, percents, adjustments) for reg in registrations], timezone.now()
    )
    return {line.source_pk: line.payout for line in lines if line.payout is not None}


def _with_payout(line: TransactionLine, payouts_by_line: dict[AdjustmentKey, LinePayout]) -> TransactionLine:
    """``line`` with its producer share's payout state (#905), the same state that split it into the columns.

    A line with no earning but a producer share still to pay (a class with no sessions, say)
    has not happened, so it is Not yet due with no date (#985); an omitted line, or one with no
    producer share left, gets no state.
    """
    from billing.payouts import NOT_DUE_UNDATED

    state = payouts_by_line.get((line.source_kind, line.source_pk))
    if state is None and not line.omitted:
        if any(cents for key, cents in line.shares.items() if key[0] in _PRODUCER_KINDS):
            state = NOT_DUE_UNDATED
    return line if state is None else replace(line, payout=state)


def _badge_producers(
    groups: dict[RecipientKind, list[RecipientAllocation]],
    accounts: dict[int, PayoutAccount],
    *,
    payouts_switched_on: bool,
) -> None:
    """Give every producer row its payee's automatic payouts badge (#905) from the accounts already loaded.

    While payouts are switched off site wide (``BillingSettings.connect_enabled``, the switch
    ``payouts.payouts_on`` reads) Stripe starts nothing new, so the account's status no longer
    says who Stripe pays. A row with nothing in Sent through Stripe reads Paid by hand, as its
    lines and Owed now do. A row that still has shares recorded through Stripe (sent,
    pending or taken back before the switch went off) reads Payouts switched off instead, since
    part of it is not the treasurer's to pay.
    """
    from billing.payouts import PayoutBadge, account_badge

    switched_off = PayoutBadge("Payouts switched off", "warn")
    for kind in GROUP_ORDER:
        if not kind.is_producer:
            continue
        for alloc in groups[kind]:
            if payouts_switched_on:
                alloc.payout_badge = account_badge(accounts.get(cast(int, alloc.recipient_id)))
            elif alloc.stripe_cents > 0:
                alloc.payout_badge = switched_off
            else:
                alloc.payout_badge = account_badge(None)


@dataclass
class _ProducerSplit:
    """Producer shares by how they are paid (#662, #985), keyed by recipient."""

    stripe: dict[RecipientKey, int] = field(default_factory=dict)
    owed_now: dict[RecipientKey, int] = field(default_factory=dict)
    not_due: dict[RecipientKey, int] = field(default_factory=dict)
    next_due: dict[RecipientKey, datetime] = field(default_factory=dict)
    retrying: dict[RecipientKey, int] = field(default_factory=dict)

    def add_paid(self, line: TransactionLine, window: PanelWindow) -> None:
        """Count a line paid in ``window`` by its payout state; Owed now only if it also fell due in ``window``."""
        payout = line.payout
        if payout is None:
            return
        for key, cents in _producer_shares(line):
            if payout.through_stripe:
                self.stripe[key] = self.stripe.get(key, 0) + cents
            elif payout.not_due:
                self.not_due[key] = self.not_due.get(key, 0) + cents
                if payout.due_at is not None and (key not in self.next_due or payout.due_at < self.next_due[key]):
                    self.next_due[key] = payout.due_at
            elif payout.retrying:
                self.retrying[key] = self.retrying.get(key, 0) + cents
            elif _owed_for(line, window):
                self.owed_now[key] = self.owed_now.get(key, 0) + cents

    def add_due_earlier(self, line: TransactionLine) -> None:
        """Count a line paid before the window that fell due in it, to Owed now."""
        for key, cents in _producer_shares(line):
            self.owed_now[key] = self.owed_now.get(key, 0) + cents


@dataclass(frozen=True)
class _Aggregate:
    totals: dict[RecipientKey, int]
    counts: dict[RecipientKey, int]
    labels: dict[RecipientKey, str]
    grand_total_cents: int
    unassigned_note_count: int
    omitted_count: int
    split: _ProducerSplit


def _aggregate_lines(
    lines: list[TransactionLine], due_earlier: list[TransactionLine], window: PanelWindow
) -> _Aggregate:
    """Sum non-omitted lines' shares per recipient; tally grand total, notes, and omissions.

    Each line's producer share also goes by its own payout state: Sent through Stripe (#662),
    Not yet due, Stripe retrying, or Owed now when it fell due inside ``window`` (#985). ``due_earlier`` are
    lines paid before the window that fell due in it: they add to Owed now only, never to the
    totals, which group by payment date. Guild and Past Lives rows ignore all of it.
    """
    split = _ProducerSplit()
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
        split.add_paid(line, window)
    for line in due_earlier:
        split.add_due_earlier(line)
        labels.update(line.labels)
    return _Aggregate(totals, counts, labels, grand_total, unassigned_note_count, omitted_count, split)


def _producer_shares(line: TransactionLine) -> list[tuple[RecipientKey, int]]:
    """``line``'s producer share as (key, cents): at most one, an instructor, orientor or manager."""
    return [(key, cents) for key, cents in line.shares.items() if key[0] in _PRODUCER_KINDS]


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
    split = aggregate.split
    for key in aggregate.totals.keys() | split.owed_now.keys():
        total = aggregate.totals.get(key, 0)
        owed_now = split.owed_now.get(key, 0)
        # Drop only exact-zero recipients. A negative total (an over refund past the
        # payment) must still show so the per recipient rows keep summing to the grand
        # total; hiding it would silently break conservation of money. A producer paid
        # nothing in the window still shows when a share paid earlier fell due in it (#985).
        if total == 0 and owed_now == 0:
            continue
        kind = RecipientKind(key[0])
        recipient_id = key[1]
        alloc = RecipientAllocation(
            kind=kind,
            recipient_id=recipient_id,
            label=aggregate.labels[key],
            total_cents=total,
            transaction_count=aggregate.counts.get(key, 0),
            stripe_cents=split.stripe.get(key, 0),
            owed_now_cents=owed_now,
            not_due_cents=split.not_due.get(key, 0),
            next_due_at=split.next_due.get(key),
            retrying_cents=split.retrying.get(key, 0),
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
            # Snapshots taken before #662 carry no split: all of it was owed by hand.
            stripe_cents = int(row.get("stripe_cents", 0))
            alloc = RecipientAllocation(
                kind=kind,
                recipient_id=row.get("recipient_id"),
                label=row["label"],
                total_cents=int(row["total_cents"]),
                transaction_count=int(row["transaction_count"]),
                voting_cents=int(row.get("voting_cents", 0)),
                voting_projected=False,
                stripe_cents=stripe_cents,
            )
            if "owed_now_cents" in row:
                alloc.owed_now_cents = int(row["owed_now_cents"])
                alloc.not_due_cents = int(row["not_due_cents"])
                alloc.retrying_cents = int(row["retrying_cents"])
                next_due = row["next_due_at"]
                alloc.next_due_at = None if next_due is None else datetime.fromisoformat(next_due)
            elif kind.is_producer:
                # Taken before #985: everything not sent through Stripe was counted as owed.
                alloc.owed_now_cents = alloc.total_cents - stripe_cents
            groups[kind].append(alloc)
    class_percents = {k: Decimal(v) for k, v in data.get("class_percents", {}).items()}
    orientation_percents = {k: Decimal(v) for k, v in data.get("orientation_percents", {}).items()}
    # Snapshots taken before #749 carry no reservation split: there were no paid reservations.
    reservation_percents = {k: Decimal(v) for k, v in data.get("reservation_percents", {}).items()}
    # Snapshots taken before #979 carry no cubby split: no cubby was paid for before it.
    cubby_percents = {k: Decimal(v) for k, v in data.get("cubby_percents", {}).items()}
    return ReconciliationResult(
        window=window,
        groups=groups,
        lines=[],
        grand_total_cents=int(data.get("grand_total_cents", snapshot.grand_total_cents)),
        unassigned_note_count=int(data.get("unassigned_note_count", 0)),
        omitted_count=int(data.get("omitted_count", 0)),
        class_percents=class_percents,
        orientation_percents=orientation_percents,
        reservation_percents=reservation_percents,
        voting_projected=False,
        voting_snapshotted_on=timezone.localtime(snapshot.taken_at).date(),
        is_snapshot=True,
        cubby_percents=cubby_percents,
    )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


class _Echo:
    """File-like object whose write() returns the value — the streaming CSV trick."""

    def write(self, value: str) -> str:
        return value


CSV_HEADERS = [
    "Group",
    "Recipient",
    "Transactions",
    "Amount",
    "Voting",
    "Total",
    "Sent through Stripe",
    "Owed now",
    "Not yet due",
    "Next due",
    "Stripe retrying",
]


def _csv_lines(result: ReconciliationResult, writer: Any) -> Iterator[str]:
    yield writer.writerow(CSV_HEADERS)
    for kind, heading, rows in result.ordered_groups():
        for alloc in rows:
            voting = f"{alloc.voting_cents / 100:.2f}" if kind is RecipientKind.GUILD else ""
            producer = kind.is_producer
            yield writer.writerow(
                [
                    heading,
                    alloc.label,
                    str(alloc.transaction_count),
                    f"{alloc.total_cents / 100:.2f}",
                    voting,
                    f"{alloc.combined_cents / 100:.2f}",
                    f"{alloc.stripe_cents / 100:.2f}" if producer else "",
                    f"{alloc.owed_now_cents / 100:.2f}" if producer else "",
                    f"{alloc.not_due_cents / 100:.2f}" if producer else "",
                    _iso_day(alloc.next_due_at),
                    f"{alloc.retrying_cents / 100:.2f}" if producer else "",
                ]
            )


def _iso_day(when: datetime | None) -> str:
    """``when`` as its Pacific day, "2026-11-05", or an empty cell."""
    return "" if when is None else timezone.localtime(when).date().isoformat()


def stream_reconciliation_csv(result: ReconciliationResult) -> StreamingHttpResponse:
    """Streaming CSV of the per-recipient allocation, one row per recipient."""
    writer = csv.writer(_Echo())
    response = StreamingHttpResponse(_csv_lines(result, writer), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="plfog-reconciliation-{stamp}.csv"'
    return response

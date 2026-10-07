"""Instructor and orientor payouts through Stripe Connect Express (#662).

Setup (part 1): who is a payee, which state their Payouts tab shows, and whether a nudge
points them at it. Sending (part 2): every paid registration or orientation booking is an
``Earning`` for its producer; ``run_payouts`` turns each one into a ``Payout`` row when it falls
due and sends it through Stripe or records it as owed at month end.

``BillingSettings.connect_enabled`` is the one switch; while it is off nothing here shows and
nothing is sent. No charge path reads that switch.

Whether a share goes through Stripe is decided once, from facts fixed when it was paid for
(``goes_through_stripe``), and the send job, the Payouts tab and the Reconciliation split all
read that same rule. That is what keeps a month-end snapshot from listing as owed by hand a
share Stripe later sends.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models import Min, QuerySet
from django.db.models.functions import Greatest
from django.utils import timezone

from billing.models import BillingSettings, Payout, PayoutAccount, ReconciliationSnapshot

if TYPE_CHECKING:
    from django.http import HttpRequest

    from billing.reconciliation import TransactionLine
    from membership.models import Member

PAYOUT_DELAY = timedelta(hours=48)
"""A share falls due this long after its class's first session or slot starts, or after payment if later."""

EARNINGS_LOOKBACK = timedelta(days=60)
"""How far back the Payouts tab lists earnings, by when they were taught."""

_THROUGH_STRIPE_STATUSES = frozenset({Payout.Status.PENDING, Payout.Status.SENT, Payout.Status.TAKEN_BACK})


def payouts_on() -> bool:
    """True when an admin has turned payouts on in Payments, Stripe tab."""
    return BillingSettings.load().connect_enabled


def has_earning(member: Member) -> bool:
    """True when anyone has paid for a class ``member`` teaches or an orientation they ran."""
    from classes.models import Registration
    from membership.models import OrientationBooking

    return (
        Registration.objects.filter(class_offering__instructor=member, amount_paid_cents__gt=0).exists()
        or OrientationBooking.objects.filter(oriented_by=member, amount_paid_cents__gt=0).exists()
    )


def is_payee(request: HttpRequest, member: Member) -> bool:
    """Whether ``member`` gets a Payouts tab: they can teach classes, can run orientations, or have an earning."""
    from membership.permissions import manages_orientations

    return member.can_create_classes or manages_orientations(request) or has_earning(member)


@dataclass(frozen=True)
class PayoutsTab:
    """What the Settings, Payouts tab renders: the payee's account in the current mode, and their earnings."""

    account: PayoutAccount | None
    earnings: EarningsList

    @property
    def state(self) -> str:
        """``not_set_up`` or one of ``PayoutAccount.Status``'s values, the key the template branches on."""
        return "not_set_up" if self.account is None else self.account.status


def settings_tab(request: HttpRequest, member: Member | None) -> PayoutsTab | None:
    """The Payouts tab for this request, or None when it should not show at all."""
    if member is None or not payouts_on() or not is_payee(request, member):
        return None
    return PayoutsTab(account=PayoutAccount.for_member(member), earnings=payee_earnings(member))


def needs_nudge(member: Member) -> bool:
    """True while payouts are on and ``member`` has not finished Stripe signup.

    The caller decides the audience (the teaching portal is for teachers, the orientations
    nudge for members who run orientations); this only answers "connected yet?".
    """
    if not payouts_on():
        return False
    account = PayoutAccount.for_member(member)
    return account is None or not account.is_connected


# ---------------------------------------------------------------------------
# Earnings: one producer share of one paid registration or orientation booking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Earning:
    """One producer's share of one payment, with the facts that decide when and how it is paid."""

    kind: str  # "class" | "orientation", the reconciliation source_kind
    source: Any  # the Registration or OrientationBooking
    payee: Member
    item: str
    payer: str
    taught_at: datetime
    paid_at: datetime
    share_cents: int

    @property
    def due_at(self) -> datetime:
        """48 hours after the class or slot starts, or after payment if that came later."""
        return max(self.taught_at, self.paid_at) + PAYOUT_DELAY

    @property
    def payout(self) -> Payout | None:
        """The ledger row once the share fell due and the send job saw it."""
        try:
            return self.source.payout
        except Payout.DoesNotExist:
            return None

    @property
    def paid_through_stripe(self) -> bool:
        """True when plfog's Stripe took the payment; legacy imports and comps have no PaymentIntent."""
        return self.source.stripe_payment_id.startswith("pi_")


def goes_through_stripe(earning: Earning, account: PayoutAccount | None, payouts_since: datetime | None) -> bool:
    """The one rule for whether a share is sent through Stripe rather than owed at month end.

    Payouts must be on (``payouts_since`` set) and on before the share fell due, the payment
    must have gone through plfog's Stripe, and the payee's account must have been on before the
    payment: a share paid for before they connected stays owed by hand.
    """
    return (
        payouts_since is not None
        and earning.due_at >= payouts_since
        and earning.paid_through_stripe
        and account is not None
        and account.active_since is not None
        and account.active_since <= earning.paid_at
    )


def _registrations() -> QuerySet[Any]:
    from classes.models import Registration

    return (
        Registration.objects.filter(
            amount_paid_cents__gt=0, confirmed_at__isnull=False, class_offering__instructor__isnull=False
        )
        .annotate(first_session_at=Min("class_offering__sessions__starts_at"))
        .filter(first_session_at__isnull=False)
        .select_related("class_offering__instructor", "class_offering__category__guild", "member", "payout")
        .prefetch_related("refunds")
    )


def _bookings() -> QuerySet[Any]:
    from membership.models import OrientationBooking

    return (
        OrientationBooking.objects.filter(amount_paid_cents__gt=0, oriented_by__isnull=False)
        .exclude(status=OrientationBooking.Status.PENDING_PAYMENT)
        .select_related(
            "slot",
            "guild",
            "member",
            "oriented_by",
            "orientation_type__guild",
            "orientation_type__equipment",
            "payout",
        )
        .prefetch_related("refunds")
    )


def _earnings(registrations: Iterable[Any], bookings: Iterable[Any]) -> list[Earning]:
    """Earnings with a positive share, built in a fixed number of queries."""
    from billing.reconciliation import ShareSource

    shares = ShareSource()
    earnings: list[Earning] = []
    for reg in registrations:
        guest = f"{reg.first_name} {reg.last_name}".strip()
        earnings.append(
            Earning(
                kind="class",
                source=reg,
                payee=reg.class_offering.instructor,
                item=reg.class_offering.title,
                payer=reg.member.display_name if reg.member is not None else (guest or reg.email),
                taught_at=reg.first_session_at,
                paid_at=reg.confirmed_at,
                share_cents=shares.for_registration(reg),
            )
        )
    for booking in bookings:
        earnings.append(
            Earning(
                kind="orientation",
                source=booking,
                payee=booking.oriented_by,
                item=f"{booking.orientation_type.owner_name} orientation",
                payer=booking.member.display_name,
                taught_at=booking.slot.starts_at,
                paid_at=booking.requested_at,
                share_cents=shares.for_booking(booking),
            )
        )
    return [earning for earning in earnings if earning.share_cents > 0]


def _accounts(earnings: Iterable[Earning]) -> dict[int, PayoutAccount]:
    member_ids = {earning.payee.pk for earning in earnings}
    return {a.member_id: a for a in PayoutAccount.objects.in_active_mode().filter(member_id__in=member_ids)}


def _payouts_since() -> datetime | None:
    settings_obj = BillingSettings.load()
    return settings_obj.payouts_on_since if settings_obj.connect_enabled else None


# ---------------------------------------------------------------------------
# The send run (``send_payouts``, every 15 minutes)
# ---------------------------------------------------------------------------


@dataclass
class PayoutRun:
    """What one run did, for the command's output."""

    created: int = 0
    sent: int = 0
    failed: int = 0
    gave_up: int = 0


def run_payouts(now: datetime | None = None) -> PayoutRun:
    """Record every share that fell due since payouts went on, then send what Stripe should send.

    Does nothing while payouts are off. A share is recorded once (a ``Payout`` row per
    registration or booking), so a rerun or an overlapping run cannot send it twice.
    """
    now = now or timezone.now()
    run = PayoutRun()
    since = _payouts_since()
    if since is None:
        return run
    window = {"due_base__lte": now - PAYOUT_DELAY, "due_base__gte": since - PAYOUT_DELAY}
    due = _earnings(
        _registrations()
        .filter(payout__isnull=True)
        .annotate(due_base=Greatest("first_session_at", "confirmed_at"))
        .filter(**window),
        _bookings()
        .filter(payout__isnull=True)
        .annotate(due_base=Greatest("slot__starts_at", "requested_at"))
        .filter(**window),
    )
    accounts = _accounts(due)
    for earning in due:
        if _record(earning, accounts.get(earning.payee.pk), since):
            run.created += 1
    snapshots = list(ReconciliationSnapshot.objects.values_list("period_start", "period_end"))
    for pk in list(Payout.objects.to_send(now).values_list("pk", flat=True)):
        _send_one(pk, now, snapshots, run)
    return run


def _record(earning: Earning, account: PayoutAccount | None, since: datetime) -> bool:
    """Make the earning's ledger row: PENDING to send, or OWED_MANUALLY with the reason."""
    from billing.notifications import send_payouts_invite

    through = goes_through_stripe(earning, account, since)
    if through:
        reason = Payout.OwedReason.NOT_APPLICABLE
    elif not earning.paid_through_stripe:
        reason = Payout.OwedReason.NOT_THROUGH_STRIPE
    else:
        reason = Payout.OwedReason.NOT_CONNECTED
    source_field = "registration" if earning.kind == "class" else "orientation_booking"
    payout, created = Payout.objects.get_or_create(
        **{source_field: earning.source},
        defaults={
            "payee": earning.payee,
            "amount_cents": earning.share_cents,
            "due_at": earning.due_at,
            "status": Payout.Status.PENDING if through else Payout.Status.OWED_MANUALLY,
            "owed_reason": reason,
        },
    )
    if created and reason == Payout.OwedReason.NOT_CONNECTED and (account is None or not account.is_connected):
        send_payouts_invite(payout)
    return created


def _send_one(pk: int, now: datetime, snapshots: list[tuple[Any, Any]], run: PayoutRun) -> None:
    """Send, retry or give up on one row, holding its lock so an overlapping run skips it."""
    with transaction.atomic():
        payout = (
            Payout.objects.to_send(now)
            .select_for_update(skip_locked=True, of=("self",))
            .select_related("registration__class_offering", "orientation_booking__orientation_type", "payee")
            .filter(pk=pk)
            .first()
        )
        if payout is None:
            return
        if payout.status == Payout.Status.FAILED:
            paid_on = timezone.localtime(payout.paid_on).date()
            if any(start <= paid_on <= end for start, end in snapshots):
                payout.give_up()
                run.gave_up += 1
                return
            payout.retry()
        else:
            payout.send()
        if payout.status == Payout.Status.SENT:
            run.sent += 1
        else:
            run.failed += 1


# ---------------------------------------------------------------------------
# Reading: the Payouts tab's earnings list and the Reconciliation split
# ---------------------------------------------------------------------------


@dataclass
class EarningRow:
    """One line of the Payouts tab: earnings for the same thing, taught the same day, in the same state."""

    item: str
    taught_on: Any
    state: str  # "upcoming" | "sent" | "owed" | "taken_back"
    label: str
    badge: str  # the pl-status-badge modifier
    amount_cents: int = 0
    count: int = 0
    payers: list[str] = field(default_factory=list)
    kind: str = "class"

    @property
    def detail(self) -> str:
        """ "3 students" for a class; the member's name for a single orientation."""
        if self.kind == "orientation" and self.count == 1:
            return self.payers[0]
        noun = "student" if self.kind == "class" else "person"
        return f"{self.count} {noun}{'' if self.count == 1 else 's'}"


@dataclass
class EarningsList:
    """The Payouts tab's earnings card: rows, newest taught first, and the three totals."""

    rows: list[EarningRow]
    upcoming_cents: int
    sent_this_month_cents: int
    owed_cents: int


def _state(earning: Earning, account: PayoutAccount | None, since: datetime | None) -> tuple[str, str, str]:
    """(state, label, badge) for one earning as its payee sees it."""
    payout = earning.payout
    if payout is None:
        if goes_through_stripe(earning, account, since):
            return (
                "upcoming",
                f"Sends {timezone.localtime(earning.due_at):%a %b} {timezone.localtime(earning.due_at).day}",
                "warn",
            )
        return "owed", "Paid at month end", "muted"
    if payout.status == Payout.Status.SENT:
        sent = timezone.localtime(payout.sent_at)
        return "sent", f"Sent {sent:%b} {sent.day}", "ok"
    if payout.status == Payout.Status.TAKEN_BACK:
        return "taken_back", "Taken back", "fail"
    if payout.status == Payout.Status.OWED_MANUALLY:
        return "owed", "Paid at month end", "muted"
    return "upcoming", "Sending", "warn"


def payee_earnings(member: Member, now: datetime | None = None) -> EarningsList:
    """What ``member`` earned: taught in the last 60 days or still to come, each earning in one row."""
    now = now or timezone.now()
    floor = now - EARNINGS_LOOKBACK
    earnings = _earnings(
        _registrations().filter(class_offering__instructor=member, first_session_at__gte=floor),
        _bookings().filter(oriented_by=member, slot__starts_at__gte=floor),
    )
    account = PayoutAccount.for_member(member)
    since = _payouts_since()
    rows: dict[tuple[Any, ...], EarningRow] = {}
    upcoming = sent_this_month = owed = 0
    this_month = timezone.localtime(now).strftime("%Y-%m")
    for earning in earnings:
        state, label, badge = _state(earning, account, since)
        taught_on = timezone.localtime(earning.taught_at).date()
        key = (earning.kind, earning.item, taught_on, state, label)
        row = rows.setdefault(key, EarningRow(earning.item, taught_on, state, label, badge, kind=earning.kind))
        signed = -earning.share_cents if state == "taken_back" else earning.share_cents
        row.amount_cents += signed
        row.count += 1
        row.payers.append(earning.payer)
        if state == "upcoming":
            upcoming += earning.share_cents
        elif state == "owed":
            owed += earning.share_cents
        elif state == "sent" and timezone.localtime(earning.payout.sent_at).strftime("%Y-%m") == this_month:  # type: ignore[union-attr]
            sent_this_month += earning.share_cents
    ordered = sorted(rows.values(), key=lambda row: (row.taught_on, row.item), reverse=True)
    return EarningsList(ordered, upcoming, sent_this_month, owed)


def through_stripe_keys(lines: Iterable[TransactionLine]) -> set[tuple[str, int]]:
    """The class and orientation lines whose producer share goes through Stripe, for the Reconciliation split.

    A recorded share counts as Stripe while pending, sent or taken back (a failed one is owed
    by hand until it succeeds); one not yet due follows ``goes_through_stripe``.
    """
    lines = [line for line in lines if not line.omitted]
    class_pks = [line.source_pk for line in lines if line.source_kind == "class"]
    booking_pks = [line.source_pk for line in lines if line.source_kind == "orientation"]
    if not class_pks and not booking_pks:
        return set()
    earnings = _earnings(_registrations().filter(pk__in=class_pks), _bookings().filter(pk__in=booking_pks))
    accounts = _accounts(earnings)
    since = _payouts_since()
    keys: set[tuple[str, int]] = set()
    for earning in earnings:
        payout = earning.payout
        if payout is not None:
            through = payout.status in _THROUGH_STRIPE_STATUSES
        else:
            through = goes_through_stripe(earning, accounts.get(earning.payee.pk), since)
        if through:
            keys.add((earning.kind, earning.source.pk))
    return keys

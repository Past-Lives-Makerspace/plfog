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

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import stripe
from django.db import transaction
from django.db.models import Min, QuerySet
from django.db.models.functions import Greatest
from django.utils import timezone

from billing.models import PAYOUT_LOOKUP_UNREACHABLE, BillingSettings, Payout, PayoutAccount, ReconciliationSnapshot

if TYPE_CHECKING:
    from django.http import HttpRequest

    from billing.reconciliation import TransactionLine
    from membership.models import Member

_UNANSWERED = (stripe.APIConnectionError, stripe.APIError, stripe.RateLimitError)

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
    # A share already sent stays listed by what was sent, even once a refund (taken back or
    # covered by Past Lives) brings its current share to zero.
    return [
        earning
        for earning in earnings
        if earning.share_cents > 0
        or (earning.payout is not None and earning.payout.status in (Payout.Status.SENT, Payout.Status.TAKEN_BACK))
    ]


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

    Records and sends nothing while payouts are off. A share is recorded once (a ``Payout``
    row per registration or booking), so a rerun or an overlapping run cannot send it twice.
    Even while off, a PENDING share more than ``PAYOUT_SEND_WINDOW`` past due is given up and
    owed by hand, so nothing waits unsent with nobody told.
    """
    now = now or timezone.now()
    run = PayoutRun()
    for refund_pk in list(_pending_take_backs().values_list("pk", flat=True)):
        take_back(refund_pk, now)
    for pk in list(Payout.objects.stale(now).in_current_mode().values_list("pk", flat=True)):
        _give_up_stale(pk, now, run)
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
    for pk in list(Payout.objects.to_send(now).in_current_mode().values_list("pk", flat=True)):
        _send_one(pk, now, snapshots, run)
    return run


def _record(
    earning: Earning,
    account: PayoutAccount | None,
    since: datetime | None,
    counted_in: ReconciliationSnapshot | None = None,
) -> bool:
    """Make the earning's ledger row: PENDING to send, or OWED_MANUALLY with the reason.

    ``counted_in`` is the snapshot freezing it (``freeze_split``); a share it puts on the
    Stripe side carries it, so a later rejection is flagged against that snapshot.
    """
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
            "counted_as_stripe_in": counted_in if through else None,
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
            .in_current_mode()
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
                with contextlib.suppress(*_UNANSWERED):  # no answer from Stripe: try again next run
                    if payout.give_up():
                        run.gave_up += 1
                return
            if not _refresh_amount(payout):  # a retry uses a new key, so it may carry a new amount
                return
            payout.retry()
        elif payout.attempted_at is None and not _refresh_amount(payout):
            return
        else:
            payout.send()
        if payout.status == Payout.Status.SENT:
            run.sent += 1
        else:
            run.failed += 1


def _give_up_stale(pk: int, now: datetime, run: PayoutRun) -> None:
    """Owe by hand a share still PENDING ``PAYOUT_SEND_WINDOW`` after it fell due, under its row lock."""
    with transaction.atomic():
        payout = (
            Payout.objects.stale(now)
            .in_current_mode()
            .select_for_update(skip_locked=True, of=("self",))
            .filter(pk=pk)
            .first()
        )
        if payout is None:
            return
        if payout.failure_reason and payout.failure_reason != PAYOUT_LOOKUP_UNREACHABLE:
            reason = f"Not sent within 3 days of falling due. Stripe's last answer: {payout.failure_reason}"
        else:
            reason = "Not sent within 3 days of falling due: payouts were off."
        try:
            if payout.give_up(reason):
                run.gave_up += 1
        except _UNANSWERED:
            # The lookup got no answer, so it cannot be owed by hand yet; flag it and alert once.
            payout.alert_unsendable()


def _refresh_amount(payout: Payout) -> bool:
    """Before an attempt under a new key, re-read the share: a refund since it was recorded lowers it.

    A row frozen by a snapshot can wait weeks for its class, and a retry can follow a refund.
    A replay of an unanswered attempt keeps its amount (same key, same request). Returns
    False, after recording NOTHING_DUE, when nothing is left to send.
    """
    if payout.registration_id is not None:
        earnings = _earnings(_registrations().filter(pk=payout.registration_id), [])
    else:
        earnings = _earnings([], _bookings().filter(pk=payout.orientation_booking_id))
    share = earnings[0].share_cents if earnings else 0
    if share <= 0:
        payout.status = Payout.Status.NOTHING_DUE
        payout.save(update_fields=["status"])
        return False
    if share != payout.amount_cents:
        payout.amount_cents = share
        payout.save(update_fields=["amount_cents"])
    return True


def freeze_split(snapshot: ReconciliationSnapshot, window: Any) -> None:
    """Bind a month-end snapshot's Sent through Stripe / Owed manually split into the ledger (#662).

    Every instructor and orientor share paid in ``window`` gets its ``Payout`` row now, even
    if its class is weeks away: on the Stripe side a PENDING row tied to ``snapshot`` (sent
    once due, whenever payouts are on), otherwise an OWED_MANUALLY row (never sent). A
    transfer still FAILED at this moment was counted as owed, so it stops retrying. The
    snapshot's results are then built from these rows, so nothing can contradict them later.
    """
    earnings = _earnings(
        _registrations().filter(confirmed_at__gte=window.start_dt, confirmed_at__lt=window.end_dt),
        _bookings().filter(requested_at__gte=window.start_dt, requested_at__lt=window.end_dt),
    )
    accounts = _accounts(earnings)
    since = _payouts_since()
    for earning in earnings:
        payout = earning.payout
        if payout is None:
            _record(earning, accounts.get(earning.payee.pk), since, counted_in=snapshot)
        elif payout.status == Payout.Status.FAILED:
            # No lookup: its last attempt was refused outright and every earlier one was refused
            # or replayed after a lookup, so no transfer exists; the snapshot counted it as owed.
            payout.give_up(lookup=False)
        elif payout.status == Payout.Status.PENDING:
            payout.counted_as_stripe_in = snapshot
            payout.save(update_fields=["counted_as_stripe_in"])


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


_SENT_STATUSES = frozenset({Payout.Status.SENT, Payout.Status.TAKEN_BACK})


def _earning_lines(
    earning: Earning, payout: Payout | None, account: PayoutAccount | None, since: datetime | None
) -> list[tuple[str, str, str, int]]:
    """(state, label, badge, cents) lines for one earning on the Payouts tab.

    A share refunds took back shows twice: Sent for what went out, Taken back for the part
    reversed, so a partial take back still shows the part the payee keeps.
    """
    if payout is not None and payout.status in _SENT_STATUSES and payout.sent_at is not None:
        sent = timezone.localtime(payout.sent_at)
        lines = [("sent", f"Sent {sent:%b} {sent.day}", "ok", payout.amount_cents)]
        if payout.reversed_cents:
            lines.append(("taken_back", "Taken back", "fail", -payout.reversed_cents))
        return lines
    state, label, badge = _state(earning, account, since)
    return [(state, label, badge, earning.share_cents)]


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
        payout = earning.payout
        taught_on = timezone.localtime(earning.taught_at).date()
        for state, label, badge, amount in _earning_lines(earning, payout, account, since):
            key = (earning.kind, earning.item, taught_on, state, label)
            row = rows.setdefault(key, EarningRow(earning.item, taught_on, state, label, badge, kind=earning.kind))
            row.amount_cents += amount
            row.count += 1
            row.payers.append(earning.payer)
            if state == "upcoming":
                upcoming += amount
            elif state == "owed":
                owed += amount
        if payout is not None and payout.sent_at is not None and payout.status in _SENT_STATUSES:
            if timezone.localtime(payout.sent_at).strftime("%Y-%m") == this_month:
                sent_this_month += payout.amount_cents - payout.reversed_cents  # what the payee keeps
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


# ---------------------------------------------------------------------------
# Refunds after a share was sent (part 3)
# ---------------------------------------------------------------------------

NOTHING_TO_REVERSE = "none"
"""``stripe_transfer_reversal_id`` for a take back whose refunded portion of the share rounds to nothing."""


def sent_payout_for(source: Any) -> Payout | None:
    """The share of ``source`` already sent through Stripe and not all taken back yet, if any.

    ``source`` is a registration or an orientation booking; a late fee earns no share.
    """
    try:
        payout = source.payout
    except (Payout.DoesNotExist, AttributeError):
        return None
    if payout.status in (Payout.Status.SENT, Payout.Status.TAKEN_BACK) and payout.amount_cents > payout.reversed_cents:
        return payout
    return None


def settle_refund_share(refund: Any) -> None:
    """On a refund's success: take the sent share back if the admin chose to, else record Past Lives covering it.

    A refund nobody was asked about (a Stripe dashboard refund, the automatic orientation
    refund) records Not asked: Past Lives covers it and Reconciliation flags the line. The
    reversal runs after the refund's transaction commits, outside its row lock.
    """
    from billing.models import PaymentRefund

    if refund.registration_id is not None:
        rows = Payout.objects.filter(registration_id=refund.registration_id)
    elif refund.orientation_booking_id is not None:
        rows = Payout.objects.filter(orientation_booking_id=refund.orientation_booking_id)
    else:
        return  # a late fee earns no share
    with transaction.atomic():
        # Blocks on the share's row while a send holds it through ``mark_sent``, so a refund
        # and a send never both miss each other: whichever runs second sees the other's write.
        payout = rows.select_for_update(of=("self",)).first()
        if payout is None or payout.status not in _SENT_STATUSES or payout.amount_cents <= payout.reversed_cents:
            return
        if refund.share_decision == PaymentRefund.ShareDecision.TAKE_BACK:
            transaction.on_commit(lambda: take_back(refund.pk))
        elif refund.share_decision == PaymentRefund.ShareDecision.NOT_APPLICABLE:
            refund.share_decision = PaymentRefund.ShareDecision.NOT_ASKED
            refund.save(update_fields=["share_decision"])


def _pending_take_backs() -> QuerySet[Any]:
    """Succeeded refunds whose chosen take back has not reached Stripe yet: the send job retries them."""
    from billing.models import PaymentRefund

    return PaymentRefund.objects.filter(
        status=PaymentRefund.Status.SUCCEEDED,
        share_decision=PaymentRefund.ShareDecision.TAKE_BACK,
        stripe_transfer_reversal_id="",
        share_reversal_error="",
    )


def _refunded_portion(refund: Any, payout: Payout) -> int:
    """The part of the sent share this refund refunds: the split before it less the split after it.

    Counts only the succeeded refunds before this one, so a retry days later, after more
    refunds, still takes back exactly this refund's part. Capped at what is left to take.
    """
    from billing.models import PaymentRefund
    from billing.reconciliation import ShareSource

    source = refund.source_object
    before = sum(
        r.amount_cents for r in source.refunds.all() if r.status == PaymentRefund.Status.SUCCEEDED and r.pk < refund.pk
    )
    shares = ShareSource()
    share = shares.for_registration if refund.registration_id is not None else shares.for_booking
    portion = share(source, refunded_cents=before) - share(source, refunded_cents=before + refund.amount_cents)
    return max(0, min(portion, payout.amount_cents - payout.reversed_cents))


def take_back(refund_pk: int, now: datetime | None = None) -> None:
    """Reverse this refund's portion of the sent share from the payee's Stripe account (#662, part 3).

    First looks for a reversal an earlier try already made (the idempotency key
    ``payout-reversal-<refund pk>`` expires after a day). A reversal Stripe refuses falls
    back to Past Lives covering it: the refund records why, Reconciliation flags the line
    and the billing admins are alerted once. No answer leaves it for the next send run;
    after three days of that the admins are alerted once too.
    """
    from billing import stripe_utils
    from billing.models import PAYOUT_SEND_WINDOW
    from billing.notifications import notify_admins_reversal_failed

    now = now or timezone.now()
    with transaction.atomic():
        refund = _pending_take_backs().select_for_update(of=("self",)).filter(pk=refund_pk).first()
        if refund is None:
            return
        source = refund.source_object
        payout = sent_payout_for(source)
        if payout is not None:  # take the same row lock a send holds, then read its committed state
            payout = Payout.objects.select_for_update(of=("self",)).get(pk=payout.pk)
            if payout.status not in _SENT_STATUSES or payout.amount_cents <= payout.reversed_cents:
                payout = None
        portion = _refunded_portion(refund, payout) if payout is not None else 0
        if payout is None or portion == 0:
            refund.stripe_transfer_reversal_id = NOTHING_TO_REVERSE
            refund.save(update_fields=["stripe_transfer_reversal_id"])
            return
        try:
            reversal_id = stripe_utils.find_transfer_reversal(
                transfer_id=payout.stripe_transfer_id, refund_pk=refund.pk
            ) or stripe_utils.reverse_transfer(
                transfer_id=payout.stripe_transfer_id, amount_cents=portion, refund_pk=refund.pk
            )
        except _UNANSWERED:
            if refund.settled_at is not None and refund.settled_at < now - PAYOUT_SEND_WINDOW:
                notify_admins_reversal_failed(refund, "Stripe could not be reached for three days; plfog keeps trying.")
            return
        except stripe.StripeError as exc:
            refund.share_reversal_error = getattr(exc, "user_message", None) or str(exc)
            refund.save(update_fields=["share_reversal_error"])
            notify_admins_reversal_failed(refund, refund.share_reversal_error)
            return
        refund.stripe_transfer_reversal_id = reversal_id
        refund.share_reversed_cents = portion
        refund.save(update_fields=["stripe_transfer_reversal_id", "share_reversed_cents"])
        payout.reversed_cents += portion
        payout.status = Payout.Status.TAKEN_BACK
        payout.save(update_fields=["reversed_cents", "status"])


def kept_share_notes(lines: Iterable[TransactionLine]) -> dict[tuple[str, int], str]:
    """Reconciliation flags: refunded payments whose producer kept a share already sent through Stripe."""
    from django.db.models import Q

    from billing.models import PaymentRefund

    lines = [line for line in lines if line.source_kind in ("class", "orientation")]
    class_pks = [line.source_pk for line in lines if line.source_kind == "class"]
    booking_pks = [line.source_pk for line in lines if line.source_kind == "orientation"]
    if not lines:
        return {}
    kept = (
        PaymentRefund.objects.filter(status=PaymentRefund.Status.SUCCEEDED)
        .filter(Q(registration_id__in=class_pks) | Q(orientation_booking_id__in=booking_pks))
        .filter(
            Q(share_decision__in=[PaymentRefund.ShareDecision.PL_COVERS, PaymentRefund.ShareDecision.NOT_ASKED])
            | ~Q(share_reversal_error="")
        )
    )
    notes: dict[tuple[str, int], str] = {}
    for refund in kept:
        key: tuple[str, int] = (
            ("class", refund.registration_id)
            if refund.registration_id is not None
            else ("orientation", cast(int, refund.orientation_booking_id))  # one of the two, by the query
        )
        if refund.share_reversal_error:
            why = f"Stripe refused to take the share back ({refund.share_reversal_error})"
        elif refund.share_decision == PaymentRefund.ShareDecision.NOT_ASKED:
            why = "refunded where nobody could be asked"
        else:
            why = "an admin chose Past Lives covers it"
        notes[key] = f"Payee kept a share already sent: {why}; Past Lives covered the refund"
    return notes


def flag_refunds_past_a_send(payout: Payout) -> None:
    """When a send completes, flag a refund that landed while the share was pending or mid-send.

    Such a refund found no sent share, so nobody chose and nothing was reversed: a replay
    keeps its pinned amount. If the share due now is less than what was sent, the refund is
    recorded as Not asked (Past Lives covers it), like a Stripe dashboard refund, and
    Reconciliation flags the line. Nothing is reversed automatically.
    """
    from billing.models import PaymentRefund
    from billing.reconciliation import ShareSource

    source = payout.source
    shares = ShareSource()
    due = shares.for_registration(source) if payout.registration_id is not None else shares.for_booking(source)
    if due >= payout.amount_cents - payout.reversed_cents:
        return
    source.refunds.filter(
        status=PaymentRefund.Status.SUCCEEDED, share_decision=PaymentRefund.ShareDecision.NOT_APPLICABLE
    ).update(share_decision=PaymentRefund.ShareDecision.NOT_ASKED)

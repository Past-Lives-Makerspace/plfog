"""Record each Stripe payment's fee, net and payout (#984).

Three passes, run daily by ``sync_stripe_fees`` and over all history by ``sync_stripe_fees --backfill``:

1. **Find**: every paid payment the Payments panel lists (tab charge, class, orientation,
   reservation, late fee) whose PaymentIntent has no :class:`StripeFeeRecord` gets a pending
   one. Webhooks add most rows the moment a payment lands; this pass catches a missed one.
2. **Fill**: each pending record that suits the current Stripe mode is read (the active
   account, then the previous one) and recorded with its refunds, or marked not found when
   both accounts say they have no such payment. A read that fails leaves it pending; one
   that needs a key that is not set leaves it pending without counting a failure; one that a
   refund webhook overtook is not written. ``--backfill`` asks again about not found ones.
3. **Payouts**: each recorded charge, refund and failed refund's reversal whose money is available is matched to the
   Stripe payout whose balance transactions include it, so rows summed by payout give the
   bank deposit.

Nothing here runs inside a webhook: a Stripe outage only delays the fee, never a payment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import stripe
from django.db.models import F, Min
from django.utils import timezone

from billing import stripe_utils
from billing.exceptions import StripeAccountUnavailableError, StripeSettlementNotReadyError
from billing.models import BillingSettings, StripeFeeRecord, StripeFeeRefund

if TYPE_CHECKING:
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

PAYOUT_LOOKBACK = timedelta(days=35)
"""How far back a daily run looks for payouts; Stripe pays out within days, so older misses are backfill work."""

DAILY_READ_LIMIT = 500
"""Stripe reads a daily run makes at most, so the first run after deploy cannot hold the dispatcher; --backfill has no cap."""

PAYOUT_ARRIVAL_SLACK = timedelta(days=1)
"""Payouts are listed from a day before the earliest availability, since arrival and availability round to days."""


@dataclass
class SyncReport:
    """What one run did, for the command's output and its pass or fail."""

    added: int = 0
    recorded: int = 0
    waiting: int = 0
    not_found: int = 0
    stripe_errors: int = 0
    unreadable: int = 0
    stale: int = 0
    payouts_stamped: int = 0
    skipped_accounts: list[str] = field(default_factory=list)
    not_found_sources: list[tuple[str, int]] = field(default_factory=list)


def paid_sources() -> dict[str, tuple[QuerySet[Any], str]]:
    """Each Payments panel source's paid rows and the field holding their PaymentIntent, by source kind.

    The same paid rules as ``billing.payments_panel``: succeeded tab charges, paid registrations,
    orientation bookings and reservations past their payment hold, paid or refunded late fees, and
    paid cubby invoices.
    """
    from billing.models import LateCancellationFee, TabCharge
    from classes.models import Registration
    from membership.models import CubbyPayment, EquipmentReservation, OrientationBooking

    return {
        "tab": (TabCharge.objects.filter(status=TabCharge.Status.SUCCEEDED), "stripe_payment_intent_id"),
        "class": (Registration.objects.filter(amount_paid_cents__gt=0), "stripe_payment_id"),
        "orientation": (
            OrientationBooking.objects.filter(amount_paid_cents__gt=0).exclude(
                status=OrientationBooking.Status.PENDING_PAYMENT
            ),
            "stripe_payment_id",
        ),
        "reservation": (
            EquipmentReservation.objects.filter(amount_paid_cents__gt=0).exclude(
                status=EquipmentReservation.Status.PENDING_PAYMENT
            ),
            "stripe_payment_id",
        ),
        "late_fee": (
            LateCancellationFee.objects.filter(
                status__in=[LateCancellationFee.Status.PAID, LateCancellationFee.Status.REFUNDED]
            ),
            "stripe_payment_id",
        ),
        # A paid cubby invoice (#979): Reconciliation's Cubby line reads its fee and net too.
        "cubby": (CubbyPayment.objects.all(), "stripe_payment_intent_id"),
    }


def add_missing_records() -> int:
    """Add a pending record for every paid payment with a PaymentIntent and no record; returns how many."""
    known = StripeFeeRecord.objects.values("stripe_payment_intent_id")
    missing: set[str] = set()
    for queryset, pi_field in paid_sources().values():
        missing.update(
            queryset.exclude(**{pi_field: ""}).exclude(**{f"{pi_field}__in": known}).values_list(pi_field, flat=True)
        )
    StripeFeeRecord.objects.bulk_create(
        [StripeFeeRecord(stripe_payment_intent_id=pi) for pi in sorted(missing)], ignore_conflicts=True
    )
    return len(missing)


def fill(record: StripeFeeRecord, report: SyncReport, now: datetime) -> None:
    """Read one pending record from Stripe and record it, mark it not found, or leave it pending."""
    record.start_read()
    try:
        settlement = stripe_utils.retrieve_charge_settlement(payment_intent_id=record.stripe_payment_intent_id)
    except StripeAccountUnavailableError as exc:
        record.note_unreadable(str(exc), now)
        report.unreadable += 1
        return
    except StripeSettlementNotReadyError as exc:
        record.note_failed_read(str(exc), now)
        report.waiting += 1
        return
    except stripe.StripeError as exc:
        logger.warning("Stripe could not answer for fee record %s: %s", record.pk, exc)
        record.note_failed_read(f"Stripe could not answer: {exc}", now)
        report.stripe_errors += 1
        return
    if settlement is None:
        record.mark_not_found(now)
        report.not_found += 1
    elif record.record(settlement, now=now):
        report.recorded += 1
    else:
        report.stale += 1


def _earliest_by_account(
    queryset: QuerySet[Any], account_field: str, date_field: str = "available_on"
) -> dict[str, datetime]:
    """The earliest availability per account among rows awaiting a payout."""
    return {
        row[account_field]: row["earliest"]
        for row in queryset.values(account_field).annotate(earliest=Min(date_field)).order_by()
    }


def stamp_payouts(report: SyncReport, now: datetime, *, live: bool, backfill: bool) -> None:
    """Match available charges and refunds to the Stripe payouts that carried them, account by account.

    A daily run looks back :data:`PAYOUT_LOOKBACK`; the backfill looks back to the earliest
    unmatched row. An account with no key for this mode (the previous account once it closes)
    is skipped with a note, and the other account still runs.
    """
    charges = StripeFeeRecord.objects.readable(live=live).awaiting_payout(now)
    refunds = StripeFeeRefund.objects.readable(live=live).awaiting_payout(now)
    reversals = StripeFeeRefund.objects.readable(live=live).awaiting_failure_payout(now)
    earliest = _earliest_by_account(charges, "account")
    for account, when in [
        *_earliest_by_account(refunds, "record__account").items(),
        *_earliest_by_account(reversals, "record__account", "failure_available_on").items(),
    ]:
        earliest[account] = min(when, earliest.get(account, when))
    clients = stripe_utils.fee_accounts()
    for account, when in sorted(earliest.items()):
        if account not in clients:
            logger.info("Skipped matching %s Stripe account payouts: no secret key is set for this mode.", account)
            report.skipped_accounts.append(account)
            continue
        since = when - PAYOUT_ARRIVAL_SLACK
        if not backfill:
            since = max(since, now - PAYOUT_LOOKBACK)
        for payout_id, transaction_ids in stripe_utils.iter_paid_payouts(client=clients[account], arrived_since=since):
            if not transaction_ids:
                continue
            report.payouts_stamped += charges.filter(
                account=account, stripe_balance_transaction_id__in=transaction_ids
            ).update(stripe_payout_id=payout_id, updated_at=now)
            report.payouts_stamped += refunds.filter(
                record__account=account, stripe_balance_transaction_id__in=transaction_ids
            ).update(stripe_payout_id=payout_id, updated_at=now)
            report.payouts_stamped += reversals.filter(
                record__account=account, failure_balance_transaction_id__in=transaction_ids
            ).update(failure_payout_id=payout_id, updated_at=now)


def not_found_sources() -> list[tuple[str, int]]:
    """Each paid payment whose PaymentIntent neither Stripe account holds, as (source kind, pk)."""
    missing = set(
        StripeFeeRecord.objects.filter(status=StripeFeeRecord.Status.NOT_FOUND).values_list(
            "stripe_payment_intent_id", flat=True
        )
    )
    if not missing:
        return []
    found: list[tuple[str, int]] = []
    for kind, (queryset, pi_field) in paid_sources().items():
        found.extend((kind, pk) for pk in queryset.filter(**{f"{pi_field}__in": missing}).values_list("pk", flat=True))
    return sorted(found)


def sync(*, backfill: bool = False) -> SyncReport:
    """Run the three passes once; see the module docstring. ``backfill`` also re-asks Stripe about not found payments."""
    report = SyncReport()
    now = timezone.now()
    live = not BillingSettings.load().test_mode
    report.added = add_missing_records()
    if backfill:
        StripeFeeRecord.objects.filter(status=StripeFeeRecord.Status.NOT_FOUND).update(
            status=StripeFeeRecord.Status.PENDING, attempts=0, updated_at=now
        )
    # Never read first, then the longest waiting, so a record that keeps failing cannot starve the rest.
    pending = (
        StripeFeeRecord.objects.pending()
        .readable(live=live)
        .order_by(F("last_attempt_at").asc(nulls_first=True), "created_at")
    )
    for record in pending if backfill else pending[:DAILY_READ_LIMIT]:
        fill(record, report, now)
    stamp_payouts(report, now, live=live, backfill=backfill)
    if backfill:
        report.not_found_sources = not_found_sources()
    return report


def note_event(event: dict[str, Any]) -> None:
    """Add or refresh the fee record a payment webhook touches. A database write only, never a Stripe call.

    Runs after the event's own handler, so confirming a payment never waits on it. Connected
    account events are skipped: their payments are the payee's, not Past Lives'.
    """
    if event.get("account"):  # present only on events from the Connected accounts endpoint
        return
    event_type = event["type"]
    obj = event["data"]["object"]
    livemode = event.get("livemode")  # every real Stripe event carries it; None reads as unknown
    if event_type == "payment_intent.succeeded":
        StripeFeeRecord.add_pending(obj["id"], livemode=livemode)
    elif event_type.startswith("checkout.session."):
        if obj["payment_status"] == "paid" and obj["payment_intent"]:
            StripeFeeRecord.add_pending(obj["payment_intent"], livemode=livemode)
    elif event_type in ("charge.refunded", "refund.updated") and obj["payment_intent"]:
        StripeFeeRecord.add_pending(obj["payment_intent"], livemode=livemode)
        StripeFeeRecord.refresh(obj["payment_intent"])

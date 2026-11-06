"""BDD specs for a paid orientation bill in the finance readers (#1111, part 1).

A paid bill is a paid booking to every reader: the Payments panel, Reconciliation with the
orientor, guild and Past Lives split, payouts and the Stripe fee sync. It is dated by when
it was paid, so a bill sent one month and paid the next counts in the month it was paid;
ordinary paid bookings are dated as before; an unpaid or cancelled bill counts nowhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from billing import payouts, stripe_fees
from billing.models import BillingSettings, Payout
from billing.payments_panel import PanelWindow, build_payments_ledger
from billing.reconciliation import RecipientKind, build_reconciliation
from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

#: The bill was sent on October 28 and paid on November 3 (UTC).
SENT = datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
PAID = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)
OCTOBER = PanelWindow(start=datetime(2026, 10, 1).date(), end=datetime(2026, 10, 31).date())
NOVEMBER = PanelWindow(start=datetime(2026, 11, 1).date(), end=datetime(2026, 11, 30).date())


def _orientor(name: str = "Odile Orientor") -> Member:
    return MemberFactory(full_legal_name=name)


def _bill(orientor: Member | None, *, paid: bool = True, cancelled: bool = False, **fields: Any) -> OrientationBooking:
    """A bill on a guild's type, sent in October and, by default, paid in November."""
    MembershipPlanFactory()
    member = User.objects.create_user(username=f"fin_{orientor.pk if orientor else 0}_{paid}").member
    guild = GuildFactory(name="Finance Guild")
    orientation_type = OrientationTypeFactory(guild=guild, name="Finance Lathe")
    starts = SENT - timedelta(days=2)
    slot = OrientationSlotFactory(
        guild=guild, orientation_type=orientation_type, starts_at=starts, ends_at=starts + timedelta(hours=1), seats=1
    )
    booking = OrientationBooking.create_bill(slot=slot, member=member, oriented_by=orientor, amount_cents=10000)
    update: dict[str, Any] = {"requested_at": SENT, "billed_at": SENT, **fields}
    if paid:
        update.update(paid_at=PAID, amount_paid_cents=10000, stripe_payment_id="pi_fin_bill")
    if cancelled:
        update.update(bill_cancelled_at=SENT + timedelta(days=1))
    OrientationBooking.objects.filter(pk=booking.pk).update(**update)
    booking.refresh_from_db()
    return booking


def _ordinary(*, when: datetime) -> OrientationBooking:
    booking = OrientationBookingFactory(
        slot=OrientationSlotFactory(),
        amount_paid_cents=5000,
        stripe_payment_id="pi_fin_plain",
        status=OrientationBooking.Status.CONFIRMED,
    )
    OrientationBooking.objects.filter(pk=booking.pk).update(requested_at=when)
    booking.refresh_from_db()
    return booking


def _alloc_cents(result: Any, kind: RecipientKind, label: str) -> int:
    return next(a.total_cents for a in result.groups[kind] if a.label == label)


def describe_the_payments_panel():
    def it_lists_a_paid_bill_in_the_month_it_was_paid():
        booking = _bill(_orientor())
        november = build_payments_ledger(window=NOVEMBER, source="orientation").rows
        assert [(row.source_pk, row.amount_cents, row.date) for row in november] == [(booking.pk, 10000, PAID)]
        assert november[0].can_refund is True
        assert build_payments_ledger(window=OCTOBER, source="orientation").rows == ()

    def it_lists_an_ordinary_booking_by_its_request_as_before():
        booking = _ordinary(when=SENT)
        october = build_payments_ledger(window=OCTOBER, source="orientation").rows
        assert [(row.source_pk, row.date) for row in october] == [(booking.pk, SENT)]
        assert build_payments_ledger(window=NOVEMBER, source="orientation").rows == ()

    def it_leaves_out_an_unpaid_or_cancelled_bill():
        _bill(_orientor(), paid=False)
        _bill(_orientor("Cass Cancel"), paid=False, cancelled=True)
        for window in (OCTOBER, NOVEMBER):
            assert build_payments_ledger(window=window, source="orientation").rows == ()


def describe_reconciliation():
    def it_credits_the_orientor_guild_and_past_lives_in_the_month_paid():
        orientor = _orientor()
        _bill(orientor)
        november = build_reconciliation(window=NOVEMBER, adjustments={})
        assert _alloc_cents(november, RecipientKind.ORIENTATOR, orientor.display_name) == 7000
        assert _alloc_cents(november, RecipientKind.GUILD, "Finance Guild") == 1500
        assert _alloc_cents(november, RecipientKind.PL, "Past Lives") == 1500
        line = next(line for line in november.lines if line.source_kind == "orientation")
        assert line.date == PAID
        assert build_reconciliation(window=OCTOBER, adjustments={}).grand_total_cents == 0

    def it_counts_an_unpaid_or_cancelled_bill_nowhere():
        _bill(_orientor(), paid=False)
        _bill(_orientor("Cass Cancel"), paid=False, cancelled=True)
        for window in (OCTOBER, NOVEMBER):
            assert build_reconciliation(window=window, adjustments={}).grand_total_cents == 0

    def it_dates_an_ordinary_booking_by_its_request_as_before():
        _ordinary(when=SENT)
        assert build_reconciliation(window=OCTOBER, adjustments={}).grand_total_cents == 5000
        assert build_reconciliation(window=NOVEMBER, adjustments={}).grand_total_cents == 0


def describe_payouts():
    NOW = PAID + timedelta(days=3)

    @pytest.fixture(autouse=True)
    def _frozen_now():
        with patch("django.utils.timezone.now", return_value=NOW):
            yield

    def _turn_on() -> None:
        bs = BillingSettings.load()
        bs.connect_enabled = True
        bs.orientation_orientator_percent = Decimal("70")
        bs.orientation_guild_percent = Decimal("15")
        bs.orientation_pl_percent = Decimal("15")
        bs.save()
        BillingSettings.objects.filter(pk=1).update(payouts_on_since=SENT - timedelta(days=30))

    def it_falls_due_two_days_after_payment_to_the_orientor_picked():
        _turn_on()
        orientor = _orientor()
        booking = _bill(orientor)
        with patch("billing.stripe_utils.create_transfer", return_value="tr_1"):
            payouts.run_payouts(NOW)
        payout = Payout.objects.get(orientation_booking=booking)
        assert (payout.payee, payout.amount_cents, payout.due_at) == (orientor, 7000, PAID + payouts.PAYOUT_DELAY)
        assert payout.paid_on == PAID

    def it_records_nothing_for_an_unpaid_bill():
        _turn_on()
        _bill(_orientor(), paid=False)
        payouts.run_payouts(NOW)
        assert not Payout.objects.exists()

    def it_lists_the_share_on_the_orientors_earnings():
        orientor = _orientor()
        _bill(orientor)
        earnings = payouts.payee_earnings(orientor, now=NOW)
        assert sum(row.amount_cents for row in earnings.rows) == 7000


def describe_the_stripe_fee_sync():
    def it_dates_a_bill_by_when_it_was_paid():
        booking = _bill(_orientor())
        queryset, field = stripe_fees._dated_paid_sources()["orientation"]
        assert field == "stripe_payment_id"
        assert queryset.get(pk=booking.pk).fee_paid_at == PAID

    def it_dates_an_ordinary_booking_by_its_request():
        booking = _ordinary(when=SENT)
        queryset, _field = stripe_fees._dated_paid_sources()["orientation"]
        assert queryset.get(pk=booking.pk).fee_paid_at == SENT


def describe_the_money_date_filters():
    def it_splits_bills_and_ordinary_bookings_by_their_own_dates():
        bill = _bill(_orientor())
        plain = _ordinary(when=SENT)
        start = datetime(2026, 11, 1, tzinfo=UTC)
        end = datetime(2026, 12, 1, tzinfo=UTC)
        assert set(OrientationBooking.objects.filter(OrientationBooking.money_between_q(start, end))) == {bill}
        assert set(OrientationBooking.objects.filter(OrientationBooking.money_before_q(start))) == {plain}
        assert timezone.is_aware(bill.money_at)

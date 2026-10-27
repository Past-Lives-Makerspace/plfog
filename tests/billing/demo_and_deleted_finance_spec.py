"""Finance counts only real money and names a deleted member (#983).

A ``demo-`` class never reaches Reconciliation, its CSV, the Payments panel or the payout
ledger, even when a seeder puts one back. A member who deleted their account reads as
"Name (account deleted)" as payer and as payee.
"""

from __future__ import annotations

import csv

import pytest
from django.utils import timezone

from billing.models import Payout, ReconciliationSnapshot
from billing.payments_panel import build_payments_ledger, parse_window
from billing.reconciliation import RecipientKind, build_reconciliation, stream_reconciliation_csv
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db


def _window():
    return parse_window("", "")


def _paid_registration(*, cents, slug, instructor=None, guild=None, member=None, pi=""):
    from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory, RegistrationFactory

    offering = ClassOfferingFactory(slug=slug, category=CategoryFactory(guild=guild), instructor=instructor)
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now())
    return RegistrationFactory(
        class_offering=offering,
        amount_paid_cents=cents,
        member=member,
        confirmed_at=timezone.now(),
        stripe_payment_id=pi,
    )


def _refund(reg, cents):
    from billing.models import PaymentRefund
    from tests.billing.factories import PaymentRefundFactory

    PaymentRefundFactory(registration=reg, amount_cents=cents, status=PaymentRefund.Status.SUCCEEDED)


def _deleted(name: str):
    return MemberFactory(full_legal_name="Deleted Member", name_at_deletion=name, deleted_at=timezone.now())


def describe_demo_classes():
    def it_leaves_a_demo_class_out_of_reconciliation():
        guild = GuildFactory(name="Glassblowing")
        instructor = MemberFactory()
        real = _paid_registration(cents=10000, slug="real-glass", instructor=instructor, guild=guild)
        _paid_registration(cents=40000, slug="demo-cart-glass", instructor=instructor, guild=guild)

        result = build_reconciliation(window=_window(), adjustments={})

        assert result.grand_total_cents == 10000
        assert [line.source_pk for line in result.lines] == [real.pk]

    def it_leaves_a_demo_class_out_of_the_csv():
        instructor = MemberFactory(full_legal_name="Only Demo Teacher")
        _paid_registration(cents=40000, slug="demo-only", instructor=instructor, guild=GuildFactory())

        response = stream_reconciliation_csv(build_reconciliation(window=_window(), adjustments={}))
        rows = list(csv.reader(b"".join(response.streaming_content).decode().splitlines()))

        assert "Only Demo Teacher" not in {row[1] for row in rows[1:]}

    def it_leaves_a_demo_class_out_of_the_payments_panel():
        real = _paid_registration(cents=10000, slug="real-clay", guild=GuildFactory())
        _paid_registration(cents=40000, slug="demo-clay", guild=GuildFactory())

        ledger = build_payments_ledger(window=_window(), source="class")

        assert [row.source_pk for row in ledger.rows] == [real.pk]
        assert ledger.collected_cents == 10000

    def it_freezes_no_payout_for_a_demo_registration():
        instructor = MemberFactory()
        real = _paid_registration(cents=10000, slug="real-wood", instructor=instructor, guild=GuildFactory())
        demo = _paid_registration(cents=40000, slug="demo-wood", instructor=instructor, guild=GuildFactory())
        window = _window()

        ReconciliationSnapshot.take(period_start=window.start, period_end=window.end)

        assert Payout.objects.filter(registration=real).exists()
        assert not Payout.objects.filter(registration=demo).exists()


def describe_real_money_on_a_demo_slug():
    """A real class titled "Demo Day" is slugged demo-day-<date>: its Stripe money always counts."""

    def it_counts_a_real_demo_day_class_in_reconciliation_and_the_panel():
        reg = _paid_registration(cents=10000, slug="demo-day-2026-11-02", guild=GuildFactory(), pi="pi_real")
        window = _window()

        result = build_reconciliation(window=window, adjustments={})
        ledger = build_payments_ledger(window=window, source="class")

        assert [line.source_pk for line in result.lines] == [reg.pk]
        assert [row.source_pk for row in ledger.rows] == [reg.pk]

    def it_counts_a_partly_refunded_stripe_payment_on_a_demo_slug():
        reg = _paid_registration(cents=10000, slug="demo-day-2026-11-02", guild=GuildFactory(), pi="pi_real")
        _refund(reg, 4000)

        result = build_reconciliation(window=_window(), adjustments={})

        assert result.grand_total_cents == 6000

    def it_leaves_out_a_fully_refunded_stripe_test_charge_on_a_demo_class():
        reg = _paid_registration(cents=500, slug="demo-cart-test", guild=GuildFactory(), pi="pi_test_charge")
        _refund(reg, 500)

        result = build_reconciliation(window=_window(), adjustments={})

        assert result.lines == []

    def it_freezes_a_payout_for_a_real_demo_day_class():
        reg = _paid_registration(
            cents=10000, slug="demo-day-2026-11-02", instructor=MemberFactory(), guild=GuildFactory(), pi="pi_real"
        )
        window = _window()

        ReconciliationSnapshot.take(period_start=window.start, period_end=window.end)

        assert Payout.objects.filter(registration=reg).exists()


def describe_has_earning():
    def it_ignores_a_demo_class():
        from billing.payouts import has_earning

        instructor = MemberFactory()
        _paid_registration(cents=40000, slug="demo-cart-wood", instructor=instructor, guild=GuildFactory())

        assert has_earning(instructor) is False

    def it_counts_real_money_on_a_demo_slug():
        from billing.payouts import has_earning

        instructor = MemberFactory()
        _paid_registration(
            cents=10000, slug="demo-day-2026-11-02", instructor=instructor, guild=GuildFactory(), pi="pi_real"
        )

        assert has_earning(instructor) is True


def describe_deleted_member_names():
    def it_names_a_deleted_payer_on_the_reconciliation_line_and_panel():
        payer = _deleted("Morgan Lathe")
        _paid_registration(cents=10000, slug="real-lathe", member=payer, guild=GuildFactory())
        window = _window()

        result = build_reconciliation(window=window, adjustments={})
        ledger = build_payments_ledger(window=window, source="class")

        assert [line.payer_name for line in result.lines] == ["Morgan Lathe (account deleted)"]
        assert [row.payer_name for row in ledger.rows] == ["Morgan Lathe (account deleted)"]

    def it_names_a_deleted_instructor_on_their_recipient_row_and_in_the_snapshot():
        instructor = _deleted("Sasha Forge")
        _paid_registration(cents=10000, slug="real-forge", instructor=instructor, guild=GuildFactory())
        window = _window()

        result = build_reconciliation(window=window, adjustments={})
        snapshot = ReconciliationSnapshot.take(period_start=window.start, period_end=window.end)

        labels = [alloc.label for alloc in result.groups[RecipientKind.INSTRUCTOR]]
        assert labels == ["Sasha Forge (account deleted)"]
        instructor_rows = [g for g in snapshot.results["groups"] if g["kind"] == RecipientKind.INSTRUCTOR.value]
        assert instructor_rows[0]["rows"][0]["label"] == "Sasha Forge (account deleted)"

    def it_keeps_deleted_member_for_someone_deleted_before_names_were_kept():
        payer = _deleted("")
        _paid_registration(cents=10000, slug="real-old", member=payer, guild=GuildFactory())

        result = build_reconciliation(window=_window(), adjustments={})

        assert [line.payer_name for line in result.lines] == ["Deleted Member"]

"""#979: a paid cubby invoice in the books and in payouts.

The admins' cubby split (a pair summing to 100), a Cubby line per payment on Reconciliation (the
guild's share to the payee frozen on the payment, the rest to Past Lives; no payee keeps it all
with Past Lives; a refunded payment owes nobody), the Cubby Payees producer group, adjustments,
snapshots, and the payee's ``Payout`` made and sent once by ``send_payouts`` from the invoice's
charge 48 hours after payment. Names are factory strings no changelog could contain.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing import payouts, stripe_fees
from billing.forms import ReconciliationSettingsForm, TransactionAdjustmentForm
from billing.models import BillingSettings, Payout, PayoutAccount, ReconciliationSnapshot, TransactionAdjustment
from billing.notifications import _payout_item
from billing.payments_panel import PanelWindow
from billing.reconciliation import (
    GROUP_LABELS,
    RecipientKind,
    ShareSource,
    build_reconciliation,
    result_from_snapshot,
)
from membership.models import CubbyPayment, Member
from tests.membership.factories import (
    CubbyFactory,
    CubbyPaymentFactory,
    CubbyRentalFactory,
    GuildFactory,
    MemberFactory,
    ShelfUnitFactory,
)

pytestmark = pytest.mark.django_db


def _now() -> datetime:
    return timezone.now()


def _window() -> PanelWindow:
    today = timezone.localdate()
    return PanelWindow(start=today - timedelta(days=40), end=today)


def _payee(name: str = "Wrenna Quillfeather", *, connected: bool = True) -> Member:
    member = MemberFactory(full_legal_name=name, preferred_name="")
    if connected:
        PayoutAccount.objects.create(
            member=member,
            stripe_account_id=f"acct_{member.pk}",
            livemode=False,
            status=PayoutAccount.Status.ACTIVE,
            active_since=_now() - timedelta(days=20),
        )
    return member


def _payment(
    payee: Member | None = None,
    *,
    amount: int = 2000,
    paid_at: datetime | None = None,
    refunded: bool = False,
    pi: str = "pi_cub_pay",
) -> CubbyPayment:
    guild = GuildFactory(name="Thornbury Kiln Guild", cubby_payee=payee)
    cubby = CubbyFactory(unit=ShelfUnitFactory(guild=guild, name="Glaze Wall"), label="Q7")
    renter = MemberFactory(full_legal_name="Ottoline Brackwater", preferred_name="")
    rental = CubbyRentalFactory(cubby=cubby, member=renter, amount_cents=amount)
    return CubbyPaymentFactory(
        rental=rental,
        amount_cents=amount,
        payee=None if refunded else payee,
        stripe_payment_intent_id=pi,
        stripe_refund_id="re_cub" if refunded else "",
        paid_at=paid_at or _now() - timedelta(days=3),
    )


def _turn_on(since: datetime | None = None) -> None:
    settings_obj = BillingSettings.load()
    settings_obj.connect_enabled = True
    settings_obj.save()
    BillingSettings.objects.filter(pk=1).update(payouts_on_since=since or _now() - timedelta(days=30))


class _Stripe:
    """The Stripe calls a send makes, mocked."""

    def __init__(self) -> None:
        self.transfers: list[dict[str, Any]] = []

    def __enter__(self) -> _Stripe:
        def transfer(**kwargs: Any) -> str:
            self.transfers.append(kwargs)
            return f"tr_{len(self.transfers)}"

        self._patches = [
            patch("billing.stripe_utils.find_payout_transfer", return_value=None),
            patch("billing.stripe_utils.charge_for_payment_intent", return_value="ch_cub"),
            patch("billing.stripe_utils.create_transfer", side_effect=transfer),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in self._patches:
            p.stop()


def describe_the_cubby_split():
    def it_defaults_to_85_for_the_guild_and_15_for_past_lives():
        settings_obj = BillingSettings.load()
        assert (settings_obj.cubby_guild_percent, settings_obj.cubby_pl_percent) == (Decimal("85"), Decimal("15"))

    def _data(**overrides: str) -> dict[str, str]:
        data = {
            "orientation_orientator_percent": "70",
            "orientation_guild_percent": "15",
            "orientation_pl_percent": "15",
            "class_instructor_percent": "70",
            "class_guild_percent": "10",
            "class_pl_percent": "20",
            "reservation_manager_percent": "70",
            "reservation_guild_percent": "15",
            "reservation_pl_percent": "15",
            "cubby_guild_percent": "80",
            "cubby_pl_percent": "20",
        }
        data.update(overrides)
        return data

    def it_saves_a_pair_that_adds_up_to_100():
        form = ReconciliationSettingsForm(_data(), instance=BillingSettings.load())
        assert form.is_valid(), form.errors
        form.save()
        assert BillingSettings.load().cubby_guild_percent == Decimal("80")

    def it_refuses_a_pair_that_does_not_add_up_with_the_triads_words():
        form = ReconciliationSettingsForm(_data(cubby_pl_percent="25"), instance=BillingSettings.load())
        assert not form.is_valid()
        assert form.errors["cubby_guild_percent"] == ["Cubby percentages must add up to 100 (currently 105)."]
        assert form.errors["cubby_pl_percent"] == ["Cubby percentages must add up to 100 (currently 105)."]

    def it_shows_the_pair_on_the_settings_tab(client: Client):
        User.objects.create_superuser(username="splitadmin", password="pass", email="splitadmin@example.com")
        client.login(username="splitadmin", password="pass")
        content = client.get("/billing/admin/dashboard/?tab=settings").content.decode()
        assert "Cubby Splits (= 100%)" in content
        assert 'name="cubby_guild_percent"' in content
        assert "data-cubby-split-note" in content


def describe_reconciliation():
    def it_files_a_cubby_line_with_the_guilds_share_to_the_frozen_payee():
        payee = _payee()
        payment = _payment(payee, amount=2000)
        result = build_reconciliation(window=_window(), include_voting=False)
        [line] = [line for line in result.lines if line.source_kind == "cubby"]
        assert (line.source_pk, line.item, line.payer_name) == (
            payment.pk,
            "Cubby Q7, Thornbury Kiln Guild",
            "Ottoline Brackwater",
        )
        assert line.shares == {("cubby_payee", payee.pk): 1700, ("pl", None): 300}
        assert line.stripe_payment_intent_id == "pi_cub_pay"
        [row] = result.groups[RecipientKind.CUBBY_PAYEE]
        assert (row.label, row.total_cents) == ("Wrenna Quillfeather", 1700)
        assert GROUP_LABELS[RecipientKind.CUBBY_PAYEE] == "Cubby Payees"
        assert result.cubby_percents == {"cubby_payee": Decimal("85.00"), "pl": Decimal("15.00")}

    def it_keeps_the_whole_payment_with_past_lives_without_a_payee():
        _payment(None, amount=2000)
        result = build_reconciliation(window=_window(), include_voting=False)
        [line] = [line for line in result.lines if line.source_kind == "cubby"]
        assert line.shares == {("pl", None): 2000}
        assert line.unassigned and "cubby payee unset" in line.note
        assert result.groups[RecipientKind.CUBBY_PAYEE] == []

    def it_nets_a_refunded_payment_to_nothing_with_no_note():
        _payment(None, amount=2000, refunded=True)
        result = build_reconciliation(window=_window(), include_voting=False)
        [line] = [line for line in result.lines if line.source_kind == "cubby"]
        assert (line.refunded_cents, line.net_cents, line.unassigned, line.note) == (2000, 0, False, "")

    def it_applies_an_override_and_an_omission():
        payee = _payee()
        payment = _payment(payee, amount=2000)
        TransactionAdjustment.objects.create(
            source_kind="cubby", source_pk=payment.pk, override_percents={"cubby_payee": "50", "pl": "50"}
        )
        [line] = [line for line in build_reconciliation(window=_window(), include_voting=False).lines]
        assert line.overridden and line.shares[("cubby_payee", payee.pk)] == 1000
        TransactionAdjustment.objects.filter(source_pk=payment.pk).update(override_percents=None, is_omitted=True)
        assert ShareSource().for_cubby_payment(payment) == 0

    def it_ignores_a_malformed_override_with_a_note():
        payee = _payee()
        payment = _payment(payee, amount=2000)
        TransactionAdjustment.objects.create(source_kind="cubby", source_pk=payment.pk, override_percents={"pl": "1"})
        [line] = build_reconciliation(window=_window(), include_voting=False).lines
        assert line.note == "override ignored (malformed)" and not line.overridden

    def it_freezes_the_cubby_split_into_a_snapshot():
        _payment(_payee(), amount=2000)
        result = build_reconciliation(window=_window(), include_voting=False)
        snapshot = ReconciliationSnapshot.objects.create(
            period_start=_window().start,
            period_end=_window().end,
            results=result.to_snapshot_dict(),
            grand_total_cents=result.grand_total_cents,
        )
        rebuilt = result_from_snapshot(snapshot)
        assert rebuilt.cubby_percents == {"cubby_payee": Decimal("85.00"), "pl": Decimal("15.00")}
        assert [row.total_cents for row in rebuilt.groups[RecipientKind.CUBBY_PAYEE]] == [1700]

    def it_lets_an_admin_adjust_a_cubby_line(client: Client):
        User.objects.create_superuser(username="adjadmin", password="pass", email="adjadmin@example.com")
        client.login(username="adjadmin", password="pass")
        payment = _payment(_payee(), amount=2000)
        form = client.get(reverse("billing_reconciliation_adjust_form", args=["cubby", payment.pk]))
        assert 'name="percent_cubby_payee"' in form.content.decode()
        response = client.post(
            reverse("billing_reconciliation_adjust", args=["cubby", payment.pk]),
            {"percent_cubby_payee": "60", "percent_pl": "40"},
        )
        assert response.status_code == 204
        assert TransactionAdjustment.objects.get().override_percents == {"cubby_payee": "60", "pl": "40"}

    def it_asks_for_both_percentages_of_a_pair():
        form = TransactionAdjustmentForm({"percent_cubby_payee": "60"}, source_kind="cubby", source_pk=1)
        assert not form.is_valid() and form.non_field_errors() == ["Enter both percentages."]
        form = TransactionAdjustmentForm(
            {"percent_cubby_payee": "60", "percent_pl": "30"}, source_kind="cubby", source_pk=1
        )
        assert not form.is_valid() and form.non_field_errors() == ["The two percentages must add up to 100."]


def describe_payouts():
    def it_pays_the_payee_the_guild_percent_once_48_hours_after_payment():
        _turn_on()
        payee = _payee()
        payment = _payment(payee, amount=2000, paid_at=_now() - timedelta(days=3), pi="pi_cub_send")
        with _Stripe() as fake:
            first = payouts.run_payouts(_now())
            second = payouts.run_payouts(_now())
        assert (first.created, first.sent, second.created, second.sent) == (1, 1, 0, 0)
        payout = Payout.objects.get()
        assert (payout.cubby_payment, payout.payee, payout.amount_cents, payout.status) == (
            payment,
            payee,
            1700,
            Payout.Status.SENT,
        )
        assert payout.due_at == payment.paid_at + timedelta(hours=48)
        assert (payout.source, payout.source_kind, payout.paid_on) == (payment, "cubby", payment.paid_at)
        assert len(fake.transfers) == 1 and fake.transfers[0]["source_transaction"] == "ch_cub"

    def it_waits_until_48_hours_have_passed():
        _turn_on()
        _payment(_payee(), paid_at=_now() - timedelta(hours=10))
        with _Stripe():
            assert payouts.run_payouts(_now()).created == 0

    def it_pays_nobody_without_a_payee_or_for_a_refunded_payment():
        _turn_on()
        _payment(None)
        _payment(None, refunded=True, pi="pi_cub_refunded")
        with _Stripe() as fake:
            run = payouts.run_payouts(_now())
        assert (run.created, fake.transfers, Payout.objects.count()) == (0, [], 0)

    def it_owes_by_hand_a_share_whose_payee_never_connected():
        _turn_on()
        _payment(_payee(connected=False))
        with _Stripe(), patch("billing.notifications.send_payouts_invite"):
            payouts.run_payouts(_now())
        payout = Payout.objects.get()
        assert (payout.status, payout.owed_reason) == (Payout.Status.OWED_MANUALLY, Payout.OwedReason.NOT_CONNECTED)

    def it_sends_nothing_when_a_refund_lands_before_the_send():
        _turn_on()
        payment = _payment(_payee())
        payout = Payout.objects.create(
            cubby_payment=payment, payee=payment.payee, amount_cents=1700, due_at=_now() - timedelta(hours=1)
        )
        CubbyPayment.objects.filter(pk=payment.pk).update(stripe_refund_id="re_late", payee=None)
        with _Stripe() as fake:
            payouts.run_payouts(_now())
        payout.refresh_from_db()
        assert (payout.status, fake.transfers) == (Payout.Status.NOTHING_DUE, [])

    def it_never_flags_a_refund_on_a_cubby_share():
        payment = _payment(_payee())
        payout = Payout.objects.create(cubby_payment=payment, payee=payment.payee, amount_cents=1700, due_at=_now())
        payouts.flag_refunds_past_a_send(payout)  # a cubby payment has no PaymentRefund rows to look at

    def it_freezes_a_cubby_share_into_a_month_end_snapshot():
        payment = _payment(_payee())
        snapshot = ReconciliationSnapshot.objects.create(
            period_start=_window().start, period_end=_window().end, results={}, grand_total_cents=0
        )
        with patch("billing.notifications.send_payouts_invite"):
            payouts.freeze_split(snapshot, _window())
        assert Payout.objects.get().cubby_payment == payment

    def it_lists_the_share_on_the_payees_payouts_tab():
        payee = _payee()
        _payment(payee)
        assert payouts.has_earning(payee)
        [row] = payouts.payee_earnings(payee).rows
        assert (row.item, row.kind, row.amount_cents, row.detail) == (
            "Cubby Q7",
            "cubby",
            1700,
            "Thornbury Kiln Guild, Ottoline B.",
        )

    def it_gives_the_reconciliation_line_its_payout_state():
        _turn_on()
        payee = _payee()
        payment = _payment(payee)
        result = build_reconciliation(window=_window(), include_voting=False)
        [line] = result.lines
        assert line.payout is not None and line.payout.state == "will_send"
        [row] = result.groups[RecipientKind.CUBBY_PAYEE]
        assert row.stripe_cents == 1700 and row.payout_badge is not None
        Payout.objects.create(
            cubby_payment=payment,
            payee=payee,
            amount_cents=1700,
            due_at=_now(),
            status=Payout.Status.SENT,
            sent_at=_now(),
        )
        [line] = build_reconciliation(window=_window(), include_voting=False).lines
        assert line.payout is not None and line.payout.state == "sent"

    def it_names_the_cubby_in_the_admins_payout_alert():
        payment = _payment(_payee())
        payout = Payout.objects.create(cubby_payment=payment, payee=payment.payee, amount_cents=1700, due_at=_now())
        assert _payout_item(payout) == "Cubby Q7 rent, Thornbury Kiln Guild"

    def it_labels_a_cubby_payout_needing_attention_on_reconciliation(client: Client):
        User.objects.create_superuser(username="attnadmin", password="pass", email="attnadmin@example.com")
        client.login(username="attnadmin", password="pass")
        payment = _payment(_payee())
        Payout.objects.create(
            cubby_payment=payment,
            payee=payment.payee,
            amount_cents=1700,
            due_at=_now(),
            status=Payout.Status.FAILED,
            failure_reason="Declined",
        )
        content = client.get("/billing/admin/dashboard/?tab=reconciliation").content.decode()
        assert "Cubby Q7 rent" in content


def describe_stripe_fees():
    def it_adds_a_fee_record_for_a_paid_cubby_invoice():
        _payment(None, pi="pi_cub_fee")
        assert "cubby" in stripe_fees.paid_sources()
        stripe_fees.add_missing_records()
        from billing.models import StripeFeeRecord

        assert StripeFeeRecord.objects.filter(stripe_payment_intent_id="pi_cub_fee").exists()


def describe_cancel_subscription_with_a_key():
    def it_says_no_proration_and_replays_the_first_answer():
        from billing import stripe_utils

        client = MagicMock()
        with patch("billing.stripe_utils._get_stripe_client", return_value=client):
            stripe_utils.cancel_subscription(subscription_id="sub_cub", idempotency_key="cubby-end-9")
        client.v1.subscriptions.cancel.assert_called_once_with(
            "sub_cub", params={"prorate": False, "invoice_now": False}, options={"idempotency_key": "cubby-end-9"}
        )

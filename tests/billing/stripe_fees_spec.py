"""BDD specs for Stripe fee, net and payout records (#984)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import stripe
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.db import IntegrityError
from django.db.models import Sum
from django.utils import timezone

from billing import stripe_fees, stripe_utils
from billing.exceptions import StripeAccountUnavailableError, StripeSettlementNotReadyError
from billing.models import (
    STRIPE_FEE_STUCK_AFTER_READS,
    BillingSettings,
    LateCancellationFee,
    StripeFeeRecord,
    StripeFeeRefund,
    TabCharge,
)
from billing.payments_panel import parse_window
from billing.reconciliation import _split_by_weight, attach_stripe_fees, build_reconciliation
from billing.stripe_utils import ChargeSettlement, RefundSettlement
from classes.factories import RegistrationFactory
from membership.models import EquipmentReservation, OrientationBooking
from tests.billing.factories import (
    LateCancellationFeeFactory,
    TabChargeFactory,
    TabEntryFactory,
    TabEntrySplitFactory,
    TabFactory,
)
from tests.membership.factories import EquipmentReservationFactory, OrientationBookingFactory

pytestmark = pytest.mark.django_db

ACTIVE_KEY = "sk_test_fee_active"
PREVIOUS_KEY = "sk_test_fee_previous"
AVAILABLE_TS = 1_800_000_000


def _configure(*, previous_key: str = PREVIOUS_KEY) -> None:
    bs = BillingSettings.load()
    bs.test_mode = False
    bs.connect_platform_secret_key = ACTIVE_KEY
    bs.previous_secret_key = previous_key
    bs.save()


def _missing() -> stripe.InvalidRequestError:
    return stripe.InvalidRequestError("No such payment_intent", param="id", code="resource_missing")


@pytest.fixture
def accounts() -> Iterator[dict[str, MagicMock]]:
    """One fake Stripe client per secret key, so a spec can see which account answered."""
    clients = {key: MagicMock(name=key) for key in (ACTIVE_KEY, PREVIOUS_KEY)}
    with patch("billing.stripe_utils.stripe.StripeClient", side_effect=lambda key: clients[key]):
        yield clients


def _balance(**overrides: Any) -> SimpleNamespace:
    values = {"id": "txn_fake_1", "amount": 5000, "fee": 175, "net": 4825, "available_on": AVAILABLE_TS}
    values.update(overrides)
    return SimpleNamespace(**values)


def _answer(client: MagicMock, *, charge: Any, refunds: list[Any] | None = None) -> None:
    client.v1.payment_intents.retrieve.return_value = SimpleNamespace(latest_charge=charge)
    client.v1.refunds.list.return_value.auto_paging_iter.return_value = refunds or []


def _charge(balance: Any = None) -> SimpleNamespace:
    return SimpleNamespace(id="ch_fake_1", balance_transaction=_balance() if balance is None else balance)


def _settlement(**overrides: Any) -> ChargeSettlement:
    values: dict[str, Any] = {
        "account": "active",
        "charge_id": "ch_fake_1",
        "balance_transaction_id": "txn_fake_1",
        "amount_cents": 5000,
        "fee_cents": 175,
        "net_cents": 4825,
        "available_on": datetime.fromtimestamp(AVAILABLE_TS, tz=UTC),
    }
    values.update(overrides)
    return ChargeSettlement(**values)


def _refund(refund_id: str = "re_fake_1", **overrides: Any) -> RefundSettlement:
    values: dict[str, Any] = {
        "refund_id": refund_id,
        "balance_transaction_id": f"txn_{refund_id}",
        "amount_cents": -1000,
        "fee_cents": -30,
        "net_cents": -970,
        "available_on": timezone.now() - timedelta(days=1),
    }
    values.update(overrides)
    return RefundSettlement(**values)


def _recorded(pi: str, **overrides: Any) -> StripeFeeRecord:
    values: dict[str, Any] = {
        "stripe_payment_intent_id": pi,
        "status": StripeFeeRecord.Status.RECORDED,
        "account": StripeFeeRecord.Account.ACTIVE,
        "stripe_balance_transaction_id": f"txn_{pi}",
        "amount_cents": 5000,
        "fee_cents": 175,
        "net_cents": 4825,
        "available_on": timezone.now() - timedelta(days=2),
    }
    values.update(overrides)
    return StripeFeeRecord.objects.create(**values)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def describe_StripeFeeRecord():
    def it_reads_unknown_until_stripe_answers():
        record = StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_unknown")
        assert record.fee_after_refunds_cents is None
        assert record.net_after_refunds_cents is None
        assert "pi_fake_unknown" in str(record)
        assert "Waiting on Stripe" in str(record)

    def it_folds_refunds_into_fee_and_net():
        record = _recorded("pi_fake_refunded", refunded_cents=2000, refund_fee_cents=-50)
        assert record.fee_after_refunds_cents == 125
        assert record.net_after_refunds_cents == 4825 - 2000 + 50

    def it_refuses_a_recorded_row_without_amounts():
        with pytest.raises(IntegrityError):
            StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_bad", status="recorded")

    def it_reads_a_pending_row_as_unknown_even_with_old_amounts():
        record = _recorded("pi_fake_stale_amounts", status=StripeFeeRecord.Status.PENDING)
        assert record.fee_after_refunds_cents is None
        assert record.net_after_refunds_cents is None

    def describe_record():
        def it_stores_the_charge_and_its_refunds_and_keeps_payout_ids_stamped_elsewhere():
            now = timezone.now()
            record = StripeFeeRecord.objects.create(
                stripe_payment_intent_id="pi_fake_r", attempts=3, last_error="old", stripe_payout_id="po_fake_kept"
            )
            StripeFeeRefund.objects.create(
                record=record,
                stripe_refund_id="re_fake_1",
                stripe_balance_transaction_id="txn_old",
                amount_cents=-1,
                fee_cents=0,
                net_cents=-1,
                available_on=now,
                stripe_payout_id="po_fake_refund_kept",
            )
            StripeFeeRefund.objects.create(
                record=record,
                stripe_refund_id="re_fake_failed_later",
                stripe_balance_transaction_id="txn_gone",
                amount_cents=-5,
                fee_cents=0,
                net_cents=-5,
                available_on=now,
            )
            # The stale in-memory row says no payout; the save must not blank the stamped one.
            record.stripe_payout_id = ""
            assert record.record(
                _settlement(account="previous", refunds=(_refund("re_fake_1"),)), read_started_at=now, now=now
            )
            assert record.status == StripeFeeRecord.Status.RECORDED
            assert (record.account, record.fee_cents, record.refunded_cents, record.refund_fee_cents) == (
                "previous",
                175,
                1000,
                -30,
            )
            assert (record.attempts, record.last_error, record.stripe_payout_id) == (0, "", "po_fake_kept")
            refund = StripeFeeRefund.objects.get()
            assert (refund.stripe_refund_id, refund.net_cents, refund.stripe_payout_id) == (
                "re_fake_1",
                -970,
                "po_fake_refund_kept",
            )

        def it_writes_nothing_when_a_refund_webhook_overtook_the_read():
            started = timezone.now()
            record = StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_race")
            StripeFeeRecord.refresh("pi_fake_race", started + timedelta(seconds=1))
            assert not record.record(_settlement(), read_started_at=started, now=started)
            record.refresh_from_db()
            assert record.status == StripeFeeRecord.Status.PENDING
            assert record.fee_cents is None

        def it_records_a_read_that_started_after_the_last_refresh():
            requested = timezone.now()
            record = StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_after")
            StripeFeeRecord.refresh("pi_fake_after", requested)
            assert record.record(_settlement(), read_started_at=requested + timedelta(seconds=1), now=requested)

    def it_counts_a_failed_read_and_trims_the_reason():
        now = timezone.now()
        record = StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_f")
        record.note_failed_read("x" * 900, now)
        record.refresh_from_db()
        assert record.status == StripeFeeRecord.Status.PENDING
        assert record.attempts == 1
        assert len(record.last_error) == 500
        assert record.last_attempt_at == now

    def it_marks_a_payment_neither_account_has():
        record = StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_nf")
        record.mark_not_found(timezone.now())
        record.refresh_from_db()
        assert record.status == StripeFeeRecord.Status.NOT_FOUND

    def it_adds_one_pending_record_per_payment_intent_and_learns_its_mode_once():
        StripeFeeRecord.add_pending("pi_fake_once")
        StripeFeeRecord.add_pending("pi_fake_once", livemode=True)
        StripeFeeRecord.add_pending("pi_fake_once", livemode=False)
        record = StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_once")
        assert record.livemode is True

    def it_reads_unreadable_as_pending_without_counting_a_failure():
        record = StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_nokey", attempts=2)
        record.note_unreadable("no key", timezone.now())
        record.refresh_from_db()
        assert (record.status, record.attempts, record.last_error) == ("pending", 2, "no key")

    def it_names_a_refund_row():
        record = _recorded("pi_fake_named")
        refund = StripeFeeRefund.objects.create(
            record=record,
            stripe_refund_id="re_fake_named",
            stripe_balance_transaction_id="txn_x",
            amount_cents=-1,
            fee_cents=0,
            net_cents=-1,
            available_on=timezone.now(),
        )
        assert "re_fake_named" in str(refund)

    def describe_refresh():
        @pytest.mark.parametrize("status", ["recorded", "pending", "not_found"])
        def it_sends_the_payment_back_to_pending_whatever_its_state_and_keeps_its_payout(status):
            now = timezone.now()
            _recorded("pi_fake_ref", status=status, stripe_payout_id="po_fake_1", attempts=4)
            assert StripeFeeRecord.refresh("pi_fake_ref", now) == 1
            record = StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_ref")
            assert (record.status, record.attempts, record.refresh_requested_at) == ("pending", 0, now)
            assert record.stripe_payout_id == "po_fake_1"

    def describe_queryset():
        def it_finds_records_stuck_for_a_week():
            StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_s", attempts=STRIPE_FEE_STUCK_AFTER_READS)
            StripeFeeRecord.objects.create(
                stripe_payment_intent_id="pi_fake_ok", attempts=STRIPE_FEE_STUCK_AFTER_READS - 1
            )
            assert list(StripeFeeRecord.objects.stuck().values_list("stripe_payment_intent_id", flat=True)) == [
                "pi_fake_s"
            ]

        def it_lists_available_unmatched_payments_as_awaiting_payout():
            now = timezone.now()
            _recorded("pi_fake_due")
            _recorded("pi_fake_paid", stripe_payout_id="po_fake")
            _recorded("pi_fake_later", available_on=now + timedelta(days=1))
            _recorded("pi_fake_nobt", stripe_balance_transaction_id="")
            StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_pending")
            ids = set(StripeFeeRecord.objects.awaiting_payout(now).values_list("stripe_payment_intent_id", flat=True))
            assert ids == {"pi_fake_due"}

        def it_reads_live_and_unknown_rows_live_and_only_test_rows_in_test_mode():
            for pi, livemode in (("pi_fake_live", True), ("pi_fake_test", False), ("pi_fake_unknown", None)):
                StripeFeeRecord.objects.create(stripe_payment_intent_id=pi, livemode=livemode)

            def ids(live: bool) -> set[str]:
                return set(
                    StripeFeeRecord.objects.readable(live=live).values_list("stripe_payment_intent_id", flat=True)
                )

            assert ids(True) == {"pi_fake_live", "pi_fake_unknown"}
            assert ids(False) == {"pi_fake_test"}

        def it_keys_records_by_payment_intent():
            _recorded("pi_fake_a")
            assert set(StripeFeeRecord.objects.by_payment_intent(["pi_fake_a", "pi_fake_b"])) == {"pi_fake_a"}


# ---------------------------------------------------------------------------
# Reading Stripe
# ---------------------------------------------------------------------------


def describe_retrieve_charge_settlement():
    def it_reads_fee_net_and_availability_from_the_active_account(accounts):
        _configure()
        _answer(accounts[ACTIVE_KEY], charge=_charge())
        settlement = stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_1")
        assert settlement == _settlement()
        accounts[ACTIVE_KEY].v1.payment_intents.retrieve.assert_called_once_with(
            "pi_fake_1", params={"expand": ["latest_charge.balance_transaction"]}
        )
        accounts[PREVIOUS_KEY].v1.payment_intents.retrieve.assert_not_called()

    def it_falls_back_to_the_previous_account(accounts):
        _configure()
        accounts[ACTIVE_KEY].v1.payment_intents.retrieve.side_effect = _missing()
        _answer(accounts[PREVIOUS_KEY], charge=_charge())
        settlement = stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_old")
        assert settlement is not None
        assert settlement.account == "previous"

    def it_answers_none_when_neither_account_has_it(accounts):
        _configure()
        accounts[ACTIVE_KEY].v1.payment_intents.retrieve.side_effect = _missing()
        accounts[PREVIOUS_KEY].v1.payment_intents.retrieve.side_effect = _missing()
        assert stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_gone") is None

    def it_will_not_call_a_payment_missing_without_a_previous_account_to_ask(accounts):
        _configure(previous_key="")
        accounts[ACTIVE_KEY].v1.payment_intents.retrieve.side_effect = _missing()
        with pytest.raises(StripeAccountUnavailableError):
            stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_gone")

    def it_reads_nothing_without_a_key_for_this_mode(accounts):
        bs = BillingSettings.load()
        bs.test_mode = True
        bs.connect_platform_secret_key = ACTIVE_KEY
        bs.save()
        with pytest.raises(StripeAccountUnavailableError):
            stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_live")
        accounts[ACTIVE_KEY].v1.payment_intents.retrieve.assert_not_called()

    def it_raises_any_other_stripe_refusal(accounts):
        _configure()
        accounts[ACTIVE_KEY].v1.payment_intents.retrieve.side_effect = stripe.InvalidRequestError(
            "bad", param="expand", code="parameter_invalid"
        )
        with pytest.raises(stripe.InvalidRequestError):
            stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_1")

    @pytest.mark.parametrize(
        "charge",
        [None, "ch_fake_unexpanded", SimpleNamespace(id="ch_fake", balance_transaction=None)],
        ids=["no_charge", "charge_not_expanded", "no_balance_transaction"],
    )
    def it_says_not_ready_before_stripe_settles(accounts, charge):
        _configure()
        _answer(accounts[ACTIVE_KEY], charge=charge)
        with pytest.raises(StripeSettlementNotReadyError):
            stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_1")

    def it_says_not_ready_when_the_balance_transaction_is_not_expanded(accounts):
        _configure()
        _answer(accounts[ACTIVE_KEY], charge=_charge(balance="txn_fake_unexpanded"))
        with pytest.raises(StripeSettlementNotReadyError):
            stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_1")

    def it_reads_each_succeeded_refund_on_its_own_balance_transaction(accounts):
        _configure()
        refunds = [
            SimpleNamespace(
                id="re_fake_1",
                status="succeeded",
                balance_transaction=_balance(id="txn_re_1", amount=-1000, fee=-30, net=-970),
            ),
            SimpleNamespace(id="re_fake_2", status="pending", balance_transaction=None),
        ]
        _answer(accounts[ACTIVE_KEY], charge=_charge(), refunds=refunds)
        settlement = stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_1")
        assert settlement is not None
        assert settlement.refunds == (
            RefundSettlement(
                refund_id="re_fake_1",
                balance_transaction_id="txn_re_1",
                amount_cents=-1000,
                fee_cents=-30,
                net_cents=-970,
                available_on=datetime.fromtimestamp(AVAILABLE_TS, tz=UTC),
            ),
        )
        assert (settlement.refunded_cents, settlement.refund_fee_cents) == (1000, -30)

    @pytest.mark.parametrize("balance", [None, "txn_fake_unexpanded"], ids=["none", "not_expanded"])
    def it_says_not_ready_while_a_succeeded_refund_is_unsettled(accounts, balance):
        _configure()
        refund = SimpleNamespace(id="re_fake_1", status="succeeded", balance_transaction=balance)
        _answer(accounts[ACTIVE_KEY], charge=_charge(), refunds=[refund])
        with pytest.raises(StripeSettlementNotReadyError):
            stripe_utils.retrieve_charge_settlement(payment_intent_id="pi_fake_1")


def describe_fee_accounts():
    def it_offers_only_accounts_with_a_key_for_this_mode(accounts):
        _configure(previous_key="")
        assert list(stripe_utils.fee_accounts()) == ["active"]
        _configure()
        assert list(stripe_utils.fee_accounts()) == ["active", "previous"]


def describe_iter_paid_payouts():
    def it_yields_each_paid_payout_with_its_balance_transactions():
        client = MagicMock()
        client.v1.payouts.list.return_value.auto_paging_iter.return_value = [SimpleNamespace(id="po_fake_1")]
        client.v1.balance_transactions.list.return_value.auto_paging_iter.return_value = [
            SimpleNamespace(id="txn_fake_1"),
            SimpleNamespace(id="txn_fake_2"),
        ]
        since = datetime.fromtimestamp(AVAILABLE_TS, tz=UTC)
        assert list(stripe_utils.iter_paid_payouts(client=client, arrived_since=since)) == [
            ("po_fake_1", ["txn_fake_1", "txn_fake_2"])
        ]
        client.v1.payouts.list.assert_called_once_with(
            params={"status": "paid", "arrival_date": {"gte": AVAILABLE_TS}, "limit": 100}
        )
        client.v1.balance_transactions.list.assert_called_once_with(params={"payout": "po_fake_1", "limit": 100})


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


def _every_paid_source() -> dict[str, Any]:
    return {
        "tab": TabChargeFactory(status=TabCharge.Status.SUCCEEDED, stripe_payment_intent_id="pi_fake_tab"),
        "class": RegistrationFactory(amount_paid_cents=5000, stripe_payment_id="pi_fake_class"),
        "orientation": OrientationBookingFactory(
            amount_paid_cents=2500, stripe_payment_id="pi_fake_orient", status=OrientationBooking.Status.CONFIRMED
        ),
        "reservation": EquipmentReservationFactory(amount_paid_cents=3000, stripe_payment_id="pi_fake_resv"),
        "late_fee": LateCancellationFeeFactory(
            status=LateCancellationFee.Status.REFUNDED, stripe_payment_id="pi_fake_late"
        ),
    }


def describe_add_missing_records():
    def it_adds_every_paid_payment_the_payment_report_lists():
        _every_paid_source()
        assert stripe_fees.add_missing_records() == 5
        assert set(StripeFeeRecord.objects.values_list("stripe_payment_intent_id", flat=True)) == {
            "pi_fake_tab",
            "pi_fake_class",
            "pi_fake_orient",
            "pi_fake_resv",
            "pi_fake_late",
        }
        assert set(StripeFeeRecord.objects.values_list("status", flat=True)) == {StripeFeeRecord.Status.PENDING}

    def it_skips_unpaid_holds_failed_charges_and_money_stripe_never_saw():
        TabChargeFactory(status=TabCharge.Status.FAILED, stripe_payment_intent_id="pi_fake_failed")
        RegistrationFactory(amount_paid_cents=0, stripe_payment_id="pi_fake_free")
        RegistrationFactory(amount_paid_cents=4000, stripe_payment_id="")
        OrientationBookingFactory(
            amount_paid_cents=2500, stripe_payment_id="pi_fake_hold", status=OrientationBooking.Status.PENDING_PAYMENT
        )
        EquipmentReservationFactory(
            amount_paid_cents=3000,
            stripe_payment_id="pi_fake_rhold",
            status=EquipmentReservation.Status.PENDING_PAYMENT,
        )
        LateCancellationFeeFactory(status=LateCancellationFee.Status.UNPAID, stripe_payment_id="pi_fake_unpaid")
        assert stripe_fees.add_missing_records() == 0
        assert not StripeFeeRecord.objects.exists()

    def it_is_safe_to_rerun():
        _every_paid_source()
        _recorded("pi_fake_class")
        assert stripe_fees.add_missing_records() == 4
        assert stripe_fees.add_missing_records() == 0
        assert StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_class").fee_cents == 175


def describe_fill():
    def _pending() -> StripeFeeRecord:
        return StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_fill")

    def it_records_the_settlement():
        record = _pending()
        report = stripe_fees.SyncReport()
        settlement = _settlement(refunds=(_refund(),))
        with patch.object(stripe_utils, "retrieve_charge_settlement", return_value=settlement):
            stripe_fees.fill(record, report, timezone.now())
        record.refresh_from_db()
        assert record.status == StripeFeeRecord.Status.RECORDED
        assert (record.fee_cents, record.net_cents, record.refunded_cents) == (175, 4825, 1000)
        assert report.recorded == 1

    def it_leaves_the_record_pending_when_a_refund_lands_mid_read():
        record = _pending()
        report = stripe_fees.SyncReport()

        def refund_lands_during_the_read(*, payment_intent_id: str) -> ChargeSettlement:
            StripeFeeRecord.refresh(payment_intent_id, timezone.now() + timedelta(seconds=1))
            return _settlement()

        with patch.object(stripe_utils, "retrieve_charge_settlement", side_effect=refund_lands_during_the_read):
            stripe_fees.fill(record, report, timezone.now())
        record.refresh_from_db()
        assert record.status == StripeFeeRecord.Status.PENDING
        assert report.stale == 1

    def it_leaves_the_record_pending_without_a_key_to_read_it_with():
        record = _pending()
        report = stripe_fees.SyncReport()
        with patch.object(
            stripe_utils, "retrieve_charge_settlement", side_effect=StripeAccountUnavailableError("no key")
        ):
            stripe_fees.fill(record, report, timezone.now())
        record.refresh_from_db()
        assert (record.status, record.attempts) == ("pending", 0)
        assert report.unreadable == 1

    def it_waits_when_stripe_has_not_settled():
        record = _pending()
        report = stripe_fees.SyncReport()
        with patch.object(
            stripe_utils, "retrieve_charge_settlement", side_effect=StripeSettlementNotReadyError("not yet")
        ):
            stripe_fees.fill(record, report, timezone.now())
        record.refresh_from_db()
        assert (record.status, record.attempts, record.last_error) == ("pending", 1, "not yet")
        assert report.waiting == 1

    def it_keeps_the_record_pending_through_a_stripe_outage():
        record = _pending()
        report = stripe_fees.SyncReport()
        with patch.object(stripe_utils, "retrieve_charge_settlement", side_effect=stripe.APIConnectionError("down")):
            stripe_fees.fill(record, report, timezone.now())
        record.refresh_from_db()
        assert record.status == StripeFeeRecord.Status.PENDING
        assert record.last_error.startswith("Stripe could not answer")
        assert report.stripe_errors == 1

    def it_marks_a_payment_neither_account_has():
        record = _pending()
        report = stripe_fees.SyncReport()
        with patch.object(stripe_utils, "retrieve_charge_settlement", return_value=None):
            stripe_fees.fill(record, report, timezone.now())
        record.refresh_from_db()
        assert record.status == StripeFeeRecord.Status.NOT_FOUND
        assert report.not_found == 1


def _payout_answers(payouts: dict[str, list[tuple[str, list[str]]]]) -> Any:
    """Patch Stripe so each account (by its fake client) lists the given payouts."""
    clients = {account: MagicMock(name=account) for account in payouts}
    by_client = {id(client): payouts[account] for account, client in clients.items()}
    return (
        patch.object(stripe_utils, "fee_accounts", return_value=clients),
        patch.object(
            stripe_utils,
            "iter_paid_payouts",
            side_effect=lambda client, arrived_since: iter(by_client[id(client)]),
        ),
    )


def _refund_row(record: StripeFeeRecord, refund_id: str, **overrides: Any) -> StripeFeeRefund:
    values: dict[str, Any] = {
        "record": record,
        "stripe_refund_id": refund_id,
        "stripe_balance_transaction_id": f"txn_{refund_id}",
        "amount_cents": -1000,
        "fee_cents": 0,
        "net_cents": -1000,
        "available_on": timezone.now() - timedelta(days=1),
    }
    values.update(overrides)
    return StripeFeeRefund.objects.create(**values)


def describe_stamp_payouts():
    def it_matches_charges_to_the_payout_that_carried_them():
        _recorded("pi_fake_1")
        _recorded("pi_fake_2")
        _recorded("pi_fake_old", account=StripeFeeRecord.Account.PREVIOUS)
        accounts_patch, payouts_patch = _payout_answers(
            {
                "active": [("po_fake_empty", []), ("po_fake_a", ["txn_pi_fake_1", "txn_pi_fake_old"])],
                "previous": [("po_fake_b", ["txn_pi_fake_old"])],
            }
        )
        report = stripe_fees.SyncReport()
        with accounts_patch, payouts_patch:
            stripe_fees.stamp_payouts(report, timezone.now(), live=True, backfill=False)
        stamped = dict(StripeFeeRecord.objects.values_list("stripe_payment_intent_id", "stripe_payout_id"))
        assert stamped == {"pi_fake_1": "po_fake_a", "pi_fake_2": "", "pi_fake_old": "po_fake_b"}
        assert report.payouts_stamped == 2

    def it_puts_a_later_refund_in_its_own_payout_so_each_payout_sums_to_its_deposit():
        charge_row = _recorded("pi_fake_dep", amount_cents=5000, fee_cents=175, net_cents=4825)
        _refund_row(charge_row, "re_fake_dep", amount_cents=-2000, fee_cents=0, net_cents=-2000)
        other = _recorded("pi_fake_dep2", amount_cents=3000, fee_cents=117, net_cents=2883)
        accounts_patch, payouts_patch = _payout_answers(
            {
                "active": [
                    ("po_fake_monday", ["txn_pi_fake_dep"]),
                    ("po_fake_friday", ["txn_re_fake_dep", "txn_pi_fake_dep2"]),
                ]
            }
        )
        with accounts_patch, payouts_patch:
            stripe_fees.stamp_payouts(stripe_fees.SyncReport(), timezone.now(), live=True, backfill=False)
        charge_row.refresh_from_db()
        other.refresh_from_db()
        assert (charge_row.stripe_payout_id, other.stripe_payout_id) == ("po_fake_monday", "po_fake_friday")
        assert StripeFeeRefund.objects.get().stripe_payout_id == "po_fake_friday"

        def deposit(payout_id: str) -> int:
            charges = StripeFeeRecord.objects.filter(stripe_payout_id=payout_id).aggregate(total=Sum("net_cents"))
            refunds = StripeFeeRefund.objects.filter(stripe_payout_id=payout_id).aggregate(total=Sum("net_cents"))
            return (charges["total"] or 0) + (refunds["total"] or 0)

        assert deposit("po_fake_monday") == 4825
        assert deposit("po_fake_friday") == 2883 - 2000

    def it_looks_back_35_days_daily_and_all_the_way_on_a_backfill():
        now = timezone.now()
        old = now - timedelta(days=200)
        record = _recorded("pi_fake_ancient", available_on=now - timedelta(days=1))
        _refund_row(record, "re_fake_ancient", available_on=old)
        calls: list[datetime] = []

        def record_call(client: Any, arrived_since: datetime) -> Iterator[tuple[str, list[str]]]:
            calls.append(arrived_since)
            return iter([])

        with (
            patch.object(stripe_utils, "fee_accounts", return_value={"active": MagicMock()}),
            patch.object(stripe_utils, "iter_paid_payouts", side_effect=record_call),
        ):
            stripe_fees.stamp_payouts(stripe_fees.SyncReport(), now, live=True, backfill=False)
            stripe_fees.stamp_payouts(stripe_fees.SyncReport(), now, live=True, backfill=True)
        assert calls == [now - stripe_fees.PAYOUT_LOOKBACK, old - stripe_fees.PAYOUT_ARRIVAL_SLACK]

    def it_skips_an_account_with_no_key_and_still_matches_the_other():
        _recorded("pi_fake_cur")
        _recorded("pi_fake_closed", account=StripeFeeRecord.Account.PREVIOUS)
        accounts_patch, payouts_patch = _payout_answers({"active": [("po_fake_cur", ["txn_pi_fake_cur"])]})
        report = stripe_fees.SyncReport()
        with accounts_patch, payouts_patch:
            stripe_fees.stamp_payouts(report, timezone.now(), live=True, backfill=True)
        assert report.skipped_accounts == ["previous"]
        assert StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_cur").stripe_payout_id == "po_fake_cur"

    def it_matches_only_rows_that_suit_the_mode():
        _recorded("pi_fake_livepay", livemode=True)
        with (
            patch.object(stripe_utils, "fee_accounts") as accounts_call,
            patch.object(stripe_utils, "iter_paid_payouts") as payouts,
        ):
            stripe_fees.stamp_payouts(stripe_fees.SyncReport(), timezone.now(), live=False, backfill=True)
        payouts.assert_not_called()
        accounts_call.assert_called_once()

    def it_asks_stripe_nothing_when_nothing_awaits_a_payout():
        with (
            patch.object(stripe_utils, "fee_accounts", return_value={}),
            patch.object(stripe_utils, "iter_paid_payouts") as payouts,
        ):
            stripe_fees.stamp_payouts(stripe_fees.SyncReport(), timezone.now(), live=True, backfill=True)
        payouts.assert_not_called()


def describe_not_found_sources():
    def it_names_each_paid_payment_stripe_does_not_have_by_kind_and_pk():
        sources = _every_paid_source()
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_late", status="not_found")
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_tab", status="not_found")
        assert stripe_fees.not_found_sources() == sorted(
            [("late_fee", sources["late_fee"].pk), ("tab", sources["tab"].pk)]
        )

    def it_is_empty_when_everything_was_found():
        assert stripe_fees.not_found_sources() == []


def describe_sync():
    @pytest.fixture(autouse=True)
    def live_mode() -> None:
        _configure()

    def it_adds_fills_and_stamps_in_one_run():
        RegistrationFactory(amount_paid_cents=5000, stripe_payment_id="pi_fake_s1")
        past = _settlement(available_on=timezone.now() - timedelta(days=3))
        with (
            patch.object(stripe_utils, "retrieve_charge_settlement", return_value=past),
            patch.object(stripe_utils, "fee_accounts", return_value={"active": MagicMock()}),
            patch.object(stripe_utils, "iter_paid_payouts", return_value=iter([("po_fake_s", ["txn_fake_1"])])),
        ):
            report = stripe_fees.sync()
        record = StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_s1")
        assert (report.added, report.recorded, report.payouts_stamped) == (1, 1, 1)
        assert record.stripe_payout_id == "po_fake_s"
        assert report.not_found_sources == []

    def it_reads_at_most_the_daily_limit_never_tried_first_and_all_on_a_backfill():
        now = timezone.now()
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_tried", last_attempt_at=now, attempts=1)
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_new")
        read: list[str] = []

        def answer(*, payment_intent_id: str) -> None:
            read.append(payment_intent_id)

        with (
            patch.object(stripe_fees, "DAILY_READ_LIMIT", 1),
            patch.object(stripe_utils, "retrieve_charge_settlement", side_effect=answer),
        ):
            stripe_fees.sync()
            assert read == ["pi_fake_new"]
            StripeFeeRecord.objects.update(status=StripeFeeRecord.Status.PENDING)
            read.clear()
            stripe_fees.sync(backfill=True)
        assert sorted(read) == ["pi_fake_new", "pi_fake_tried"]

    def it_lists_what_it_could_not_find_on_a_backfill():
        reg = RegistrationFactory(amount_paid_cents=5000, stripe_payment_id="pi_fake_gone")
        with patch.object(stripe_utils, "retrieve_charge_settlement", return_value=None):
            report = stripe_fees.sync(backfill=True)
        assert report.not_found_sources == [("class", reg.pk)]

    def it_asks_again_about_not_found_payments_on_a_backfill_only():
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_retry", status="not_found")
        with patch.object(stripe_utils, "retrieve_charge_settlement", return_value=_settlement()) as read:
            stripe_fees.sync()
            read.assert_not_called()
            stripe_fees.sync(backfill=True)
        assert StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_retry").status == "recorded"

    def it_never_reads_live_or_unknown_payments_in_test_mode():
        bs = BillingSettings.load()
        bs.test_mode = True
        bs.save()
        RegistrationFactory(amount_paid_cents=5000, stripe_payment_id="pi_fake_unknown_mode")
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_live_mode", livemode=True)
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_test_mode", livemode=False)
        with patch.object(stripe_utils, "retrieve_charge_settlement", return_value=None) as read:
            stripe_fees.sync()
        read.assert_called_once_with(payment_intent_id="pi_fake_test_mode")
        assert set(
            StripeFeeRecord.objects.filter(status="pending").values_list("stripe_payment_intent_id", flat=True)
        ) == {
            "pi_fake_unknown_mode",
            "pi_fake_live_mode",
        }


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def describe_sync_stripe_fees_command():
    def _run(*args: str) -> str:
        out = StringIO()
        call_command("sync_stripe_fees", *args, stdout=out)
        return out.getvalue()

    def it_reports_its_counts():
        with patch.object(stripe_fees, "sync", return_value=stripe_fees.SyncReport(added=2, recorded=2)):
            output = _run()
        assert "Added 2, recorded 2" in output
        assert "neither Stripe account" not in output

    def it_lists_every_payment_it_could_not_find_on_a_backfill():
        report = stripe_fees.SyncReport(not_found=1, not_found_sources=[("late_fee", 7)])
        with patch.object(stripe_fees, "sync", return_value=report) as sync:
            output = _run("--backfill")
        sync.assert_called_once_with(backfill=True)
        assert "1 paid payment(s) are on neither Stripe account" in output
        assert "  late_fee 7" in output

    def it_names_an_account_whose_payouts_it_skipped():
        with patch.object(stripe_fees, "sync", return_value=stripe_fees.SyncReport(skipped_accounts=["previous"])):
            assert "Skipped previous account payouts" in _run()

    def it_says_so_when_a_backfill_found_everything():
        with patch.object(stripe_fees, "sync", return_value=stripe_fees.SyncReport()):
            assert "Every paid payment was found on Stripe." in _run("--backfill")

    def it_fails_the_run_when_stripe_could_not_answer():
        with patch.object(stripe_fees, "sync", return_value=stripe_fees.SyncReport(stripe_errors=1)):
            with pytest.raises(CommandError, match="1 Stripe read"):
                _run()

    def it_fails_the_run_when_a_payment_has_been_stuck_for_a_week():
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_stuck", attempts=STRIPE_FEE_STUCK_AFTER_READS)
        with patch.object(stripe_fees, "sync", return_value=stripe_fees.SyncReport()):
            with pytest.raises(CommandError, match="1 payment"):
                _run()


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def _event(event_type: str, obj: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": event_type, "data": {"object": obj}, **extra}


def describe_note_event():
    def it_adds_a_pending_record_when_a_payment_intent_succeeds():
        stripe_fees.note_event(_event("payment_intent.succeeded", {"id": "pi_fake_w1"}, livemode=True))
        record = StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_w1")
        assert (record.status, record.livemode) == ("pending", True)

    def it_adds_a_pending_record_for_a_paid_checkout():
        stripe_fees.note_event(
            _event("checkout.session.completed", {"payment_status": "paid", "payment_intent": "pi_fake_w2"})
        )
        assert StripeFeeRecord.objects.filter(stripe_payment_intent_id="pi_fake_w2").exists()

    @pytest.mark.parametrize(
        "session",
        [
            {"payment_status": "unpaid", "payment_intent": "pi_fake_w3"},
            {"payment_status": "paid", "payment_intent": None},
        ],
        ids=["unpaid", "no_payment_intent"],
    )
    def it_skips_a_checkout_that_moved_no_money(session):
        stripe_fees.note_event(_event("checkout.session.async_payment_succeeded", session))
        assert not StripeFeeRecord.objects.exists()

    @pytest.mark.parametrize("event_type", ["charge.refunded", "refund.updated"])
    def it_sends_a_refunded_payment_back_to_pending(event_type):
        _recorded("pi_fake_w4")
        stripe_fees.note_event(_event(event_type, {"payment_intent": "pi_fake_w4"}))
        assert StripeFeeRecord.objects.get(stripe_payment_intent_id="pi_fake_w4").status == "pending"

    def it_adds_a_record_for_a_refund_on_a_payment_it_has_not_seen():
        stripe_fees.note_event(_event("charge.refunded", {"payment_intent": "pi_fake_w5"}))
        assert StripeFeeRecord.objects.filter(stripe_payment_intent_id="pi_fake_w5").exists()

    def it_ignores_a_refund_with_no_payment_intent():
        stripe_fees.note_event(_event("refund.updated", {"payment_intent": None}))
        assert not StripeFeeRecord.objects.exists()

    def it_ignores_other_event_types():
        stripe_fees.note_event(_event("payment_intent.payment_failed", {"id": "pi_fake_w6"}))
        assert not StripeFeeRecord.objects.exists()

    def it_skips_connected_account_events():
        stripe_fees.note_event(_event("payment_intent.succeeded", {"id": "pi_fake_w7"}, account="acct_fake"))
        assert not StripeFeeRecord.objects.exists()


def _post_event(event: dict[str, Any]) -> int:
    """POST ``event`` to the real webhook view, with signature checking stubbed out."""
    fake = MagicMock(type=event["type"])
    fake.to_dict.return_value = event
    with patch.object(stripe_utils, "construct_webhook_event", return_value=fake):
        return Client().post(reverse("billing_stripe_webhook"), data=b"{}", content_type="application/json").status_code


def describe_webhook_wiring():
    def it_confirms_the_payment_then_adds_its_record():
        charge = TabChargeFactory(status=TabCharge.Status.PROCESSING, stripe_payment_intent_id="pi_fake_wire")
        event = _event(
            "payment_intent.succeeded",
            {"id": "pi_fake_wire", "charges": {"data": [{"id": "ch_fake_wire", "receipt_url": ""}]}},
        )
        with patch("billing.webhook_handlers.send_receipt"):
            assert _post_event(event) == 200
        charge.refresh_from_db()
        assert charge.status == TabCharge.Status.SUCCEEDED
        assert StripeFeeRecord.objects.filter(stripe_payment_intent_id="pi_fake_wire").exists()

    def it_adds_no_record_when_the_handler_fails():
        event = _event("payment_intent.succeeded", {"id": "pi_fake_broken"})
        with patch("billing.webhook_handlers.TabCharge.objects.get", side_effect=RuntimeError("handler failed")):
            assert _post_event(event) == 500
        assert not StripeFeeRecord.objects.exists()


# ---------------------------------------------------------------------------
# Reconciliation lines
# ---------------------------------------------------------------------------


def describe_split_by_weight():
    def it_splits_in_proportion_and_sums_exactly():
        assert _split_by_weight(100, [1, 1, 1]) == [33, 33, 34]
        assert _split_by_weight(-101, [1, 1]) == [-51, -50]
        assert sum(_split_by_weight(175, [5000, 2500])) == 175


def describe_reconciliation_fees():
    def _lines_by_pk(kind: str) -> dict[int, Any]:
        result = build_reconciliation(window=parse_window("", ""), include_voting=False)
        return {line.source_pk: line for line in result.lines if line.source_kind == kind}

    def _paid_class(pi: str, cents: int = 5000) -> Any:
        return RegistrationFactory(amount_paid_cents=cents, stripe_payment_id=pi, confirmed_at=timezone.now())

    def it_carries_stripe_fee_and_net_on_each_line():
        reg = _paid_class("pi_fake_line", 5000)
        _recorded("pi_fake_line", refunded_cents=1000, refund_fee_cents=0)
        line = _lines_by_pk("class")[reg.pk]
        assert line.stripe_payment_intent_id == "pi_fake_line"
        assert (line.stripe_fee_cents, line.stripe_net_cents) == (175, 3825)

    def it_reads_unknown_while_a_refund_waits_to_be_read_again():
        reg = _paid_class("pi_fake_refunding", 5000)
        _recorded("pi_fake_refunding")
        StripeFeeRecord.refresh("pi_fake_refunding", timezone.now())
        line = _lines_by_pk("class")[reg.pk]
        assert (line.stripe_fee_cents, line.stripe_net_cents) == (None, None)

    def it_reads_unknown_without_a_record_or_before_stripe_answers():
        no_record = _paid_class("pi_fake_none")
        pending = _paid_class("pi_fake_pend")
        cash = _paid_class("")
        StripeFeeRecord.objects.create(stripe_payment_intent_id="pi_fake_pend")
        lines = _lines_by_pk("class")
        for reg in (no_record, pending, cash):
            assert lines[reg.pk].stripe_fee_cents is None
            assert lines[reg.pk].stripe_net_cents is None

    def it_splits_one_payment_across_the_lines_that_share_it():
        first = _paid_class("pi_fake_shared", 5000)
        second = _paid_class("pi_fake_shared", 2500)
        _recorded("pi_fake_shared", amount_cents=7500, fee_cents=300, net_cents=7200)
        lines = _lines_by_pk("class")
        assert (lines[first.pk].stripe_fee_cents, lines[second.pk].stripe_fee_cents) == (200, 100)
        assert lines[first.pk].stripe_net_cents + lines[second.pk].stripe_net_cents == 7200

    def it_gives_a_tab_charge_line_its_fee():
        tab = TabFactory()
        charge = TabChargeFactory(tab=tab, status=TabCharge.Status.SUCCEEDED, stripe_payment_intent_id="pi_fake_tabl")
        TabCharge.objects.filter(pk=charge.pk).update(charged_at=timezone.now())
        entry = TabEntryFactory(tab=tab, tab_charge=charge, amount=charge.amount)
        TabEntrySplitFactory(entry=entry, amount=charge.amount)
        _recorded("pi_fake_tabl", fee_cents=320, net_cents=9680, amount_cents=10000)
        line = _lines_by_pk("tab")[charge.pk]
        assert (line.stripe_fee_cents, line.stripe_net_cents) == (320, 9680)

    def it_leaves_lines_without_any_payment_intent_untouched():
        assert attach_stripe_fees([]) == []

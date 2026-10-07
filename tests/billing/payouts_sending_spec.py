"""Payouts part 2 (#662): the Payout ledger, the send run, the Reconciliation split and the earnings list."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
import stripe
from django.core.management import call_command
from django.db import IntegrityError

from billing import payouts
from billing.models import (
    BillingSettings,
    Payout,
    PayoutAccount,
    PaymentRefund,
    ReconciliationSnapshot,
    TransactionAdjustment,
)
from billing.payments_panel import PanelWindow
from billing.reconciliation import RecipientKind, build_reconciliation, result_from_snapshot
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory, UserFactory
from classes.models import Registration
from core.models import EventDelivery
from membership.models import Member, OrientationBooking
from tests.membership.factories import MemberFactory, OrientationBookingFactory, OrientationSlotFactory

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
CONNECTED = NOW - timedelta(days=20)


@pytest.fixture(autouse=True)
def _frozen_now():
    """Stamps written during a run (attempted_at, sent_at) read the same clock the run is given."""
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


def _turn_on(*, since: datetime = NOW - timedelta(days=30)) -> None:
    bs = BillingSettings.load()
    bs.connect_enabled = True
    bs.class_instructor_percent = Decimal("70")
    bs.class_guild_percent = Decimal("10")
    bs.class_pl_percent = Decimal("20")
    bs.orientation_orientator_percent = Decimal("70")
    bs.orientation_guild_percent = Decimal("15")
    bs.orientation_pl_percent = Decimal("15")
    bs.save()
    BillingSettings.objects.filter(pk=1).update(payouts_on_since=since)


def _payee(*, active_since: datetime | None = CONNECTED, status: str = PayoutAccount.Status.ACTIVE) -> Member:
    member = MemberFactory()
    if active_since is not None:
        PayoutAccount.objects.create(
            member=member,
            stripe_account_id=f"acct_{member.pk}",
            livemode=False,
            status=status,
            active_since=active_since,
        )
    return member


def _registration(
    instructor: Member,
    *,
    paid_at: datetime = NOW - timedelta(days=10),
    class_at: datetime = NOW - timedelta(days=3),
    amount: int = 10000,
    pi: str = "pi_123",
    later_sessions: int = 0,
) -> Registration:
    offering = ClassOfferingFactory(instructor=instructor)
    ClassSessionFactory(class_offering=offering, starts_at=class_at)
    for day in range(later_sessions):
        ClassSessionFactory(class_offering=offering, starts_at=class_at + timedelta(days=day + 1))
    reg = RegistrationFactory(class_offering=offering, amount_paid_cents=amount, stripe_payment_id=pi)
    Registration.objects.filter(pk=reg.pk).update(confirmed_at=paid_at)
    reg.refresh_from_db()
    return reg


def _booking(
    orientor: Member,
    *,
    paid_at: datetime = NOW - timedelta(days=10),
    slot_at: datetime = NOW - timedelta(days=3),
    amount: int = 5000,
) -> OrientationBooking:
    slot = OrientationSlotFactory(starts_at=slot_at, ends_at=slot_at + timedelta(hours=1))
    booking = OrientationBookingFactory(
        slot=slot,
        oriented_by=orientor,
        amount_paid_cents=amount,
        stripe_payment_id="pi_456",
        status=OrientationBooking.Status.CONFIRMED,
    )
    OrientationBooking.objects.filter(pk=booking.pk).update(requested_at=paid_at)
    booking.refresh_from_db()
    return booking


class _Stripe:
    """The two Stripe calls a send makes, mocked; ``fail`` makes the transfer raise."""

    def __init__(self, fail: str | None = None, error: type[stripe.StripeError] | None = None) -> None:
        self.fail = fail
        self.error = error
        self.transfers: list[dict[str, Any]] = []

    def __enter__(self) -> _Stripe:
        def transfer(**kwargs: Any) -> str:
            self.transfers.append(kwargs)
            if self.error is not None:
                raise self.error("Request timed out.")
            if self.fail:
                raise stripe.InvalidRequestError(self.fail, param=None)
            return f"tr_{len(self.transfers)}"

        self._patches = [
            patch("billing.stripe_utils.charge_for_payment_intent", return_value="ch_789"),
            patch("billing.stripe_utils.create_transfer", side_effect=transfer),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in self._patches:
            p.stop()


def describe_run_payouts():
    def it_does_nothing_while_payouts_are_off():
        _registration(_payee())
        BillingSettings.objects.filter(pk=1).update(payouts_on_since=NOW - timedelta(days=30))
        with _Stripe() as fake:
            run = payouts.run_payouts(NOW)
        assert run.created == 0
        assert fake.transfers == []
        assert not Payout.objects.exists()

    def it_sends_the_instructor_share_linked_to_the_charge():
        _turn_on()
        instructor = _payee()
        reg = _registration(instructor)
        with _Stripe() as fake:
            run = payouts.run_payouts(NOW)
        payout = Payout.objects.get()
        assert (run.created, run.sent) == (1, 1)
        assert (payout.registration, payout.payee, payout.amount_cents) == (reg, instructor, 7000)
        assert payout.status == Payout.Status.SENT
        assert payout.stripe_transfer_id == "tr_1"
        assert fake.transfers == [
            {
                "amount_cents": 7000,
                "destination": f"acct_{instructor.pk}",
                "source_transaction": "ch_789",
                "idempotency_key": f"payout-{payout.pk}-a1",
                "metadata": {"payout_pk": str(payout.pk)},
            }
        ]

    def it_waits_48_hours_after_the_first_session():
        _turn_on()
        reg = _registration(_payee(), class_at=NOW - timedelta(hours=47), later_sessions=2)
        with _Stripe():
            payouts.run_payouts(NOW)
            assert not Payout.objects.exists()
            payouts.run_payouts(NOW + timedelta(hours=1))
        assert Payout.objects.get().due_at == reg.class_offering.sessions.order_by(
            "starts_at"
        ).first().starts_at + timedelta(hours=48)

    def it_waits_48_hours_after_a_late_payment():
        _turn_on()
        _registration(_payee(), class_at=NOW - timedelta(days=5), paid_at=NOW - timedelta(hours=47))
        with _Stripe():
            payouts.run_payouts(NOW)
            assert not Payout.objects.exists()
            payouts.run_payouts(NOW + timedelta(hours=2))
        assert Payout.objects.get().status == Payout.Status.SENT

    def it_sends_the_orientor_share_48_hours_after_the_slot():
        _turn_on()
        orientor = _payee()
        booking = _booking(orientor)
        with _Stripe() as fake:
            payouts.run_payouts(NOW)
        payout = Payout.objects.get()
        assert (payout.orientation_booking, payout.payee, payout.amount_cents) == (booking, orientor, 3500)
        assert payout.status == Payout.Status.SENT
        assert fake.transfers[0]["destination"] == f"acct_{orientor.pk}"

    def it_pays_the_split_of_what_was_collected_less_refunds():
        _turn_on()
        reg = _registration(_payee())
        PaymentRefund.objects.create(registration=reg, amount_cents=4000, status=PaymentRefund.Status.SUCCEEDED)
        with _Stripe():
            payouts.run_payouts(NOW)
        assert Payout.objects.get().amount_cents == 4200

    def it_records_nothing_for_a_fully_refunded_or_omitted_payment():
        _turn_on()
        refunded = _registration(_payee())
        PaymentRefund.objects.create(registration=refunded, amount_cents=10000, status=PaymentRefund.Status.SUCCEEDED)
        omitted = _registration(_payee())
        TransactionAdjustment.objects.create(source_kind="class", source_pk=omitted.pk, is_omitted=True)
        with _Stripe() as fake:
            payouts.run_payouts(NOW)
        assert not Payout.objects.exists()
        assert fake.transfers == []

    def it_ignores_a_share_that_fell_due_before_payouts_went_on():
        _turn_on(since=NOW - timedelta(hours=1))
        _registration(_payee(active_since=NOW - timedelta(days=30)))
        with _Stripe() as fake:
            payouts.run_payouts(NOW)
        assert not Payout.objects.exists()
        assert fake.transfers == []

    def it_never_sends_twice_on_a_rerun():
        _turn_on()
        _registration(_payee())
        with _Stripe() as fake:
            payouts.run_payouts(NOW)
            payouts.run_payouts(NOW + timedelta(minutes=15))
        assert len(fake.transfers) == 1
        assert Payout.objects.count() == 1

    def describe_owed_at_month_end():
        def it_owes_a_payee_who_is_not_connected_and_invites_them_once():
            _turn_on()
            instructor = Member.objects.get(user=UserFactory())
            _registration(instructor)
            _registration(instructor)
            with _Stripe() as fake:
                payouts.run_payouts(NOW)
            assert fake.transfers == []
            assert set(Payout.objects.values_list("status", "owed_reason")) == {
                (Payout.Status.OWED_MANUALLY, Payout.OwedReason.NOT_CONNECTED)
            }
            invites = EventDelivery.objects.filter(event_key="billing.payouts_invite")
            assert invites.count() >= 1
            assert set(invites.values_list("period", flat=True)) == {f"payouts:invite:{instructor.pk}"}

        def it_skips_the_invite_for_a_payee_with_no_login():
            _turn_on()
            _registration(MemberFactory())
            with _Stripe():
                payouts.run_payouts(NOW)
            assert not EventDelivery.objects.filter(event_key="billing.payouts_invite").exists()

        def it_owes_a_share_paid_for_before_the_payee_connected_without_inviting():
            _turn_on()
            _registration(_payee(active_since=NOW - timedelta(days=5)), paid_at=NOW - timedelta(days=10))
            with _Stripe() as fake:
                payouts.run_payouts(NOW)
            payout = Payout.objects.get()
            assert (payout.status, payout.owed_reason) == (Payout.Status.OWED_MANUALLY, Payout.OwedReason.NOT_CONNECTED)
            assert fake.transfers == []
            assert not EventDelivery.objects.filter(event_key="billing.payouts_invite").exists()

        def it_owes_a_payment_not_taken_through_stripe():
            _turn_on()
            _registration(_payee(), pi="")
            with _Stripe():
                payouts.run_payouts(NOW)
            assert Payout.objects.get().owed_reason == Payout.OwedReason.NOT_THROUGH_STRIPE

    def describe_rejected_transfers():
        def it_records_stripes_reason_and_alerts_the_admins():
            _turn_on()
            _registration(_payee())
            with _Stripe(fail="The account needs verification."), patch("core.events.emit.emit") as emit:
                run = payouts.run_payouts(NOW)
            payout = Payout.objects.get()
            assert run.failed == 1
            assert (payout.status, payout.failure_reason) == (Payout.Status.FAILED, "The account needs verification.")
            emit.assert_called_once()
            assert emit.call_args.args[0] == "billing.payout_failed_admin"
            assert emit.call_args.kwargs["period"] == f"payout:{payout.pk}:failed"

        def it_retries_on_the_first_run_a_day_later_under_a_new_key():
            _turn_on()
            _registration(_payee())
            with _Stripe(fail="Insufficient funds."), patch("core.events.emit.emit") as emit:
                payouts.run_payouts(NOW)
                payouts.run_payouts(NOW + timedelta(hours=1))
            payout = Payout.objects.get()
            Payout.objects.filter(pk=payout.pk).update(attempted_at=NOW - timedelta(hours=25))
            with _Stripe() as fake:
                payouts.run_payouts(NOW)
            payout.refresh_from_db()
            assert payout.status == Payout.Status.SENT
            assert payout.attempt == 2
            assert fake.transfers[0]["idempotency_key"] == f"payout-{payout.pk}-a2"
            assert {call.kwargs["period"] for call in emit.call_args_list} == {f"payout:{payout.pk}:failed"}

        def it_gives_up_once_the_month_is_snapshotted():
            _turn_on()
            reg = _registration(_payee())
            with _Stripe(fail="No."), patch("core.events.emit.emit"):
                payouts.run_payouts(NOW)
            paid_on = reg.confirmed_at.date()
            ReconciliationSnapshot.objects.create(
                title="Oct", period_start=paid_on.replace(day=1), period_end=paid_on, results={}, grand_total_cents=0
            )
            Payout.objects.update(attempted_at=NOW - timedelta(days=2))
            with _Stripe() as fake:
                run = payouts.run_payouts(NOW)
            payout = Payout.objects.get()
            assert run.gave_up == 1
            assert fake.transfers == []
            assert (payout.status, payout.owed_reason) == (
                Payout.Status.OWED_MANUALLY,
                Payout.OwedReason.TRANSFER_FAILED,
            )
            assert list(Payout.objects.needs_attention()) == [payout]

        def it_fails_when_the_payee_has_no_account_in_this_mode():
            _turn_on()
            instructor = _payee()
            payout = Payout.objects.create(
                registration=_registration(instructor), payee=instructor, amount_cents=7000, due_at=NOW
            )
            PayoutAccount.objects.all().delete()
            with _Stripe() as fake, patch("core.events.emit.emit"):
                payout.send()
            assert fake.transfers == []
            assert (payout.status, payout.failure_reason) == (
                Payout.Status.FAILED,
                "The payee has no payout account in the current Stripe mode.",
            )


def describe_payout_model():
    def it_holds_exactly_one_source():
        with pytest.raises(IntegrityError):
            Payout.objects.create(payee=MemberFactory(), amount_cents=1, due_at=NOW)

    def it_names_payee_amount_and_status():
        payout = Payout(
            payee=MemberFactory(full_legal_name="Renee Marsh"), amount_cents=4200, status=Payout.Status.SENT
        )
        assert str(payout).endswith("$42.00 (Sent)")

    def it_is_run_by_the_scheduled_command(capsys):
        from core.scheduled_jobs import JOBS_BY_KEY, Cadence

        job = JOBS_BY_KEY["send_payouts"]
        assert (job.command, job.cadence, job.money_job) == ("send_payouts", Cadence.ALWAYS, True)
        call_command("send_payouts")
        assert "0 recorded, 0 sent" in capsys.readouterr().out


def describe_payouts_switch_and_account_stamps():
    def it_stamps_payouts_on_since_the_first_time_payouts_turn_on():
        bs = BillingSettings.load()
        assert bs.payouts_on_since is None
        bs.connect_enabled = True
        bs.save()
        first = BillingSettings.load().payouts_on_since
        assert first is not None
        bs.save()
        assert BillingSettings.load().payouts_on_since == first
        bs.connect_enabled = False
        bs.save(update_fields=["connect_enabled"])
        bs.connect_enabled = True
        with patch("django.utils.timezone.now", return_value=NOW + timedelta(minutes=5)):
            bs.save(update_fields=["connect_enabled"])
        assert BillingSettings.load().payouts_on_since == first  # stamped once; off only pauses sending

    def it_stamps_active_since_once_and_keeps_it_through_a_pause():
        account = PayoutAccount.objects.create(member=MemberFactory(), stripe_account_id="acct_1", livemode=False)
        active = {
            "payouts_enabled": True,
            "details_submitted": True,
            "capabilities": {"transfers": "active"},
            "requirements": {"disabled_reason": None},
        }
        account.apply_stripe_account(active)
        stamped = account.active_since
        assert stamped is not None
        account.apply_stripe_account(
            {**active, "payouts_enabled": False, "requirements": {"disabled_reason": "requirements.past_due"}}
        )
        account.apply_stripe_account(active)
        account.refresh_from_db()
        assert account.active_since == stamped


def describe_reconciliation_split():
    def _window() -> PanelWindow:
        return PanelWindow(start=date(2026, 10, 1), end=date(2026, 10, 31))

    def _row(result: Any, kind: RecipientKind, member: Member) -> Any:
        return next(alloc for alloc in result.groups[kind] if alloc.recipient_id == member.pk)

    def it_splits_an_instructor_into_sent_through_stripe_and_owed_manually():
        _turn_on()
        instructor = _payee()
        _registration(instructor)  # sent
        _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW + timedelta(days=5))  # scheduled
        _registration(instructor, pi="")  # legacy import
        with _Stripe():
            payouts.run_payouts(NOW)
        row = _row(build_reconciliation(window=_window()), RecipientKind.INSTRUCTOR, instructor)
        assert (row.total_cents, row.stripe_cents, row.manual_cents) == (21000, 14000, 7000)

    def it_counts_a_failed_transfer_as_owed_manually():
        _turn_on()
        instructor = _payee()
        _registration(instructor)
        with _Stripe(fail="No."), patch("core.events.emit.emit"):
            payouts.run_payouts(NOW)
        row = _row(build_reconciliation(window=_window()), RecipientKind.INSTRUCTOR, instructor)
        assert (row.stripe_cents, row.manual_cents) == (0, 7000)

    def it_splits_orientors_and_leaves_guild_rows_unchanged():
        _turn_on()
        orientor = _payee()
        booking = _booking(orientor)
        with _Stripe():
            payouts.run_payouts(NOW)
        result = build_reconciliation(window=_window())
        assert _row(result, RecipientKind.ORIENTATOR, orientor).stripe_cents == 3500
        guild_row = next(a for a in result.groups[RecipientKind.GUILD] if a.recipient_id == booking.guild_id)
        assert guild_row.stripe_cents == 0

    def it_owes_everything_by_hand_while_payouts_are_off():
        instructor = _payee()
        _registration(instructor)
        row = _row(build_reconciliation(window=_window()), RecipientKind.INSTRUCTOR, instructor)
        assert (row.stripe_cents, row.manual_cents) == (0, 7000)

    def it_carries_the_split_through_a_snapshot_and_reads_old_ones_as_owed():
        _turn_on()
        instructor = _payee()
        _registration(instructor)
        with _Stripe():
            payouts.run_payouts(NOW)
        snapshot = ReconciliationSnapshot.take(
            period_start=date(2026, 10, 1), period_end=date(2026, 10, 31), title="Oct"
        )
        assert _row(result_from_snapshot(snapshot), RecipientKind.INSTRUCTOR, instructor).stripe_cents == 7000
        for group in snapshot.results["groups"]:
            for row in group["rows"]:
                del row["stripe_cents"]
        old = _row(result_from_snapshot(snapshot), RecipientKind.INSTRUCTOR, instructor)
        assert (old.stripe_cents, old.manual_cents) == (0, 7000)

    def it_adds_both_columns_to_the_csv():
        from billing.reconciliation import CSV_HEADERS, stream_reconciliation_csv

        _turn_on()
        instructor = _payee()
        _registration(instructor)
        with _Stripe():
            payouts.run_payouts(NOW)
        body = b"".join(stream_reconciliation_csv(build_reconciliation(window=_window())).streaming_content).decode()
        assert CSV_HEADERS[-2:] == ["Sent through Stripe", "Owed manually"]
        assert f"{instructor.display_name},1,70.00,,70.00,70.00,0.00" in body


def describe_payee_earnings():
    def it_lists_each_earning_once_in_its_state_with_totals():
        _turn_on()
        instructor = _payee()
        sent = _registration(instructor)
        _registration(instructor, class_at=NOW + timedelta(days=4))  # upcoming
        _registration(instructor, pi="")  # owed, paid outside Stripe
        taken = _registration(instructor, class_at=NOW - timedelta(days=6))
        with _Stripe():
            payouts.run_payouts(NOW)
        Payout.objects.filter(registration=taken).update(status=Payout.Status.TAKEN_BACK)
        assert Payout.objects.get(registration=sent).status == Payout.Status.SENT
        with patch("django.utils.timezone.now", return_value=NOW):
            earnings = payouts.payee_earnings(instructor, NOW)
        states = sorted((row.state, row.amount_cents, row.count) for row in earnings.rows)
        assert states == [("owed", 7000, 1), ("sent", 7000, 1), ("taken_back", -7000, 1), ("upcoming", 7000, 1)]
        upcoming = next(row for row in earnings.rows if row.state == "upcoming")
        assert upcoming.label.startswith("Sends ")
        assert (earnings.upcoming_cents, earnings.sent_this_month_cents, earnings.owed_cents) == (7000, 7000, 7000)

    def it_groups_students_of_one_class_day():
        _turn_on()
        instructor = _payee()
        first = _registration(instructor)
        second = RegistrationFactory(
            class_offering=first.class_offering, amount_paid_cents=10000, stripe_payment_id="pi_9"
        )
        Registration.objects.filter(pk=second.pk).update(confirmed_at=first.confirmed_at)
        with _Stripe():
            payouts.run_payouts(NOW)
        [row] = payouts.payee_earnings(instructor, NOW).rows
        assert (row.count, row.amount_cents, row.detail) == (2, 14000, "2 students")

    def it_names_the_member_on_a_single_orientation():
        _turn_on()
        orientor = _payee()
        booking = _booking(orientor)
        [row] = payouts.payee_earnings(orientor, NOW).rows
        assert row.detail == booking.member.display_name

    def it_is_empty_with_no_earnings():
        assert payouts.payee_earnings(MemberFactory(), NOW).rows == []


def describe_reconciliation_tab_attention():
    def it_flags_a_rejected_transfer_with_stripes_reason(client):
        from django.contrib.auth.models import User
        from django.urls import reverse

        user = User.objects.create_user(username="adm", email="adm@example.com", password="pw12345!")
        admin = Member.objects.get(user=user)
        admin.fog_role = Member.FogRole.ADMIN
        admin.save()
        client.force_login(user)
        instructor = _payee()
        Payout.objects.create(
            registration=_registration(instructor),
            payee=instructor,
            amount_cents=7000,
            due_at=NOW,
            status=Payout.Status.FAILED,
            failure_reason="Zebra account needs verification",
        )
        html = client.get(reverse("billing_admin_dashboard") + "?tab=reconciliation").content.decode()
        assert "data-payouts-attention" in html
        assert "Zebra account needs verification" in html


def describe_stripe_utils_transfers():
    @pytest.fixture
    def client_mock(configured_billing_stripe):
        from unittest.mock import MagicMock

        mock = MagicMock()
        with patch("billing.stripe_utils._get_stripe_client", return_value=mock):
            yield mock

    def it_reads_the_charge_behind_a_payment_intent(client_mock):
        from billing import stripe_utils

        client_mock.v1.payment_intents.retrieve.return_value.latest_charge = "ch_1"
        assert stripe_utils.charge_for_payment_intent(payment_intent_id="pi_1") == "ch_1"

    def it_reads_an_expanded_charge(client_mock):
        from unittest.mock import MagicMock

        from billing import stripe_utils

        client_mock.v1.payment_intents.retrieve.return_value.latest_charge = MagicMock(id="ch_2")
        assert stripe_utils.charge_for_payment_intent(payment_intent_id="pi_1") == "ch_2"

    def it_refuses_a_payment_intent_with_no_charge(client_mock):
        from billing import stripe_utils

        client_mock.v1.payment_intents.retrieve.return_value.latest_charge = None
        with pytest.raises(stripe.InvalidRequestError):
            stripe_utils.charge_for_payment_intent(payment_intent_id="pi_1")

    def it_creates_a_usd_transfer_with_its_source_charge_and_key(client_mock):
        from billing import stripe_utils

        client_mock.v1.transfers.create.return_value.id = "tr_9"
        transfer_id = stripe_utils.create_transfer(
            amount_cents=7000,
            destination="acct_1",
            source_transaction="ch_1",
            idempotency_key="payout-1-a1",
            metadata={},
        )
        assert transfer_id == "tr_9"
        assert client_mock.v1.transfers.create.call_args.kwargs == {
            "params": {
                "amount": 7000,
                "currency": "usd",
                "destination": "acct_1",
                "source_transaction": "ch_1",
                "metadata": {},
            },
            "options": {"idempotency_key": "payout-1-a1"},
        }


def describe_unanswered_transfers():
    @pytest.mark.parametrize("error", [stripe.APIConnectionError, stripe.APIError, stripe.RateLimitError])
    def it_replays_the_same_key_after_no_definite_answer(error):
        _turn_on()
        _registration(_payee())
        with _Stripe(error=error), patch("core.events.emit.emit") as emit:
            payouts.run_payouts(NOW)
        payout = Payout.objects.get()
        assert (payout.status, payout.attempt) == (Payout.Status.PENDING, 1)
        emit.assert_not_called()
        with _Stripe() as fake:
            payouts.run_payouts(NOW + timedelta(minutes=15))
        payout.refresh_from_db()
        assert payout.status == Payout.Status.SENT
        assert [t["idempotency_key"] for t in fake.transfers] == [f"payout-{payout.pk}-a1"]

    def it_keeps_the_new_key_when_a_retry_times_out():
        _turn_on()
        _registration(_payee())
        with _Stripe(fail="No."), patch("core.events.emit.emit"):
            payouts.run_payouts(NOW)
        Payout.objects.update(attempted_at=NOW - timedelta(hours=25))
        with _Stripe(error=stripe.APIConnectionError):
            payouts.run_payouts(NOW)
        with _Stripe() as fake:
            payouts.run_payouts(NOW + timedelta(minutes=15))
        payout = Payout.objects.get()
        assert payout.status == Payout.Status.SENT
        assert [t["idempotency_key"] for t in fake.transfers] == [f"payout-{payout.pk}-a2"]


def describe_snapshot_binds_the_split():
    def _october() -> ReconciliationSnapshot:
        return ReconciliationSnapshot.take(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31), title="Oct")

    def _switch(on: bool) -> None:
        bs = BillingSettings.load()
        bs.connect_enabled = on
        bs.save()

    def it_still_sends_a_share_counted_as_stripe_once_after_the_switch_goes_off_and_on():
        _turn_on()
        instructor = _payee()
        _registration(instructor, class_at=NOW + timedelta(days=5))
        snapshot = _october()
        row = next(r for g in snapshot.results["groups"] if g["kind"] == "instructor" for r in g["rows"])
        assert row["stripe_cents"] == 7000
        payout = Payout.objects.get()
        assert (payout.status, payout.counted_as_stripe_in) == (Payout.Status.PENDING, snapshot)
        _switch(False)
        with _Stripe() as fake:
            payouts.run_payouts(NOW + timedelta(days=8))
            assert fake.transfers == []
        _switch(True)
        with _Stripe() as fake:
            payouts.run_payouts(NOW)  # not due yet
            payouts.run_payouts(NOW + timedelta(days=8))
            payouts.run_payouts(NOW + timedelta(days=9))
        assert len(fake.transfers) == 1
        assert Payout.objects.get().status == Payout.Status.SENT

    def it_never_sends_a_share_frozen_as_owed_while_payouts_were_off():
        _turn_on()
        instructor = _payee()
        _registration(instructor, class_at=NOW + timedelta(days=5))
        _switch(False)
        snapshot = _october()
        row = next(r for g in snapshot.results["groups"] if g["kind"] == "instructor" for r in g["rows"])
        assert (row["stripe_cents"], row["total_cents"]) == (0, 7000)
        _switch(True)
        with _Stripe() as fake:
            payouts.run_payouts(NOW + timedelta(days=8))
        assert fake.transfers == []
        assert Payout.objects.get().status == Payout.Status.OWED_MANUALLY

    def it_owes_a_counted_share_by_hand_at_once_when_stripe_rejects_it_and_says_so(client):
        from django.contrib.auth.models import User
        from django.urls import reverse

        _turn_on()
        instructor = _payee()
        _registration(instructor, class_at=NOW + timedelta(days=5))
        _october()
        with _Stripe(fail="Account closed."), patch("core.events.emit.emit") as emit:
            payouts.run_payouts(NOW + timedelta(days=8))
        payout = Payout.objects.get()
        assert (payout.status, payout.owed_reason) == (Payout.Status.OWED_MANUALLY, Payout.OwedReason.TRANSFER_FAILED)
        assert (
            "October 2026 snapshot counted it as Sent through Stripe"
            in emit.call_args.kwargs["context"]["counted_note"]
        )
        user = User.objects.create_user(username="adm", email="adm@example.com", password="pw12345!")
        admin = Member.objects.get(user=user)
        admin.fog_role = Member.FogRole.ADMIN
        admin.save()
        client.force_login(user)
        html = client.get(reverse("billing_admin_dashboard") + "?tab=reconciliation").content.decode()
        assert "data-counted-as-stripe" in html
        assert "October 2026 snapshot" in html

    def it_stops_retrying_a_transfer_still_failed_when_the_snapshot_counts_it_as_owed():
        _turn_on()
        instructor = _payee()
        _registration(instructor)
        with _Stripe(fail="No."), patch("core.events.emit.emit"):
            payouts.run_payouts(NOW)
        snapshot = _october()
        row = next(r for g in snapshot.results["groups"] if g["kind"] == "instructor" for r in g["rows"])
        assert row["stripe_cents"] == 0
        assert Payout.objects.get().owed_reason == Payout.OwedReason.TRANSFER_FAILED
        Payout.objects.update(attempted_at=NOW - timedelta(days=2))
        with _Stripe() as fake:
            payouts.run_payouts(NOW)
        assert fake.transfers == []

    def it_sends_what_is_left_after_a_refund_and_nothing_after_a_full_one():
        _turn_on()
        instructor = _payee()
        partial = _registration(instructor, class_at=NOW + timedelta(days=5))
        full = _registration(instructor, class_at=NOW + timedelta(days=5))
        _october()
        PaymentRefund.objects.create(registration=partial, amount_cents=4000, status=PaymentRefund.Status.SUCCEEDED)
        PaymentRefund.objects.create(registration=full, amount_cents=10000, status=PaymentRefund.Status.SUCCEEDED)
        with _Stripe() as fake:
            payouts.run_payouts(NOW + timedelta(days=8))
        assert [t["amount_cents"] for t in fake.transfers] == [4200]
        assert Payout.objects.get(registration=full).status == Payout.Status.NOTHING_DUE

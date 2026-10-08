"""Payouts part 3 (#662): refunding a payment whose share was already sent through Stripe."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from billing import payouts
from billing.models import BillingSettings, PaymentRefund, Payout, PayoutAccount
from billing.payments_panel import PanelWindow
from billing.reconciliation import build_reconciliation
from billing.refunds import issue_refund
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import Registration
from membership.models import Member, OrientationBooking
from tests.membership.factories import MemberFactory, OrientationBookingFactory, OrientationSlotFactory

pytestmark = pytest.mark.django_db

TAKE_BACK = PaymentRefund.ShareDecision.TAKE_BACK
PL_COVERS = PaymentRefund.ShareDecision.PL_COVERS


def _payee() -> Member:
    member = MemberFactory(full_legal_name="Renee Marsh", preferred_name="Renee")
    PayoutAccount.objects.create(
        member=member,
        stripe_account_id=f"acct_{member.pk}",
        livemode=False,
        status=PayoutAccount.Status.ACTIVE,
        active_since=timezone.now() - timedelta(days=30),
    )
    return member


def _sent_registration(payee: Member, *, amount: int = 10000, share: int = 7000) -> Registration:
    offering = ClassOfferingFactory(instructor=payee, title="Intro to Wheel Throwing")
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now() - timedelta(days=3))
    reg = RegistrationFactory(
        class_offering=offering,
        amount_paid_cents=amount,
        stripe_payment_id="pi_sent",
        status=Registration.Status.CONFIRMED,
        confirmed_at=timezone.now() - timedelta(days=10),
    )
    Payout.objects.create(
        registration=reg,
        payee=payee,
        amount_cents=share,
        due_at=timezone.now() - timedelta(days=1),
        status=Payout.Status.SENT,
        stripe_transfer_id="tr_sent",
        sent_at=timezone.now() - timedelta(days=1),
    )
    return reg


def _sent_booking(payee: Member, *, amount: int = 5000, share: int = 3500) -> OrientationBooking:
    slot = OrientationSlotFactory(
        starts_at=timezone.now() - timedelta(days=3), ends_at=timezone.now() - timedelta(days=3, hours=-1)
    )
    booking = OrientationBookingFactory(
        slot=slot,
        oriented_by=payee,
        amount_paid_cents=amount,
        stripe_payment_id="pi_ob",
        status=OrientationBooking.Status.CONFIRMED,
    )
    Payout.objects.create(
        orientation_booking=booking,
        payee=payee,
        amount_cents=share,
        due_at=timezone.now() - timedelta(days=1),
        status=Payout.Status.SENT,
        stripe_transfer_id="tr_ob",
        sent_at=timezone.now() - timedelta(days=1),
    )
    return booking


def _login_admin(client) -> None:
    user = User.objects.create_user(username="adm", email="adm@example.com", password="pw12345!")
    admin = Member.objects.get(user=user)
    admin.fog_role = Member.FogRole.ADMIN
    admin.save()
    client.force_login(user)


class _Reversals:
    """Mocked Stripe refund, reversal lookup and reversal create."""

    def __init__(self, *, create_error: Exception | None = None, found: str | None = None) -> None:
        self.create_error = create_error
        self.found = found
        self.created: list[dict[str, Any]] = []

    def __enter__(self) -> _Reversals:
        def refund(*, payment_intent_id: str, amount_cents: int | None, idempotency_key: str) -> dict[str, Any]:
            return {"id": f"re_{idempotency_key}", "status": "succeeded", "amount": amount_cents}

        def create(**kwargs: Any) -> str:
            self.created.append(kwargs)
            if self.create_error is not None:
                raise self.create_error
            return f"trr_{len(self.created)}"

        self._patches = [
            patch("billing.stripe_utils.create_refund", side_effect=refund),
            patch("billing.stripe_utils.find_transfer_reversal", side_effect=lambda **kw: self.found),
            patch("billing.stripe_utils.reverse_transfer", side_effect=create),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in self._patches:
            p.stop()


def describe_the_refund_forms_ask_for_the_choice():
    def it_asks_on_the_class_refund_form_once_the_share_was_sent(client):
        _login_admin(client)
        reg = _sent_registration(_payee())
        html = client.get(reverse("classes:admin_registration_refund_form", args=[reg.pk])).content.decode()
        assert 'name="share_decision"' in html
        assert 'value="take_back"' in html
        assert 'value="pl_covers"' in html
        assert "share was already sent ($70.00" in html

    def it_asks_on_the_orientation_refund_form_once_the_share_was_sent(client):
        _login_admin(client)
        booking = _sent_booking(_payee())
        html = client.get(reverse("billing_orientation_refund_form", args=[booking.pk])).content.decode()
        assert 'name="share_decision"' in html
        assert "share was already sent ($35.00" in html

    def it_does_not_ask_when_no_share_was_sent(client):
        _login_admin(client)
        reg = RegistrationFactory(amount_paid_cents=5000, stripe_payment_id="pi_x", confirmed_at=timezone.now())
        html = client.get(reverse("classes:admin_registration_refund_form", args=[reg.pk])).content.decode()
        assert 'name="share_decision"' not in html

    def it_requires_the_choice(client):
        _login_admin(client)
        reg = _sent_registration(_payee())
        with _Reversals():
            response = client.post(
                reverse("classes:admin_registration_refund", args=[reg.pk]), {"amount": "100.00", "reason": ""}
            )
        assert "Choose whether to take the share back" in response.content.decode()
        assert not PaymentRefund.objects.exists()


def describe_taking_the_share_back():
    def it_reverses_the_whole_share_on_a_full_class_refund(client, django_capture_on_commit_callbacks):
        _login_admin(client)
        reg = _sent_registration(_payee())
        with _Reversals() as fake, django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                reverse("classes:admin_registration_refund", args=[reg.pk]),
                {"amount": "100.00", "reason": "", "share_decision": "take_back"},
            )
        assert response.status_code == 204
        refund = PaymentRefund.objects.get()
        assert fake.created == [{"transfer_id": "tr_sent", "amount_cents": 7000, "refund_pk": refund.pk}]
        assert (refund.share_decision, refund.stripe_transfer_reversal_id, refund.share_reversed_cents) == (
            TAKE_BACK,
            "trr_1",
            7000,
        )
        payout = Payout.objects.get()
        assert (payout.status, payout.reversed_cents) == (Payout.Status.TAKEN_BACK, 7000)

    def it_takes_back_the_same_part_on_a_partial_refund(client, django_capture_on_commit_callbacks):
        _login_admin(client)
        reg = _sent_registration(_payee())
        with _Reversals() as fake, django_capture_on_commit_callbacks(execute=True):
            client.post(
                reverse("classes:admin_registration_refund", args=[reg.pk]),
                {"amount": "40.00", "reason": "", "share_decision": "take_back"},
            )
        assert [c["amount_cents"] for c in fake.created] == [2800]
        assert Payout.objects.get().reversed_cents == 2800

    def it_takes_only_its_own_part_after_an_earlier_refund_past_lives_covered(django_capture_on_commit_callbacks):
        reg = _sent_registration(_payee())
        with _Reversals() as fake, django_capture_on_commit_callbacks(execute=True):
            issue_refund(reg, amount_cents=4000, share_decision=PL_COVERS)
            issue_refund(reg, amount_cents=2000, share_decision=TAKE_BACK)
        assert [c["amount_cents"] for c in fake.created] == [1400]

    def it_reverses_the_orientors_part_from_the_orientation_refund_form(client, django_capture_on_commit_callbacks):
        _login_admin(client)
        booking = _sent_booking(_payee())
        with _Reversals() as fake, django_capture_on_commit_callbacks(execute=True):
            client.post(
                reverse("billing_orientation_refund", args=[booking.pk]),
                {"amount": "50.00", "reason": "", "share_decision": "take_back"},
            )
        assert [(c["transfer_id"], c["amount_cents"]) for c in fake.created] == [("tr_ob", 3500)]
        assert PaymentRefund.objects.get().share_decision == TAKE_BACK

    def it_shows_as_taken_back_on_the_payees_tab(django_capture_on_commit_callbacks):
        payee = _payee()
        reg = _sent_registration(payee)
        with _Reversals(), django_capture_on_commit_callbacks(execute=True):
            issue_refund(reg, share_decision=TAKE_BACK)
        rows = sorted((row.state, row.amount_cents) for row in payouts.payee_earnings(payee).rows)
        assert rows == [("sent", 7000), ("taken_back", -7000)]


def describe_past_lives_covering_it():
    def _window() -> PanelWindow:
        today = timezone.localdate()
        return PanelWindow(start=today - timedelta(days=40), end=today)

    def it_records_the_choice_sends_nothing_and_flags_the_line(client, django_capture_on_commit_callbacks):
        _login_admin(client)
        reg = _sent_registration(_payee())
        with _Reversals() as fake, django_capture_on_commit_callbacks(execute=True):
            client.post(
                reverse("classes:admin_registration_refund", args=[reg.pk]),
                {"amount": "100.00", "reason": "", "share_decision": "pl_covers"},
            )
        assert fake.created == []
        assert PaymentRefund.objects.get().share_decision == PL_COVERS
        assert Payout.objects.get().status == Payout.Status.SENT
        result = build_reconciliation(window=_window())
        assert result.kept_share_count == 1
        [line] = [line for line in result.lines if line.source_pk == reg.pk]
        assert line.payee_kept_share
        assert "an admin chose Past Lives covers it" in line.note

    def it_records_not_asked_for_a_refund_nobody_was_asked_about(django_capture_on_commit_callbacks):
        reg = _sent_registration(_payee())
        booking = _sent_booking(_payee())
        with _Reversals() as fake, django_capture_on_commit_callbacks(execute=True):
            issue_refund(reg)  # a Stripe dashboard refund reconciles through the same success path
            issue_refund(booking)  # the automatic refund on a declined or cancelled orientation
        assert fake.created == []
        assert set(PaymentRefund.objects.values_list("share_decision", flat=True)) == {
            PaymentRefund.ShareDecision.NOT_ASKED
        }
        notes = payouts.kept_share_notes(build_reconciliation(window=_window()).lines)
        assert notes[("class", reg.pk)].startswith(
            "Payee kept a share already sent: refunded where nobody could be asked"
        )

    def it_leaves_the_decision_empty_when_no_share_was_sent(django_capture_on_commit_callbacks):
        reg = RegistrationFactory(amount_paid_cents=5000, stripe_payment_id="pi_x", confirmed_at=timezone.now())
        with _Reversals(), django_capture_on_commit_callbacks(execute=True):
            issue_refund(reg)
        assert PaymentRefund.objects.get().share_decision == PaymentRefund.ShareDecision.NOT_APPLICABLE

    def it_shows_the_flag_on_the_reconciliation_tab(client, django_capture_on_commit_callbacks):
        _login_admin(client)
        reg = _sent_registration(_payee())
        with _Reversals(), django_capture_on_commit_callbacks(execute=True):
            issue_refund(reg, share_decision=PL_COVERS)
        start = (timezone.localdate() - timedelta(days=40)).isoformat()
        html = client.get(
            reverse("billing_admin_dashboard") + f"?tab=reconciliation&start={start}&end={timezone.localdate()}"
        ).content.decode()
        assert "data-payee-kept-share" in html
        assert "Payee kept share" in html


def describe_a_take_back_that_does_not_happen():
    def it_falls_back_to_past_lives_covering_it_with_a_flag_and_one_alert(django_capture_on_commit_callbacks):
        reg = _sent_registration(_payee())
        refusal = stripe.InvalidRequestError("Insufficient funds in the connected account.", param=None)
        with (
            _Reversals(create_error=refusal),
            patch("core.events.emit.emit") as emit,
            django_capture_on_commit_callbacks(execute=True),
        ):
            issue_refund(reg, share_decision=TAKE_BACK)
            payouts.run_payouts()
        refund = PaymentRefund.objects.get()
        assert refund.share_reversal_error == "Insufficient funds in the connected account."
        assert Payout.objects.get().status == Payout.Status.SENT
        alerts = [c for c in emit.call_args_list if c.args[0] == "billing.payout_reversal_failed_admin"]
        assert [c.kwargs["period"] for c in alerts] == [f"refund:{refund.pk}:take-back-failed"]
        assert (
            "Stripe refused to take the share back"
            in payouts.kept_share_notes(
                build_reconciliation(window=PanelWindow(start=date(2026, 1, 1), end=timezone.localdate())).lines
            )[("class", reg.pk)]
        )

    def it_retries_an_unanswered_take_back_and_finds_the_earlier_reversal(django_capture_on_commit_callbacks):
        reg = _sent_registration(_payee())
        with (
            _Reversals(create_error=stripe.APIConnectionError("timeout")),
            django_capture_on_commit_callbacks(execute=True),
        ):
            issue_refund(reg, share_decision=TAKE_BACK)
        refund = PaymentRefund.objects.get()
        assert (refund.stripe_transfer_reversal_id, refund.share_reversal_error) == ("", "")
        with _Reversals(found="trr_landed") as fake:
            payouts.run_payouts()
        refund.refresh_from_db()
        assert fake.created == []
        assert (refund.stripe_transfer_reversal_id, refund.share_reversed_cents) == ("trr_landed", 7000)
        assert Payout.objects.get().status == Payout.Status.TAKEN_BACK

    def it_alerts_once_stripe_has_not_answered_for_three_days(django_capture_on_commit_callbacks):
        reg = _sent_registration(_payee())
        with (
            _Reversals(create_error=stripe.APIConnectionError("timeout")),
            django_capture_on_commit_callbacks(execute=True),
        ):
            issue_refund(reg, share_decision=TAKE_BACK)
        refund = PaymentRefund.objects.get()
        with _Reversals(create_error=stripe.APIConnectionError("timeout")), patch("core.events.emit.emit") as emit:
            payouts.run_payouts(timezone.now() + timedelta(days=1))
            emit.assert_not_called()
            payouts.run_payouts(timezone.now() + timedelta(days=4))
        assert {call.kwargs["period"] for call in emit.call_args_list} == {f"refund:{refund.pk}:take-back-failed"}
        refund.refresh_from_db()
        assert refund.share_reversal_error == ""  # still pending: plfog keeps trying


def describe_stripe_reversal_calls():
    @pytest.fixture
    def client_mock(configured_billing_stripe):
        mock = MagicMock()
        with patch("billing.stripe_utils._get_stripe_client", return_value=mock):
            yield mock

    def it_reverses_with_the_refund_key_and_tag(client_mock):
        from billing import stripe_utils

        client_mock.v1.transfers.reversals.create.return_value.id = "trr_9"
        assert stripe_utils.reverse_transfer(transfer_id="tr_1", amount_cents=2800, refund_pk=12) == "trr_9"
        client_mock.v1.transfers.reversals.create.assert_called_once_with(
            "tr_1",
            params={"amount": 2800, "metadata": {"refund_pk": "12"}},
            options={"idempotency_key": "payout-reversal-12"},
        )

    def it_finds_an_earlier_reversal_by_its_tag(client_mock):
        from billing import stripe_utils

        others = [MagicMock(id="trr_a", metadata={"refund_pk": "3"}), MagicMock(id="trr_b", metadata=None)]
        mine = MagicMock(id="trr_mine", metadata={"refund_pk": "12"})
        client_mock.v1.transfers.reversals.list.return_value.auto_paging_iter.return_value = iter([*others, mine])
        assert stripe_utils.find_transfer_reversal(transfer_id="tr_1", refund_pk=12) == "trr_mine"
        client_mock.v1.transfers.reversals.list.return_value.auto_paging_iter.return_value = iter(others)
        assert stripe_utils.find_transfer_reversal(transfer_id="tr_1", refund_pk=12) is None


def describe_review_round_1():
    def it_shows_the_part_kept_and_the_part_taken_back_with_net_totals(django_capture_on_commit_callbacks):
        payee = _payee()
        reg = _sent_registration(payee)
        with _Reversals(), django_capture_on_commit_callbacks(execute=True):
            issue_refund(reg, amount_cents=4000, share_decision=TAKE_BACK)
        earnings = payouts.payee_earnings(payee)
        assert sorted((row.state, row.label.split()[0], row.amount_cents) for row in earnings.rows) == [
            ("sent", "Sent", 7000),
            ("taken_back", "Taken", -2800),
        ]
        assert earnings.sent_this_month_cents == 4200

    def it_flags_a_refund_that_landed_while_the_share_was_still_being_sent():
        from tests.billing.payouts_sending_spec import _Stripe

        payee = _payee()
        reg = _sent_registration(payee)
        payout = Payout.objects.get()
        Payout.objects.filter(pk=payout.pk).update(
            status=Payout.Status.PENDING, stripe_transfer_id="", sent_at=None, attempted_at=timezone.now()
        )
        with _Reversals() as fake:
            issue_refund(reg, amount_cents=10000)  # no sent share to ask about yet
        assert PaymentRefund.objects.get().share_decision == PaymentRefund.ShareDecision.NOT_APPLICABLE
        bs = BillingSettings.load()
        bs.connect_enabled = True
        bs.save()
        with _Stripe() as stripe_fake:
            payouts.run_payouts()
        payout.refresh_from_db()
        assert payout.status == Payout.Status.SENT
        assert [t["amount_cents"] for t in stripe_fake.transfers] == [7000]  # the pinned replay
        assert fake.created == []  # nothing reversed automatically
        assert PaymentRefund.objects.get().share_decision == PaymentRefund.ShareDecision.NOT_ASKED
        today = timezone.localdate()
        notes = payouts.kept_share_notes(
            build_reconciliation(window=PanelWindow(start=today - timedelta(days=40), end=today)).lines
        )
        assert ("class", reg.pk) in notes

    def it_sends_the_failure_alert_and_the_pay_it_by_hand_alert_once_each():
        from billing.models import ReconciliationSnapshot
        from tests.billing.payouts_sending_spec import _Stripe

        payee = _payee()
        reg = _sent_registration(payee)
        payout = Payout.objects.get()
        Payout.objects.filter(pk=payout.pk).update(
            status=Payout.Status.PENDING, stripe_transfer_id="", sent_at=None, attempted_at=None
        )
        bs = BillingSettings.load()
        bs.connect_enabled = True
        bs.save()
        BillingSettings.objects.filter(pk=1).update(payouts_on_since=timezone.now() - timedelta(days=40))
        with _Stripe(fail="Account restricted."), patch("core.events.emit.emit") as emit:
            payouts.run_payouts()
            ReconciliationSnapshot.objects.create(
                title="Month",
                period_start=reg.confirmed_at.date() - timedelta(days=1),
                period_end=reg.confirmed_at.date() + timedelta(days=1),
                results={},
                grand_total_cents=0,
            )
            Payout.objects.update(attempted_at=timezone.now() - timedelta(days=2))
            payouts.run_payouts()
            payouts.run_payouts(timezone.now() + timedelta(days=5))
        periods = [c.kwargs["period"] for c in emit.call_args_list if c.args[0] == "billing.payout_failed_admin"]
        assert periods == [f"payout:{payout.pk}:failed", f"payout:{payout.pk}:owed"]
        assert Payout.objects.get().status == Payout.Status.OWED_MANUALLY


def describe_review_round_2():
    def it_still_lists_a_fully_refunded_share_past_lives_covered_as_sent(django_capture_on_commit_callbacks):
        payee = _payee()
        reg = _sent_registration(payee)
        with _Reversals(), django_capture_on_commit_callbacks(execute=True):
            issue_refund(reg, share_decision=PL_COVERS)
        earnings = payouts.payee_earnings(payee)
        assert [(row.state, row.amount_cents) for row in earnings.rows] == [("sent", 7000)]
        assert earnings.sent_this_month_cents == 7000

    def it_records_not_asked_when_the_refund_lands_after_the_send():
        reg = _sent_registration(_payee())  # the send committed first
        with _Reversals():
            issue_refund(reg)
        assert PaymentRefund.objects.get().share_decision == PaymentRefund.ShareDecision.NOT_ASKED

    def it_records_not_asked_when_the_send_lands_after_the_refund():
        reg = _sent_registration(_payee())
        payout = Payout.objects.get()
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PENDING, stripe_transfer_id="", sent_at=None)
        with _Reversals():
            issue_refund(reg)  # the refund committed first: no sent share yet
        assert PaymentRefund.objects.get().share_decision == PaymentRefund.ShareDecision.NOT_APPLICABLE
        payout.refresh_from_db()
        payout.mark_sent("tr_after")  # the send completes second and sees the refund
        assert PaymentRefund.objects.get().share_decision == PaymentRefund.ShareDecision.NOT_ASKED

    def it_locks_the_share_row_when_a_refund_settles():
        from django.db.models.query import QuerySet

        reg = _sent_registration(_payee())
        calls: list[dict[str, Any]] = []
        real = QuerySet.select_for_update

        def spy(self: QuerySet, *args: Any, **kwargs: Any) -> QuerySet:
            if self.model is Payout:
                calls.append(kwargs)
            return real(self, *args, **kwargs)

        with _Reversals(), patch.object(QuerySet, "select_for_update", spy):
            issue_refund(reg)
        assert calls and all("skip_locked" not in kw for kw in calls)

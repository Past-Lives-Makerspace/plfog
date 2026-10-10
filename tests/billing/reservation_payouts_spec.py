"""#749 part 2: a paid equipment reservation in the books (Payments panel, CSV, reconciliation, snapshot)
and in payouts (the picked manager's earning, its send, and its take back on a refund)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
import stripe
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from billing import payouts
from billing.models import (
    BillingSettings,
    PaymentRefund,
    Payout,
    PayoutAccount,
    ReconciliationSnapshot,
    TransactionAdjustment,
)
from billing.notifications import _payout_item
from billing.payments_panel import PanelWindow, build_payments_ledger, stream_payments_csv
from billing.reconciliation import (
    GROUP_LABELS,
    RecipientKind,
    ShareSource,
    build_reconciliation,
    result_from_snapshot,
    stream_reconciliation_csv,
)
from billing.refunds import issue_refund
from classes.factories import RegistrationFactory, UserFactory
from classes.webhook_handlers import _refundable_source_for_payment_intent, handle_charge_refunded
from core.models import EventDelivery
from membership.models import AdminCapability, Equipment, EquipmentReservation, EquipmentStaffMembership, Guild, Member
from tests.billing.factories import LateCancellationFeeFactory, PaymentRefundFactory
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MemberFactory,
    OrientationBookingFactory,
)

pytestmark = pytest.mark.django_db

TAKE_BACK = PaymentRefund.ShareDecision.TAKE_BACK
NOT_ASKED = PaymentRefund.ShareDecision.NOT_ASKED


def _now() -> datetime:
    return timezone.now()


def _window() -> PanelWindow:
    today = timezone.localdate()
    return PanelWindow(start=today - timedelta(days=40), end=today)


def _manager(name: str = "Sami Okafor", *, connected: bool = True) -> Member:
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


def _equipment(payee: Member | None = None, *, guild: Guild | None = None, name: str = "CNC Machine") -> Equipment:
    equipment = EquipmentFactory(
        name=name, guild=guild, pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, payee=payee
    )
    if payee is not None:
        EquipmentStaffMembershipFactory(equipment=equipment, member=payee)
    return equipment


def _paid(
    equipment: Equipment,
    *,
    member: Member | None = None,
    amount: int = 5000,
    starts_at: datetime | None = None,
    minutes: int = 120,
    paid_at: datetime | None = None,
    status: str = EquipmentReservation.Status.CONFIRMED,
    pi: str = "pi_res",
) -> EquipmentReservation:
    """A paid reservation that started three days ago and was paid for ten days ago, by default."""
    starts_at = starts_at or _now() - timedelta(days=3)
    reservation = EquipmentReservationFactory(
        equipment=equipment,
        member=member or MemberFactory(full_legal_name="Jane Doe", preferred_name=""),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes),
        status=status,
        amount_paid_cents=amount,
        stripe_payment_id=pi,
    )
    EquipmentReservation.objects.filter(pk=reservation.pk).update(created_at=paid_at or _now() - timedelta(days=10))
    reservation.refresh_from_db()
    return reservation


def _turn_on(since: datetime | None = None) -> None:
    settings_obj = BillingSettings.load()
    settings_obj.connect_enabled = True
    settings_obj.save()
    BillingSettings.objects.filter(pk=1).update(payouts_on_since=since or _now() - timedelta(days=30))


def _sent_payout(reservation: EquipmentReservation, *, share: int = 3500) -> Payout:
    return Payout.objects.create(
        reservation=reservation,
        payee=reservation.equipment.payee,
        amount_cents=share,
        due_at=_now() - timedelta(days=1),
        status=Payout.Status.SENT,
        stripe_transfer_id="tr_res",
        sent_at=_now() - timedelta(days=1),
    )


def _login_admin(client: Client, username: str = "fogadmin") -> User:
    user = User.objects.create_superuser(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user


def _login_billing_approver(client: Client, username: str = "biller") -> Member:
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    member: Member = user.member  # type: ignore[attr-defined]
    member.admin_capabilities.create(capability=AdminCapability.Capability.BILLING_APPROVER)
    return member


class _Stripe:
    """The Stripe calls a send, a refund and a take back make, mocked."""

    def __init__(self) -> None:
        self.transfers: list[dict[str, Any]] = []
        self.reversals: list[dict[str, Any]] = []

    def __enter__(self) -> _Stripe:
        def transfer(**kwargs: Any) -> str:
            self.transfers.append(kwargs)
            return f"tr_{len(self.transfers)}"

        def refund(*, payment_intent_id: str, amount_cents: int | None, idempotency_key: str) -> dict[str, Any]:
            return {"id": f"re_{idempotency_key}", "status": "succeeded", "amount": amount_cents}

        def reverse(**kwargs: Any) -> str:
            self.reversals.append(kwargs)
            return f"trr_{len(self.reversals)}"

        self._patches = [
            patch("billing.stripe_utils.find_payout_transfer", return_value=None),
            patch("billing.stripe_utils.charge_for_payment_intent", return_value="ch_res"),
            patch("billing.stripe_utils.create_transfer", side_effect=transfer),
            patch("billing.stripe_utils.create_refund", side_effect=refund),
            patch("billing.stripe_utils.find_transfer_reversal", return_value=None),
            patch("billing.stripe_utils.reverse_transfer", side_effect=reverse),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for p in self._patches:
            p.stop()


# ---------------------------------------------------------------------------
# The Payments panel and its ledger CSV
# ---------------------------------------------------------------------------


def describe_the_payments_panel():
    def it_lists_a_paid_reservation_as_a_reservation_source_row():
        reservation = _paid(_equipment(_manager()), amount=5000)
        [row] = build_payments_ledger(window=_window(), viewer_is_admin=True).rows
        assert (row.source_kind, row.source_label, row.source_pk) == ("reservation", "Reservation", reservation.pk)
        assert (row.item, row.amount_cents, row.status) == ("Reservation, CNC Machine", 5000, "paid")
        assert row.date == reservation.created_at
        assert row.payer_name == "Jane Doe"
        assert row.payer_url == reverse("hub_admin_member_edit", args=[reservation.member_id])
        assert row.item_url == reverse("hub_equipment_detail", args=[reservation.equipment.slug])
        assert row.can_refund

    def it_links_the_payer_only_for_an_admin():
        _paid(_equipment())
        [row] = build_payments_ledger(window=_window()).rows
        assert row.payer_url is None

    def it_leaves_out_an_unpaid_hold_a_free_reservation_and_one_outside_the_window():
        equipment = _equipment()
        _paid(equipment, status=EquipmentReservation.Status.PENDING_PAYMENT, pi="")
        _paid(equipment, amount=0, pi="")
        _paid(equipment, paid_at=_now() - timedelta(days=90))
        assert build_payments_ledger(window=_window()).rows == ()

    def it_keeps_a_declined_reservation_listed_as_refunded():
        reservation = _paid(_equipment(), status=EquipmentReservation.Status.DECLINED)
        PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED
        )
        [row] = build_payments_ledger(window=_window()).rows
        assert row.status == "refunded"
        assert not row.can_refund

    def it_cannot_refund_a_payment_with_no_payment_intent():
        _paid(_equipment(), pi="")
        [row] = build_payments_ledger(window=_window()).rows
        assert not row.can_refund

    def it_filters_to_reservations_through_the_source():
        _paid(_equipment())
        RegistrationFactory(amount_paid_cents=4000, stripe_payment_id="pi_reg", confirmed_at=_now())
        ledger = build_payments_ledger(window=_window(), source="reservation")
        assert [row.source_kind for row in ledger.rows] == ["reservation"]
        assert {row.source_kind for row in build_payments_ledger(window=_window()).rows} == {"class", "reservation"}

    def it_exports_the_row_and_its_refund_in_the_ledger_csv():
        reservation = _paid(_equipment(), amount=5000)
        PaymentRefundFactory(
            registration=None,
            reservation=reservation,
            amount_cents=2000,
            status=PaymentRefund.Status.SUCCEEDED,
            settled_at=_now(),
        )
        response = stream_payments_csv(build_payments_ledger(window=_window()))
        body = b"".join(response.streaming_content).decode()
        day = timezone.localtime(reservation.created_at).date().isoformat()
        assert f'{day},Reservation,Jane Doe,"Reservation, CNC Machine",50.00,Partially refunded,Succeeded,20.00' in body


def describe_the_payments_tab():
    def it_shows_the_chip_the_row_and_the_refund_button_to_a_fog_admin(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment(), paid_at=_now())
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "source=reservation" in content
        assert "Reservation, CNC Machine" in content
        assert reverse("billing_reservation_refund_form", args=[reservation.pk]) in content

    def it_hides_the_refund_button_from_a_billing_administrator(client: Client):
        _login_billing_approver(client)
        reservation = _paid(_equipment(), paid_at=_now())
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "Reservation, CNC Machine" in content
        assert reverse("billing_reservation_refund_form", args=[reservation.pk]) not in content

    def it_shows_retry_refund_on_a_failed_reservation_row(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment(), paid_at=_now())
        PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=5000, status=PaymentRefund.Status.FAILED
        )
        content = client.get("/billing/admin/dashboard/?tab=payments&source=reservation").content.decode()
        assert "Retry Refund" in content
        assert reverse("billing_reservation_refund_form", args=[reservation.pk]) in content

    def it_draws_no_action_on_a_row_with_nothing_left_to_refund(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment(), pi="", paid_at=_now())
        content = client.get("/billing/admin/dashboard/?tab=payments").content.decode()
        assert "Reservation, CNC Machine" in content
        assert reverse("billing_reservation_refund_form", args=[reservation.pk]) not in content


def describe_the_reservation_refund_endpoints():
    def it_serves_the_refund_form_to_refund_authority(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        response = client.get(reverse("billing_reservation_refund_form", args=[reservation.pk]))
        content = response.content.decode()
        assert response.status_code == 200
        assert "Reservation, CNC Machine" in content
        assert "Paid $50.00" in content
        assert reverse("billing_reservation_refund", args=[reservation.pk]) in content
        assert 'name="share_decision"' not in content

    def it_names_what_was_already_refunded(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=1000, status=PaymentRefund.Status.SUCCEEDED
        )
        content = client.get(reverse("billing_reservation_refund_form", args=[reservation.pk])).content.decode()
        assert "$10.00 already refunded" in content
        assert "Up to $40.00" in content

    def it_asks_for_the_share_choice_once_the_managers_share_was_sent(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment(_manager()))
        _sent_payout(reservation)
        content = client.get(reverse("billing_reservation_refund_form", args=[reservation.pk])).content.decode()
        assert 'name="share_decision"' in content
        assert "Sami Okafor&#x27;s share was already sent ($35.00" in content

    def it_serves_the_retry_variant_when_the_latest_attempt_failed(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        refund = PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=5000, status=PaymentRefund.Status.FAILED
        )
        content = client.get(reverse("billing_reservation_refund_form", args=[reservation.pk])).content.decode()
        assert "Retry Refund" in content
        assert reverse("billing_payment_refund_retry", args=[refund.pk]) in content

    def it_403s_without_refund_authority_and_404s_an_unknown_row(client: Client):
        _login_billing_approver(client)
        reservation = _paid(_equipment())
        assert client.get(reverse("billing_reservation_refund_form", args=[reservation.pk])).status_code == 403
        assert client.post(reverse("billing_reservation_refund", args=[reservation.pk])).status_code == 403
        client.logout()
        _login_admin(client)
        assert client.get(reverse("billing_reservation_refund_form", args=[999999])).status_code == 404

    def it_refunds_from_the_panel_and_signals_refund_done(client: Client):
        user = _login_admin(client)
        reservation = _paid(_equipment())
        with _Stripe():
            response = client.post(
                reverse("billing_reservation_refund", args=[reservation.pk]), {"amount": "20.00", "reason": "noisy"}
            )
        assert response.status_code == 204
        triggers = json.loads(response["HX-Trigger"])
        assert triggers["refund-done"] is True
        assert triggers["showToast"]["message"] == "Refunded $20.00."
        refund = PaymentRefund.objects.get(reservation=reservation)
        assert (refund.status, refund.amount_cents, refund.reason, refund.initiated_by) == (
            PaymentRefund.Status.SUCCEEDED,
            2000,
            "noisy",
            user,
        )
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CONFIRMED  # money only; the booking stands

    def it_says_processing_when_stripe_answers_pending(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        pending = {"id": "re_pending", "status": "pending", "amount": 5000}
        with patch("billing.stripe_utils.create_refund", return_value=pending):
            response = client.post(reverse("billing_reservation_refund", args=[reservation.pk]), {"amount": "50.00"})
        assert json.loads(response["HX-Trigger"])["showToast"]["message"] == "Refund sent. Stripe is processing it."

    def it_rerenders_the_form_on_a_bad_amount(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        response = client.post(reverse("billing_reservation_refund", args=[reservation.pk]), {"amount": "99.00"})
        assert response.status_code == 200
        assert "between $0.01 and $50.00" in response.content.decode()
        assert not PaymentRefund.objects.exists()

    def it_surfaces_a_stripe_rejection_loudly_in_the_failed_state(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        with patch("billing.stripe_utils.create_refund", side_effect=stripe.StripeError("Disputed.")):
            response = client.post(reverse("billing_reservation_refund", args=[reservation.pk]), {"amount": "50.00"})
        triggers = json.loads(response["HX-Trigger"])
        assert (triggers["showToast"]["type"], "refund-done" in triggers) == ("error", False)
        assert "Disputed." in triggers["showToast"]["message"]
        assert "Retry Refund" in response.content.decode()
        assert PaymentRefund.objects.get(reservation=reservation).status == PaymentRefund.Status.FAILED

    def it_sends_a_failed_refund_alert_to_the_reservation_ledger():
        from billing import refunds

        reservation = _paid(_equipment())
        refund = PaymentRefundFactory(registration=None, reservation=reservation, status=PaymentRefund.Status.FAILED)
        assert refunds._refund_admin_url(refund).endswith(
            "/billing/admin/dashboard/?tab=payments&source=reservation&status=failed"
        )


def describe_a_stripe_dashboard_refund():
    def _event(payment_intent: str, refund_items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"data": {"object": {"payment_intent": payment_intent, "refunds": {"data": refund_items}}}}

    def it_finds_a_reservation_by_its_payment_intent_after_registrations_and_fees():
        reservation = _paid(_equipment(), pi="pi_res_lookup")
        assert _refundable_source_for_payment_intent("pi_res_lookup") == reservation
        fee = LateCancellationFeeFactory(stripe_payment_id="pi_res_shared")
        _paid(_equipment(name="Shared Lathe"), pi="pi_res_shared")
        assert _refundable_source_for_payment_intent("pi_res_shared") == fee
        assert _refundable_source_for_payment_intent("pi_nobody") is None

    def it_reconciles_into_the_ledger_and_records_not_asked_on_a_sent_share(django_capture_on_commit_callbacks):
        reservation = _paid(_equipment(_manager()), pi="pi_res_dash")
        _sent_payout(reservation)
        with _Stripe() as fake, django_capture_on_commit_callbacks(execute=True):
            handle_charge_refunded(_event("pi_res_dash", [{"id": "re_dash", "amount": 5000, "status": "succeeded"}]))
        refund = PaymentRefund.objects.get(stripe_refund_id="re_dash")
        assert (refund.reservation, refund.source, refund.status) == (
            reservation,
            PaymentRefund.Source.STRIPE_DASHBOARD,
            PaymentRefund.Status.SUCCEEDED,
        )
        assert refund.share_decision == NOT_ASKED
        assert fake.reversals == []
        notes = payouts.kept_share_notes(build_reconciliation(window=_window()).lines)
        assert notes[("reservation", reservation.pk)].startswith("Payee kept a share already sent")


# ---------------------------------------------------------------------------
# Reconciliation: the reservation line, its triad and the snapshot
# ---------------------------------------------------------------------------


def _line(result: Any, reservation: EquipmentReservation) -> Any:
    [line] = [line for line in result.lines if (line.source_kind, line.source_pk) == ("reservation", reservation.pk)]
    return line


def describe_the_reconciliation_line():
    def it_splits_a_paid_reservation_by_the_reservation_triad():
        payee = _manager()
        guild = GuildFactory(name="Printmaking Guild")
        reservation = _paid(_equipment(payee, guild=guild), amount=10000)
        result = build_reconciliation(window=_window(), include_voting=False)
        line = _line(result, reservation)
        assert (line.item, line.date, line.payer_name) == (
            "Reservation, CNC Machine",
            reservation.created_at,
            "Jane Doe",
        )
        assert line.shares == {("manager", payee.pk): 7000, ("guild", guild.pk): 1500, ("pl", None): 1500}
        assert not line.unassigned
        assert result.reservation_percents == {"manager": Decimal("70"), "guild": Decimal("15"), "pl": Decimal("15")}
        [manager_row] = result.groups[RecipientKind.MANAGER]
        assert (manager_row.label, manager_row.total_cents, manager_row.transaction_count) == ("Sami Okafor", 7000, 1)
        assert result.producers_total_cents == 8500

    def it_follows_an_edited_triad():
        settings_obj = BillingSettings.load()
        settings_obj.reservation_manager_percent = Decimal("80")
        settings_obj.reservation_guild_percent = Decimal("5")
        settings_obj.reservation_pl_percent = Decimal("15")
        settings_obj.save()
        payee = _manager()
        reservation = _paid(_equipment(payee, guild=GuildFactory()), amount=10000)
        line = _line(build_reconciliation(window=_window(), include_voting=False), reservation)
        assert line.shares[("manager", payee.pk)] == 8000

    def it_rolls_the_share_to_past_lives_and_notes_it_when_nobody_is_picked_or_there_is_no_guild():
        reservation = _paid(_equipment(None), amount=10000)
        result = build_reconciliation(window=_window(), include_voting=False)
        line = _line(result, reservation)
        assert line.shares == {("pl", None): 10000}
        assert line.unassigned
        assert line.note == "manager unset -> Past Lives; guild unset -> Past Lives"
        assert result.unassigned_note_count == 1
        assert result.groups[RecipientKind.MANAGER] == []

    def it_nets_refunds_and_leaves_out_unpaid_holds():
        reservation = _paid(_equipment(_manager()), amount=10000)
        PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=4000, status=PaymentRefund.Status.SUCCEEDED
        )
        _paid(_equipment(name="Held Saw"), status=EquipmentReservation.Status.PENDING_PAYMENT, pi="")
        result = build_reconciliation(window=_window(), include_voting=False)
        [line] = [line for line in result.lines if line.source_kind == "reservation"]
        assert (line.gross_cents, line.refunded_cents, line.net_cents) == (10000, 4000, 6000)

    def it_applies_an_admin_override_and_an_omission():
        payee = _manager()
        overridden = _paid(_equipment(payee), amount=10000)
        omitted = _paid(_equipment(name="Omitted Kiln"), amount=2000)
        TransactionAdjustment.objects.create(
            source_kind="reservation",
            source_pk=overridden.pk,
            override_percents={"manager": 50, "guild": 0, "pl": 50},
        )
        TransactionAdjustment.objects.create(source_kind="reservation", source_pk=omitted.pk, is_omitted=True)
        result = build_reconciliation(window=_window(), include_voting=False)
        line = _line(result, overridden)
        assert line.overridden
        assert line.shares[("manager", payee.pk)] == 5000
        assert _line(result, omitted).omitted
        assert ShareSource().for_reservation(omitted) == 0

    def it_marks_the_manager_kind_a_producer_with_its_own_heading():
        assert RecipientKind.MANAGER.is_producer
        assert RecipientKind.INSTRUCTOR.is_producer
        assert not RecipientKind.GUILD.is_producer
        assert not RecipientKind.PL.is_producer
        assert GROUP_LABELS[RecipientKind.MANAGER] == "Equipment Managers"

    def it_splits_sent_through_stripe_from_owed_manually_for_a_manager():
        _turn_on()
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=10000)
        unconnected = _manager("Lee Tran", connected=False)
        _paid(_equipment(unconnected, name="Band Saw"), amount=10000, pi="pi_res_2")
        result = build_reconciliation(window=_window(), include_voting=False)
        rows = {row.label: (row.stripe_cents, row.manual_cents) for row in result.groups[RecipientKind.MANAGER]}
        assert rows == {"Sami Okafor": (7000, 0), "Lee Tran": (0, 7000)}
        body = b"".join(stream_reconciliation_csv(result).streaming_content).decode()
        assert "Equipment Managers,Sami Okafor,1,70.00,,70.00,70.00,0.00" in body
        assert reservation.pk

    def it_draws_the_manager_group_and_an_attention_row_on_the_tab(client: Client):
        _login_admin(client)
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=10000)
        Payout.objects.create(
            reservation=reservation,
            payee=payee,
            amount_cents=7000,
            due_at=_now(),
            status=Payout.Status.FAILED,
            failure_reason="Account closed.",
        )
        window = _window()
        content = client.get(
            reverse("billing_admin_reconciliation_table"),
            {"start": window.start.isoformat(), "end": window.end.isoformat()},
        ).content.decode()
        assert "Equipment Managers" in content
        assert "Sent through Stripe" in content
        assert "CNC Machine reservation" in content
        assert "Reservation, CNC Machine" in content
        assert reverse("billing_reconciliation_adjust_form", args=["reservation", reservation.pk]) in content


def describe_adjusting_a_reservation_line():
    def it_serves_the_adjust_form_with_the_reservation_triad(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        response = client.get(reverse("billing_reconciliation_adjust_form", args=["reservation", reservation.pk]))
        assert response.status_code == 200
        form = response.context["form"]
        assert form.percent_keys == ["manager", "guild", "pl"]
        assert form["percent_manager"].value() == Decimal("70.00")

    def it_saves_an_override(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment())
        response = client.post(
            reverse("billing_reconciliation_adjust", args=["reservation", reservation.pk]),
            {"percent_manager": "60", "percent_guild": "20", "percent_pl": "20", "reason": "shared run"},
        )
        assert response.status_code == 204
        adjustment = TransactionAdjustment.objects.get(source_kind="reservation", source_pk=reservation.pk)
        assert {k: Decimal(str(v)) for k, v in adjustment.override_percents.items()} == {
            "manager": Decimal("60"),
            "guild": Decimal("20"),
            "pl": Decimal("20"),
        }


def describe_the_month_end_snapshot():
    def it_carries_the_reservation_split_and_the_manager_rows():
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=10000)
        today = timezone.localdate()
        snapshot = ReconciliationSnapshot.take(period_start=today - timedelta(days=40), period_end=today, title="Oct")
        assert snapshot.results["reservation_percents"] == {"manager": "70.00", "guild": "15.00", "pl": "15.00"}
        [group] = [group for group in snapshot.results["groups"] if group["kind"] == "manager"]
        assert group["rows"][0]["label"] == "Sami Okafor"
        assert group["rows"][0]["total_cents"] == 7000
        rebuilt = result_from_snapshot(snapshot)
        assert rebuilt.groups[RecipientKind.MANAGER][0].total_cents == 7000
        assert rebuilt.reservation_percents["manager"] == Decimal("70.00")
        # The snapshot binds the payout split: the share gets its row now, counted where the rule put it.
        payout = Payout.objects.get(reservation=reservation)
        assert (payout.payee, payout.amount_cents, payout.status) == (payee, 7000, Payout.Status.OWED_MANUALLY)

    def it_reads_a_snapshot_taken_before_reservations_had_a_split():
        snapshot = ReconciliationSnapshot.objects.create(
            title="Sep", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), results={}, grand_total_cents=0
        )
        assert result_from_snapshot(snapshot).reservation_percents == {}

    def it_draws_the_snapshot_detail_with_the_producer_columns(client: Client):
        _login_admin(client)
        _paid(_equipment(_manager()), amount=10000)
        today = timezone.localdate()
        snapshot = ReconciliationSnapshot.take(period_start=today - timedelta(days=40), period_end=today)
        content = client.get(reverse("billing_reconciliation_snapshot_detail", args=[snapshot.pk])).content.decode()
        assert "Equipment Managers" in content
        assert "Owed manually" in content


# ---------------------------------------------------------------------------
# Payouts: the earning, its send, the tab rows and the take back
# ---------------------------------------------------------------------------


def describe_the_reservation_earning():
    def it_is_the_picked_managers_share_due_48_hours_after_it_starts():
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=5000)
        [earning] = payouts._earnings([], [], payouts._reservations())
        assert (earning.kind, earning.source, earning.payee, earning.share_cents) == (
            "reservation",
            reservation,
            payee,
            3500,
        )
        assert (earning.item, earning.detail, earning.taught_at) == (
            "CNC Machine",
            "2 hours, Jane D.",
            reservation.starts_at,
        )
        assert earning.due_at == reservation.starts_at + timedelta(hours=48)

    def it_falls_due_48_hours_after_payment_when_that_came_later():
        payee = _manager()
        reservation = _paid(_equipment(payee), starts_at=_now() - timedelta(days=5), paid_at=_now() - timedelta(days=1))
        [earning] = payouts._earnings([], [], payouts._reservations())
        assert earning.due_at == reservation.created_at + timedelta(hours=48)

    def it_never_earns_without_a_pick_an_unpaid_hold_or_a_refunded_decline_or_cancel():
        payee = _manager()
        equipment = _equipment(payee)
        _paid(_equipment(None, name="Unpicked Lathe"))
        _paid(equipment, status=EquipmentReservation.Status.PENDING_PAYMENT)
        _paid(equipment, amount=0, pi="")
        for status in (EquipmentReservation.Status.DECLINED, EquipmentReservation.Status.CANCELLED):
            refunded = _paid(equipment, status=status)
            PaymentRefundFactory(
                registration=None, reservation=refunded, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED
            )
        assert payouts._earnings([], [], payouts._reservations()) == []

    def it_keeps_a_request_awaiting_approval_as_an_earning_like_reconciliation_does():
        payee = _manager()
        waiting = _paid(_equipment(payee), status=EquipmentReservation.Status.PENDING_APPROVAL)
        [earning] = payouts._earnings([], [], payouts._reservations())
        assert (earning.source, earning.share_cents) == (waiting, 3500)

    def it_counts_as_an_earning_for_the_payee_only():
        payee = _manager()
        _paid(_equipment(payee))
        assert payouts.has_earning(payee)
        assert not payouts.has_earning(_manager("Other Person"))

    def it_words_a_reservations_length():
        assert [payouts.length_words(m) for m in (30, 60, 90, 120, 61)] == [
            "30 minutes",
            "1 hour",
            "1 hour 30 minutes",
            "2 hours",
            "1 hour 1 minute",
        ]

    def it_names_the_reservation_in_the_payout_alerts():
        reservation = _paid(_equipment(_manager()))
        assert _payout_item(_sent_payout(reservation)) == "CNC Machine reservation"

    def it_still_names_dates_and_kinds_an_orientation_share():
        booking = OrientationBookingFactory(amount_paid_cents=5000, stripe_payment_id="pi_ob")
        payout = Payout.objects.create(orientation_booking=booking, payee=_manager(), amount_cents=1, due_at=_now())
        assert _payout_item(payout) == f"{booking.orientation_type.owner_name} orientation"
        assert (payout.source, payout.source_kind, payout.paid_on) == (booking, "orientation", booking.requested_at)


def describe_sending_the_share():
    def it_records_and_sends_through_the_existing_job():
        _turn_on()
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=5000)
        with _Stripe() as fake:
            run = payouts.run_payouts(_now())
        assert (run.created, run.sent) == (1, 1)
        payout = Payout.objects.get()
        assert (payout.reservation, payout.payee, payout.amount_cents, payout.status) == (
            reservation,
            payee,
            3500,
            Payout.Status.SENT,
        )
        assert payout.source == reservation
        assert payout.source_kind == "reservation"
        assert payout.paid_on == reservation.created_at
        [transfer] = fake.transfers
        assert transfer["amount_cents"] == 3500
        assert transfer["destination"] == f"acct_{payee.pk}"
        assert transfer["source_transaction"] == "ch_res"
        assert transfer["idempotency_key"] == f"payout-{payout.pk}-a1"

    def it_waits_until_48_hours_after_the_start():
        _turn_on()
        _paid(_equipment(_manager()), starts_at=_now() - timedelta(hours=47))
        with _Stripe() as fake:
            payouts.run_payouts(_now())
        assert not Payout.objects.exists()
        assert fake.transfers == []

    def it_records_nothing_while_payouts_are_off():
        _paid(_equipment(_manager()))
        with _Stripe():
            payouts.run_payouts(_now())
        assert not Payout.objects.exists()

    def it_owes_by_hand_and_invites_a_payee_who_is_not_connected():
        _turn_on()
        payee = Member.objects.get(user=UserFactory())  # the invite needs a login to reach
        _paid(_equipment(payee))
        with _Stripe() as fake:
            payouts.run_payouts(_now())
        payout = Payout.objects.get()
        assert (payout.status, payout.owed_reason) == (Payout.Status.OWED_MANUALLY, Payout.OwedReason.NOT_CONNECTED)
        assert fake.transfers == []
        invites = EventDelivery.objects.filter(event_key="billing.payouts_invite")
        assert set(invites.values_list("period", flat=True)) == {f"payouts:invite:{payee.pk}"}

    def it_pays_a_manager_their_share_of_their_own_reservation():
        _turn_on()
        payee = _manager()
        _paid(_equipment(payee), member=payee, amount=5000)
        with _Stripe() as fake:
            payouts.run_payouts(_now())
        assert [t["amount_cents"] for t in fake.transfers] == [3500]
        assert Payout.objects.get().payee == payee

    def it_re_reads_the_share_before_a_retry():
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=5000)
        payout = Payout.objects.create(
            reservation=reservation, payee=payee, amount_cents=3500, due_at=_now(), status=Payout.Status.FAILED
        )
        PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=2000, status=PaymentRefund.Status.SUCCEEDED
        )
        assert payouts._refresh_amount(payout)
        assert payout.amount_cents == 2100
        PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=3000, status=PaymentRefund.Status.SUCCEEDED
        )
        assert not payouts._refresh_amount(payout)
        assert payout.status == Payout.Status.NOTHING_DUE

    def it_holds_exactly_one_source():
        payee = _manager()
        reservation = _paid(_equipment(payee))
        with pytest.raises(IntegrityError), transaction.atomic():
            Payout.objects.create(
                reservation=reservation,
                registration=RegistrationFactory(),
                payee=payee,
                amount_cents=1,
                due_at=_now(),
            )
        with pytest.raises(IntegrityError), transaction.atomic():
            Payout.objects.create(payee=payee, amount_cents=1, due_at=_now())


def describe_the_payouts_tab_rows():
    def it_lists_each_reservation_on_its_own_row_with_its_length_and_member():
        payee = _manager()
        equipment = _equipment(payee)
        start = _now() - timedelta(days=3)
        _paid(equipment, starts_at=start, minutes=120, amount=5000)
        _paid(
            equipment,
            starts_at=start + timedelta(hours=3),
            minutes=90,
            amount=3750,
            member=MemberFactory(full_legal_name="Lee Tran", preferred_name=""),
        )
        rows = payouts.payee_earnings(payee).rows
        assert sorted((row.item, row.detail, row.amount_cents) for row in rows) == [
            # 70% of $37.50 is $26.25, but the guild and Past Lives each round up half a cent: the
            # largest share absorbs the drift so the three sum to the payment.
            ("CNC Machine", "1 hour 30 minutes, Lee T.", 2624),
            ("CNC Machine", "2 hours, Jane D.", 3500),
        ]
        assert {row.state for row in rows} == {"owed"}

    def it_keeps_another_items_earnings_off_this_payees_tab():
        payee = _manager()
        _paid(_equipment(_manager("Somebody Else")))
        assert payouts.payee_earnings(payee).rows == []


def describe_taking_the_share_back():
    def it_takes_back_the_share_when_the_panel_refund_chooses_it(client: Client, django_capture_on_commit_callbacks):
        _login_admin(client)
        reservation = _paid(_equipment(_manager()), amount=5000)
        _sent_payout(reservation)
        with _Stripe() as fake, django_capture_on_commit_callbacks(execute=True):
            response = client.post(
                reverse("billing_reservation_refund", args=[reservation.pk]),
                {"amount": "20.00", "reason": "", "share_decision": "take_back"},
            )
        assert response.status_code == 204
        refund = PaymentRefund.objects.get()
        assert fake.reversals == [{"transfer_id": "tr_res", "amount_cents": 1400, "refund_pk": refund.pk}]
        assert (refund.share_decision, refund.share_reversed_cents) == (TAKE_BACK, 1400)
        payout = Payout.objects.get()
        assert (payout.status, payout.reversed_cents) == (Payout.Status.TAKEN_BACK, 1400)
        states = sorted((row.state, row.amount_cents) for row in payouts.payee_earnings(payout.payee).rows)
        assert states == [("sent", 3500), ("taken_back", -1400)]

    def it_records_not_asked_on_the_automatic_decline_refund(django_capture_on_commit_callbacks):
        reservation = _paid(_equipment(_manager()), amount=5000)
        _sent_payout(reservation)
        with _Stripe() as fake, django_capture_on_commit_callbacks(execute=True):
            issue_refund(reservation)
        assert fake.reversals == []
        assert PaymentRefund.objects.get().share_decision == NOT_ASKED
        assert Payout.objects.get().status == Payout.Status.SENT

    def it_flags_a_refund_that_landed_while_the_share_was_being_sent():
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=5000)
        payout = Payout.objects.create(
            reservation=reservation, payee=payee, amount_cents=3500, due_at=_now(), status=Payout.Status.PENDING
        )
        refund = PaymentRefundFactory(
            registration=None, reservation=reservation, amount_cents=5000, status=PaymentRefund.Status.SUCCEEDED
        )
        payout.mark_sent("tr_late")
        refund.refresh_from_db()
        assert refund.share_decision == NOT_ASKED

    def it_still_skips_a_late_fee_refund():
        refund = PaymentRefundFactory(registration=None, late_fee=LateCancellationFeeFactory())
        payouts.settle_refund_share(refund)  # no share to settle: nothing raises, nothing changes
        refund.refresh_from_db()
        assert refund.share_decision == PaymentRefund.ShareDecision.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Review round 1: a share is recorded once, for the payee it was recorded for
# ---------------------------------------------------------------------------


def _take_snapshot() -> ReconciliationSnapshot:
    today = timezone.localdate()
    return ReconciliationSnapshot.take(period_start=today - timedelta(days=40), period_end=today, title="Oct")


def _manager_row(snapshot: ReconciliationSnapshot) -> dict[str, Any]:
    [group] = [group for group in snapshot.results["groups"] if group["kind"] == "manager"]
    [row] = group["rows"]
    return row


def describe_a_request_awaiting_approval_at_month_end():
    def it_is_counted_once_held_until_approved_and_sent_once():
        _turn_on(_now() - timedelta(days=60))
        payee = _manager()
        starts = _now() + timedelta(days=2)
        waiting = _paid(
            _equipment(payee),
            amount=10000,
            starts_at=starts,
            paid_at=_now() - timedelta(days=1),
            status=EquipmentReservation.Status.PENDING_APPROVAL,
        )
        snapshot = _take_snapshot()
        row = _manager_row(snapshot)
        assert (row["total_cents"], row["stripe_cents"]) == (7000, 7000)  # Sent through Stripe, not owed by hand
        payout = Payout.objects.get(reservation=waiting)
        assert (payout.status, payout.counted_as_stripe_in) == (Payout.Status.PENDING, snapshot)

        due = starts + timedelta(hours=49)
        with _Stripe() as fake:
            payouts.run_payouts(due)  # still undecided: held, nothing recorded
        payout.refresh_from_db()
        assert (fake.transfers, payout.status) == ([], Payout.Status.PENDING)

        EquipmentReservation.objects.filter(pk=waiting.pk).update(status=EquipmentReservation.Status.CONFIRMED)
        with _Stripe() as fake:
            payouts.run_payouts(due)
            payouts.run_payouts(due + timedelta(hours=1))
        assert [t["amount_cents"] for t in fake.transfers] == [7000]
        assert Payout.objects.filter(reservation=waiting).count() == 1
        assert Payout.objects.get().status == Payout.Status.SENT

    def it_is_nothing_due_once_declined():
        _turn_on(_now() - timedelta(days=60))
        starts = _now() + timedelta(days=2)
        waiting = _paid(
            _equipment(_manager()),
            starts_at=starts,
            paid_at=_now() - timedelta(days=1),
            status=EquipmentReservation.Status.PENDING_APPROVAL,
        )
        _take_snapshot()
        EquipmentReservation.objects.filter(pk=waiting.pk).update(status=EquipmentReservation.Status.DECLINED)
        with _Stripe() as fake:
            payouts.run_payouts(starts + timedelta(hours=49))
        assert fake.transfers == []
        assert Payout.objects.get().status == Payout.Status.NOTHING_DUE


def describe_removing_the_manager_a_share_was_recorded_for():
    def _remove(equipment: Equipment, member: Member) -> None:
        EquipmentStaffMembership.objects.get(equipment=equipment, member=member).delete()

    def it_still_sends_a_pending_share_to_them():
        _turn_on()
        payee = _manager()
        equipment = _equipment(payee)
        reservation = _paid(
            equipment, amount=5000, starts_at=_now() + timedelta(days=1), paid_at=_now() - timedelta(days=1)
        )
        payout = Payout.objects.create(
            reservation=reservation,
            payee=payee,
            amount_cents=3500,
            due_at=_now() + timedelta(days=3),
            status=Payout.Status.PENDING,
        )
        _remove(equipment, payee)
        equipment.refresh_from_db()
        assert equipment.payee is None
        with _Stripe() as fake:
            payouts.run_payouts(_now() + timedelta(days=4))
        payout.refresh_from_db()
        assert payout.status == Payout.Status.SENT
        assert [(t["amount_cents"], t["destination"]) for t in fake.transfers] == [(3500, f"acct_{payee.pk}")]

    def it_takes_back_their_sent_share_on_a_full_refund(django_capture_on_commit_callbacks):
        payee = _manager()
        equipment = _equipment(payee)
        reservation = _paid(equipment, amount=5000)
        payout = _sent_payout(reservation)
        _remove(equipment, payee)
        reservation.refresh_from_db()
        with _Stripe() as fake, django_capture_on_commit_callbacks(execute=True):
            issue_refund(reservation, share_decision=TAKE_BACK)
        payout.refresh_from_db()
        assert [r["amount_cents"] for r in fake.reversals] == [3500]
        assert (payout.status, payout.reversed_cents) == (Payout.Status.TAKEN_BACK, 3500)

    def it_reads_the_refunded_portion_from_the_payee_recorded():
        payee = _manager()
        equipment = _equipment(payee)
        reservation = _paid(equipment, amount=5000)
        payout = _sent_payout(reservation)
        _remove(equipment, payee)
        refund = PaymentRefundFactory(
            registration=None,
            reservation=reservation,
            amount_cents=5000,
            status=PaymentRefund.Status.SUCCEEDED,
            share_decision=TAKE_BACK,
        )
        assert payouts._refunded_portion(refund, payout) == 3500


def describe_a_payment_closed_out_before_anyone_was_picked():
    def it_is_not_sent_once_someone_is_picked_later():
        _turn_on(_now() - timedelta(days=60))
        equipment = _equipment(None)
        _paid(equipment, amount=10000)
        snapshot = _take_snapshot()
        assert [g["rows"] for g in snapshot.results["groups"] if g["kind"] == "manager"] == [[]]
        sami = _manager()
        EquipmentStaffMembershipFactory(equipment=equipment, member=sami)
        Equipment.objects.filter(pk=equipment.pk).update(payee=sami)
        with _Stripe() as fake:
            run = payouts.run_payouts(_now())
        assert (run.created, fake.transfers) == (0, [])
        assert not Payout.objects.exists()

    def it_still_records_a_later_payment_on_the_newly_picked_item():
        _turn_on(_now() - timedelta(days=60))
        equipment = _equipment(None)
        _take_snapshot()  # an empty month closed out earlier
        sami = _manager()
        EquipmentStaffMembershipFactory(equipment=equipment, member=sami)
        Equipment.objects.filter(pk=equipment.pk).update(payee=sami)
        tomorrow = timezone.localdate() + timedelta(days=1)
        paid_at = timezone.make_aware(datetime.combine(tomorrow, datetime.min.time())) + timedelta(hours=12)
        _paid(equipment, amount=10000, starts_at=paid_at, paid_at=paid_at)
        with _Stripe() as fake:
            payouts.run_payouts(paid_at + timedelta(hours=49))
        assert [t["amount_cents"] for t in fake.transfers] == [7000]


def describe_the_payee_a_share_was_recorded_for():
    def it_keeps_the_share_on_their_tab_and_line_after_the_pick_changes():
        old = _manager()
        equipment = _equipment(old)
        reservation = _paid(equipment, amount=5000)
        _sent_payout(reservation)
        new = _manager("New Person")
        EquipmentStaffMembershipFactory(equipment=equipment, member=new)
        Equipment.objects.filter(pk=equipment.pk).update(payee=new)
        assert payouts.has_earning(old)
        assert not payouts.has_earning(new)
        assert [row.state for row in payouts.payee_earnings(old).rows] == ["sent"]
        assert payouts.payee_earnings(new).rows == []
        line = _line(build_reconciliation(window=_window(), include_voting=False), reservation)
        assert line.shares[("manager", old.pk)] == 3500
        assert ("manager", new.pk) not in line.shares

    def it_keeps_a_sent_share_on_the_tab_after_the_reservation_is_cancelled():
        payee = _manager()
        reservation = _paid(_equipment(payee), amount=5000)
        _sent_payout(reservation)
        EquipmentReservation.objects.filter(pk=reservation.pk).update(status=EquipmentReservation.Status.CANCELLED)
        assert [(row.state, row.amount_cents) for row in payouts.payee_earnings(payee).rows] == [("sent", 3500)]
        assert payouts.has_earning(payee)


def describe_retrying_a_failed_reservation_refund():
    def it_retries_from_the_panel_and_lands_succeeded(client: Client):
        _login_admin(client)
        reservation = _paid(_equipment(), amount=5000)
        refund = PaymentRefundFactory(
            registration=None,
            reservation=reservation,
            amount_cents=5000,
            status=PaymentRefund.Status.FAILED,
            failure_reason="card_declined",
        )
        assert reservation.refund_state == "failed"
        succeeded = {"id": "re_retry_res", "status": "succeeded", "amount": 5000}
        with patch("billing.stripe_utils.create_refund", return_value=succeeded) as create:
            response = client.post(reverse("billing_payment_refund_retry", args=[refund.pk]))
        assert response.status_code == 204
        assert json.loads(response["HX-Trigger"])["refund-done"] is True
        assert create.call_args.kwargs["payment_intent_id"] == "pi_res"
        refund.refresh_from_db()
        assert (refund.status, refund.attempt) == (PaymentRefund.Status.SUCCEEDED, 2)
        reservation = EquipmentReservation.objects.get(pk=reservation.pk)
        assert (reservation.refund_state, reservation.refundable_cents) == ("full", 0)

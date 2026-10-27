"""#905: the Reconciliation tab says which producers Stripe already pays and how each line's share is paid."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from billing.models import BillingSettings, Payout, PayoutAccount, ReconciliationSnapshot, TransactionAdjustment
from billing.payments_panel import PanelWindow
from billing.payouts import LinePayout, LineState, PayoutBadge, account_badge
from billing.reconciliation import RecipientKind, ReconciliationResult, build_reconciliation, result_from_snapshot
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import Registration
from membership.models import Equipment, EquipmentReservation, Member, OrientationBooking
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
OCT_1 = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
WINDOW = PanelWindow(start=date(2026, 10, 1), end=date(2026, 10, 31))
WINDOW_QUERY = {"start": "2026-10-01", "end": "2026-10-31"}


@pytest.fixture(autouse=True)
def _frozen_now():
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


def _turn_on() -> None:
    bs = BillingSettings.load()
    bs.connect_enabled = True
    bs.class_instructor_percent = Decimal("70")
    bs.class_guild_percent = Decimal("10")
    bs.class_pl_percent = Decimal("20")
    bs.save()
    BillingSettings.objects.filter(pk=1).update(payouts_on_since=NOW - timedelta(days=30))


def _producer(
    name: str,
    *,
    status: str | None = PayoutAccount.Status.ACTIVE,
    active_since: datetime | None = OCT_1,
    livemode: bool = False,
) -> Member:
    """A member with a payout account in ``status`` (test mode by default), or none when ``status`` is None."""
    member = MemberFactory(full_legal_name=name, preferred_name="")
    if status is not None:
        PayoutAccount.objects.create(
            member=member,
            stripe_account_id=f"acct_{member.pk}",
            livemode=livemode,
            status=status,
            active_since=active_since,
        )
    return member


def _registration(
    instructor: Member,
    *,
    class_at: datetime | None = NOW - timedelta(days=3),
    pi: str = "pi_123",
) -> Registration:
    """A $100 registration paid Oct 10; ``class_at=None`` leaves the class with no sessions."""
    offering = ClassOfferingFactory(instructor=instructor)
    if class_at is not None:
        ClassSessionFactory(class_offering=offering, starts_at=class_at)
    reg = RegistrationFactory(class_offering=offering, amount_paid_cents=10000, stripe_payment_id=pi)
    Registration.objects.filter(pk=reg.pk).update(confirmed_at=NOW - timedelta(days=10))
    reg.refresh_from_db()
    return reg


def _payout(reg: Registration, status: str, *, sent_at: datetime | None = None) -> Payout:
    return Payout.objects.create(
        registration=reg,
        payee=reg.class_offering.instructor,
        amount_cents=7000,
        due_at=NOW - timedelta(days=1),
        status=status,
        sent_at=sent_at,
    )


def _booking(orientor: Member) -> OrientationBooking:
    slot_at = NOW - timedelta(days=3)
    slot = OrientationSlotFactory(starts_at=slot_at, ends_at=slot_at + timedelta(hours=1))
    booking = OrientationBookingFactory(
        slot=slot,
        oriented_by=orientor,
        amount_paid_cents=5000,
        stripe_payment_id="pi_456",
        status=OrientationBooking.Status.CONFIRMED,
    )
    OrientationBooking.objects.filter(pk=booking.pk).update(requested_at=NOW - timedelta(days=10))
    return booking


def _reservation(manager: Member) -> EquipmentReservation:
    equipment = EquipmentFactory(
        name="CNC Machine", pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=2500, payee=manager
    )
    EquipmentStaffMembershipFactory(equipment=equipment, member=manager)
    starts_at = NOW - timedelta(days=3)
    reservation = EquipmentReservationFactory(
        equipment=equipment,
        member=MemberFactory(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=2),
        status=EquipmentReservation.Status.CONFIRMED,
        amount_paid_cents=5000,
        stripe_payment_id="pi_res",
    )
    EquipmentReservation.objects.filter(pk=reservation.pk).update(created_at=NOW - timedelta(days=10))
    return reservation


def _row(result: ReconciliationResult, kind: RecipientKind, member: Member) -> Any:
    return next(alloc for alloc in result.groups[kind] if alloc.recipient_id == member.pk)


def _line(result: ReconciliationResult, kind: str, pk: int) -> Any:
    return next(line for line in result.lines if (line.source_kind, line.source_pk) == (kind, pk))


def _login_admin(client: Client) -> None:
    User.objects.create_superuser(username="treasurer", password="pass", email="treasurer@example.com")
    client.login(username="treasurer", password="pass")


def describe_account_badge():
    def it_says_paid_by_hand_for_a_payee_with_no_account():
        assert account_badge(None) == PayoutBadge("Paid by hand", "muted")

    def it_names_the_day_automatic_payouts_came_on():
        account = PayoutAccount(status=PayoutAccount.Status.ACTIVE, active_since=datetime(2026, 10, 12, 18, tzinfo=UTC))
        assert account_badge(account) == PayoutBadge("Automatic payouts on since Oct 12, 2026", "ok")

    def it_leaves_the_date_off_an_active_account_with_no_stamp():
        account = PayoutAccount(status=PayoutAccount.Status.ACTIVE, active_since=None)
        assert account_badge(account) == PayoutBadge("Automatic payouts on", "ok")

    def it_says_paused_by_stripe():
        account = PayoutAccount(status=PayoutAccount.Status.PAUSED, active_since=OCT_1)
        assert account_badge(account) == PayoutBadge("Paused by Stripe", "fail")

    def it_says_signup_is_not_finished():
        account = PayoutAccount(status=PayoutAccount.Status.NEEDS_INFO)
        assert account_badge(account) == PayoutBadge("Stripe signup not finished", "warn")


def describe_line_payout():
    def it_dates_a_sent_share_in_local_time():
        # 2am UTC on Oct 15 is still Oct 14 in Portland.
        state = LinePayout(LineState.SENT, datetime(2026, 10, 15, 2, 0, tzinfo=UTC))
        assert state.badge == PayoutBadge("Sent through Stripe Oct 14", "muted")
        assert state.through_stripe is True

    def it_leaves_the_date_off_a_sent_share_with_no_stamp():
        assert LinePayout(LineState.SENT).badge == PayoutBadge("Sent through Stripe", "muted")

    def it_says_a_share_will_be_sent():
        state = LinePayout(LineState.WILL_SEND)
        assert state.badge == PayoutBadge("Will be sent through Stripe", "muted")
        assert state.through_stripe is True

    def it_names_a_refund_taken_back_from_a_sent_share():
        state = LinePayout(LineState.TAKEN_BACK, datetime(2026, 10, 13, 18, 0, tzinfo=UTC))
        assert state.badge == PayoutBadge("Sent through Stripe Oct 13, refund taken back", "muted")
        assert state.through_stripe is True
        assert LinePayout(LineState.TAKEN_BACK).badge == PayoutBadge("Sent through Stripe, refund taken back", "muted")

    def it_says_stripe_is_retrying_a_rejected_transfer_in_red_but_counts_it_owed():
        state = LinePayout(LineState.RETRYING)
        assert state.badge == PayoutBadge("Stripe is retrying", "fail")
        assert (state.through_stripe, state.owed) == (False, True)

    def it_says_in_red_that_stripe_could_not_confirm_a_pending_transfer():
        state = LinePayout(LineState.UNCONFIRMED)
        assert state.badge == PayoutBadge("Not sent, Stripe unreachable", "fail")
        assert (state.through_stripe, state.owed) == (True, False)

    def it_says_owed_now_with_the_day_it_fell_due():
        state = LinePayout(LineState.OWED, due_at=datetime(2026, 10, 15, 2, 0, tzinfo=UTC))
        assert state.badge == PayoutBadge("Owed now, fell due Oct 14", "ok")
        assert state.export_label == "Owed now, fell due 2026-10-14"
        assert (state.through_stripe, state.owed, state.not_due) == (False, True, False)

    def it_says_owed_now_without_a_day_when_it_has_none():
        assert LinePayout(LineState.OWED).badge == PayoutBadge("Owed now", "ok")

    def it_says_not_yet_due_with_the_day_it_falls_due():
        state = LinePayout(LineState.NOT_DUE, due_at=datetime(2026, 11, 5, 18, 0, tzinfo=UTC))
        assert state.badge == PayoutBadge("Not yet due, falls due Nov 5", "warn")
        assert state.export_label == "Not yet due, falls due 2026-11-05"
        assert (state.through_stripe, state.owed, state.not_due) == (False, False, True)

    def it_says_not_yet_due_for_a_class_with_no_date():
        assert LinePayout(LineState.NOT_DUE).badge == PayoutBadge("Not yet due, no class date yet", "warn")

    def describe_for_window():
        start = datetime(2026, 10, 1, 7, tzinfo=UTC)
        end = datetime(2026, 11, 1, 7, tzinfo=UTC)

        def it_keeps_owed_now_for_a_share_that_fell_due_inside():
            state = LinePayout(LineState.OWED, due_at=datetime(2026, 10, 20, tzinfo=UTC))
            assert state.for_window(start, end) == state

        def it_says_a_share_that_fell_due_after_the_window_counts_there():
            state = LinePayout(LineState.OWED, due_at=datetime(2026, 11, 3, 18, tzinfo=UTC)).for_window(start, end)
            assert state.state == LineState.DUE_LATER
            assert state.badge == PayoutBadge("Fell due Nov 3, after these dates", "warn")
            assert (state.through_stripe, state.owed, state.not_due) == (False, False, False)

        def it_leaves_every_other_state_alone():
            for line_state in (LineState.SENT, LineState.RETRYING, LineState.NOT_DUE):
                state = LinePayout(line_state, due_at=datetime(2026, 11, 3, 18, tzinfo=UTC))
                assert state.for_window(start, end) == state


def describe_producer_rows():
    def it_badges_each_instructor_by_their_payout_account():
        _turn_on()
        on = _producer("Alex Eason")
        paused = _producer("Paula Paused", status=PayoutAccount.Status.PAUSED)
        signing = _producer("Nina Needsinfo", status=PayoutAccount.Status.NEEDS_INFO, active_since=None)
        none = _producer("Hank Byhand", status=None)
        for member in (on, paused, signing, none):
            _registration(member)
        result = build_reconciliation(window=WINDOW)
        labels = {
            m.pk: _row(result, RecipientKind.INSTRUCTOR, m).payout_badge.label for m in (on, paused, signing, none)
        }
        assert labels == {
            on.pk: "Automatic payouts on since Oct 1, 2026",
            paused.pk: "Paused by Stripe",
            signing.pk: "Stripe signup not finished",
            none.pk: "Paid by hand",
        }

    def it_says_paid_by_hand_for_an_active_account_while_payouts_are_switched_off():
        _turn_on()
        BillingSettings.objects.filter(pk=1).update(connect_enabled=False)
        on = _producer("Alex Eason")
        reg = _registration(on)
        result = build_reconciliation(window=WINDOW)
        row = _row(result, RecipientKind.INSTRUCTOR, on)
        assert row.payout_badge == PayoutBadge("Paid by hand", "muted")
        assert _line(result, "class", reg.pk).payout.state == LineState.OWED
        assert (row.stripe_cents, row.owed_now_cents) == (0, 7000)

    def it_says_payouts_switched_off_for_a_row_with_shares_already_through_stripe():
        _turn_on()
        on = _producer("Alex Eason")
        _payout(_registration(on), Payout.Status.SENT, sent_at=datetime(2026, 10, 14, 18, tzinfo=UTC))
        BillingSettings.objects.filter(pk=1).update(connect_enabled=False)
        row = _row(build_reconciliation(window=WINDOW), RecipientKind.INSTRUCTOR, on)
        assert row.payout_badge == PayoutBadge("Payouts switched off", "warn")
        assert (row.stripe_cents, row.owed_now_cents) == (7000, 0)

    def it_counts_only_accounts_in_the_current_stripe_mode():
        # Testing Mode is on by default, so a live mode account is not this payee's account now.
        live_only = _producer("Lena Live", livemode=True)
        _registration(live_only)
        row = _row(build_reconciliation(window=WINDOW), RecipientKind.INSTRUCTOR, live_only)
        assert row.payout_badge == PayoutBadge("Paid by hand", "muted")

    def it_badges_orientor_and_manager_rows_too():
        _turn_on()
        orientor = _producer("Oren Orients")
        manager = _producer("Sami Okafor", status=None)
        _booking(orientor)
        _reservation(manager)
        result = build_reconciliation(window=WINDOW)
        assert _row(result, RecipientKind.ORIENTATOR, orientor).payout_badge.label.startswith("Automatic payouts on")
        assert _row(result, RecipientKind.MANAGER, manager).payout_badge.label == "Paid by hand"

    def it_leaves_guild_and_past_lives_rows_unbadged():
        _booking(_producer("Oren Orients"))  # the booking factory's guild gets the guild share
        result = build_reconciliation(window=WINDOW)
        assert result.groups[RecipientKind.GUILD]
        assert result.groups[RecipientKind.PL]
        for kind in (RecipientKind.GUILD, RecipientKind.PL):
            assert all(alloc.payout_badge is None for alloc in result.groups[kind])


def describe_transaction_lines():
    @pytest.fixture
    def setup() -> dict[str, Any]:
        _turn_on()
        instructor = _producer("Alex Eason")
        regs = {
            "sent": _registration(instructor),
            "taken_back": _registration(instructor),
            "pending": _registration(instructor),
            "not_yet_due": _registration(instructor, class_at=NOW + timedelta(days=5)),
            "failed": _registration(instructor),
            "owed": _registration(instructor),
            "legacy": _registration(instructor, pi=""),
            "no_sessions": _registration(instructor, class_at=None),
        }
        _payout(regs["sent"], Payout.Status.SENT, sent_at=datetime(2026, 10, 14, 18, tzinfo=UTC))
        _payout(regs["taken_back"], Payout.Status.TAKEN_BACK, sent_at=datetime(2026, 10, 13, 18, tzinfo=UTC))
        _payout(regs["pending"], Payout.Status.PENDING)
        _payout(regs["failed"], Payout.Status.FAILED)
        _payout(regs["owed"], Payout.Status.OWED_MANUALLY)
        return {"instructor": instructor, "regs": regs}

    def it_marks_each_line_by_how_its_share_is_paid(setup: dict[str, Any]):
        result = build_reconciliation(window=WINDOW)
        labels = {name: _line(result, "class", reg.pk).payout.badge.label for name, reg in setup["regs"].items()}
        assert labels == {
            "sent": "Sent through Stripe Oct 14",
            "taken_back": "Sent through Stripe Oct 13, refund taken back",
            "pending": "Will be sent through Stripe",
            "not_yet_due": "Will be sent through Stripe",
            "failed": "Stripe is retrying",
            "owed": "Owed now, fell due Oct 19",
            "legacy": "Owed now, fell due Oct 19",
            "no_sessions": "Not yet due, no class date yet",
        }

    def it_agrees_with_the_sent_through_stripe_owed_now_and_not_yet_due_columns(setup: dict[str, Any]):
        result = build_reconciliation(window=WINDOW)
        instructor = setup["instructor"]
        key = ("instructor", instructor.pk)

        def summed(test: Any) -> int:
            return sum(line.shares[key] for line in result.lines if line.payout and test(line.payout))

        row = _row(result, RecipientKind.INSTRUCTOR, instructor)
        columns = (row.stripe_cents, row.owed_now_cents, row.not_due_cents)
        through, owed, not_due = (
            summed(lambda p: p.through_stripe),
            summed(lambda p: p.owed),
            summed(lambda p: p.not_due),
        )
        assert columns == (through, owed, not_due) == (28000, 21000, 7000)

    def it_marks_a_failed_transfer_retrying_and_leaves_the_columns_as_they_were():
        _turn_on()
        instructor = _producer("Alex Eason")
        reg = _registration(instructor)
        _payout(reg, Payout.Status.FAILED)
        result = build_reconciliation(window=WINDOW)
        assert _line(result, "class", reg.pk).payout.state == LineState.RETRYING
        row = _row(result, RecipientKind.INSTRUCTOR, instructor)
        assert (row.total_cents, row.stripe_cents, row.owed_now_cents) == (7000, 0, 7000)

    def it_leaves_an_omitted_line_unbadged():
        reg = _registration(_producer("Alex Eason"))
        TransactionAdjustment.objects.create(source_kind="class", source_pk=reg.pk, is_omitted=True)
        assert _line(build_reconciliation(window=WINDOW), "class", reg.pk).payout is None

    def it_leaves_a_line_with_no_producer_share_unbadged():
        reg = _registration(_producer("Alex Eason"))
        TransactionAdjustment.objects.create(
            source_kind="class", source_pk=reg.pk, override_percents={"instructor": "0", "guild": "30", "pl": "70"}
        )
        assert _line(build_reconciliation(window=WINDOW), "class", reg.pk).payout is None

    def it_badges_orientation_and_reservation_lines():
        _turn_on()
        booking = _booking(_producer("Oren Orients"))
        reservation = _reservation(_producer("Sami Okafor", status=None))
        result = build_reconciliation(window=WINDOW)
        assert _line(result, "orientation", booking.pk).payout.state == LineState.WILL_SEND
        assert _line(result, "reservation", reservation.pk).payout.state == LineState.OWED


def describe_snapshots():
    def it_freezes_the_split_but_no_payout_state_into_a_snapshot():
        _turn_on()
        _registration(_producer("Alex Eason"))
        snapshot = ReconciliationSnapshot.take(period_start=WINDOW.start, period_end=WINDOW.end, title="Oct")
        rows = [row for group in snapshot.results["groups"] for row in group["rows"]]
        assert rows
        assert all(
            set(row)
            == {
                "recipient_id",
                "label",
                "total_cents",
                "transaction_count",
                "voting_cents",
                "stripe_cents",
                "owed_now_cents",
                "not_due_cents",
                "next_due_at",
            }
            for row in rows
        )
        rebuilt = result_from_snapshot(snapshot)
        assert all(alloc.payout_badge is None for kind in rebuilt.groups for alloc in rebuilt.groups[kind])


def describe_reconciliation_tab():
    def it_shows_the_row_and_line_badges_and_the_line_above_the_producer_tables(client: Client):
        _turn_on()
        _login_admin(client)
        reg = _registration(_producer("Alex Eason"))
        _payout(reg, Payout.Status.SENT, sent_at=datetime(2026, 10, 14, 18, tzinfo=UTC))
        content = client.get(reverse("billing_admin_reconciliation_table"), WINDOW_QUERY).content.decode()
        assert "data-recon-pay-lead" in content
        assert "Pay by hand only the shares marked Owed now in Transaction Detail." in content
        assert "data-payee-payout-state" in content
        assert "Automatic payouts on since Oct 1, 2026" in content
        assert 'data-line-payout-state="sent"' in content
        assert "Sent through Stripe Oct 14" in content
        assert content.index("data-recon-pay-lead") < content.index("data-payee-payout-state")

    def it_shows_the_frozen_split_without_payout_badges_on_the_snapshot_detail_page(client: Client):
        _turn_on()
        _login_admin(client)
        _registration(_producer("Alex Eason"))
        snapshot = ReconciliationSnapshot.take(period_start=WINDOW.start, period_end=WINDOW.end, title="Oct")
        content = client.get(reverse("billing_reconciliation_snapshot_detail", args=[snapshot.pk])).content.decode()
        assert "data-recon-owed-now" in content
        assert "data-recon-not-due" in content
        for marker in ("data-recon-pay-lead", "data-payee-payout-state", "data-line-payout-state"):
            assert marker not in content

    def it_keeps_the_query_count_flat_as_producers_and_lines_grow(client: Client):
        _turn_on()
        _login_admin(client)
        # One line of every kind, so the baseline runs every stream's queries (an empty stream runs none).
        first = _producer("Alex Eason")
        _payout(_registration(first), Payout.Status.SENT, sent_at=NOW - timedelta(days=2))
        _booking(first)
        _reservation(first)
        url = reverse("billing_admin_reconciliation_table")
        client.get(url, WINDOW_QUERY)  # warm the session and any per process caches
        with CaptureQueriesContext(connection) as one:
            assert client.get(url, WINDOW_QUERY).status_code == 200

        statuses = [PayoutAccount.Status.ACTIVE, PayoutAccount.Status.PAUSED, PayoutAccount.Status.NEEDS_INFO, None]
        for index, status in enumerate(statuses):
            member = _producer(f"Producer {index}", status=status)
            sent = _registration(member)
            _payout(sent, Payout.Status.SENT, sent_at=NOW - timedelta(days=2))
            _registration(member, class_at=NOW + timedelta(days=5))
            _registration(member, pi="")
            _booking(member)
            _reservation(member)
        with CaptureQueriesContext(connection) as many:
            content = client.get(url, WINDOW_QUERY).content.decode()
        assert content.count("data-payee-payout-state") > 10
        assert content.count("data-line-payout-state") > 15
        assert len(many) == len(one)

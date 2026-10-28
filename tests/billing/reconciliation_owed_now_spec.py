"""#985: Owed now counts only shares whose class, orientation or reservation has happened, in the window they fell due."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from billing.models import (
    BillingSettings,
    Payout,
    PayoutAccount,
    ReconciliationSnapshot,
    SnapshotTooEarlyError,
    TransactionAdjustment,
)
from billing.payments_panel import PanelWindow
from billing.payouts import PAYOUT_DELAY, LineState, PayoutBadge, _earnings, _registrations
from billing.reconciliation import (
    SPLIT_BY_DUE_KEY,
    RecipientKind,
    ReconciliationResult,
    build_reconciliation,
    result_from_snapshot,
    stream_reconciliation_csv,
)
from classes.factories import ClassOfferingFactory, ClassSessionFactory, RegistrationFactory
from classes.models import Registration
from membership.models import Equipment, EquipmentReservation, Member, OrientationBooking
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    MemberFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
OCTOBER = PanelWindow(start=date(2026, 10, 1), end=date(2026, 10, 31))
SEPTEMBER = PanelWindow(start=date(2026, 9, 1), end=date(2026, 9, 30))
# NOW is Oct 20, and a snapshot may only freeze dates that are over (#985).
OCT_1_TO_19 = PanelWindow(start=date(2026, 10, 1), end=date(2026, 10, 19))
OCTOBER_QUERY = {"start": "2026-10-01", "end": "2026-10-31"}


@pytest.fixture(autouse=True)
def _frozen_now():
    with patch("django.utils.timezone.now", return_value=NOW):
        yield


@pytest.fixture(autouse=True)
def _split() -> None:
    """The 70/10/20 class split and 70/15/15 for orientations and reservations; payouts stay off, so every share is by hand."""
    bs = BillingSettings.load()
    bs.class_instructor_percent = Decimal("70")
    bs.class_guild_percent = Decimal("10")
    bs.class_pl_percent = Decimal("20")
    bs.save()


def _instructor(name: str = "Test Instructor") -> Member:
    return MemberFactory(full_legal_name=name, preferred_name="")


def _registration(
    instructor: Member, *, paid_at: datetime, class_at: datetime | None, amount: int = 10000
) -> Registration:
    """A registration paid at ``paid_at`` for a class at ``class_at`` (None: a class with no sessions)."""
    offering = ClassOfferingFactory(instructor=instructor)
    if class_at is not None:
        ClassSessionFactory(class_offering=offering, starts_at=class_at)
    reg = RegistrationFactory(class_offering=offering, amount_paid_cents=amount, stripe_payment_id="pi_985")
    Registration.objects.filter(pk=reg.pk).update(confirmed_at=paid_at)
    reg.refresh_from_db()
    return reg


def _booking(orientor: Member, *, paid_at: datetime, slot_at: datetime) -> OrientationBooking:
    slot = OrientationSlotFactory(starts_at=slot_at, ends_at=slot_at + timedelta(hours=1))
    booking = OrientationBookingFactory(
        slot=slot,
        oriented_by=orientor,
        amount_paid_cents=10000,
        stripe_payment_id="pi_985o",
        status=OrientationBooking.Status.CONFIRMED,
    )
    OrientationBooking.objects.filter(pk=booking.pk).update(requested_at=paid_at)
    return booking


def _reservation(manager: Member, *, paid_at: datetime, starts_at: datetime) -> EquipmentReservation:
    equipment = EquipmentFactory(pricing=Equipment.Pricing.HOURLY, hourly_rate_cents=5000, payee=manager)
    reservation = EquipmentReservationFactory(
        equipment=equipment,
        member=MemberFactory(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=2),
        status=EquipmentReservation.Status.CONFIRMED,
        amount_paid_cents=10000,
        stripe_payment_id="pi_985r",
    )
    EquipmentReservation.objects.filter(pk=reservation.pk).update(created_at=paid_at)
    return reservation


def _row(result: ReconciliationResult, kind: RecipientKind, member: Member) -> Any:
    return next(alloc for alloc in result.groups[kind] if alloc.recipient_id == member.pk)


def _split_of(result: ReconciliationResult, member: Member, kind: RecipientKind = RecipientKind.INSTRUCTOR) -> tuple:
    row = _row(result, kind, member)
    return (row.total_cents, row.stripe_cents, row.owed_now_cents, row.not_due_cents)


def _line(result: ReconciliationResult, pk: int, kind: str = "class") -> Any:
    return next(line for line in result.lines if (line.source_kind, line.source_pk) == (kind, pk))


def _csv_rows(result: ReconciliationResult) -> list[dict[str, str]]:
    body = b"".join(stream_reconciliation_csv(result).streaming_content).decode()
    return list(csv.DictReader(io.StringIO(body)))


def _login_admin(client: Client) -> None:
    User.objects.create_superuser(username="treasurer", password="pass", email="treasurer@example.com")
    client.login(username="treasurer", password="pass")


def describe_a_share_whose_class_has_not_happened():
    """AC1, AC2: never in Owed now; Not yet due with the day it falls due, on the row and on the line."""

    @pytest.fixture
    def setup() -> dict[str, Any]:
        instructor = _instructor()
        happened = _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        upcoming = _registration(
            instructor, paid_at=NOW - timedelta(days=5), class_at=datetime(2026, 11, 5, 18, tzinfo=UTC)
        )
        later = _registration(
            instructor, paid_at=NOW - timedelta(days=4), class_at=datetime(2026, 11, 12, 18, tzinfo=UTC)
        )
        return {"instructor": instructor, "happened": happened, "upcoming": upcoming, "later": later}

    def it_counts_only_the_class_that_happened_in_owed_now(setup: dict[str, Any]):
        result = build_reconciliation(window=OCTOBER)
        assert _split_of(result, setup["instructor"]) == (21000, 0, 7000, 14000)

    def it_dates_the_row_by_the_soonest_share_to_fall_due(setup: dict[str, Any]):
        row = _row(build_reconciliation(window=OCTOBER), RecipientKind.INSTRUCTOR, setup["instructor"])
        assert row.next_due_at == datetime(2026, 11, 7, 18, tzinfo=UTC)

    def it_marks_each_line(setup: dict[str, Any]):
        result = build_reconciliation(window=OCTOBER)
        assert _line(result, setup["happened"].pk).payout.badge.label == "Owed now, fell due Oct 19"
        assert _line(result, setup["upcoming"].pk).payout.badge.label == "Not yet due, falls due Nov 7"
        assert _line(result, setup["upcoming"].pk).payout.badge.tone == "warn"
        assert _line(result, setup["happened"].pk).payout.badge.tone == "ok"

    def it_keeps_it_out_of_owed_now_in_the_csv(setup: dict[str, Any]):
        (row,) = (r for r in _csv_rows(build_reconciliation(window=OCTOBER)) if r["Group"] == "Instructors")
        assert (row["Owed now"], row["Not yet due"], row["Next due"]) == ("70.00", "140.00", "2026-11-07")
        assert "Owed manually" not in row

    def it_keeps_it_out_of_owed_now_on_the_page_and_the_print_page(setup: dict[str, Any], client: Client):
        _login_admin(client)
        page = client.get(reverse("billing_admin_reconciliation_table"), OCTOBER_QUERY).content.decode()
        assert '<td class="text-right" data-recon-owed-now>$70.00</td>' in page
        assert "$140.00" in page and "Next due Nov 7" in page
        assert 'data-line-payout-state="not_due"' in page
        printed = client.get(reverse("billing_admin_reconciliation_print"), OCTOBER_QUERY).content.decode()
        assert '<th class="r">Owed now</th><th class="r">Not yet due</th>' in printed
        assert (
            '<td class="r">$70.00</td><td class="r">$140.00<br><span class="muted">next due Nov 7</span></td>'
            in printed
        )
        assert '<th class="r">Owed manually</th>' not in printed

    def it_reads_a_class_with_no_sessions_as_not_yet_due_with_no_date():
        instructor = _instructor()
        reg = _registration(instructor, paid_at=NOW - timedelta(days=5), class_at=None)
        result = build_reconciliation(window=OCTOBER)
        assert _line(result, reg.pk).payout.state == LineState.NOT_DUE
        assert _line(result, reg.pk).payout.badge.label == "Not yet due, no class date yet"
        assert _split_of(result, instructor) == (7000, 0, 0, 7000)
        assert _row(result, RecipientKind.INSTRUCTOR, instructor).next_due_at is None


def describe_the_due_rule():
    """AC3: a share falls due by Earning.due_at, the rule the Stripe payouts follow."""

    def it_uses_earning_due_at_for_every_line():
        instructor = _instructor()
        regs = [
            _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3)),
            _registration(instructor, paid_at=NOW - timedelta(days=1), class_at=NOW - timedelta(days=20)),
            _registration(instructor, paid_at=NOW - timedelta(days=5), class_at=NOW + timedelta(days=4)),
        ]
        result = build_reconciliation(window=OCTOBER)
        earnings = {e.source.pk: e.due_at for e in _earnings(_registrations().filter(pk__in=[r.pk for r in regs]), [])}
        assert {reg.pk: _line(result, reg.pk).payout.due_at for reg in regs} == earnings

    def it_waits_48_hours_after_the_class_starts():
        instructor = _instructor()
        just_due = _registration(instructor, paid_at=NOW - timedelta(days=9), class_at=NOW - PAYOUT_DELAY)
        not_quite = _registration(
            instructor, paid_at=NOW - timedelta(days=9), class_at=NOW - PAYOUT_DELAY + timedelta(minutes=1)
        )
        result = build_reconciliation(window=OCTOBER)
        assert _line(result, just_due.pk).payout.state == LineState.OWED
        assert _line(result, not_quite.pk).payout.state == LineState.NOT_DUE

    def it_waits_48_hours_after_payment_when_paid_after_the_class():
        instructor = _instructor()
        reg = _registration(instructor, paid_at=NOW - timedelta(days=1), class_at=NOW - timedelta(days=20))
        payout = _line(build_reconciliation(window=OCTOBER), reg.pk).payout
        assert (payout.state, payout.due_at) == (LineState.NOT_DUE, NOW - timedelta(days=1) + PAYOUT_DELAY)

    def it_never_moves_a_share_stripe_pays():
        instructor = _instructor()
        reg = _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW + timedelta(days=3))
        Payout.objects.create(
            registration=reg,
            payee=instructor,
            amount_cents=7000,
            due_at=NOW + timedelta(days=5),
            status=Payout.Status.PENDING,
        )
        result = build_reconciliation(window=OCTOBER)
        assert _line(result, reg.pk).payout.state == LineState.STRIPE_NOT_DUE
        assert _split_of(result, instructor) == (7000, 0, 0, 7000)


def describe_owed_now_by_the_window_a_share_falls_due_in():
    """Felix, 2026-10-27: Owed now groups by the month a share falls due; revenue keeps the month it was paid."""

    def it_counts_a_share_paid_last_month_in_the_month_it_fell_due():
        instructor = _instructor()
        reg = _registration(
            instructor, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        october = build_reconciliation(window=OCTOBER)
        assert _split_of(october, instructor) == (0, 0, 7000, 0)
        assert [line.source_pk for line in october.due_earlier_lines] == [reg.pk]
        assert october.grand_total_cents == 0
        assert october.groups[RecipientKind.PL] == []
        september = build_reconciliation(window=SEPTEMBER)
        assert _split_of(september, instructor) == (7000, 0, 0, 0)
        assert _line(september, reg.pk).payout.badge.label == "Owed from Oct 7, after these dates"

    def it_shows_it_as_not_yet_due_in_its_paid_month_before_it_falls_due():
        instructor = _instructor()
        _registration(
            instructor, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 25, 18, tzinfo=UTC)
        )
        assert _split_of(build_reconciliation(window=SEPTEMBER), instructor) == (7000, 0, 0, 7000)
        october = build_reconciliation(window=OCTOBER)
        assert october.due_earlier_lines == []
        assert all(alloc.recipient_id != instructor.pk for alloc in october.groups[RecipientKind.INSTRUCTOR])

    def it_adds_a_share_paid_earlier_to_a_producer_also_paid_this_month():
        instructor = _instructor()
        _registration(
            instructor, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        assert _split_of(build_reconciliation(window=OCTOBER), instructor) == (7000, 0, 14000, 0)

    def it_leaves_out_a_share_paid_earlier_that_stripe_sent():
        instructor = _instructor()
        reg = _registration(
            instructor, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        Payout.objects.create(
            registration=reg,
            payee=instructor,
            amount_cents=7000,
            due_at=datetime(2026, 10, 7, 18, tzinfo=UTC),
            status=Payout.Status.SENT,
            sent_at=datetime(2026, 10, 7, 19, tzinfo=UTC),
        )
        result = build_reconciliation(window=OCTOBER)
        assert result.due_earlier_lines == []
        assert all(alloc.recipient_id != instructor.pk for alloc in result.groups[RecipientKind.INSTRUCTOR])

    def it_leaves_out_an_omitted_share_paid_earlier():
        reg = _registration(
            _instructor(), paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        TransactionAdjustment.objects.create(source_kind="class", source_pk=reg.pk, is_omitted=True)
        assert build_reconciliation(window=OCTOBER).due_earlier_lines == []

    def it_counts_a_share_paid_earlier_in_the_month_it_fell_due_only():
        _registration(
            _instructor(), paid_at=datetime(2026, 8, 20, 18, tzinfo=UTC), class_at=datetime(2026, 9, 5, 18, tzinfo=UTC)
        )
        assert build_reconciliation(window=OCTOBER).due_earlier_lines == []

    def it_counts_orientations_and_reservations_paid_earlier_too():
        orientor = _instructor("Test Orientor")
        manager = _instructor("Test Manager")
        _booking(orientor, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), slot_at=datetime(2026, 10, 5, 18, tzinfo=UTC))
        _reservation(
            manager, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), starts_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        result = build_reconciliation(window=OCTOBER)
        assert _row(result, RecipientKind.ORIENTATOR, orientor).owed_now_cents == 7000
        assert _row(result, RecipientKind.MANAGER, manager).owed_now_cents == 7000
        assert {line.source_kind for line in result.due_earlier_lines} == {"orientation", "reservation"}

    def it_does_not_count_twice_what_a_snapshot_from_before_this_change_already_owed():
        instructor = _instructor()
        _registration(
            instructor, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        snapshot = ReconciliationSnapshot.take(period_start=SEPTEMBER.start, period_end=SEPTEMBER.end, title="Sep")
        assert build_reconciliation(window=OCTOBER).due_earlier_lines != []
        results = dict(snapshot.results)
        del results[SPLIT_BY_DUE_KEY]
        ReconciliationSnapshot.objects.filter(pk=snapshot.pk).update(results=results)
        assert build_reconciliation(window=OCTOBER).due_earlier_lines == []

    def it_lists_the_earlier_payments_under_transaction_detail(client: Client):
        _login_admin(client)
        _registration(
            _instructor(), paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        page = client.get(reverse("billing_admin_reconciliation_table"), OCTOBER_QUERY).content.decode()
        assert "data-recon-due-earlier" in page
        assert page.index("Transaction Detail") < page.index("data-recon-due-earlier")
        section = page[page.index("data-recon-due-earlier") :]
        assert 'data-line-payout-state="by_hand"' in section
        assert "Owed now, fell due Oct 7" in section

    def it_omits_the_section_when_nothing_paid_earlier_fell_due(client: Client):
        _login_admin(client)
        _registration(_instructor(), paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        page = client.get(reverse("billing_admin_reconciliation_table"), OCTOBER_QUERY).content.decode()
        assert "data-recon-due-earlier" not in page

    def it_keeps_the_query_count_flat_as_earlier_payments_grow(client: Client):
        _login_admin(client)
        first = _instructor()
        _registration(first, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        _registration(
            first, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), class_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        _booking(first, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), slot_at=datetime(2026, 10, 5, 18, tzinfo=UTC))
        _reservation(
            first, paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC), starts_at=datetime(2026, 10, 5, 18, tzinfo=UTC)
        )
        url = reverse("billing_admin_reconciliation_table")
        client.get(url, OCTOBER_QUERY)
        with CaptureQueriesContext(connection) as one:
            client.get(url, OCTOBER_QUERY)
        for index in range(4):
            member = _instructor(f"Producer {index}")
            paid = datetime(2026, 9, 10 + index, 18, tzinfo=UTC)
            _registration(member, paid_at=paid, class_at=datetime(2026, 10, 5, 18, tzinfo=UTC))
            _booking(member, paid_at=paid, slot_at=datetime(2026, 10, 6, 18, tzinfo=UTC))
            _reservation(member, paid_at=paid, starts_at=datetime(2026, 10, 7, 18, tzinfo=UTC))
        with CaptureQueriesContext(connection) as many:
            content = client.get(url, OCTOBER_QUERY).content.decode()
        assert content.count('data-line-payout-state="by_hand"') == 16
        assert len(many) == len(one)


def describe_snapshots():
    def it_freezes_the_split_as_it_stood():
        instructor = _instructor()
        _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        _registration(instructor, paid_at=NOW - timedelta(days=5), class_at=datetime(2026, 11, 5, 18, tzinfo=UTC))
        snapshot = ReconciliationSnapshot.take(period_start=OCT_1_TO_19.start, period_end=OCT_1_TO_19.end, title="Oct")
        assert snapshot.results[SPLIT_BY_DUE_KEY] is True
        frozen = result_from_snapshot(snapshot)
        assert _split_of(frozen, instructor) == (14000, 0, 7000, 7000)
        assert _row(frozen, RecipientKind.INSTRUCTOR, instructor).next_due_at == datetime(2026, 11, 7, 18, tzinfo=UTC)
        with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 20, 18, tzinfo=UTC)):
            assert _split_of(result_from_snapshot(snapshot), instructor) == (14000, 0, 7000, 7000)

    def it_reads_a_snapshot_from_before_this_change_as_everything_owed():
        instructor = _instructor()
        _registration(instructor, paid_at=NOW - timedelta(days=5), class_at=datetime(2026, 11, 5, 18, tzinfo=UTC))
        snapshot = ReconciliationSnapshot.take(period_start=OCT_1_TO_19.start, period_end=OCT_1_TO_19.end, title="Oct")
        for group in snapshot.results["groups"]:
            for row in group["rows"]:
                for key in ("owed_now_cents", "not_due_cents", "retrying_cents", "next_due_at"):
                    del row[key]
        old = result_from_snapshot(snapshot)
        assert _split_of(old, instructor) == (7000, 0, 7000, 0)
        assert _row(old, RecipientKind.INSTRUCTOR, instructor).next_due_at is None
        assert all(
            alloc.owed_now_cents == 0 for alloc in old.groups[RecipientKind.GUILD] + old.groups[RecipientKind.PL]
        )

    def it_draws_the_frozen_split_on_the_snapshot_page(client: Client):
        _login_admin(client)
        _registration(_instructor(), paid_at=NOW - timedelta(days=5), class_at=datetime(2026, 11, 5, 18, tzinfo=UTC))
        snapshot = ReconciliationSnapshot.take(period_start=OCT_1_TO_19.start, period_end=OCT_1_TO_19.end, title="Oct")
        page = client.get(reverse("billing_reconciliation_snapshot_detail", args=[snapshot.pk])).content.decode()
        assert '<td class="text-right" data-recon-owed-now>$0.00</td>' in page
        assert "Next due Nov 7" in page


def _payouts_on_for(member: Member) -> None:
    """Payouts on since a month ago, and ``member`` connected since then, so their Stripe paid shares go through Stripe."""
    BillingSettings.objects.filter(pk=1).update(connect_enabled=True, payouts_on_since=NOW - timedelta(days=30))
    PayoutAccount.objects.create(
        member=member,
        stripe_account_id=f"acct_{member.pk}",
        livemode=False,
        status=PayoutAccount.Status.ACTIVE,
        active_since=NOW - timedelta(days=30),
    )


def describe_a_share_stripe_will_send():
    """Prod finding: a scheduled share read as money sent. Only a sent or in flight transfer counts as Sent."""

    def it_reads_as_not_yet_due_until_it_falls_due():
        instructor = _instructor()
        _payouts_on_for(instructor)
        reg = _registration(instructor, paid_at=NOW - timedelta(days=5), class_at=NOW + timedelta(days=3))
        result = build_reconciliation(window=OCTOBER)
        assert _line(result, reg.pk).payout.badge.label == "Not yet due, Stripe sends it Oct 25"
        assert _split_of(result, instructor) == (7000, 0, 0, 7000)
        (row,) = (r for r in _csv_rows(result) if r["Group"] == "Instructors")
        assert (row["Sent through Stripe"], row["Not yet due"]) == ("0.00", "70.00")

    def it_reads_a_pending_row_made_before_it_fell_due_as_not_yet_due():
        instructor = _instructor()
        _payouts_on_for(instructor)
        reg = _registration(instructor, paid_at=NOW - timedelta(days=5), class_at=NOW + timedelta(days=3))
        Payout.objects.create(
            registration=reg, payee=instructor, amount_cents=7000, due_at=NOW + timedelta(days=5), status="pending"
        )
        assert _split_of(build_reconciliation(window=OCTOBER), instructor) == (7000, 0, 0, 7000)

    def it_counts_as_sending_through_stripe_once_due():
        instructor = _instructor()
        _payouts_on_for(instructor)
        recorded = _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        Payout.objects.create(
            registration=recorded, payee=instructor, amount_cents=7000, due_at=NOW - timedelta(days=1), status="pending"
        )
        unrecorded = _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        result = build_reconciliation(window=OCTOBER)
        for reg in (recorded, unrecorded):
            assert _line(result, reg.pk).payout.badge == PayoutBadge("Sending through Stripe", "muted")
        assert _split_of(result, instructor) == (14000, 14000, 0, 0)


def describe_a_transfer_stripe_is_retrying():
    """Prod finding: paying a retrying transfer by hand pays it twice. It has its own figure, never Owed now."""

    @pytest.fixture
    def failed() -> Registration:
        instructor = _instructor()
        reg = _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        Payout.objects.create(
            registration=reg,
            payee=instructor,
            amount_cents=7000,
            due_at=NOW - timedelta(days=1),
            status=Payout.Status.FAILED,
            attempted_at=NOW - timedelta(hours=2),
            failure_reason="Test refusal",
        )
        return reg

    def it_counts_in_stripe_retrying_and_never_in_owed_now(failed: Registration):
        result = build_reconciliation(window=OCTOBER)
        instructor = failed.class_offering.instructor
        row = _row(result, RecipientKind.INSTRUCTOR, instructor)
        assert (row.owed_now_cents, row.retrying_cents) == (0, 7000)
        assert result.retrying_total_cents == 7000
        badge = _line(result, failed.pk).payout.badge
        assert badge == PayoutBadge("Transfer failed, Stripe retrying, do not pay by hand", "fail")
        (csv_row,) = (r for r in _csv_rows(result) if r["Group"] == "Instructors")
        assert (csv_row["Owed now"], csv_row["Stripe retrying"]) == ("0.00", "70.00")

    def it_shows_a_red_stripe_retrying_column_on_the_page_and_print_page(failed: Registration, client: Client):
        _login_admin(client)
        page = client.get(reverse("billing_admin_reconciliation_table"), OCTOBER_QUERY).content.decode()
        assert '<th class="text-right">Stripe retrying</th>' in page
        assert '<span class="pl-status-badge pl-status-badge--fail">$70.00</span>' in page
        assert "Do not pay by hand" in page
        assert '<td class="text-right" data-recon-owed-now>$0.00</td>' in page
        printed = client.get(reverse("billing_admin_reconciliation_print"), OCTOBER_QUERY).content.decode()
        assert '<th class="r">Stripe retrying</th>' in printed
        assert "do not pay by hand" in printed

    def it_leaves_the_column_off_when_nothing_is_retrying(client: Client):
        _login_admin(client)
        _registration(_instructor(), paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
        page = client.get(reverse("billing_admin_reconciliation_table"), OCTOBER_QUERY).content.decode()
        assert "Stripe retrying</th>" not in page
        assert "data-recon-retrying" not in page

    def it_is_given_up_and_owed_by_hand_when_the_month_is_snapshotted(failed: Registration):
        snapshot = ReconciliationSnapshot.take(period_start=OCT_1_TO_19.start, period_end=OCT_1_TO_19.end, title="Oct")
        instructor = failed.class_offering.instructor
        frozen = _row(result_from_snapshot(snapshot), RecipientKind.INSTRUCTOR, instructor)
        assert (frozen.owed_now_cents, frozen.retrying_cents) == (7000, 0)
        failed.payout.refresh_from_db()
        assert failed.payout.status == Payout.Status.OWED_MANUALLY


NOV_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)  # 2 AM in Portland: October is over
OCT_31_NOON = datetime(2026, 10, 31, 19, 0, tzinfo=UTC)  # noon in Portland


def _october_snapshot() -> ReconciliationSnapshot:
    with patch("django.utils.timezone.now", return_value=NOV_1):
        return ReconciliationSnapshot.take(period_start=OCTOBER.start, period_end=OCTOBER.end, title="Oct")


def _owed_now(window: PanelWindow, member: Member, at: datetime) -> int:
    with patch("django.utils.timezone.now", return_value=at):
        result = build_reconciliation(window=window)
    rows = [alloc for alloc in result.groups[RecipientKind.INSTRUCTOR] if alloc.recipient_id == member.pk]
    return rows[0].owed_now_cents if rows else 0


def describe_review_round_1():
    """Pay exactly once: no share lost between snapshots, none counted twice."""

    def describe_a_snapshot_before_its_dates_are_over():
        def it_is_refused_with_the_day_it_can_be_taken():
            with (
                patch("django.utils.timezone.now", return_value=OCT_31_NOON),
                pytest.raises(SnapshotTooEarlyError) as exc,
            ):
                ReconciliationSnapshot.take(period_start=OCTOBER.start, period_end=OCTOBER.end)
            assert "can be taken from November 1, 2026" in str(exc.value)
            assert not ReconciliationSnapshot.objects.exists()

        def it_is_taken_once_the_last_day_is_over_with_the_evening_share_owed():
            instructor = _instructor()
            _registration(
                instructor,
                paid_at=datetime(2026, 10, 20, 18, tzinfo=UTC),
                class_at=datetime(2026, 10, 30, 1, tzinfo=UTC),
            )  # due Oct 31, 6 PM Portland
            snapshot = _october_snapshot()
            assert _split_of(result_from_snapshot(snapshot), instructor) == (7000, 0, 7000, 0)

        def it_shows_the_message_on_the_button_and_takes_nothing(client: Client):
            _login_admin(client)
            url = reverse("billing_reconciliation_snapshot_take") + "?start=2026-10-01&end=2026-10-31"
            response = client.post(url, {"title": "Oct"}, follow=True)
            assert "can be taken from November 1, 2026" in response.content.decode()
            assert not ReconciliationSnapshot.objects.exists()

        def it_refuses_the_command_for_a_month_still_running():
            with pytest.raises(CommandError, match="can be taken from November 1, 2026"):
                call_command("take_reconciliation_snapshot", "--month", "2026-10", stdout=io.StringIO())

        def it_takes_the_just_ended_month_on_the_first_automatic_run():
            with patch("django.utils.timezone.now", return_value=NOV_1):
                call_command("take_reconciliation_snapshot", stdout=io.StringIO())
            snapshot = ReconciliationSnapshot.objects.get()
            assert (snapshot.period_start, snapshot.period_end, snapshot.is_auto) == (OCTOBER.start, OCTOBER.end, True)

    def describe_a_failed_transfer_paid_before_the_dates_it_fell_due_in():
        def it_is_given_up_and_counted_owed_by_that_snapshot():
            instructor = _instructor()
            reg = _registration(
                instructor,
                paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC),
                class_at=datetime(2026, 10, 5, 18, tzinfo=UTC),
            )
            payout = Payout.objects.create(
                registration=reg,
                payee=instructor,
                amount_cents=7000,
                due_at=datetime(2026, 10, 7, 18, tzinfo=UTC),
                status=Payout.Status.FAILED,
                failure_reason="Test refusal",
            )
            snapshot = _october_snapshot()
            payout.refresh_from_db()
            assert (payout.status, payout.owed_since, payout.counted_owed_in) == (
                Payout.Status.OWED_MANUALLY,
                None,
                snapshot,
            )
            assert _row(result_from_snapshot(snapshot), RecipientKind.INSTRUCTOR, instructor).owed_now_cents == 7000

        def it_records_a_row_for_a_share_with_none_yet_and_ties_it_to_the_snapshot():
            instructor = _instructor()
            reg = _registration(
                instructor,
                paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC),
                class_at=datetime(2026, 10, 5, 18, tzinfo=UTC),
            )
            snapshot = _october_snapshot()
            payout = Payout.objects.get(registration=reg)
            assert (payout.status, payout.counted_owed_in) == (Payout.Status.OWED_MANUALLY, snapshot)

        def it_leaves_a_share_stripe_already_sent_alone():
            instructor = _instructor()
            reg = _registration(
                instructor,
                paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC),
                class_at=datetime(2026, 10, 5, 18, tzinfo=UTC),
            )
            payout = Payout.objects.create(
                registration=reg,
                payee=instructor,
                amount_cents=7000,
                due_at=datetime(2026, 10, 7, 18, tzinfo=UTC),
                status=Payout.Status.SENT,
                sent_at=datetime(2026, 10, 7, 19, tzinfo=UTC),
            )
            _october_snapshot()
            payout.refresh_from_db()
            assert (payout.status, payout.counted_owed_in) == (Payout.Status.SENT, None)

    def describe_a_share_given_up_after_its_month_was_snapshotted():
        def it_is_owed_now_in_the_month_it_was_given_up():
            instructor = _instructor()
            reg = _registration(
                instructor,
                paid_at=datetime(2026, 10, 1, 18, tzinfo=UTC),
                class_at=datetime(2026, 10, 28, 18, tzinfo=UTC),
            )
            payout = Payout.objects.create(
                registration=reg,
                payee=instructor,
                amount_cents=7000,
                due_at=datetime(2026, 10, 30, 18, tzinfo=UTC),
                status=Payout.Status.PENDING,
            )
            snapshot = _october_snapshot()  # in flight: Sent through Stripe, not owed
            assert _split_of(result_from_snapshot(snapshot), instructor) == (7000, 7000, 0, 0)
            given_up_at = datetime(2026, 11, 3, 18, tzinfo=UTC)
            with patch("django.utils.timezone.now", return_value=given_up_at):
                payout.give_up("Test give up", lookup=False)
            payout.refresh_from_db()
            assert payout.owed_since == given_up_at
            november = PanelWindow(start=date(2026, 11, 1), end=date(2026, 11, 30))
            assert _owed_now(november, instructor, datetime(2026, 11, 10, 18, tzinfo=UTC)) == 7000
            assert _owed_now(OCTOBER, instructor, datetime(2026, 11, 10, 18, tzinfo=UTC)) == 0

    def describe_a_share_whose_class_moves_after_it_was_counted():
        def it_keeps_the_recorded_due_date():
            instructor = _instructor()
            reg = _registration(instructor, paid_at=NOW - timedelta(days=10), class_at=NOW - timedelta(days=3))
            counted_in = ReconciliationSnapshot.objects.create(
                title="Oct",
                period_start=OCT_1_TO_19.start,
                period_end=OCT_1_TO_19.end,
                results={SPLIT_BY_DUE_KEY: True},
            )
            Payout.objects.create(
                registration=reg,
                payee=instructor,
                amount_cents=7000,
                due_at=NOW - timedelta(days=1),
                status=Payout.Status.OWED_MANUALLY,
                counted_owed_in=counted_in,
            )
            reg.class_offering.sessions.update(
                starts_at=datetime(2026, 11, 20, 18, tzinfo=UTC), ends_at=datetime(2026, 11, 20, 20, tzinfo=UTC)
            )
            line = _line(build_reconciliation(window=OCTOBER), reg.pk)
            assert (line.payout.state, line.payout.due_at) == (LineState.OWED, NOW - timedelta(days=1))

        def it_follows_a_class_moved_later_before_it_happens():
            instructor = _instructor()
            reg = _registration(
                instructor,
                paid_at=datetime(2026, 10, 5, 18, tzinfo=UTC),
                class_at=datetime(2026, 11, 20, 18, tzinfo=UTC),
            )
            _october_snapshot()  # records the share, due Nov 22, as Not yet due
            payout = Payout.objects.get(registration=reg)
            assert (payout.due_at, payout.counted_owed_in) == (datetime(2026, 11, 22, 18, tzinfo=UTC), None)
            # On Nov 10 the class moves to January.
            reg.class_offering.sessions.update(
                starts_at=datetime(2027, 1, 15, 18, tzinfo=UTC), ends_at=datetime(2027, 1, 15, 20, tzinfo=UTC)
            )
            november = PanelWindow(start=date(2026, 11, 1), end=date(2026, 11, 30))
            assert _owed_now(november, instructor, datetime(2026, 11, 25, 18, tzinfo=UTC)) == 0
            with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 25, 18, tzinfo=UTC)):
                line = _line(build_reconciliation(window=OCTOBER), reg.pk)
            assert line.payout.badge.label == "Not yet due, falls due Jan 17"
            january = PanelWindow(start=date(2027, 1, 1), end=date(2027, 1, 31))
            assert _owed_now(january, instructor, datetime(2027, 1, 20, 18, tzinfo=UTC)) == 7000

        def it_is_never_pulled_into_a_later_month_once_a_snapshot_counted_it():
            instructor = _instructor()
            reg = _registration(
                instructor,
                paid_at=datetime(2026, 9, 20, 18, tzinfo=UTC),
                class_at=datetime(2026, 10, 5, 18, tzinfo=UTC),
            )
            snapshot = _october_snapshot()
            payout = Payout.objects.get(registration=reg)
            assert payout.counted_owed_in == snapshot
            # The class moves into November, and the recorded date is edited along with it.
            reg.class_offering.sessions.update(
                starts_at=datetime(2026, 11, 20, 18, tzinfo=UTC), ends_at=datetime(2026, 11, 20, 20, tzinfo=UTC)
            )
            Payout.objects.filter(pk=payout.pk).update(due_at=datetime(2026, 11, 22, 18, tzinfo=UTC))
            november = PanelWindow(start=date(2026, 11, 1), end=date(2026, 11, 30))
            assert _owed_now(november, instructor, datetime(2026, 12, 1, 18, tzinfo=UTC)) == 0
            assert _owed_now(OCTOBER, instructor, datetime(2026, 12, 1, 18, tzinfo=UTC)) == 7000


def describe_a_cubby_share():
    """#979 cubby shares follow every #985 rule: due 48 hours after payment, counted once."""

    @pytest.fixture
    def payment() -> Any:
        from tests.membership.factories import CubbyPaymentFactory

        payee = _instructor("Test Cubby Payee")
        # 6 PM Portland on Oct 31: due 6 PM on Nov 2.
        return CubbyPaymentFactory(payee=payee, amount_cents=2000, paid_at=datetime(2026, 11, 1, 1, tzinfo=UTC))

    def _cubby_row(result: ReconciliationResult, payment: Any) -> Any:
        return _row(result, RecipientKind.CUBBY_PAYEE, payment.payee)

    def it_is_not_yet_due_in_the_month_it_was_paid_and_owed_now_in_the_next(payment: Any):
        with patch("django.utils.timezone.now", return_value=NOV_1):
            october = build_reconciliation(window=OCTOBER)
        row = _cubby_row(october, payment)
        share = row.total_cents
        assert share > 0
        assert (row.owed_now_cents, row.not_due_cents, row.next_due_at) == (
            0,
            share,
            datetime(2026, 11, 3, 1, tzinfo=UTC),
        )
        assert _line(october, payment.pk, "cubby").payout.badge.label == "Not yet due, falls due Nov 2"

        november = PanelWindow(start=date(2026, 11, 1), end=date(2026, 11, 30))
        with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 5, 18, tzinfo=UTC)):
            later = build_reconciliation(window=november)
        row = _cubby_row(later, payment)
        assert (row.total_cents, row.owed_now_cents, row.not_due_cents) == (0, share, 0)
        assert [line.source_pk for line in later.due_earlier_lines if line.source_kind == "cubby"] == [payment.pk]

    def it_stays_owed_once_in_november_after_october_is_snapshotted(payment: Any):
        snapshot = _october_snapshot()
        frozen = _cubby_row(result_from_snapshot(snapshot), payment)
        assert (frozen.owed_now_cents, frozen.not_due_cents) == (0, frozen.total_cents)
        november = PanelWindow(start=date(2026, 11, 1), end=date(2026, 11, 30))
        with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 5, 18, tzinfo=UTC)):
            assert _cubby_row(build_reconciliation(window=november), payment).owed_now_cents == frozen.total_cents

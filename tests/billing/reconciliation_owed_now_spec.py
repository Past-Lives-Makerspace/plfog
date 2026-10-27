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
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from billing.models import BillingSettings, Payout, ReconciliationSnapshot, TransactionAdjustment
from billing.payments_panel import PanelWindow
from billing.payouts import PAYOUT_DELAY, LineState, _earnings, _registrations
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
        assert "Owed manually" not in printed

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
        assert _line(result, reg.pk).payout.state == LineState.WILL_SEND
        assert _split_of(result, instructor) == (7000, 7000, 0, 0)


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
        assert _line(september, reg.pk).payout.badge.label == "Fell due Oct 7, after these dates"

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
        snapshot = ReconciliationSnapshot.take(period_start=OCTOBER.start, period_end=OCTOBER.end, title="Oct")
        assert snapshot.results[SPLIT_BY_DUE_KEY] is True
        frozen = result_from_snapshot(snapshot)
        assert _split_of(frozen, instructor) == (14000, 0, 7000, 7000)
        assert _row(frozen, RecipientKind.INSTRUCTOR, instructor).next_due_at == datetime(2026, 11, 7, 18, tzinfo=UTC)
        with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 20, 18, tzinfo=UTC)):
            assert _split_of(result_from_snapshot(snapshot), instructor) == (14000, 0, 7000, 7000)

    def it_reads_a_snapshot_from_before_this_change_as_everything_owed():
        instructor = _instructor()
        _registration(instructor, paid_at=NOW - timedelta(days=5), class_at=datetime(2026, 11, 5, 18, tzinfo=UTC))
        snapshot = ReconciliationSnapshot.take(period_start=OCTOBER.start, period_end=OCTOBER.end, title="Oct")
        for group in snapshot.results["groups"]:
            for row in group["rows"]:
                for key in ("owed_now_cents", "not_due_cents", "next_due_at"):
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
        snapshot = ReconciliationSnapshot.take(period_start=OCTOBER.start, period_end=OCTOBER.end, title="Oct")
        page = client.get(reverse("billing_reconciliation_snapshot_detail", args=[snapshot.pk])).content.decode()
        assert '<td class="text-right" data-recon-owed-now>$0.00</td>' in page
        assert "Next due Nov 7" in page
        assert "Owed manually" not in page

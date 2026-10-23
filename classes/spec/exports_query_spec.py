"""BDD specs for the cross-class registrations CSV exporter."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from billing.models import BillingSettings, Payout, PayoutAccount, TransactionAdjustment
from billing.payments_panel import PanelWindow
from billing.reconciliation import build_reconciliation
from classes.exports import REGISTRATIONS_CSV_HEADERS, stream_registrations_query_csv
from classes.factories import ClassOfferingFactory, ClassSessionFactory, InstructorFactory, RegistrationFactory
from classes.models import Registration
from membership.models import Member
from tests.membership.factories import MemberFactory

pytestmark = pytest.mark.django_db

PACIFIC = ZoneInfo("America/Los_Angeles")


def _read(response) -> list[list[str]]:
    body = b"".join(response.streaming_content).decode()
    return list(csv.reader(io.StringIO(body)))


def describe_stream_registrations_query_csv():
    def it_writes_the_header_row_first():
        rows = _read(stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations"))
        assert rows[0] == REGISTRATIONS_CSV_HEADERS

    def it_includes_the_order_number_and_class_columns():
        offering = ClassOfferingFactory(title="Welding 101")
        RegistrationFactory(
            class_offering=offering,
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="503-555-0100",
            status=Registration.Status.CONFIRMED,
            amount_paid_cents=2500,
        )
        rows = _read(stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations"))
        data = rows[1]
        assert data[0]  # order number present
        assert data[1] == "Welding 101"
        assert data[5] == "Ada"
        assert data[7] == "ada@example.com"
        assert data[9] == "Confirmed"  # human label
        assert data[11] == "25.00"  # cents → dollars

    def it_only_includes_rows_in_the_given_queryset():
        mine = ClassOfferingFactory(slug="q-mine")
        other = ClassOfferingFactory(slug="q-other")
        RegistrationFactory(class_offering=mine, email="in@example.com")
        RegistrationFactory(class_offering=other, email="out@example.com")
        body = b"".join(
            stream_registrations_query_csv(
                Registration.objects.filter(class_offering=mine), filename_stem="registrations"
            ).streaming_content
        ).decode()
        assert "in@example.com" in body
        assert "out@example.com" not in body

    def it_sets_a_csv_attachment_disposition_with_stem_and_date():
        response = stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations")
        assert response["Content-Type"] == "text/csv"
        disp = response["Content-Disposition"]
        assert "attachment" in disp
        assert "registrations-" in disp
        assert disp.endswith('.csv"')


def _row_for(offering) -> dict[str, str]:
    RegistrationFactory(class_offering=offering)
    rows = _read(stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations"))
    return dict(zip(rows[0], rows[1], strict=True))


def describe_class_date_and_instructor_columns():
    """#904: the treasurer pays instructors by class date, so each row carries both."""

    def it_puts_class_date_and_instructor_right_after_class():
        assert REGISTRATIONS_CSV_HEADERS[1:4] == ["Class", "Class Date", "Instructor"]

    def it_writes_a_one_date_class_as_its_pacific_day():
        offering = ClassOfferingFactory()
        # 5 PM Pacific on Oct 5 is already Oct 6 in UTC; the column must say the 5th.
        ClassSessionFactory(class_offering=offering, starts_at=datetime(2026, 10, 5, 17, tzinfo=PACIFIC))
        ClassSessionFactory(class_offering=offering, starts_at=datetime(2026, 10, 5, 20, tzinfo=PACIFIC))
        assert _row_for(offering)["Class Date"] == "2026-10-05"

    def it_writes_a_several_date_class_as_first_to_last():
        offering = ClassOfferingFactory()
        for day in (19, 5, 12):
            ClassSessionFactory(class_offering=offering, starts_at=datetime(2026, 10, day, 10, tzinfo=PACIFIC))
        assert _row_for(offering)["Class Date"] == "2026-10-05 to 2026-10-19"

    def it_writes_a_flexible_class_as_its_window():
        offering = ClassOfferingFactory(
            scheduling_model="flexible", flexible_starts_on=date(2026, 11, 2), flexible_ends_on=date(2026, 12, 1)
        )
        assert _row_for(offering)["Class Date"] == "Nov 2 to Dec 1, 2026"

    def it_writes_a_flexible_class_with_no_window_as_flexible():
        assert _row_for(ClassOfferingFactory(scheduling_model="flexible"))["Class Date"] == "Flexible"

    def it_leaves_the_date_empty_for_a_class_with_no_session():
        assert _row_for(ClassOfferingFactory())["Class Date"] == ""

    def it_names_every_instructor_in_order():
        ada = InstructorFactory(full_legal_name="Ada Lovelace")
        grace = InstructorFactory(full_legal_name="Grace Hopper")
        offering = ClassOfferingFactory(instructors=[grace, ada])
        assert _row_for(offering)["Instructor"] == f"{grace.display_name} and {ada.display_name}"

    def it_neutralises_an_instructor_name_that_reads_as_a_formula():
        offering = ClassOfferingFactory(instructor=InstructorFactory(preferred_name="=HYPERLINK(1)"))
        assert _row_for(offering)["Instructor"].startswith("'=")

    def it_costs_the_same_queries_for_one_class_or_many():
        def count() -> int:
            response = stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations")
            with CaptureQueriesContext(connection) as ctx:
                b"".join(response.streaming_content)
            return len(ctx.captured_queries)

        RegistrationFactory(class_offering=ClassOfferingFactory())
        count()  # the first export creates the BillingSettings row the payout lookup reads (#910)
        one = count()
        for _ in range(4):
            offering = ClassOfferingFactory()
            ClassSessionFactory(class_offering=offering)
            RegistrationFactory(class_offering=offering)
        assert count() == one


def _payouts_on() -> None:
    bs = BillingSettings.load()
    bs.connect_enabled = True
    bs.class_instructor_percent = Decimal("70")
    bs.class_guild_percent = Decimal("10")
    bs.class_pl_percent = Decimal("20")
    bs.save()
    BillingSettings.objects.filter(pk=1).update(payouts_on_since=timezone.now() - timedelta(days=30))


def _instructor(*, connected: bool = True) -> Member:
    member = MemberFactory()
    if connected:
        PayoutAccount.objects.create(
            member=member,
            stripe_account_id=f"acct_{member.pk}",
            livemode=False,
            status=PayoutAccount.Status.ACTIVE,
            active_since=timezone.now() - timedelta(days=20),
        )
    return member


def _paid(
    instructor: Member | None,
    *,
    class_in_days: int = -3,
    pi: str = "pi_123",
    amount: int = 10000,
    confirmed: bool = True,
) -> Registration:
    """A registration paid ten days ago for a class ``class_in_days`` from now."""
    offering = ClassOfferingFactory(instructor=instructor)
    ClassSessionFactory(class_offering=offering, starts_at=timezone.now() + timedelta(days=class_in_days))
    reg = RegistrationFactory(class_offering=offering, amount_paid_cents=amount, stripe_payment_id=pi)
    Registration.objects.filter(pk=reg.pk).update(
        confirmed_at=timezone.now() - timedelta(days=10) if confirmed else None
    )
    return reg


def _payout(reg: Registration, status: str, *, sent_at: datetime | None = None) -> None:
    Payout.objects.create(
        registration=reg,
        payee=reg.class_offering.instructor,
        amount_cents=7000,
        due_at=timezone.now() - timedelta(days=1),
        status=status,
        sent_at=sent_at,
    )


def _payout_cells() -> dict[int, str]:
    """Registration pk to Payout cell, read back through the order number."""
    rows = _read(stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations"))
    by_order = {reg.order_number: reg.pk for reg in Registration.objects.all()}
    payout_at = rows[0].index("Payout")
    return {by_order[row[0]]: row[payout_at] for row in rows[1:]}


def _mixed_states() -> dict[str, Registration]:
    """One registration in every payout state, and every empty case, for one connected instructor."""
    _payouts_on()
    alex = _instructor()
    regs = {
        "sent": _paid(alex),
        "taken_back": _paid(alex),
        "pending": _paid(alex),
        "not_yet_due": _paid(alex, class_in_days=5),
        "failed": _paid(alex),
        "owed": _paid(alex),
        "legacy": _paid(alex, pi=""),
        "free": _paid(alex, amount=0),
        "unconfirmed": _paid(alex, confirmed=False),
        "no_instructor": _paid(None),
        "omitted": _paid(alex),
    }
    # 2am UTC on Oct 22 is still Oct 21 in Portland.
    _payout(regs["sent"], Payout.Status.SENT, sent_at=datetime(2026, 10, 22, 2, 0, tzinfo=UTC))
    _payout(regs["taken_back"], Payout.Status.TAKEN_BACK, sent_at=datetime(2026, 10, 13, 18, 0, tzinfo=UTC))
    _payout(regs["pending"], Payout.Status.PENDING)
    _payout(regs["failed"], Payout.Status.FAILED)
    _payout(regs["owed"], Payout.Status.OWED_MANUALLY)
    TransactionAdjustment.objects.create(source_kind="class", source_pk=regs["omitted"].pk, is_omitted=True)
    return regs


def describe_payout_column():
    """#910: the treasurer pays by hand only what Stripe will not, so each row says which."""

    def it_puts_payout_right_after_instructor():
        assert REGISTRATIONS_CSV_HEADERS[3:5] == ["Instructor", "Payout"]

    def it_writes_every_state_as_reconciliation_names_it_with_iso_pacific_dates():
        regs = _mixed_states()
        cells = _payout_cells()
        assert {name: cells[reg.pk] for name, reg in regs.items()} == {
            "sent": "Sent through Stripe 2026-10-21",
            "taken_back": "Sent through Stripe 2026-10-13, refund taken back",
            "pending": "Will be sent through Stripe",
            "not_yet_due": "Will be sent through Stripe",
            "failed": "Stripe is retrying",
            "owed": "Pay by hand",
            "legacy": "Pay by hand",
            "free": "",
            "unconfirmed": "",
            "no_instructor": "",
            "omitted": "",
        }

    def it_agrees_with_reconciliation_for_every_registration():
        _mixed_states()
        today = timezone.localdate()
        result = build_reconciliation(window=PanelWindow(start=today - timedelta(days=40), end=today))
        shown = {
            line.source_pk: "" if line.payout is None else line.payout.export_label
            for line in result.lines
            if line.source_kind == "class"
        }
        cells = _payout_cells()
        assert shown  # the paid, confirmed registrations are all in the window
        assert {pk: cells[pk] for pk in shown} == shown
        assert all(cells[pk] == "" for pk in cells.keys() - shown.keys())

    def it_writes_pay_by_hand_for_a_connected_instructor_while_payouts_are_switched_off():
        _payouts_on()
        reg = _paid(_instructor())
        assert _payout_cells()[reg.pk] == "Will be sent through Stripe"
        BillingSettings.objects.filter(pk=1).update(connect_enabled=False)
        assert _payout_cells()[reg.pk] == "Pay by hand"

    def it_looks_payouts_up_once_per_chunk_of_rows():
        def count() -> int:
            response = stream_registrations_query_csv(Registration.objects.all(), filename_stem="registrations")
            with CaptureQueriesContext(connection) as ctx:
                b"".join(response.streaming_content)
            return len(ctx.captured_queries)

        _payouts_on()
        first = _instructor()
        _payout(_paid(first), Payout.Status.SENT, sent_at=timezone.now() - timedelta(days=2))
        one = count()
        for connected in (True, False, True):
            member = _instructor(connected=connected)
            _payout(_paid(member), Payout.Status.SENT, sent_at=timezone.now() - timedelta(days=2))
            _payout(_paid(member), Payout.Status.FAILED)
            _paid(member, class_in_days=5)
            _paid(member, pi="")
            _paid(member, amount=0)
        assert count() == one

        with patch("classes.exports._CHUNK", 4):
            assert count() == one + 3 * (one - 1)  # four chunks of rows: the payout lookup once per chunk

"""The volunteer roster, the vendor tables, the ledger, the drink sales, and the ledger's vendors with the attendance and
its staff time as CSV, in the streaming shape of ``classes/exports.py``."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.http import StreamingHttpResponse
from django.utils import timezone

from hq.models import BarPay
from hq.services import _plural, ledger_kind_short
from hq.vendor_import import COLUMNS as VENDOR_IMPORT_COLUMNS

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from hq.models import (
        BarSale,
        LedgerEntry,
        ShiftSignup,
        StaffTime,
        VendorTable,
    )
    from hq.services import Attendance, BarTotals, StaffTotals

ROSTER_CSV_HEADERS = [
    "Name",
    "Email",
    "Phone",
    "OLCC permit",
    "Shift",
    "Phase",
    "When",
    "Note",
    "Signed up",
]

_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


def _neutralise_formula(cell: str) -> str:
    """Prefix an apostrophe to text a spreadsheet would run as a formula (CSV injection).

    Names and notes are what volunteers typed, so ``=HYPERLINK(...)`` in a note must open
    as text in Excel or Sheets, not as a live formula.
    """
    return f"'{cell}" if cell.startswith(_FORMULA_LEADS) else cell


def stream_roster_csv(signups: QuerySet[ShiftSignup], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream ``signups`` as CSV, one row per person per shift, in page order."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)
    rows = signups.select_related("shift").order_by(
        "shift__phase", "shift__sort_order", "shift__pk", "created_at", "pk"
    )

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(ROSTER_CSV_HEADERS)
        for signup in rows.iterator(chunk_size=500):
            row = [
                signup.name,
                signup.email,
                signup.phone,
                "yes" if signup.has_olcc else "no",
                signup.shift.role,
                signup.shift.get_phase_display(),
                signup.shift.when_text,
                signup.note,
                timezone.localtime(signup.created_at).strftime("%Y-%m-%d %H:%M"),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-volunteers-{stamp}.csv"'
    return response


# The sheet's columns in the sheet's order (hq.vendor_import.COLUMNS, so an export imports back unchanged), then the
# page's own: who entered the request and when.
VENDOR_TABLES_CSV_HEADERS = [*(header for _key, header in VENDOR_IMPORT_COLUMNS), "Added by", "Added"]


def stream_vendor_tables_csv(tables: QuerySet[VendorTable], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream ``tables`` as CSV, one row per table request in list order, in the columns of Kate's sheet."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(VENDOR_TABLES_CSV_HEADERS)
        for table in tables.iterator(chunk_size=500):
            row = [
                table.name,
                table.guild_display,
                table.get_size_display(),
                table.get_status_display(),
                table.sells,
                table.get_setup_display(),
                table.table_no,
                table.email,
                table.phone,
                table.note,
                table.requested_on.isoformat() if table.requested_on is not None else "",
                table.request_source,
                table.discord_handle,
                table.needs,
                table.created_by.staff_label if table.created_by is not None else "",
                timezone.localtime(table.created_at).strftime("%Y-%m-%d %H:%M"),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-vendor-tables-{stamp}.csv"'
    return response


LEDGER_CSV_HEADERS = ["Date", "Type", "Budget line", "Category", "Sponsor or vendor", "Amount", "Note"]


def stream_ledger_csv(entries: QuerySet[LedgerEntry], *, approval: str, filename_stem: str) -> StreamingHttpResponse:
    """Stream ``entries`` as CSV in Lee's columns, oldest first: the amount in dollars, a refund negative, and the Type
    in her short words with the hub's approval ("From $900")."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(LEDGER_CSV_HEADERS)
        for entry in entries.iterator(chunk_size=500):
            # The amount is ours, a number a spreadsheet must read as one (a refund starts with a minus); the names
            # and the note are what organisers typed, so they are neutralised.
            yield writer.writerow(
                [
                    entry.date.isoformat(),
                    ledger_kind_short(entry.kind, approval),
                    _neutralise_formula(entry.line.name),
                    entry.line.get_category_display(),
                    _neutralise_formula(entry.source),
                    f"{entry.signed_cents / 100:.2f}",
                    _neutralise_formula(entry.note),
                ]
            )

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-ledger-{stamp}.csv"'
    return response


BAR_SALES_CSV_HEADERS = ["Time", "Drink", "Qty", "Amount", "Paid by", "Note", "Logged as"]


def stream_bar_sales_csv(sales: QuerySet[BarSale], *, totals: BarTotals, filename_stem: str) -> StreamingHttpResponse:
    """Stream the drink sales as CSV in Lee's columns, oldest first, the amount in dollars, then a blank row and her
    totals row: the drinks, the sales, what each way to pay took in, the bar's cost and its profit."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(BAR_SALES_CSV_HEADERS)
        for sale in sales.iterator(chunk_size=500):
            yield writer.writerow(
                [
                    timezone.localtime(sale.created_at).strftime("%Y-%m-%d %H:%M"),
                    sale.get_drink_display(),
                    str(sale.qty),
                    f"{sale.amount_cents / 100:.2f}",
                    sale.get_pay_display(),
                    _neutralise_formula(sale.note),
                    sale.get_source_display(),
                ]
            )
        yield writer.writerow([])
        by_pay = " / ".join(f"{pay.label} {totals.pay_cents(pay) / 100:.2f}" for pay in BarPay)
        yield writer.writerow(
            [
                "Totals",
                "",
                str(totals.count),
                f"{totals.sales_cents / 100:.2f}",
                by_pay,
                f"Bar cost {totals.cost_cents / 100:.2f}",
                f"Profit {totals.profit_cents / 100:.2f}",
            ]
        )

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-drink-sales-{stamp}.csv"'
    return response


PEOPLE_CSV_HEADERS = ["Row", "Count", "Checked in", "At the door"]


def stream_people_csv(*, vendors: int, attendance: Attendance, filename_stem: str) -> StreamingHttpResponse:
    """Stream the ledger's Attendance and Vendors as CSV: the confirmed vendors (read from Vendor Tables), then the
    attendance (everyone, the check-ins and the walk-ins at the door)."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(PEOPLE_CSV_HEADERS)
        yield writer.writerow(["Vendors", _plural(vendors, "vendor", "vendors"), "", ""])
        yield writer.writerow(
            [
                "Attendance",
                str(attendance.total),
                f"{attendance.checked_in} checked in",
                f"{attendance.walk_ins} at the door",
            ]
        )

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-vendors-attendance-{stamp}.csv"'
    return response


STAFF_TIME_CSV_HEADERS = ["Date", "Who", "Hours", "Work area", "What", "Logged by"]


def stream_staff_time_csv(
    entries: QuerySet[StaffTime], *, totals: StaffTotals, filename_stem: str
) -> StreamingHttpResponse:
    """Stream the staff time as CSV in Lee's columns, oldest first, then a blank row and her total row: all the hours,
    and each person's (hers named Lee only; here everyone, most first)."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(STAFF_TIME_CSV_HEADERS)
        for entry in entries.iterator(chunk_size=500):
            row = [
                entry.date.isoformat(),
                entry.who,
                str(entry.hours),
                entry.get_area_display(),
                entry.note,
                entry.created_by.staff_label if entry.created_by is not None else "",
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])
        yield writer.writerow([])
        people = " / ".join(f"{person.name}: {person.hours_display}" for person in totals.people)
        yield writer.writerow(["Total", "", totals.hours_display, _neutralise_formula(people), "", ""])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-staff-time-{stamp}.csv"'
    return response

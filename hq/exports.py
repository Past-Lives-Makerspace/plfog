"""The volunteer roster, the wine donors, the sponsors, the vendor tables, the Market Check-in's reports and hours, the
ledger, the drink sales, and the ledger's vendors with the attendance and its staff time as CSV, in the streaming shape of
``classes/exports.py``."""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from django.http import StreamingHttpResponse
from django.utils import timezone

from hq.models import BarPay
from hq.services import ledger_kind_short

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from hq.models import (
        BarSale,
        LedgerEntry,
        MarketReport,
        ShiftSignup,
        Sponsor,
        StaffTime,
        VendorTable,
        VolunteerHours,
    )
    from hq.services import Attendance, BarTotals, CheckinBand, DonorRow, StaffTotals

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
    "Hours logged",
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


def stream_roster_csv(
    signups: QuerySet[ShiftSignup], *, hours: Mapping[str, Decimal], filename_stem: str
) -> StreamingHttpResponse:
    """Stream ``signups`` as CSV, one row per person per shift, in page order.

    ``hours`` is ``hq.services.hours_by_email``: each row carries the Market Check-in hours of the member whose
    primary email is the sign-up's, 0 for anyone else.
    """
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
                str(hours.get(signup.email, Decimal(0))),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-volunteers-{stamp}.csv"'
    return response


DONORS_CSV_HEADERS = ["Giver", "Type", "Email", "Sponsor listing", "Bottles", "Value", "Thanked", "Logged by", "Notes"]


def stream_donors_csv(donors: Sequence[DonorRow], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream the Wine Donors to Thank table as CSV, one row per giver, the value in dollars and whether they were
    thanked."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(DONORS_CSV_HEADERS)
        for donor in donors:
            row = [
                donor.name,
                donor.giver_type,
                donor.email,
                donor.listing,
                str(donor.bottles),
                f"{donor.value_cents / 100:.2f}",
                "yes" if donor.thanked else "no",
                "; ".join(donor.logged_by),
                "; ".join(donor.notes),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-wine-donors-{stamp}.csv"'
    return response


SPONSORS_CSV_HEADERS = [
    "Organization",
    "Tier",
    "Status",
    "Ask",
    "Committed or received",
    "Who's asking",
    "Contact name",
    "Role",
    "Email",
    "Phone",
    "Next step",
    "Follow up by",
    "Thank-you sent",
    "Why they're a fit",
    "Benefits promised",
    "Notes",
    "Last year (2025)",
    "Added",
]


def stream_sponsors_csv(sponsors: QuerySet[Sponsor], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream ``sponsors`` as CSV, one row per sponsor in pipeline order, the money in dollars."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(SPONSORS_CSV_HEADERS)
        for sponsor in sponsors.iterator(chunk_size=500):
            row = [
                sponsor.organization,
                sponsor.get_tier_display(),
                sponsor.get_status_display(),
                f"{sponsor.ask_cents / 100:.2f}",
                f"{sponsor.amount_cents / 100:.2f}",
                sponsor.owner,
                sponsor.contact_name,
                sponsor.contact_role,
                sponsor.email,
                sponsor.phone,
                sponsor.next_step,
                sponsor.follow_up_by.isoformat() if sponsor.follow_up_by is not None else "",
                "yes" if sponsor.thanked else "no",
                sponsor.fit_note,
                sponsor.benefits,
                sponsor.note,
                sponsor.history,
                timezone.localtime(sponsor.created_at).strftime("%Y-%m-%d %H:%M"),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-sponsors-{stamp}.csv"'
    return response


VENDOR_TABLES_CSV_HEADERS = [
    "Artist or group",
    "Guild",
    "What they sell",
    "Table size",
    "Status",
    "Table #",
    "Setup day",
    "Email",
    "Phone",
    "Needs",
    "Added by",
    "Added",
]


def stream_vendor_tables_csv(tables: QuerySet[VendorTable], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream ``tables`` as CSV, one row per table request in list order."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(VENDOR_TABLES_CSV_HEADERS)
        for table in tables.iterator(chunk_size=500):
            row = [
                table.name,
                table.guild_display,
                table.sells,
                table.get_size_display(),
                table.get_status_display(),
                table.table_no,
                table.get_setup_display(),
                table.email,
                table.phone,
                table.needs,
                table.created_by.display_name if table.created_by is not None else "",
                timezone.localtime(table.created_at).strftime("%Y-%m-%d %H:%M"),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-vendor-tables-{stamp}.csv"'
    return response


MARKET_REPORTS_CSV_HEADERS = ["Name or shop", "Member", "Guild", "Items sold", "Sales", "Experience", "Updated"]


def stream_market_reports_csv(reports: QuerySet[MarketReport], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream ``reports`` as CSV, one row per report by name, the sales in dollars; items blank until reported."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(MARKET_REPORTS_CSV_HEADERS)
        for report in reports.iterator(chunk_size=500):
            row = [
                report.name,
                report.member.display_name,
                report.guild_display,
                "" if report.items_sold is None else str(report.items_sold),
                f"{report.sales_cents / 100:.2f}",
                report.experience,
                timezone.localtime(report.updated_at).strftime("%Y-%m-%d %H:%M"),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-market-reports-{stamp}.csv"'
    return response


VOLUNTEER_HOURS_CSV_HEADERS = ["Member", "Date", "Hours", "Task", "Note", "Logged"]


def stream_volunteer_hours_csv(entries: QuerySet[VolunteerHours], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream ``entries`` as CSV, one row per hours entry, newest date first."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(VOLUNTEER_HOURS_CSV_HEADERS)
        for entry in entries.iterator(chunk_size=500):
            row = [
                entry.member.display_name,
                entry.date.isoformat(),
                str(entry.hours),
                entry.get_task_display(),
                entry.note,
                timezone.localtime(entry.created_at).strftime("%Y-%m-%d %H:%M"),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-volunteer-hours-{stamp}.csv"'
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


PEOPLE_CSV_HEADERS = ["Vendor", "Guild", "Items sold", "Total sales", "Their experience"]


def stream_people_csv(
    reports: QuerySet[MarketReport],
    *,
    vendors: int,
    checkin: CheckinBand,
    attendance: Attendance,
    filename_stem: str,
) -> StreamingHttpResponse:
    """Stream the ledger's Attendance and Vendors as CSV in Lee's columns: every market report by name (read from the
    Market Check-in), then a blank row, her totals row (the confirmed tables from Vendor Tables, the items and the sales)
    and the attendance row (everyone, then the check-ins and the walk-ins at the door)."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(PEOPLE_CSV_HEADERS)
        for report in reports.iterator(chunk_size=500):
            row = [
                report.name,
                report.guild_display,
                "" if report.items_sold is None else str(report.items_sold),
                f"{report.sales_cents / 100:.2f}",
                report.experience,
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])
        yield writer.writerow([])
        yield writer.writerow(
            ["Total", f"{vendors} vendors", str(checkin.items), f"{checkin.sales_cents / 100:.2f}", ""]
        )
        yield writer.writerow(
            [
                "Attendance",
                str(attendance.total),
                f"{attendance.checked_in} checked in",
                f"{attendance.walk_ins} at the door",
                "",
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
                entry.created_by.display_name if entry.created_by is not None else "",
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])
        yield writer.writerow([])
        people = " / ".join(f"{person.name}: {person.hours_display}" for person in totals.people)
        yield writer.writerow(["Total", "", totals.hours_display, _neutralise_formula(people), "", ""])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-staff-time-{stamp}.csv"'
    return response

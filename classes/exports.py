"""Registrations CSV export (the consolidated, filtered registrations list).

Mirrors the streaming-CSV pattern in ``billing/reports.py`` — an ``_Echo``
file-like object feeds ``csv.writer`` row-by-row into a
``StreamingHttpResponse``. The column labels are human-readable (this file is
for class organizers, not engineers).
"""

from __future__ import annotations

import csv
from itertools import batched
from typing import TYPE_CHECKING, Iterator

from django.db.models import Max, Min
from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

    from billing.payouts import LinePayout

    from classes.models import ClassOffering, Registration


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


# Cross-class registrations export carries the order number and class title, then the
# class's date and instructor (#904), which is what the treasurer pays instructors by, and
# whether Stripe pays that instructor share (#910).
REGISTRATIONS_CSV_HEADERS = [
    "Order #",
    "Class",
    "Class Date",
    "Instructor",
    "Payout",
    "First Name",
    "Last Name",
    "Email Address",
    "Registration Date",
    "Payment Status",
    "Phone",
    "Amount Paid",
]


def _class_date(offering: ClassOffering, first: datetime | None, last: datetime | None) -> str:
    """The class's date as a spreadsheet sorts it: "2026-10-05", or "2026-10-05 to 2026-10-19".

    The same span the on screen Class Date column shows, from the class's sessions in Pacific
    time; a flexible class gives its window label ("Flexible" with none, as the screen does),
    and a class with no session gives "".
    """
    if offering.is_flexible:
        return offering.flexible_window_label or "Flexible"
    if first is None or last is None:
        return ""
    first_day = timezone.localtime(first).date().isoformat()
    last_day = timezone.localtime(last).date().isoformat()
    return first_day if first_day == last_day else f"{first_day} to {last_day}"


def stream_registrations_query_csv(
    registrations: QuerySet[Registration], *, filename_stem: str
) -> StreamingHttpResponse:
    """Stream an arbitrary (already filtered/scoped) registration queryset as CSV.

    The Payout cell is what Reconciliation's Transaction Detail shows for the registration
    (``billing.reconciliation.registration_payouts``), looked up once per chunk of rows, and
    empty when no instructor share is owed.
    """
    from billing.reconciliation import registration_payouts

    pseudo = _Echo()
    writer = csv.writer(pseudo)
    registrations = (
        registrations.select_related("class_offering")
        .prefetch_related("class_offering__instructor_rows")
        .annotate(
            class_first_session=Min("class_offering__sessions__starts_at"),
            class_last_session=Max("class_offering__sessions__starts_at"),
        )
        .order_by("-registered_at")
    )

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(REGISTRATIONS_CSV_HEADERS)
        for chunk in batched(registrations.iterator(chunk_size=_CHUNK), _CHUNK):
            payouts = registration_payouts(reg.pk for reg in chunk)
            for reg in chunk:
                yield writer.writerow(_registration_cells(reg, payouts.get(reg.pk)))

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-{stamp}.csv"'
    return response


_CHUNK = 500


def _registration_cells(reg: Registration, payout: LinePayout | None) -> list[str]:
    """One registration's CSV cells; ``payout`` is its instructor share's state, or None for an empty cell.

    ``reg`` carries the export's ``class_first_session`` / ``class_last_session`` annotations.
    """
    return [
        reg.order_number,
        reg.class_offering.title,
        _class_date(reg.class_offering, reg.class_first_session, reg.class_last_session),  # type: ignore[attr-defined]
        _neutralise_formula(reg.class_offering.staff_instructor_names),
        "" if payout is None else payout.export_label,
        reg.first_name,
        reg.last_name,
        reg.email,
        reg.registered_at.date().isoformat(),
        reg.get_status_display(),
        reg.phone,
        f"{reg.amount_paid_cents / 100:.2f}",
    ]


_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _neutralise_formula(cell: str) -> str:
    """Prefix an apostrophe to text a spreadsheet would run as a formula (CSV injection).

    A cell holding what someone typed (an instructor's name) must open as text in Excel or
    Sheets, never as a live formula such as ``=HYPERLINK(...)``.
    """
    return f"'{cell}" if cell.startswith(_FORMULA_LEADS) else cell

"""Registrations CSV export (the consolidated, filtered registrations list).

Mirrors the streaming-CSV pattern in ``billing/reports.py`` — an ``_Echo``
file-like object feeds ``csv.writer`` row-by-row into a
``StreamingHttpResponse``. The column labels are human-readable (this file is
for class organizers, not engineers).
"""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Iterator

from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from classes.models import Registration
    from membership.models import Member


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


# Cross-class registrations export carries the order number and class title.
REGISTRATIONS_CSV_HEADERS = [
    "Order #",
    "Class",
    "First Name",
    "Last Name",
    "Email Address",
    "Registration Date",
    "Payment Status",
    "Phone",
    "Amount Paid",
]


def stream_registrations_query_csv(
    registrations: QuerySet[Registration], *, filename_stem: str
) -> StreamingHttpResponse:
    """Stream an arbitrary (already filtered/scoped) registration queryset as CSV."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)
    registrations = registrations.select_related("class_offering").order_by("-registered_at")

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(REGISTRATIONS_CSV_HEADERS)
        for reg in registrations.iterator(chunk_size=500):
            yield writer.writerow(
                [
                    reg.order_number,
                    reg.class_offering.title,
                    reg.first_name,
                    reg.last_name,
                    reg.email,
                    reg.registered_at.date().isoformat(),
                    reg.get_status_display(),
                    reg.phone,
                    f"{reg.amount_paid_cents / 100:.2f}",
                ]
            )

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-{stamp}.csv"'
    return response


INSTRUCTOR_INQUIRIES_CSV_HEADERS = [
    "Date Reached Out",
    "Name",
    "What They Want to Teach",
    "Website",
    "Socials",
    "Experience Level",
    "Contact",
    "Status",
]


_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _neutralise_formula(cell: str) -> str:
    """Prefix an apostrophe to text a spreadsheet would run as a formula (CSV injection).

    The inquiry columns are what applicants typed, so ``=HYPERLINK(...)`` in a note must
    open as text in Excel or Sheets, not as a live formula.
    """
    return f"'{cell}" if cell.startswith(_FORMULA_LEADS) else cell


def stream_instructor_inquiries_csv(inquiries: QuerySet[Member]) -> StreamingHttpResponse:
    """Stream the filtered Instructor Inquiries (#690) as CSV, one row per member, every column."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(INSTRUCTOR_INQUIRIES_CSV_HEADERS)
        for member in inquiries.iterator(chunk_size=500):
            row = [
                timezone.localtime(member.teaching_applied_at).date().isoformat(),
                member.display_name,
                member.teaching_application_note,
                member.teaching_website,
                member.teaching_socials,
                member.get_teaching_experience_display(),
                member.teaching_contact_summary,
                member.teaching_application_state.value.capitalize(),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="instructor-inquiries-{stamp}.csv"'
    return response

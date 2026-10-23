"""Registrations CSV export (the consolidated, filtered registrations list).

Mirrors the streaming-CSV pattern in ``billing/reports.py`` — an ``_Echo``
file-like object feeds ``csv.writer`` row-by-row into a
``StreamingHttpResponse``. The column labels are human-readable (this file is
for class organizers, not engineers).
"""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Iterator

from django.db.models import Max, Min
from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from django.db.models import QuerySet

    from classes.models import ClassOffering, Registration
    from membership.models import Member


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


# Cross-class registrations export carries the order number and class title, then the
# class's date and instructor (#904), which is what the treasurer pays instructors by.
REGISTRATIONS_CSV_HEADERS = [
    "Order #",
    "Class",
    "Class Date",
    "Instructor",
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
    time; a flexible class gives its window label, and a class with no session gives "".
    """
    if offering.is_flexible:
        return offering.flexible_window_label
    if first is None or last is None:
        return ""
    first_day = timezone.localtime(first).date().isoformat()
    last_day = timezone.localtime(last).date().isoformat()
    return first_day if first_day == last_day else f"{first_day} to {last_day}"


def stream_registrations_query_csv(
    registrations: QuerySet[Registration], *, filename_stem: str
) -> StreamingHttpResponse:
    """Stream an arbitrary (already filtered/scoped) registration queryset as CSV."""
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
        for reg in registrations.iterator(chunk_size=500):
            yield writer.writerow(
                [
                    reg.order_number,
                    reg.class_offering.title,
                    _class_date(reg.class_offering, reg.class_first_session, reg.class_last_session),
                    _neutralise_formula(reg.class_offering.instructor_names),
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
    # The application page's answers (#793), after the original eight so a saved
    # spreadsheet's columns keep their places.
    "Class Title",
    "Format",
    "Shop or Area",
    "Years Practicing",
    "Where They Have Taught",
    "Certifications or Safety Training",
    "Equipment or Space Needed",
    "Class Size",
    "When They Can Teach",
    "Anything Else",
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
                member.teaching_class_title,
                member.get_teaching_class_format_display(),
                member.teaching_shop_area,
                member.get_teaching_practice_years_display(),
                member.teaching_taught_where,
                member.teaching_certifications,
                member.teaching_space_needs,
                member.get_teaching_class_size_display(),
                member.teaching_availability_display,
                member.teaching_anything_else,
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="instructor-inquiries-{stamp}.csv"'
    return response

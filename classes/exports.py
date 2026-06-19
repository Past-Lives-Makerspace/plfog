"""Per-class registration CSV export.

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
    from classes.models import ClassOffering


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


CSV_HEADERS = [
    "First Name",
    "Last Name",
    "Email Address",
    "Registration Date",
    "Payment Status",
    "Phone",
    "Amount Paid",
]


def stream_registrations_csv(offering: ClassOffering) -> StreamingHttpResponse:
    """Stream every registration for one class as a CSV download."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)
    registrations = offering.registrations.order_by("-registered_at")

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(CSV_HEADERS)
        for reg in registrations.iterator(chunk_size=500):
            yield writer.writerow(
                [
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
    response["Content-Disposition"] = f'attachment; filename="participants-{offering.slug}-{stamp}.csv"'
    return response

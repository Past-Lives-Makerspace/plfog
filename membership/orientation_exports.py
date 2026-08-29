"""CSV export for the orientations dashboard (mirrors ``classes/exports.py``)."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from membership.models import OrientationBooking

ORIENTATIONS_CSV_HEADERS = [
    "Member",
    "Guild",
    "Orienter",
    "When",
    "Status",
    "Completed",
    "Oriented by",
    "Requested at",
    "Paid",
    "Refund status",
]


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


def stream_orientations_csv(bookings: QuerySet[OrientationBooking]) -> StreamingHttpResponse:
    """Stream an (already filtered) orientation-booking queryset as a CSV download."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)
    bookings = (
        bookings.select_related("slot", "slot__orienter", "guild", "member", "oriented_by")
        .prefetch_related("refunds")
        .order_by("-slot__starts_at")
    )

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(ORIENTATIONS_CSV_HEADERS)
        for booking in bookings.iterator(chunk_size=500):
            yield writer.writerow(
                [
                    booking.member.display_name,
                    booking.guild.name,
                    booking.slot.orienter.display_name if booking.slot.orienter is not None else "",
                    booking.slot.starts_at.strftime("%Y-%m-%d %H:%M"),
                    booking.get_status_display(),
                    "yes" if booking.is_completed else "no",
                    booking.oriented_by.display_name if booking.oriented_by is not None else "",
                    booking.requested_at.strftime("%Y-%m-%d %H:%M"),
                    f"{booking.amount_paid_cents / 100:.2f}" if booking.amount_paid_cents else "",
                    booking.refund_state if booking.amount_paid_cents else "",
                ]
            )

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="orientations-{stamp}.csv"'
    return response

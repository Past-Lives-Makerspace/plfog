"""The Guest list CSV for one event (#831, part 4): every RSVP row, members and guests alike.

The streaming-CSV pattern of ``classes/exports.py``: an ``_Echo`` file-like object feeds
``csv.writer`` row by row into a ``StreamingHttpResponse``, so a long roster never sits in
memory twice. The column labels are for the people running the door, not engineers.
Anyone can RSVP from the public page, so every text cell goes through ``_neutralise_formula``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.http import StreamingHttpResponse
from django.utils import timezone

from classes.exports import _neutralise_formula

if TYPE_CHECKING:
    from membership.models import CommunityEvent


class _Echo:
    """File-like object whose ``write()`` returns the payload (for StreamingHttpResponse)."""

    def write(self, value: str) -> str:
        return value


EVENT_GUESTS_CSV_HEADERS = [
    "Name",
    "People",
    "From",
    "Email",
    "Phone",
    "Updates",
    "Checked In",
    "Note",
    "Signed Up",
]


def stream_event_guests_csv(event: CommunityEvent) -> StreamingHttpResponse:
    """Stream ``event``'s whole guest list as CSV, in RSVP order, the search box ignored."""
    pseudo = _Echo()
    writer = csv.writer(pseudo)
    rows = event.guest_list()

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(EVENT_GUESTS_CSV_HEADERS)
        for rsvp in rows.iterator(chunk_size=500):
            checked_in = timezone.localtime(rsvp.checked_in_at).strftime("%Y-%m-%d %H:%M") if rsvp.checked_in_at else ""
            row = [
                rsvp.display_name,
                rsvp.party_size,
                rsvp.get_source_display(),
                rsvp.contact_email,
                rsvp.contact_phone,
                "Yes" if rsvp.wants_updates else "",
                checked_in,
                rsvp.note,
                timezone.localtime(rsvp.created_at).strftime("%Y-%m-%d %H:%M"),
            ]
            yield writer.writerow([_neutralise_formula(cell) if isinstance(cell, str) else cell for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="event-{event.pk}-guests-{stamp}.csv"'
    return response

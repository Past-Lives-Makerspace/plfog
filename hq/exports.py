"""The volunteer roster and the wine donors as CSV, in the streaming shape of ``classes/exports.py``."""

from __future__ import annotations

import csv
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING

from django.http import StreamingHttpResponse
from django.utils import timezone

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from hq.models import ShiftSignup
    from hq.services import DonorRow

ROSTER_CSV_HEADERS = ["Name", "Email", "Phone", "OLCC permit", "Shift", "Phase", "When", "Note", "Signed up"]

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


DONORS_CSV_HEADERS = ["Giver", "Type", "Email", "Sponsor listing", "Bottles", "Value", "Logged by", "Notes"]


def stream_donors_csv(donors: Sequence[DonorRow], *, filename_stem: str) -> StreamingHttpResponse:
    """Stream the Wine Donors to Thank table as CSV, one row per giver, the value in dollars."""
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
                "; ".join(donor.logged_by),
                "; ".join(donor.notes),
            ]
            yield writer.writerow([_neutralise_formula(cell) for cell in row])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{filename_stem}-wine-donors-{stamp}.csv"'
    return response

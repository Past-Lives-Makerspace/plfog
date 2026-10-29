"""The staff Rentals tab's CSV (#979): one row per cubby rental in a guild.

:func:`rental_row` is the flat row: member, cubby, unit, amount, start, end, status and source.
It is the shape a later ``rental_to_airtable`` beside ``airtable_sync.config.lease_to_airtable``
would send, so the CSV and any sync can never disagree. Nothing syncs yet.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.http import StreamingHttpResponse
from django.utils import timezone

from membership.models import CubbyRental

if TYPE_CHECKING:
    from datetime import date

    from membership.models import Guild

RENTALS_CSV_HEADERS = ["Member", "Cubby", "Unit", "Amount", "Start", "End", "Status", "Source"]

_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


class _Echo:
    """File-like object whose write() returns the value: the streaming CSV trick."""

    def write(self, value: str) -> str:
        return value


def _neutralise_formula(cell: str) -> str:
    """Prefix an apostrophe to text a spreadsheet would run as a formula (CSV injection).

    Member names and cubby labels are typed by people, so ``=HYPERLINK(...)`` must open as text.
    """
    return f"'{cell}" if cell.startswith(_FORMULA_LEADS) else cell


def _day(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def rental_row(rental: CubbyRental) -> dict[str, str]:
    """One rental as flat strings, keyed by :data:`RENTALS_CSV_HEADERS`: the Airtable seam's row.

    ``rental`` needs its member and its cubby's unit loaded.
    """
    return {
        "Member": rental.member.finance_name,
        "Cubby": rental.cubby.label,
        "Unit": rental.cubby.unit.name,
        "Amount": f"{rental.amount_cents / 100:.2f}",
        "Start": _day(rental.start_date),
        "End": _day(rental.end_date),
        "Status": rental.staff_state_label,
        "Source": rental.get_source_display(),
    }


def guild_rentals(guild: Guild) -> list[CubbyRental]:
    """Every rental the guild's cubbies have had, holds awaiting payment left out, by cubby then newest."""
    return list(
        CubbyRental.objects.filter(cubby__unit__guild=guild)
        .exclude(status=CubbyRental.Status.PENDING_PAYMENT)
        .select_related("member", "cubby__unit")
        .order_by("cubby__label", "-created_at", "-pk")
    )


def stream_rentals_csv(guild: Guild) -> StreamingHttpResponse:
    """Stream ``guild``'s rentals as CSV, one row per rental, whatever the tab's filter."""
    writer = csv.writer(_Echo())
    rentals = guild_rentals(guild)

    def iter_rows() -> Iterator[str]:
        yield writer.writerow(RENTALS_CSV_HEADERS)
        for rental in rentals:
            row = rental_row(rental)
            yield writer.writerow([_neutralise_formula(row[header]) for header in RENTALS_CSV_HEADERS])

    response = StreamingHttpResponse(iter_rows(), content_type="text/csv")
    stamp = timezone.now().strftime("%Y%m%d")
    response["Content-Disposition"] = f'attachment; filename="{guild.slug}-cubby-rentals-{stamp}.csv"'
    return response

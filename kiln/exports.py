"""The Reports tab's CSV (#1234): one Kiln Use Overview, every section in one file.

A ``Section`` column tells the rows apart (Totals, Member, Clay, Glaze), so a spreadsheet can
filter one out. Cubic inches are whole numbers with no separators, so they sum as numbers.
Maker, clay and glaze names are typed by people, so every one goes through
``_neutralise_formula`` (CSV injection).
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from django.http import HttpResponse

from classes.exports import _neutralise_formula

if TYPE_CHECKING:
    from kiln.services import KilnUseReport, KilnUseRow

KILN_USE_CSV_HEADERS = ["Section", "Name", "Count", "Pieces", "Cubic inches"]


def _cubic_inches(value: Decimal) -> str:
    """Whole cubic inches as a plain number: "1536"."""
    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def kiln_use_rows(report: KilnUseReport) -> Iterator[list[str]]:
    """The report as CSV rows under :data:`KILN_USE_CSV_HEADERS`, totals first (zeros for an empty period)."""
    yield ["Totals", "Bisque runs", str(report.bisque_runs), "", ""]
    yield ["Totals", "Glaze runs", str(report.glaze_runs), "", ""]
    yield ["Totals", "Pieces fired", str(report.pieces_fired), "", ""]
    yield ["Totals", "Tickets with a size missing", str(report.unmeasured_tickets), "", ""]
    for row in report.makers:
        yield ["Member", _neutralise_formula(row.name), "", str(row.pieces), _cubic_inches(row.cubic_inches)]
    yield from _use_rows("Clay", report.clays)
    yield from _use_rows("Glaze", report.glazes)


def _use_rows(section: str, rows: tuple[KilnUseRow, ...]) -> Iterator[list[str]]:
    for row in rows:
        yield [section, _neutralise_formula(row.name), "", "", _cubic_inches(row.cubic_inches)]


def kiln_use_csv(report: KilnUseReport) -> HttpResponse:
    """The report as a CSV download named for its period: ``kiln-use-2026-10.csv``."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="kiln-use-{report.file_stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(KILN_USE_CSV_HEADERS)
    writer.writerows(kiln_use_rows(report))
    return response

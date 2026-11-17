"""What the Reports tab and its print page draw (#1234), in the house report standard (FRONTEND.md, Report Pages).

Every figure comes from :func:`kiln.services.kiln_use_period`, the same sum the monthly email
sends: the period's own report and, for the "vs" line on each tile, the period just before it
(the previous month for a whole month; for a From and To range, the same number of days ending
the day before From). Nothing here adds anything up a second way; it only arranges the two
reports into tiles, a bisque and glaze split and ranked lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Literal

from django.utils import timezone

from kiln.services import cubic_inches_label, kiln_use_period

if TYPE_CHECKING:
    from kiln.services import KilnUseReport, KilnUseRow

Direction = Literal["up", "down", "same", "none"]

# A count under this shows the change alone; at or past it the percent is added (the standard's rule).
PERCENT_FROM = 100


def _midnight(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def _whole(value: Decimal | int) -> int:
    """A count or a volume as a whole number, rounded half up like :func:`cubic_inches_label`."""
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def percent(part: Decimal | int, whole: Decimal | int) -> int:
    """``part`` as a whole percent of ``whole``; 0 when ``whole`` is 0."""
    if not whole:
        return 0
    return _whole(Decimal(part) * 100 / Decimal(whole))


def previous_window(report: KilnUseReport) -> tuple[datetime, datetime]:
    """The period a report is compared with: the month before a whole month, else the same
    number of days ending the day before its first day, in Portland time."""
    first = timezone.localdate(report.month_start)
    if report.is_whole_month:
        before = date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
        return _midnight(before), _midnight(first)
    days = (report.last_day - first).days + 1
    return _midnight(first - timedelta(days=days)), _midnight(first)


@dataclass(frozen=True)
class Delta:
    """One tile's change against the previous period: "▲ 2 vs September"."""

    direction: Direction
    change: str  # "▲ 2", "▼ 1", "▲ 2,440 (+13%)"; "" when same or none
    versus: str  # "September", "the previous 15 days"

    @property
    def text(self) -> str:
        """The whole line as words, for a spec or a screen reader."""
        if self.direction == "none":
            return f"Nothing in {self.versus} to compare"
        if self.direction == "same":
            return f"Same as {self.versus}"
        return f"{self.change} vs {self.versus}"


def delta(now: int, before: int, versus: str) -> Delta:
    """Compare two whole numbers. Nothing before is "none", whatever ``now`` is."""
    if not before:
        return Delta("none", "", versus)
    if now == before:
        return Delta("same", "", versus)
    up = now > before
    diff = abs(now - before)
    change = f"{'▲' if up else '▼'} {diff:,}"
    if before >= PERCENT_FROM:
        change += f" ({'+' if up else '−'}{percent(diff, before)}%)"
    return Delta("up" if up else "down", change, versus)


@dataclass(frozen=True)
class Tile:
    """A hero tile: a slot colour, an icon from ``kiln/partials/_icons.html``, the figure and its change."""

    key: str
    slot: int
    icon: str
    label: str
    value: int
    delta: Delta
    unit: str = ""
    note: str = ""

    @property
    def value_label(self) -> str:
        return f"{self.value:,}"


@dataclass(frozen=True)
class SplitPart:
    """One part of the bisque and glaze split bar."""

    name: str
    slot: int
    runs: int
    share: int


@dataclass(frozen=True)
class RankRow:
    """A ranked list row: its bar is ``fill`` percent of the top row's."""

    rank: int
    name: str
    cubic_inches: Decimal
    fill: str
    tip_more: str
    pieces: int | None = None
    share: int | None = None

    @property
    def value_label(self) -> str:
        return cubic_inches_label(self.cubic_inches)


@dataclass(frozen=True)
class RankList:
    """A ranked list card: title, one line under it, the column head and its rows."""

    key: str
    slot: str  # "1", "2" or "accent"
    title: str
    sub: str
    head: str
    rows: tuple[RankRow, ...]
    shows_share: bool


@dataclass(frozen=True)
class KilnReportPage:
    """Everything the Reports tab and its print page draw for one period."""

    report: KilnUseReport
    previous: KilnUseReport
    versus: str
    compared_with: str
    tiles: tuple[Tile, ...]
    split: tuple[SplitPart, ...]
    makers: RankList
    clays: RankList
    glazes: RankList

    @property
    def total_runs(self) -> int:
        return self.report.bisque_runs + self.report.glaze_runs

    @property
    def kiln_use_label(self) -> str:
        return cubic_inches_label(self.report.cubic_inches)


def _fill(value: Decimal, top: Decimal) -> str:
    """The bar's width as a percent of the top row's, one decimal: "42.0"."""
    if not top:
        return "0.0"
    return f"{(value * 100 / top).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}"


def _rank_rows(
    rows: tuple[KilnUseRow, ...], total: Decimal, *, shares: bool, pieces: bool, noun: str
) -> tuple[RankRow, ...]:
    # Largest first, the catch-all rows (Other, Commercial) by their size too; the email keeps them last.
    rows = tuple(sorted(rows, key=lambda row: -row.cubic_inches))
    top = rows[0].cubic_inches if rows else Decimal(0)
    ranked = []
    for index, row in enumerate(rows, start=1):
        share = percent(row.cubic_inches, total) if shares else None
        more = [f"{row.pieces} pieces"] if pieces else []
        more.append(f"{share}% of kiln use" if shares else f"#{index} of {len(rows)} {noun}")
        ranked.append(
            RankRow(
                rank=index,
                name=row.name,
                cubic_inches=row.cubic_inches,
                fill=_fill(row.cubic_inches, top),
                tip_more=" · ".join(more),
                pieces=row.pieces if pieces else None,
                share=share,
            )
        )
    return tuple(ranked)


def _versus(report: KilnUseReport, previous: KilnUseReport) -> tuple[str, str]:
    """How a tile names the earlier period ("September") and how the heading does ("September 2026")."""
    if report.is_whole_month:
        return f"{timezone.localdate(previous.month_start):%B}", previous.month_label
    days = (report.last_day - timezone.localdate(report.month_start)).days + 1
    return f"the previous {days} day{'s' if days != 1 else ''}", previous.dates_label


def kiln_report_page(report: KilnUseReport) -> KilnReportPage:
    """Arrange ``report`` and the period before it (one more :func:`kiln_use_period`) for the page."""
    previous = kiln_use_period(*previous_window(report))
    versus, compared_with = _versus(report, previous)
    unmeasured = report.unmeasured_tickets
    note = f"{unmeasured} ticket{'s' if unmeasured != 1 else ''} had no size" if unmeasured else ""
    tiles = (
        Tile(
            "bisque",
            1,
            "flame",
            "Bisque runs",
            report.bisque_runs,
            delta(report.bisque_runs, previous.bisque_runs, versus),
        ),
        Tile(
            "glaze", 2, "drop", "Glaze runs", report.glaze_runs, delta(report.glaze_runs, previous.glaze_runs, versus)
        ),
        Tile(
            "pieces",
            3,
            "pot",
            "Pieces fired",
            report.pieces_fired,
            delta(report.pieces_fired, previous.pieces_fired, versus),
        ),
        Tile(
            "kiln_use",
            4,
            "cube",
            "Kiln use",
            _whole(report.cubic_inches),
            delta(_whole(report.cubic_inches), _whole(previous.cubic_inches), versus),
            unit="in³",
            note=note,
        ),
    )
    runs = report.bisque_runs + report.glaze_runs
    split = (
        SplitPart("Bisque", 1, report.bisque_runs, percent(report.bisque_runs, runs)),
        SplitPart("Glaze", 2, report.glaze_runs, percent(report.glaze_runs, runs)),
    )
    total = report.cubic_inches
    return KilnReportPage(
        report=report,
        previous=previous,
        versus=versus,
        compared_with=compared_with,
        tiles=tiles,
        split=split,
        makers=RankList(
            "makers",
            "accent",
            "Per Member",
            "Who used the kiln, by cubic inches of their pieces.",
            "Maker",
            _rank_rows(report.makers, total, shares=True, pieces=True, noun="makers"),
            shows_share=True,
        ),
        clays=RankList(
            "clays",
            "1",
            "Clay Use",
            "Cubic inches fired, by clay body.",
            "Clay",
            _rank_rows(report.clays, total, shares=True, pieces=False, noun="clays"),
            shows_share=True,
        ),
        glazes=RankList(
            "glazes",
            "2",
            "Glaze Use",
            "A piece with several glazes counts under each one, so these add up to more than the total.",
            "Glaze",
            _rank_rows(report.glazes, total, shares=False, pieces=False, noun="glazes"),
            shows_share=False,
        ),
    )

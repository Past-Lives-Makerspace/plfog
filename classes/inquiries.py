"""The Instructor Inquiries Board Report (#690): inquiries per month and the success funnel.

Both charts read the date range only, never the status filter: a funnel of only the
pending inquiries would say nothing. The charts are inline SVG drawn from the
:class:`ChartBar` geometry here, so the template does no arithmetic and needs no chart
library.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from django.db.models import Count, Exists, OuterRef
from django.db.models.functions import TruncMonth
from django.utils import timezone

from classes.models import ClassOffering, ClassSession

if TYPE_CHECKING:
    from membership.models import MemberQuerySet

#: The SVG's drawing area, in viewBox units (the template's viewBox is 400 by 300). Bars
#: stand on BASELINE and reach up to PLOT_TOP, leaving room for the title and the labels.
CHART_WIDTH = 400
PLOT_TOP = 80
BASELINE = 250
BAR_GAP = 0.25  # the share of each slot left empty between bars


@dataclass(frozen=True)
class ChartBar:
    """One bar: what it counts, its label (and an optional second line), and where it sits."""

    label: str
    sublabel: str
    count: int
    x: float
    width: float
    y: float
    height: float

    @property
    def center(self) -> float:
        return round(self.x + self.width / 2, 1)

    @property
    def value_y(self) -> float:
        """Where the count sits: just above the bar's top."""
        return round(self.y - 6, 1)


@dataclass(frozen=True)
class BoardReport:
    """The two Board Report charts for one date range."""

    range_label: str
    by_month: list[ChartBar]
    funnel: list[ChartBar]

    @property
    def is_empty(self) -> bool:
        return self.funnel[0].count == 0


def _bars(items: list[tuple[str, str, int]]) -> list[ChartBar]:
    """Lay ``(label, sublabel, count)`` items out as bars scaled to the tallest one."""
    if not items:
        return []
    tallest = max(count for _, _, count in items)
    slot = CHART_WIDTH / len(items)
    width = slot * (1 - BAR_GAP)
    bars = []
    for i, (label, sublabel, count) in enumerate(items):
        height = (BASELINE - PLOT_TOP) * count / tallest if tallest else 0
        bars.append(
            ChartBar(
                label=label,
                sublabel=sublabel,
                count=count,
                x=round(i * slot + (slot - width) / 2, 1),
                width=round(width, 1),
                y=round(BASELINE - height, 1),
                height=round(height, 1),
            )
        )
    return bars


def _months(first: date, last: date) -> list[date]:
    """The first day of every month from ``first``'s month through ``last``'s, in order."""
    months = []
    month = first.replace(day=1)
    while month <= last:
        months.append(month)
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _day(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def _range_label(applied_from: date | None, applied_to: date | None) -> str:
    """The range as the charts caption it, so a downloaded image says what it covers."""
    if applied_from and applied_to:
        return f"{_day(applied_from)} to {_day(applied_to)}"
    if applied_from:
        return f"Since {_day(applied_from)}"
    if applied_to:
        return f"Through {_day(applied_to)}"
    return "All time"


def board_report(
    inquiries: MemberQuerySet, *, applied_from: date | None, applied_to: date | None, now: datetime
) -> BoardReport:
    """Count ``inquiries`` (already narrowed to the date range) per month and down the funnel.

    The months run from the range's first day (or the earliest inquiry) to its last day
    (or today), with empty months drawn as zero. "First class run" means the member is
    the instructor on a class that went live (published, or archived after publishing;
    never a draft, a pending or a cancelled class) with a session that started before
    ``now``.

    Args:
        inquiries: ``Member.objects.teaching_inquiries(...)`` for the range.
        applied_from: The range's first day, or None for no lower bound.
        applied_to: The range's last day, or None for no upper bound.
        now: The moment a session must have started by to count as run.
    """
    from membership.models import Member

    unordered = inquiries.order_by()
    per_month: Counter[date] = Counter(
        {
            timezone.localtime(row["month"]).date(): row["total"]
            for row in unordered.annotate(month=TruncMonth("teaching_applied_at"))
            .values("month")
            .annotate(total=Count("pk"))
        }
    )
    today = timezone.localdate(now)
    first = applied_from or min(per_month, default=today)
    last = applied_to or today
    months = _months(first, last) if first <= last else []
    by_month = _bars(
        [
            (f"{month:%b}", f"{month:%Y}" if i == 0 or month.month == 1 else "", per_month[month])
            for i, month in enumerate(months)
        ]
    )

    ran = ClassSession.objects.filter(
        class_offering__instructor=OuterRef("pk"),
        class_offering__status__in=[ClassOffering.Status.PUBLISHED, ClassOffering.Status.ARCHIVED],
        class_offering__published_at__isnull=False,
        starts_at__lt=now,
    )
    funnel = _bars(
        [
            ("Inquired", "", unordered.count()),
            ("Approved", "", unordered.in_teaching_state(Member.TeachingApplicationState.APPROVED).count()),
            ("First class run", "", unordered.filter(Exists(ran)).count()),
        ]
    )
    return BoardReport(range_label=_range_label(applied_from, applied_to), by_month=by_month, funnel=funnel)

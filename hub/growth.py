"""The admin Growth dashboard (#795): how activity in the Member Portal has grown since launch.

Every number is an aggregate: a count per window, per week or per month, never a row. Weeks
start on Monday and every bucket is a Portland day, through ``TruncWeek`` / ``TruncMonth``
with the current time zone, so a week that crosses a clock change still holds seven local days.

Two imports would inflate the growth numbers, so they are left out of every metric:

- The legacy classes copied from the old class site (``ClassOffering.legacy_cms_id``) and the
  ``class_created`` activity rows the import wrote for them.
- The signups imported from the old class site. Each carries one ``CmsActivity`` row whose
  ``payload`` says ``source: classes.pastlives.space``; that row and its registration are left
  out. They appear only in Class Signups by Source, as the Old Class Site.

Never exclude on the JSON key itself (``.exclude(payload__source=...)``): SQL compares a missing
key as NULL, so that exclude also drops every row without the key, which is nearly all of them.
The marker rows are matched with a filter and removed by primary key instead.

The charts are inline SVG drawn from the geometry built here (like the Instructor Inquiries
Board Report, ``classes/inquiries.py``), so the template does no arithmetic and the page loads
no chart library. Each chart is built twice, at the widths the approved mockup drew on a
desktop and on a phone; ``growth.css`` shows the one that fits.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Literal

from django import forms
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone

from classes.models import ClassOffering, CmsActivity, Registration
from core.models import FeedbackRequest
from membership.models import CommunityEvent, EquipmentReservation, Member, OrientationBooking

if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import QueryDict

#: The day the Member Portal went live; nothing before it is counted.
LAUNCH = date(2026, 3, 27)

#: The ``payload["source"]`` the old class site's signup import stamped on its activity rows.
LEGACY_SIGNUP_SOURCE = "classes.pastlives.space"

Period = Literal["week", "month"]
Mode = Literal["week", "month", "total"]

#: The bar and series colours the downloaded PNG draws (the light theme's); growth.css themes
#: them on the page. The frame's ink, grid and axis colours sit in the chart template.
GOLD = "#c98a12"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")


# ---------------------------------------------------------------------------
# What is counted
# ---------------------------------------------------------------------------


def _legacy_signup_markers() -> QuerySet[CmsActivity]:
    """The activity rows the old class site's signup import wrote (filter only, never exclude)."""
    return CmsActivity.objects.filter(payload__source=LEGACY_SIGNUP_SOURCE)


def _old_site_signup() -> Exists:
    """True for a registration imported from the old class site, for the outer ``Registration``."""
    return Exists(
        _legacy_signup_markers().filter(kind=CmsActivity.Kind.REGISTRATION_CREATED, registration=OuterRef("pk"))
    )


def _class_imports() -> QuerySet[CmsActivity]:
    """The ``class_created`` rows the legacy class import wrote, one per imported class."""
    return CmsActivity.objects.filter(kind=CmsActivity.Kind.CLASS_CREATED, class_offering__legacy_cms_id__gt="")


def class_import_count() -> int:
    """How many class import rows CMS Actions leaves out, for the footnotes."""
    return _class_imports().count()


def portal_signups() -> QuerySet[Registration]:
    """Class signups made in the Member Portal: every registration but the old site's imports."""
    return Registration.objects.filter(~_old_site_signup())


def cms_actions() -> QuerySet[CmsActivity]:
    """Every recorded CMS step except the two imports' rows."""
    return CmsActivity.objects.exclude(pk__in=_legacy_signup_markers().values("pk")).exclude(
        pk__in=_class_imports().values("pk")
    )


def portal_classes() -> QuerySet[ClassOffering]:
    """Classes made in the Member Portal, not copied from the old class site."""
    return ClassOffering.objects.exclude(legacy_cms_id__gt="")


@dataclass(frozen=True)
class Metric:
    """One thing the dashboard counts: its rows and the moment each one happened."""

    key: str
    label: str
    group: str
    rows: Callable[[], QuerySet[Any]]
    field: str


_METRIC_LIST: tuple[Metric, ...] = (
    Metric("new_members", "New Members", "People", lambda: Member.objects.all(), "created_at"),
    Metric(
        "instructor_inquiries",
        "Instructor Inquiries",
        "People",
        lambda: Member.objects.exclude(teaching_applied_at=None),
        "teaching_applied_at",
    ),
    Metric("class_signups", "Class Signups", "Classes and CMS", portal_signups, "registered_at"),
    Metric(
        "class_signups_guest",
        "Class Signups by Guests",
        "Classes and CMS",
        lambda: portal_signups().filter(member__isnull=True),
        "registered_at",
    ),
    Metric(
        "class_signups_member",
        "Class Signups by Members",
        "Classes and CMS",
        lambda: portal_signups().filter(member__isnull=False),
        "registered_at",
    ),
    Metric("classes_created", "Classes Created", "Classes and CMS", portal_classes, "created_at"),
    Metric(
        "classes_published",
        "Classes Published",
        "Classes and CMS",
        lambda: portal_classes().exclude(published_at=None),
        "published_at",
    ),
    Metric("cms_actions", "CMS Actions", "Classes and CMS", cms_actions, "created_at"),
    Metric(
        "reservations",
        "Equipment Reservations",
        "Shop and Spaces",
        lambda: EquipmentReservation.objects.all(),
        "created_at",
    ),
    Metric(
        "orientations",
        "Orientation Bookings",
        "Shop and Spaces",
        lambda: OrientationBooking.objects.all(),
        "requested_at",
    ),
    Metric("events", "Community Events", "Community", lambda: CommunityEvent.objects.all(), "created_at"),
    Metric(
        "feedback_requests",
        "Feedback Requests",
        "Community",
        lambda: FeedbackRequest.objects.all(),
        "created_at",
    ),
)

METRICS: dict[str, Metric] = {metric.key: metric for metric in _METRIC_LIST}

#: The metric picker's choices, grouped as the mockup lists them.
METRIC_CHOICES: list[tuple[str, list[tuple[str, str]]]] = [
    (group, [(m.key, m.label) for m in _METRIC_LIST if m.group == group])
    for group in dict.fromkeys(m.group for m in _METRIC_LIST)
]

#: The five tiles: a metric and the tile's own label.
TILES: tuple[tuple[str, str], ...] = (
    ("new_members", "New Members"),
    ("class_signups", "Class Signups"),
    ("classes_published", "Classes Published"),
    ("cms_actions", "CMS Actions"),
    ("reservations", "Reservations"),
)

#: CMS activity groups, in stacking order, with their series slot (1 to 5).
CMS_GROUPS: tuple[tuple[str, str], ...] = (
    ("class", "Class Lifecycle"),
    ("registration", "Registrations"),
    ("payment", "Payments"),
    ("waitlist", "Waitlist"),
    ("discount", "Discounts"),
)

_PAYMENT_KINDS = frozenset(
    {
        CmsActivity.Kind.REGISTRATION_CONFIRMED,
        CmsActivity.Kind.REGISTRATION_REFUNDED,
        CmsActivity.Kind.REGISTRATION_PARTIAL_REFUND,
        CmsActivity.Kind.REGISTRATION_REFUND_FAILED,
        CmsActivity.Kind.REGISTRATION_MARKED_PAID,
        CmsActivity.Kind.PAYMENT_LINK_SENT,
        CmsActivity.Kind.DUPLICATE_PAYMENT,
    }
)


def cms_group(kind: str) -> str:
    """The CMS Activity by Kind group a ``CmsActivity.kind`` counts under.

    Money moving (a payment confirmed, refunded or marked paid, a payment link) is Payments;
    everything else goes by its prefix.

    Raises:
        ValueError: For a kind no group holds, so a new kind cannot vanish from the chart.
    """
    if kind in _PAYMENT_KINDS:
        return "payment"
    prefix = kind.split("_", 1)[0]
    if prefix not in dict(CMS_GROUPS):
        raise ValueError(f"No CMS group for activity kind '{kind}'")
    return prefix


# ---------------------------------------------------------------------------
# Date ranges and buckets
# ---------------------------------------------------------------------------


def _short(day: date) -> str:
    return f"{day:%b} {day.day}"


def _long(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def _midnight(day: date) -> datetime:
    """The start of ``day`` in the current time zone (Portland)."""
    return datetime.combine(day, time.min, tzinfo=timezone.get_current_timezone())


@dataclass(frozen=True)
class DateRange:
    """An inclusive run of local days."""

    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def previous(self) -> DateRange | None:
        """The same number of days just before this range, or None when that reaches before launch."""
        start = self.start - timedelta(days=self.days)
        if start < LAUNCH:
            return None
        return DateRange(start, self.start - timedelta(days=1))

    def q(self, field: str) -> Q:
        """Rows whose ``field`` falls on one of these days."""
        return Q(**{f"{field}__gte": _midnight(self.start), f"{field}__lt": _midnight(self.end + timedelta(days=1))})

    @property
    def label(self) -> str:
        return f"{_long(self.start)} to {_long(self.end)}"


@dataclass(frozen=True)
class Bucket:
    """One week or month of a range: its key (the Monday, or the 1st) and the days of it in range."""

    key: date
    start: date
    end: date
    value: int

    def label(self, period: Period) -> str:
        """The x axis label: the month's short name, or the week's Monday."""
        return f"{self.key:%b}" if period == "month" else _short(self.key)

    def title(self, period: Period, today: date) -> str:
        """The bucket as a tooltip names it, saying when only part of it is in range."""
        if period == "month":
            name = f"{self.key:%B} {self.key.year}"
            whole = self.start == self.key and (self.end + timedelta(days=1)).day == 1
            return name if whole else f"{name} ({_short(self.start)} to {_short(self.end)})"
        name = f"Week of {_short(self.key)}"
        if (self.end - self.start).days == 6:
            return name
        if self.end == today:
            return f"{name} (so far)"
        return f"{name} ({_short(self.start)} to {_short(self.end)})"


def _bucket_start(day: date, period: Period) -> date:
    return day.replace(day=1) if period == "month" else day - timedelta(days=day.weekday())


def _next_bucket(key: date, period: Period) -> date:
    if period == "month":
        return (key.replace(day=28) + timedelta(days=4)).replace(day=1)
    return key + timedelta(days=7)


def bucket_keys(rng: DateRange, period: Period) -> list[Bucket]:
    """Every week or month the range touches, each clipped to the range, all at zero."""
    out = []
    key = _bucket_start(rng.start, period)
    while key <= rng.end:
        following = _next_bucket(key, period)
        out.append(Bucket(key, max(key, rng.start), min(following - timedelta(days=1), rng.end), 0))
        key = following
    return out


def _trunc(field: str, period: Period) -> TruncWeek | TruncMonth:
    tz = timezone.get_current_timezone()
    return TruncMonth(field, tzinfo=tz) if period == "month" else TruncWeek(field, tzinfo=tz)


def _bucket_date(value: datetime) -> date:
    return timezone.localtime(value).date()


def _fill(rng: DateRange, period: Period, counts: dict[date, int]) -> list[Bucket]:
    return [Bucket(b.key, b.start, b.end, counts.get(b.key, 0)) for b in bucket_keys(rng, period)]


def buckets(metric: Metric, rng: DateRange, period: Period) -> list[Bucket]:
    """The metric counted per week or month across the range, empty buckets at zero. One query."""
    rows = (
        metric.rows()
        .filter(rng.q(metric.field))
        .annotate(bucket=_trunc(metric.field, period))
        .order_by()
        .values("bucket")
        .annotate(total=Count("pk"))
    )
    return _fill(rng, period, {_bucket_date(row["bucket"]): row["total"] for row in rows})


def totals(metric: Metric, ranges: dict[str, DateRange]) -> dict[str, int]:
    """The metric counted over each named range, in one query."""
    return metric.rows().aggregate(**{name: Count("pk", filter=rng.q(metric.field)) for name, rng in ranges.items()})


# ---------------------------------------------------------------------------
# Chart geometry
# ---------------------------------------------------------------------------


def _n(value: float) -> str:
    """A coordinate as the SVG carries it: one decimal at most."""
    rounded = round(value, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _scale(top: int) -> tuple[int, int]:
    """The y axis: a round maximum at or above ``top`` with five ticks at most, and its step."""
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000):
        ticks = math.ceil(top / step)
        if ticks <= 5:
            return max(ticks, 1) * step, step
    return top, top


def _bar_path(x: float, y: float, w: float, h: float, r: float) -> str:
    """A bar whose top corners are rounded and whose foot sits square on the baseline."""
    r = min(r, w / 2, h)
    return (
        f"M{_n(x)} {_n(y + h)}V{_n(y + r)}Q{_n(x)} {_n(y)} {_n(x + r)} {_n(y)}"
        f"H{_n(x + w - r)}Q{_n(x + w)} {_n(y)} {_n(x + w)} {_n(y + r)}V{_n(y + h)}Z"
    )


@dataclass(frozen=True)
class GridLine:
    y: str
    x1: str
    x2: str
    label_x: str
    label_y: str
    label: int
    is_axis: bool


@dataclass(frozen=True)
class Text:
    x: str
    y: str
    text: str


@dataclass(frozen=True)
class Shape:
    """A bar or a stacked segment: an SVG path (``d``) or, inside a stack, a rect."""

    css: str
    fill: str
    d: str = ""
    x: str = ""
    y: str = ""
    width: str = ""
    height: str = ""


@dataclass(frozen=True)
class Hit:
    """The hover target over one bucket; its tooltip, and the running total's hover dot."""

    x: str
    y: str
    width: str
    height: str
    tip: str
    dot_x: str = ""
    dot_y: str = ""


@dataclass(frozen=True)
class Chart:
    """One SVG chart at one size."""

    width: int
    height: int
    title: str
    subtitle: str
    grid: list[GridLine]
    shapes: list[Shape]
    values: list[Text]
    hits: list[Hit]
    x_labels: list[Text]
    line: str = ""
    area: str = ""
    dot: Text | None = None


@dataclass(frozen=True)
class _Plot:
    x: float
    y: float
    w: float
    h: float


def _plot(width: int, height: int) -> _Plot:
    return _Plot(34, 64, width - 40, height - 64 - 30)


def _grid(plot: _Plot, top: int, step: int) -> list[GridLine]:
    lines = []
    for value in range(0, top + 1, step):
        y = plot.y + plot.h - value / top * plot.h
        lines.append(
            GridLine(_n(y), _n(plot.x), _n(plot.x + plot.w), _n(plot.x - 8), _n(y + 4), value, is_axis=value == 0)
        )
    return lines


def _x_labels(items: list[Bucket], period: Period, plot: _Plot, slot: float) -> list[Text]:
    """Every bucket's label when they fit, else every few, always the last."""
    count = len(items)
    every = max(1, math.ceil(count / (plot.w / 64)))
    labels = []
    for i, bucket in enumerate(items):
        last = i == count - 1
        if i % every and not last:
            continue
        if not last and count - 1 - i < every and i % every == 0 and i != 0:
            continue  # too close to the last label, which always shows
        labels.append(Text(_n(plot.x + slot * i + slot / 2), _n(plot.y + plot.h + 18), bucket.label(period)))
    return labels


def _hit(plot: _Plot, slot: float, i: int, tip: str, dot_x: str = "", dot_y: str = "") -> Hit:
    return Hit(_n(plot.x + slot * i), _n(plot.y), _n(slot), _n(plot.h), tip, dot_x, dot_y)


def bar_chart(
    width: int, height: int, title: str, subtitle: str, items: list[Bucket], period: Period, name: str, today: date
) -> Chart:
    """Bars per bucket, the tallest (the latest of equals) and the last labelled with their count."""
    plot = _plot(width, height)
    top, step = _scale(max([b.value for b in items] + [1]))
    slot = plot.w / len(items)
    bar_w = max(3, min(48, slot * 0.62))
    peak = max(range(len(items)), key=lambda i: (items[i].value, i))
    shapes, values, hits = [], [], []
    for i, bucket in enumerate(items):
        h = bucket.value / top * plot.h
        x = plot.x + slot * i + (slot - bar_w) / 2
        y = plot.y + plot.h - h
        if h > 0:
            shapes.append(Shape("pl-growth__bar", GOLD, d=_bar_path(x, y, bar_w, h, 4)))
        if i in (peak, len(items) - 1) and bucket.value > 0:
            values.append(Text(_n(x + bar_w / 2), _n(y - 6), str(bucket.value)))
        hits.append(_hit(plot, slot, i, f"{bucket.title(period, today)}\n{name}: {bucket.value}"))
    return Chart(
        width,
        height,
        title,
        subtitle,
        _grid(plot, top, step),
        shapes,
        values,
        hits,
        _x_labels(items, period, plot, slot),
    )


def running_total_chart(
    width: int, height: int, title: str, subtitle: str, items: list[Bucket], name: str, today: date
) -> Chart:
    """The weekly counts added up from the start of the range, as a line over a soft area."""
    plot = _plot(width, height)
    running, points = 0, []
    for bucket in items:
        running += bucket.value
        points.append(running)
    top, step = _scale(max(running, 1))
    slot = plot.w / len(items)
    xs = [plot.x + slot * i + slot / 2 for i in range(len(items))]
    ys = [plot.y + plot.h - v / top * plot.h for v in points]
    line = "".join(f"{'L' if i else 'M'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(zip(xs, ys, strict=True)))
    area = f"{line}L{xs[-1]:.1f} {_n(plot.y + plot.h)}L{xs[0]:.1f} {_n(plot.y + plot.h)}Z"
    hits = [
        _hit(
            plot,
            slot,
            i,
            f"{bucket.title('week', today)}\n{name}, running total: {points[i]}\nThat week: {bucket.value}",
            _n(xs[i]),
            _n(ys[i]),
        )
        for i, bucket in enumerate(items)
    ]
    return Chart(
        width,
        height,
        title,
        subtitle,
        _grid(plot, top, step),
        shapes=[],
        values=[Text(_n(xs[-1] - 8), _n(ys[-1] - 10), str(points[-1]))],
        hits=hits,
        x_labels=_x_labels(items, "week", plot, slot),
        line=line,
        area=area,
        dot=Text(_n(xs[-1]), _n(ys[-1]), ""),
    )


@dataclass(frozen=True)
class Layer:
    """One stacked series: its name, its colour slot (1 to 5) and a value per month."""

    name: str
    slot: int
    values: list[int]

    @property
    def total(self) -> int:
        return sum(self.values)


def stacked_chart(
    width: int, height: int, title: str, subtitle: str, months: list[Bucket], layers: list[Layer], today: date
) -> Chart:
    """A stacked bar per month: each layer a segment, a 2px gap between, the month's total on top."""
    plot = _plot(width, height)
    column_totals = [sum(layer.values[i] for layer in layers) for i in range(len(months))]
    top, step = _scale(max(column_totals + [1]))
    slot = plot.w / len(months)
    bar_w = min(56, slot * 0.6)
    shapes, values, hits = [], [], []
    for i, month in enumerate(months):
        x = plot.x + slot * i + (slot - bar_w) / 2
        base = plot.y + plot.h
        visible = [layer for layer in layers if layer.values[i] > 0]
        for j, layer in enumerate(visible):
            h = layer.values[i] / top * plot.h
            y = base - h
            segment_h = max(0.0, h - (2 if j > 0 else 0))
            css, fill = f"pl-growth__seg--{layer.slot}", SERIES[layer.slot - 1]
            if j == len(visible) - 1:
                shapes.append(Shape(css, fill, d=_bar_path(x, y, bar_w, segment_h, 4)))
            else:
                shapes.append(Shape(css, fill, x=_n(x), y=_n(y), width=_n(bar_w), height=_n(segment_h)))
            base = y
        if column_totals[i] > 0:
            values.append(Text(_n(x + bar_w / 2), _n(base - 6), str(column_totals[i])))
        rows = "\n".join(f"{layer.name}: {layer.values[i]}" for layer in layers)
        hits.append(_hit(plot, slot, i, f"{month.title('month', today)}\n{rows}"))
    return Chart(
        width,
        height,
        title,
        subtitle,
        _grid(plot, top, step),
        shapes,
        values,
        hits,
        _x_labels(months, "month", plot, slot),
    )


@dataclass(frozen=True)
class Sized:
    """A chart drawn at the mockup's desktop width and again at its phone width."""

    wide: Chart
    narrow: Chart


#: (width, height) per chart, as the approved mockup drew them at 1440px and on a phone.
MAIN_WIDE = (1090, 320)
CMS_WIDE = (620, 300)
SOURCE_WIDE = (396, 300)
NARROW = (316, 260)


def _sparkline(items: list[Bucket]) -> tuple[str, str]:
    """A tile's sparkline in a 100 by 32 box: the line, and the area under it."""
    tallest = max([b.value for b in items] + [1])
    count = len(items)
    points = [(50.0 if count == 1 else i / (count - 1) * 100, 30 - b.value / tallest * 26) for i, b in enumerate(items)]
    line = "".join(f"{'L' if i else 'M'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(points))
    return line, f"{line}L100 32L0 32Z"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    """One stat tile: the range's count, how it compares with the period before, a sparkline."""

    label: str
    value: int
    previous: int | None
    spark_line: str
    spark_area: str

    @property
    def change(self) -> str:
        """``since_launch`` (nothing to compare), ``none``, ``new``, ``up`` or ``down``."""
        if self.previous is None:
            return "since_launch"
        if self.previous == 0:
            return "none" if self.value == 0 else "new"
        return "up" if self.value >= self.previous else "down"

    @property
    def percent(self) -> int:
        """The change against the period before, as a whole percentage (no sign)."""
        if not self.previous:
            return 0
        return abs(round((self.value - self.previous) / self.previous * 100))


@dataclass(frozen=True)
class MainChart:
    metric: Metric
    mode: Mode
    chart: Sized
    total: int
    since_launch: int
    filename: str


@dataclass(frozen=True)
class CmsTableRow:
    name: str
    slot: int
    values: list[int]
    total: int


@dataclass(frozen=True)
class CmsCard:
    chart: Sized
    months: list[str]
    rows: list[CmsTableRow]
    column_totals: list[int]
    total: int


@dataclass(frozen=True)
class SourceCard:
    chart: Sized
    portal: int
    old_site: int
    guests: int
    members: int

    @property
    def total(self) -> int:
        return self.portal + self.old_site


@dataclass(frozen=True)
class Dashboard:
    """Everything the Growth page shows for one range, metric and mode."""

    rng: DateRange
    range_name: str
    tiles: list[Tile]
    main: MainChart
    cms: CmsCard
    source: SourceCard
    class_imports: int


def _tile(metric_key: str, label: str, rng: DateRange) -> Tile:
    metric = METRICS[metric_key]
    weeks = buckets(metric, rng, "week")
    previous = rng.previous
    before = totals(metric, {"before": previous})["before"] if previous else None
    line, area = _sparkline(weeks)
    return Tile(label, sum(b.value for b in weeks), before, line, area)


def _main_chart(metric: Metric, mode: Mode, rng: DateRange, range_name: str, today: date) -> MainChart:
    subtitle = f"{range_name}, {rng.label}"
    period: Period = "month" if mode == "month" else "week"
    items = buckets(metric, rng, period)

    def draw(size: tuple[int, int]) -> Chart:
        if mode == "total":
            return running_total_chart(*size, f"{metric.label}, Running Total", subtitle, items, metric.label, today)
        suffix = " per Week" if mode == "week" else " per Month"
        return bar_chart(*size, f"{metric.label}{suffix}", subtitle, items, period, metric.label, today)

    since = totals(metric, {"since": DateRange(LAUNCH, today)})["since"]
    filename = f"growth-{metric.key.replace('_', '-')}-{rng.start.isoformat()}-to-{rng.end.isoformat()}.png"
    return MainChart(metric, mode, Sized(draw(MAIN_WIDE), draw(NARROW)), sum(b.value for b in items), since, filename)


def _cms_card(rng: DateRange, range_name: str, today: date) -> CmsCard:
    rows = (
        cms_actions()
        .filter(rng.q("created_at"))
        .annotate(month=_trunc("created_at", "month"))
        .order_by()
        .values("month", "kind")
        .annotate(total=Count("pk"))
    )
    counts: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        counts[cms_group(row["kind"])][_bucket_date(row["month"])] += row["total"]
    months = bucket_keys(rng, "month")
    layers = [
        Layer(name, slot, [counts[key][m.key] for m in months]) for slot, (key, name) in enumerate(CMS_GROUPS, start=1)
    ]
    subtitle = f"{range_name}, {rng.label}"
    chart = Sized(
        stacked_chart(*CMS_WIDE, "CMS Activity by Kind", subtitle, months, layers, today),
        stacked_chart(*NARROW, "CMS Activity by Kind", subtitle, months, layers, today),
    )
    column_totals = [sum(layer.values[i] for layer in layers) for i in range(len(months))]
    return CmsCard(
        chart,
        [m.label("month") for m in months],
        [CmsTableRow(layer.name, layer.slot, layer.values, layer.total) for layer in layers],
        column_totals,
        sum(column_totals),
    )


def _source_card(rng: DateRange, range_name: str, today: date) -> SourceCard:
    portal = Q(old_site=False)
    rows = (
        Registration.objects.filter(rng.q("registered_at"))
        .annotate(old_site=_old_site_signup(), month=_trunc("registered_at", "month"))
        .order_by()
        .values("month")
        .annotate(
            portal=Count("pk", filter=portal),
            old=Count("pk", filter=Q(old_site=True)),
            guests=Count("pk", filter=portal & Q(member__isnull=True)),
            members=Count("pk", filter=portal & Q(member__isnull=False)),
        )
    )
    by_month: dict[date, dict[str, Any]] = {_bucket_date(row["month"]): dict(row) for row in rows}
    months = bucket_keys(rng, "month")

    def column(name: str) -> list[int]:
        return [by_month[m.key][name] if m.key in by_month else 0 for m in months]

    layers = [Layer("Member Portal", 1, column("portal")), Layer("Old Class Site", 2, column("old"))]
    chart = Sized(
        stacked_chart(*SOURCE_WIDE, "Class Signups by Source", range_name, months, layers, today),
        stacked_chart(*NARROW, "Class Signups by Source", range_name, months, layers, today),
    )
    return SourceCard(chart, layers[0].total, layers[1].total, sum(column("guests")), sum(column("members")))


def dashboard(rng: DateRange, range_name: str, metric_key: str, mode: Mode, today: date) -> Dashboard:
    """Build the Growth page for one range. Every count is an aggregate query; about fifteen in all."""
    return Dashboard(
        rng=rng,
        range_name=range_name,
        tiles=[_tile(key, label, rng) for key, label in TILES],
        main=_main_chart(METRICS[metric_key], mode, rng, range_name, today),
        cms=_cms_card(rng, range_name, today),
        source=_source_card(rng, range_name, today),
        class_imports=class_import_count(),
    )


# ---------------------------------------------------------------------------
# The date range and chart controls
# ---------------------------------------------------------------------------

PRESETS: tuple[tuple[str, str], ...] = (
    ("30", "Last 30 Days"),
    ("90", "Last 90 Days"),
    ("launch", "Since Launch"),
    ("custom", "Custom"),
)
MODES: tuple[tuple[str, str], ...] = (("week", "Per Week"), ("month", "Per Month"), ("total", "Running Total"))
DEFAULT_PRESET = "90"
DEFAULT_METRIC = "cms_actions"
DEFAULT_MODE: Mode = "week"

_DATE_ATTRS = {"type": "date", "@click": "(() => { try { $el.showPicker() } catch (e) {} })()"}


@dataclass(frozen=True)
class Selection:
    """What the page shows: the range, which preset named it, the trend's metric and mode."""

    rng: DateRange
    preset: str
    metric: str
    mode: Mode

    @property
    def range_name(self) -> str:
        return "Custom Range" if self.preset == "custom" else dict(PRESETS)[self.preset]


def preset_range(preset: str, today: date) -> DateRange:
    """The days a preset covers, ending today."""
    if preset == "launch":
        return DateRange(LAUNCH, today)
    return DateRange(max(LAUNCH, today - timedelta(days=int(preset) - 1)), today)


def _clamp(day: date, today: date) -> date:
    return min(max(day, LAUNCH), today)


class GrowthForm(forms.Form):
    """The Growth page's GET controls: a preset or a custom From and To, the metric and the mode.

    A preset fills From and To itself, so the inputs always show the range on the page. A
    custom To before From is an error on To; a day before launch moves to launch and a future
    day moves to today. Anything invalid falls back to the default for that control.
    """

    range = forms.ChoiceField(choices=PRESETS, required=False)  # noqa: A003 (the query string's name)
    date_from = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs=_DATE_ATTRS))
    date_to = forms.DateField(required=False, label="To", widget=forms.DateInput(attrs=_DATE_ATTRS))
    metric = forms.ChoiceField(choices=METRIC_CHOICES, required=False, label="Metric")
    mode = forms.ChoiceField(choices=MODES, required=False)

    def __init__(self, data: QueryDict, *, today: date) -> None:
        self.today = today
        bound = data.copy()
        preset = bound.get("range") or ("custom" if bound.get("date_from") or bound.get("date_to") else DEFAULT_PRESET)
        bound["range"] = preset
        if preset in dict(PRESETS) and preset != "custom":
            rng = preset_range(preset, today)
            bound["date_from"], bound["date_to"] = rng.start.isoformat(), rng.end.isoformat()
        self._bound = bound
        super().__init__(bound)
        for name in ("date_from", "date_to"):
            self.fields[name].widget.attrs.update({"min": LAUNCH.isoformat(), "max": today.isoformat()})
        self.fields["date_from"].widget.attrs["id"] = "growth-from"
        self.fields["date_to"].widget.attrs["id"] = "growth-to"
        # The metric switches as soon as it is picked, like the member directory's filters.
        self.fields["metric"].widget.attrs.update({"id": "growth-metric", "onchange": "this.form.requestSubmit()"})

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        date_from, date_to = data.get("date_from"), data.get("date_to")
        if date_from and date_to and date_to < date_from:
            self.add_error("date_to", "To is before From. Pick a later day.")
        return data

    def selection(self) -> Selection:
        """The range and controls to show, defaults standing in for anything invalid.

        Call after ``is_valid()``. The metric and a clamped custom day are written back into the
        bound data, so the picker and the inputs show what the page counts.
        """
        data = self.cleaned_data
        metric = data.get("metric") or DEFAULT_METRIC
        mode = data.get("mode") or DEFAULT_MODE
        self._bound["metric"] = metric
        preset = data.get("range") or DEFAULT_PRESET
        if "date_from" in self.errors or "date_to" in self.errors or "range" in self.errors:
            return Selection(preset_range(DEFAULT_PRESET, self.today), DEFAULT_PRESET, metric, mode)
        if preset != "custom":
            return Selection(preset_range(preset, self.today), preset, metric, mode)
        start = _clamp(data.get("date_from") or LAUNCH, self.today)
        end = _clamp(data.get("date_to") or self.today, self.today)
        self._bound["date_from"], self._bound["date_to"] = start.isoformat(), end.isoformat()
        return Selection(DateRange(start, end), "custom", metric, mode)

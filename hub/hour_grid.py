"""The hour by hour week (#1332): hours down the left, seven day columns, blocks placed by their times.

``components/hour_grid_week.html`` draws a :class:`HourGridWeek`. This module is the layout only,
with no database: a page builds its days (the Tours Calendar tab, ``tours.coverage``, with rails
and cards; My Hours, ``hours.page``, with blocks) and the template prints the numbers it finds here
as CSS custom properties (``--start``, ``--len``, ``--lane``, ``--lanes``, ``--rail``, ``--rails``).

Minutes on a block are counted from the top of the grid (``first_hour``), so a block at 1:00 PM on
a grid that starts at 9 AM has ``start == 240``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

#: The hours the grid always shows, 9 AM to 9 PM; anything earlier or later stretches it.
DEFAULT_FIRST_HOUR = 9
DEFAULT_LAST_HOUR = 21
MINUTES_PER_DAY = 24 * 60
#: A rail is 6px wide with 3px between rails, 4px in from the column's edge (hour-grid.css).
RAIL_INSET_PX = 4
RAIL_STEP_PX = 9


def clock(minute_of_day: int) -> str:
    """``1:00 PM``, the house style, with a no-break space so a time never splits across two lines."""
    hour, minute = divmod(minute_of_day, 60)
    suffix = "AM" if hour % 24 < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def hour_label(hour: int) -> str:
    """``9 AM``: the gutter's label for an hour line."""
    return f"{hour % 12 or 12} {'AM' if hour % 24 < 12 else 'PM'}"


def lay_lanes(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Each span's ``(lane, lanes)``, in the order given, so overlapping spans sit side by side.

    Spans that overlap, directly or through a chain of others, form a cluster; each takes the
    first lane free at its start (greedy, longest first on a tie) and every span in a cluster
    shares the cluster's lane count, so a lone span is ``(0, 1)`` and three at once are lanes
    0, 1 and 2 of 3. A span that starts as another ends does not overlap it.

    Args:
        spans: ``(start, end)`` pairs in minutes, ``start < end``.

    Returns:
        One ``(lane, lanes)`` per span, matching ``spans`` by position.
    """
    order = sorted(range(len(spans)), key=lambda index: (spans[index][0], -spans[index][1]))
    lanes: dict[int, int] = {}
    counts: dict[int, int] = {}
    cluster: list[int] = []
    lane_ends: list[int] = []
    cluster_end = 0
    for index in order:
        start, end = spans[index]
        if cluster and start >= cluster_end:
            counts.update(dict.fromkeys(cluster, len(lane_ends)))
            cluster, lane_ends = [], []
        free = [lane for lane, lane_end in enumerate(lane_ends) if lane_end <= start]
        if free:
            lanes[index] = free[0]
            lane_ends[free[0]] = end
        else:
            lanes[index] = len(lane_ends)
            lane_ends.append(end)
        cluster_end = max(cluster_end, end) if cluster else end
        cluster.append(index)
    counts.update(dict.fromkeys(cluster, len(lane_ends)))
    return [(lanes[index], counts[index]) for index in range(len(spans))]


def hour_range(spans: Sequence[tuple[int, int]]) -> tuple[int, int]:
    """``(first_hour, last_hour)``: 9 AM to 9 PM, stretched to the whole hour around any span outside it."""
    first = min([DEFAULT_FIRST_HOUR * 60, *(start for start, _ in spans)])
    last = max([DEFAULT_LAST_HOUR * 60, *(end for _, end in spans)])
    return first // 60, -(-last // 60)


@dataclass(frozen=True)
class Rail:
    """One person's hours on a day: a thin bar in their colour, and the tint for the time covered."""

    start: int
    length: int
    lane: int
    tone: str
    key: str
    title: str


@dataclass(frozen=True)
class Card:
    """A booking on the grid (a tour): its time, who and the guide, across the column beside the rails; a link to ``url``."""

    start: int
    length: int
    lane: int
    lanes: int
    tone: str
    key: str
    when: str
    label: str
    guide: str
    title: str
    pk: int
    url: str
    is_request: bool = False


@dataclass(frozen=True)
class Inner:
    """A booking inside a :class:`Block` (My Hours): ``start`` counts from the block's top, not the grid's."""

    start: int
    length: int
    lane: int
    lanes: int
    css: str
    when: str
    what: str
    title: str


@dataclass(frozen=True)
class Block:
    """One block on a day (My Hours): ``style`` is ``open`` (dashed, ``dots`` naming its kinds and the
    bookings ``inner``), ``booked`` (solid in the colour class ``css``) or ``busy`` (hatched).
    """

    start: int
    length: int
    lane: int
    lanes: int
    style: str
    when: str
    what: str
    title: str
    css: str = ""
    dots: tuple[str, ...] = ()
    inner: list[Inner] = field(default_factory=list)


@dataclass(frozen=True)
class GridDay:
    """One day column. ``state`` names a striped day (``uncovered``: nobody covers it; ``off``: a day off), with ``note`` on it."""

    day: date
    is_today: bool
    rails: list[Rail] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    state: str = ""
    note: str = ""
    note_url: str = ""
    now: int | None = None

    @property
    def key(self) -> str:
        """The day as ``YYYY-MM-DD``, the phone strip's handle on it."""
        return self.day.isoformat()

    @property
    def rails_px(self) -> int:
        """How wide the day's rails are, so the cards start clear of them."""
        if not self.rails:
            return 0
        return RAIL_INSET_PX + RAIL_STEP_PX * (max(rail.lane for rail in self.rails) + 1)

    @property
    def has_items(self) -> bool:
        """Whether anything sits on the day: the dot under its button in the phone strip."""
        return bool(self.rails or self.cards or self.blocks)


@dataclass(frozen=True)
class HourGridWeek:
    """The seven days, the hour range they share, and the template each day's contents come from."""

    first_hour: int
    last_hour: int
    days: list[GridDay]
    cell_template: str

    @property
    def rows(self) -> int:
        """How many hour rows the grid is tall."""
        return self.last_hour - self.first_hour

    @property
    def hour_labels(self) -> list[tuple[int, str]]:
        """``(row, "9 AM")`` for each hour line but the bottom one."""
        return [(row, hour_label(self.first_hour + row)) for row in range(self.rows)]

    @property
    def active_day(self) -> str:
        """The day a phone shows first: today when it is in this week, otherwise the first day."""
        return next((day.key for day in self.days if day.is_today), self.days[0].key)

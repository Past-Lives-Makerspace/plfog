"""Natural-language date/time parsing for the Discord ``/create`` command.

Turns member-typed phrases like ``next friday 6pm``, ``tomorrow 7-9pm``, or
``2026-09-12 18:00`` into naive local ``(start, end)`` datetimes — naive on purpose,
because :class:`hub.forms.CommunityEventForm` makes them aware in the site timezone
exactly like the web propose form. The grammar is deliberately predictable over
clever: a time of day is required (no silent noon/all-day default), a bare weekday
means the next upcoming one, and every rejection is a typed error the command turns
into copy that names an accepted example.

Pure functions of their arguments (``now`` is injected) so specs run against a
frozen clock. Built on stdlib + ``dateutil`` (already a dependency) — no new packages.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from dateutil import parser as dateutil_parser

# The furthest-out start we accept — beyond this it is almost always a typo'd year.
MAX_DAYS_AHEAD = 366

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# A single time token: 24-hour (18:00), or an hour with optional minutes plus am/pm
# (6pm, 6:30 pm). A bare hour with neither a colon nor am/pm is NOT a time — it would
# swallow date digits ("aug 29") and invite ambiguity.
_TIME_TOKEN = r"\d{1,2}:\d{2}(?:\s*(?:am|pm))?|\d{1,2}\s*(?:am|pm)"
# A range's *start* may additionally be a bare hour ("7-9pm") — it inherits the end's am/pm.
_RANGE_START_TOKEN = rf"{_TIME_TOKEN}|\d{{1,2}}"

_RANGE_RE = re.compile(
    rf"(?:^|(?<=\s))(?P<start>{_RANGE_START_TOKEN})\s*(?:-|–|\bto\b)\s*(?P<end>{_TIME_TOKEN})\s*$",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(rf"(?:^|(?<=\s))(?P<time>{_TIME_TOKEN})\s*$", re.IGNORECASE)


class WhenError(enum.Enum):
    """Why a ``when`` phrase was rejected — each maps to distinct member-facing copy."""

    UNPARSEABLE = "unparseable"
    NO_TIME = "no_time"
    IN_PAST = "in_past"
    TOO_FAR = "too_far"


@dataclass(frozen=True)
class WhenResult:
    """The outcome of :func:`parse_when` — either naive local datetimes or a typed error."""

    start: datetime | None = None
    end: datetime | None = None
    error: WhenError | None = None


def _parse_time_token(raw: str) -> time | None:
    """A single time token to a :class:`~datetime.time` — 24-hour first, then am/pm forms."""
    candidates = [raw.strip(), raw.strip().upper(), raw.strip().upper().replace(" ", "")]
    for candidate in candidates:
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
            try:
                return datetime.strptime(candidate, fmt).time()
            except ValueError:
                continue
    return None


def _meridiem(raw: str) -> str | None:
    """The am/pm suffix of a time token, or ``None`` when it has none (24-hour or bare)."""
    lowered = raw.strip().lower()
    if lowered.endswith("am"):
        return "am"
    if lowered.endswith("pm"):
        return "pm"
    return None


def _parse_range_start(raw: str, end_meridiem: str | None, end_t: time) -> time | None:
    """The range's start token, giving a bare hour the end's meridiem (``7-9pm`` → 7 PM).

    When inheriting the meridiem would put the start *after* the end (``11-1pm``), the
    opposite meridiem is tried so the natural reading (11 AM to 1 PM) wins. An explicit
    am/pm or a 24-hour form is taken at face value.
    """
    if not raw.strip().isdigit():
        return _parse_time_token(raw)
    if end_meridiem is None:
        return None  # a bare start needs the end's am/pm to disambiguate
    inherited = _parse_time_token(f"{raw}{end_meridiem}")
    if inherited is None:
        return None
    if inherited <= end_t:
        return inherited
    # `inherited` parsed, so `raw` is an hour in 1-12 — the flipped meridiem always parses too.
    flipped = _parse_time_token(f"{raw}{'am' if end_meridiem == 'pm' else 'pm'}")
    assert flipped is not None
    if flipped <= end_t:
        return flipped

    # Both readings wrap past the end ("11-1am"): take the shorter overnight span —
    # 11 PM to 1 AM is the natural reading, not a 14-hour 11 AM event.
    def wrapped_minutes(start: time) -> int:
        return ((end_t.hour - start.hour) * 60 + (end_t.minute - start.minute)) % (24 * 60)

    return min(inherited, flipped, key=wrapped_minutes)


def _resolve_relative_day(phrase: str, today: date) -> date | None:
    """``today``/``tonight``/``tomorrow``/``[next|this] <weekday>``/bare weekday, else ``None``.

    A weekday resolves to the next date with that weekday in ``[today, today+6]`` — the
    caller rolls it a week forward if the combined datetime has already passed, so
    "tuesday 6pm" typed on a Tuesday morning still means today.
    """
    if phrase in ("today", "tonight", ""):
        return today
    if phrase == "tomorrow":
        return today + timedelta(days=1)
    words = phrase.split()
    if len(words) == 2 and words[0] in ("next", "this"):
        words = words[1:]
    if len(words) == 1 and words[0] in _WEEKDAYS:
        target = _WEEKDAYS.index(words[0])
        return today + timedelta(days=(target - today.weekday()) % 7)
    return None


def _resolve_explicit_day(phrase: str, today: date) -> date | None:
    """An explicit date phrase via ``dateutil`` — a year-less month-day rolls forward.

    Parsing twice with different default years detects whether the member typed a year:
    when both parses land on the default's year, no year was given, and a date already
    behind ``today`` means the next occurrence (``jan 5`` typed in August → next January).
    """
    try:
        with_default = dateutil_parser.parse(phrase, default=datetime(2001, today.month, today.day), fuzzy=False)
        with_other = dateutil_parser.parse(phrase, default=datetime(2002, today.month, today.day), fuzzy=False)
    except (ValueError, OverflowError):
        return None
    year_absent = with_default.year == 2001 and with_other.year == 2002
    if not year_absent:
        return with_default.date()
    resolved = with_default.date().replace(year=today.year)
    if resolved < today:
        resolved = resolved.replace(year=today.year + 1)
    return resolved


def _split_day_and_times(normalized: str) -> tuple[str, time, time | None] | None:
    """Split ``normalized`` into (day phrase, start time, explicit end time or None)."""
    range_match = _RANGE_RE.search(normalized)
    if range_match:
        end_t = _parse_time_token(range_match["end"])
        if end_t is not None:
            start_t = _parse_range_start(range_match["start"], _meridiem(range_match["end"]), end_t)
            if start_t is not None:
                return normalized[: range_match.start()].strip(), start_t, end_t
    single_match = _SINGLE_RE.search(normalized)
    if single_match:
        start_t = _parse_time_token(single_match["time"])
        if start_t is not None:
            return normalized[: single_match.start()].strip(), start_t, None
    return None


def _resolve_day(phrase: str, today: date) -> tuple[date | None, bool]:
    """Resolve a day phrase, relative forms first: ``(date, was_relative)`` or ``(None, False)``."""
    relative = _resolve_relative_day(phrase, today)
    if relative is not None:
        return relative, True
    return _resolve_explicit_day(phrase, today), False


def parse_when(text: str, *, duration_minutes: int, now: datetime) -> WhenResult:
    """Parse a member's ``when`` phrase into naive local (start, end) datetimes.

    Args:
        text: The raw phrase, e.g. ``next friday 6pm`` or ``tomorrow 7-9pm``.
        duration_minutes: Event length when the phrase has no explicit end time.
        now: The current *naive local* datetime (inject a frozen one in specs).

    Returns:
        A :class:`WhenResult` — ``start``/``end`` set on success, ``error`` set otherwise:
        ``NO_TIME`` (a day but no start time), ``UNPARSEABLE`` (nothing recognizable),
        ``IN_PAST``, or ``TOO_FAR`` (more than :data:`MAX_DAYS_AHEAD` days out).
    """
    normalized = " ".join(text.split()).lower()
    if not normalized:
        return WhenResult(error=WhenError.UNPARSEABLE)

    split = _split_day_and_times(normalized)
    if split is None:
        # No time token — decide NO_TIME (the day part parses) vs UNPARSEABLE (nothing does).
        day_only, _ = _resolve_day(normalized, now.date())
        return WhenResult(error=WhenError.NO_TIME if day_only is not None else WhenError.UNPARSEABLE)
    day_phrase, start_t, end_t = split

    event_day, relative = _resolve_day(day_phrase, now.date())
    if event_day is None:
        return WhenResult(error=WhenError.UNPARSEABLE)

    start = datetime.combine(event_day, start_t)
    # A relative weekday that already passed today rolls a week ("tuesday 6pm" on Tuesday night).
    if relative and day_phrase not in ("today", "tonight", "tomorrow", "") and start <= now:
        start += timedelta(days=7)
    if start <= now:
        return WhenResult(error=WhenError.IN_PAST)
    if start - now > timedelta(days=MAX_DAYS_AHEAD):
        return WhenResult(error=WhenError.TOO_FAR)

    if end_t is not None:
        end = datetime.combine(start.date(), end_t)
        if end <= start:  # overnight range: 9pm-1am ends the next day
            end += timedelta(days=1)
    else:
        end = start + timedelta(minutes=duration_minutes)
    return WhenResult(start=start, end=end)

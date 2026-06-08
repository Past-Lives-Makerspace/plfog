"""Template tags for the classes public portal."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Iterable

from django import template
from django.http import QueryDict

if TYPE_CHECKING:
    from classes.models import ClassApproval

register = template.Library()


_YOUTUBE_PATTERNS = (
    re.compile(r"(?:youtube\.com/watch\?(?:[^&]+&)*v=)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube-nocookie\.com/embed/)([A-Za-z0-9_-]{11})"),
)


@register.filter
def first_with_role(approvals: Iterable[ClassApproval], role: str) -> ClassApproval | None:
    """Return the first ClassApproval row from ``approvals`` with the given role.

    Used by the reviewer page to render a per-required-role progress list
    without N database queries. The ``approvals`` argument is the prefetched
    iterable on ``offering.approvals.all``.
    """
    for row in approvals:
        if row.role == role:
            return row
    return None


@register.filter
def youtube_embed_id(url: str | None) -> str:
    """Extract the 11-char video ID from any common YouTube URL form.

    Returns an empty string when the URL is missing or doesn't parse — the
    detail template uses this to skip rendering the iframe.
    """
    if not url:
        return ""
    for pat in _YOUTUBE_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    return ""


@register.inclusion_tag("components/table_sort_header.html")
def sort_header(label: str, field: str, current_sort: str, current_dir: str, base_params: str) -> dict:
    """Render a sortable table header cell."""
    is_active = current_sort == field
    next_dir = "desc" if is_active and current_dir == "asc" else "asc"
    qd = QueryDict(base_params, mutable=True)
    qd["sort"] = field
    qd["dir"] = next_dir
    return {
        "label": label,
        "href": f"?{qd.urlencode()}",
        "is_active": is_active,
        "direction": current_dir if is_active else "",
    }


@register.filter
def cents_as_dollars(cents: int | None) -> str:
    """Format an integer cents value as $X.YY.

    Used by the /account/receipts/ template. Returns "$0.00" for None/0.
    """
    value = int(cents or 0)
    return f"${value / 100:,.2f}"


@register.filter
def cents_as_price(value: int | None) -> str:
    """Format integer cents as a dollar string. Zero renders as "Free"; whole dollars drop the decimals."""
    if value is None:
        return ""
    cents = int(value)
    if cents == 0:
        return "Free"
    dollars, remainder = divmod(cents, 100)
    if remainder == 0:
        return f"${dollars}"
    return f"${dollars}.{remainder:02d}"


@register.filter
def duration_words(minutes: int | None) -> str:
    """Human duration — '45m', '2h', '1h30m'."""
    if not minutes:
        return ""
    total = int(minutes)
    if total < 60:
        return f"{total}m"
    hours, rem = divmod(total, 60)
    return f"{hours}h" if rem == 0 else f"{hours}h{rem}m"


@register.filter
def session_duration_words(session) -> str:
    """Duration for a ClassSession (ends_at - starts_at), formatted via duration_words."""
    if session is None or session.ends_at is None or session.starts_at is None:
        return ""
    delta: timedelta = session.ends_at - session.starts_at
    return duration_words(int(delta.total_seconds() // 60))


@register.filter
def total_session_minutes(sessions: Iterable) -> int:
    """Sum of minutes across a sequence of sessions."""
    total = 0
    for session in sessions or []:
        if session.starts_at and session.ends_at:
            total += int((session.ends_at - session.starts_at).total_seconds() // 60)
    return total


@register.filter
def spots_class(spots_left: int | None) -> str:
    """CSS class for the spots-left pill — 'full' / 'low' / 'ok'."""
    if spots_left is None:
        return ""
    remaining = int(spots_left)
    if remaining <= 0:
        return "full"
    if remaining <= 3:
        return "low"
    return "ok"


@register.filter
def initials(name: str | None) -> str:
    """First letter of each word, uppercase, for avatar fallbacks."""
    if not name:
        return ""
    parts = [word[0] for word in name.split() if word]
    return "".join(parts[:3]).upper()


@register.simple_tag
def member_price_cents(price_cents: int, discount_pct: int) -> int | None:
    """Return the discounted member price in cents, or None if no discount."""
    if not discount_pct:
        return None
    return int(int(price_cents) * (100 - int(discount_pct)) / 100)


@register.simple_tag
def classes_settings():
    """Load the ClassSettings singleton for use in templates."""
    from classes.models import ClassSettings

    return ClassSettings.load()


@register.filter
def short_date_list(sessions: list) -> str:
    """Format session dates as 'Jun 7, Jun 14, Jun 21 +2 more' (first 3 shown)."""
    from django.utils.timezone import localtime

    total = len(sessions)
    parts = [localtime(s.starts_at).strftime("%b %-d") for s in sessions[:3]]
    if total > 3:
        parts.append(f"+{total - 3} more")
    return ", ".join(parts)


@register.filter
def session_time_range(session) -> str:
    """Format a session's time window as '6–8 PM' or '10:30 AM–12:30 PM'.

    Drops the period from the start when both times share the same AM/PM half.
    Returns empty string when starts_at is missing.
    """
    from django.utils.timezone import localtime

    if not session or not session.starts_at:
        return ""
    start = localtime(session.starts_at)

    def _compact(dt) -> str:
        return dt.strftime("%-I") if dt.minute == 0 else dt.strftime("%-I:%M")

    if not session.ends_at:
        return start.strftime("%-I %p") if start.minute == 0 else start.strftime("%-I:%M %p")

    end = localtime(session.ends_at)
    if start.strftime("%p") == end.strftime("%p"):
        return f"{_compact(start)}–{_compact(end)} {end.strftime('%p')}"
    start_str = start.strftime("%-I %p") if start.minute == 0 else start.strftime("%-I:%M %p")
    end_str = end.strftime("%-I %p") if end.minute == 0 else end.strftime("%-I:%M %p")
    return f"{start_str}–{end_str}"


@register.filter
def strip_date_suffix(value: str | None) -> str:
    """Strip CMS-imported date suffixes like ' - 6/5/26' or ' 9/8/26, 9/10/26' from a title."""
    if not value:
        return value or ""
    return re.sub(r"(\s*[-–]\s*|\s+)\d{1,2}/\d{1,2}/\d{2,4}.*", "", value).strip()


@register.filter
def upcoming_sessions(sessions) -> list:
    """Filter a sessions queryset to upcoming ones, sorted by start time.

    Uses the prefetch cache when sessions have been prefetch_related — no extra
    DB query on the list page.
    """
    from django.utils.timezone import now

    cutoff = now()
    return sorted(
        (s for s in sessions if s.starts_at and s.starts_at >= cutoff),
        key=lambda s: s.starts_at,
    )


@register.filter
def recurrence_label(sessions: list) -> str:
    """Return 'Every Friday at 6 PM' if all sessions share the same weekday and time.

    Uses local time so evening classes near midnight UTC don't get the wrong day.
    """
    from django.utils.timezone import localtime

    if len(sessions) < 2:
        return ""
    local_starts = [localtime(s.starts_at) for s in sessions]
    weekdays = {dt.weekday() for dt in local_starts}
    times = {(dt.hour, dt.minute) for dt in local_starts}
    if len(weekdays) != 1 or len(times) != 1:
        return ""
    dt0 = local_starts[0]
    day = dt0.strftime("%A")
    t_str = dt0.strftime("%-I %p") if dt0.minute == 0 else dt0.strftime("%-I:%M %p")
    return f"Every {day} at {t_str}"


@register.simple_tag
def concat(*parts) -> str:
    """Concatenate arbitrary args as strings — safe for building DOM ids.

    Django's built-in ``add`` filter tries numeric addition first and returns ""
    when mixing a string prefix with an int pk. Use ``{% concat "del-" obj.pk as mid %}``
    instead so template-rendered ids stay unique.
    """
    return "".join("" if p is None else str(p) for p in parts)

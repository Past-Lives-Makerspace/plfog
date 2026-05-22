"""Template tags for the classes public portal."""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from django import template

register = template.Library()


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


@register.simple_tag
def concat(*parts) -> str:
    """Concatenate arbitrary args as strings — safe for building DOM ids.

    Django's built-in ``add`` filter tries numeric addition first and returns ""
    when mixing a string prefix with an int pk. Use ``{% concat "del-" obj.pk as mid %}``
    instead so template-rendered ids stay unique.
    """
    return "".join("" if p is None else str(p) for p in parts)

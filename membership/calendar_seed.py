"""Pure matching/classification helpers for the one-time guild-calendar seed importer.

Mirrors ``membership/logos.py``: a module of constants + small, DB-free pure functions that
the ``seed_guild_calendar_events`` command composes. Kept dependency-light so the command
stays thin and every branch is unit-testable against fixture iCal bytes with no network.

The seed reads the Public Google Calendar ONCE to pre-fill each guild's existing studio hours
and meetings so leads don't start from a blank page. It never reads Google again — after the
seed, the app is the source of truth and the sync is app → Google only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime as datetime_type
from datetime import time as time_type
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from membership.models import CommunityEvent, Guild

# Case-insensitive acronym/alias (a whole title word) → a fragment of the guild name it
# identifies. Supplements a plain guild-name substring match; "FIG" is the motivating case.
# Add more as a real Public-Calendar dry-run reveals them.
_GUILD_ALIASES: dict[str, str] = {
    "fig": "food independence",  # "FIG Guild Meeting" → Food Independence Guild
}

# Title keywords → event type. Meetings are checked first so "Guild Meeting" never reads as
# studio hours; the first list to hit wins.
_MEETING_KEYWORDS: tuple[str, ...] = ("meeting", "guild meeting")
_STUDIO_HOURS_KEYWORDS: tuple[str, ...] = ("studio hours", "open studio", "shop hours")


@dataclass
class AmbiguousMatch:
    """Sentinel returned when a title matches more than one guild — the command reports the
    candidates and skips the row rather than guessing."""

    guilds: list[Guild]


@dataclass(frozen=True)
class SeedEvent:
    """One master VEVENT read from the feed (recurrence kept as the raw RRULE mapping so the
    seed can tell WEEKLY from MONTHLY — unlike the display parser, which expands + drops it)."""

    uid: str
    summary: str
    starts_at: datetime_type
    ends_at: datetime_type
    location: str
    description: str
    rrule: dict[str, Any] | None


def _first(value: Any) -> str | None:
    """The first scalar of an icalendar RRULE value (``['WEEKLY']`` → ``'WEEKLY'``), or None."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else None
    return str(value)


def guild_for_title(title: str, guilds: Iterable[Guild]) -> Guild | AmbiguousMatch | None:
    """Resolve a VEVENT summary to exactly one guild.

    A guild matches if its name appears as a substring of the (lower-cased) title, or if one of
    its ``_GUILD_ALIASES`` acronyms appears as a whole word in the title. Exactly one match →
    that guild; none → ``None``; more than one → an :class:`AmbiguousMatch` (never a guess).
    """
    import re

    lowered = title.lower()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    matched: list[Guild] = []
    for guild in guilds:
        name_l = guild.name.lower()
        if name_l and name_l in lowered:
            matched.append(guild)
        elif any(alias in tokens and fragment in name_l for alias, fragment in _GUILD_ALIASES.items()):
            matched.append(guild)
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        return AmbiguousMatch(matched)
    return None


def classify_type(title: str) -> "CommunityEvent.EventType | None":
    """Meeting keywords → GUILD_MEETING; else studio-hours keywords → STUDIO_HOURS; else None
    (reported "unclassified" and skipped)."""
    from membership.models import CommunityEvent

    lowered = title.lower()
    if any(keyword in lowered for keyword in _MEETING_KEYWORDS):
        return CommunityEvent.EventType.GUILD_MEETING
    if any(keyword in lowered for keyword in _STUDIO_HOURS_KEYWORDS):
        return CommunityEvent.EventType.STUDIO_HOURS
    return None


def recurrence_for_rrule(rrule: dict[str, Any] | None) -> "CommunityEvent.Recurrence":
    """Map a master VEVENT's RRULE to a :class:`CommunityEvent.Recurrence`.

    ``WEEKLY`` → weekly; ``MONTHLY`` with INTERVAL 1/2/3 → monthly/every-2/every-3; ``YEARLY``
    → yearly; no RRULE or an unmapped FREQ → ``NONE`` (a one-off).
    """
    from membership.models import CommunityEvent

    recurrence = CommunityEvent.Recurrence
    if not rrule:
        return recurrence.NONE
    freq = _first(rrule.get("FREQ"))
    if freq == "WEEKLY":
        return recurrence.WEEKLY
    if freq == "YEARLY":
        return recurrence.YEARLY
    if freq == "MONTHLY":
        interval = int(_first(rrule.get("INTERVAL")) or 1)
        return {
            1: recurrence.MONTHLY,
            2: recurrence.EVERY_2_MONTHS,
            3: recurrence.EVERY_3_MONTHS,
        }.get(interval, recurrence.MONTHLY)
    return recurrence.NONE


def _to_aware(value: datetime_type | date_type) -> datetime_type:
    """Normalize an iCal DTSTART/DTEND to an aware datetime in the current timezone.

    An aware datetime passes through; a naive (floating) one is localized; a bare date (an
    all-day event) is anchored to local midnight."""
    from django.utils import timezone

    if isinstance(value, datetime_type):
        return value if timezone.is_aware(value) else timezone.make_aware(value)
    return timezone.make_aware(datetime_type.combine(value, time_type()))


def parse_seed_vevents(raw_bytes: bytes) -> list[SeedEvent]:
    """Walk every master VEVENT in an iCal document, keeping the RRULE.

    This is the seed's OWN walk — it does NOT reuse ``hub.calendar_service._parse_ical_events``,
    which expands occurrences and discards the master RRULE the seed needs to detect cadence.
    VEVENTs missing a DTSTART/DTEND are skipped (they cannot become a timed event).
    """
    import icalendar

    calendar = icalendar.Calendar.from_ical(raw_bytes)
    events: list[SeedEvent] = []
    for component in calendar.walk("VEVENT"):
        dtstart = component.get("DTSTART")
        dtend = component.get("DTEND")
        if dtstart is None or dtend is None:
            continue
        rrule = component.get("RRULE")
        events.append(
            SeedEvent(
                uid=str(component.get("UID", "")),
                summary=str(component.get("SUMMARY", "")),
                starts_at=_to_aware(dtstart.dt),
                ends_at=_to_aware(dtend.dt),
                location=str(component.get("LOCATION", "")),
                description=str(component.get("DESCRIPTION", "")),
                rrule=dict(rrule) if rrule is not None else None,
            )
        )
    return events

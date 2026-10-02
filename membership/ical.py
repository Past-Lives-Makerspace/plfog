"""Shared iCal (RFC 5545) helpers.

One implementation of each so the combined Community-Calendar export
(``hub.views.calendar_export_ics``) and each event's per-event ``.ics``
(``CommunityEvent.ics_document``) never drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime

# Each zone's VTIMEZONE in the yearly-rule form Google and Apple export. Outlook reads only a
# rule's DTSTART and RRULE and ignores RDATE lists, so a zone written as a list of its clock
# changes (icalendar's own output) leaves Outlook an hour off for half the year.
_VTIMEZONES = {
    "America/Los_Angeles": [
        "BEGIN:VTIMEZONE",
        "TZID:America/Los_Angeles",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:-0800",
        "TZOFFSETTO:-0700",
        "TZNAME:PDT",
        "DTSTART:19700308T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
        "END:DAYLIGHT",
        "BEGIN:STANDARD",
        "TZOFFSETFROM:-0700",
        "TZOFFSETTO:-0800",
        "TZNAME:PST",
        "DTSTART:19701101T020000",
        "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
        "END:STANDARD",
        "END:VTIMEZONE",
    ],
}


def ical_escape(value: str) -> str:
    """Escape a text value per RFC 5545 §3.3.11 (backslash, newlines, ``;`` and ``,``)."""
    value = value.replace("\\", "\\\\")
    value = value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    value = value.replace(";", "\\;").replace(",", "\\,")
    return value


def ical_local_time(name: str, moment: datetime) -> str:
    """A ``DTSTART``/``DTEND`` line in the makerspace's local time, naming its zone.

    A repeating event's ``RRULE`` weekday is the local one, so its times must be local too: in
    UTC an evening series lands a day early on every date after the first.
    """
    local = timezone.localtime(moment)
    return f"{name};TZID={timezone.get_default_timezone_name()}:{local.strftime('%Y%m%dT%H%M%S')}"


def ical_timezone_lines() -> list[str]:
    """The ``VTIMEZONE`` block that :func:`ical_local_time`'s ``TZID`` points to.

    Raises:
        ImproperlyConfigured: ``TIME_ZONE`` names a zone with no block in ``_VTIMEZONES``.
    """
    name = timezone.get_default_timezone_name()
    if name not in _VTIMEZONES:
        raise ImproperlyConfigured(f"No VTIMEZONE for TIME_ZONE {name!r}; add its rules to membership/ical.py.")
    return list(_VTIMEZONES[name])

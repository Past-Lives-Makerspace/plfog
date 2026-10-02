"""Shared iCal (RFC 5545) helpers.

One implementation of each so the combined Community-Calendar export
(``hub.views.calendar_export_ics``) and each event's per-event ``.ics``
(``CommunityEvent.ics_document``) never drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime


def ical_escape(value: str) -> str:
    """Escape a text value per RFC 5545 §3.3.11 (backslash, newlines, ``;`` and ``,``)."""
    value = value.replace("\\", "\\\\")
    value = value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    value = value.replace(";", "\;").replace(",", "\\,")
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

    Built from the time zone database rather than written by hand, so the daylight saving dates
    stay right. The lines keep icalendar's folding.
    """
    import icalendar

    vtimezone = icalendar.Timezone.from_tzid(timezone.get_default_timezone_name())
    return vtimezone.to_ical().decode().rstrip("\r\n").split("\r\n")

"""Shared iCal (RFC 5545) helpers.

One escaping implementation so the combined Community-Calendar export
(``hub.views.calendar_export_ics``) and each event's per-event ``.ics``
(``CommunityEvent.ics_document``) never drift.
"""

from __future__ import annotations


def ical_escape(value: str) -> str:
    """Escape a text value per RFC 5545 §3.3.11 (backslash, newlines, ``;`` and ``,``)."""
    value = value.replace("\\", "\\\\")
    value = value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    value = value.replace(";", "\\;").replace(",", "\\,")
    return value

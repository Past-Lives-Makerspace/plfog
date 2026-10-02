"""BDD specs for the shared iCal helpers: escaping and the makerspace's VTIMEZONE block."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from dateutil.rrule import rrulestr
from django.core.exceptions import ImproperlyConfigured

from membership.ical import ical_escape, ical_timezone_lines


def _offset(value: str) -> timedelta:
    """``-0800`` as a timedelta."""
    sign = -1 if value.startswith("-") else 1
    return sign * timedelta(hours=int(value[1:3]), minutes=int(value[3:5]))


def _observances(lines: list[str]) -> list[dict[str, str]]:
    """Each DAYLIGHT / STANDARD block's properties."""
    observances: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        key, _, value = line.partition(":")
        if key == "BEGIN" and value in ("DAYLIGHT", "STANDARD"):
            current = {}
        elif key == "END" and value in ("DAYLIGHT", "STANDARD"):
            assert current is not None
            observances.append(current)
            current = None
        elif current is not None:
            current[key] = value
    return observances


def describe_ical_escape():
    def it_escapes_backslashes_semicolons_commas_and_newlines():
        assert ical_escape("a;b,c\\d\ne") == r"a\;b\,c\\d\ne"


def describe_ical_timezone_lines():
    def it_writes_each_clock_change_as_a_yearly_rule_outlook_reads():
        # Outlook reads a rule's DTSTART and RRULE and ignores RDATE lists.
        lines = ical_timezone_lines()
        assert lines[0] == "BEGIN:VTIMEZONE"
        assert "TZID:America/Los_Angeles" in lines
        assert "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU" in lines
        assert "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU" in lines
        assert not any(line.startswith("RDATE") for line in lines)
        assert lines[-1] == "END:VTIMEZONE"

    def it_changes_the_clocks_when_the_time_zone_database_does_for_twenty_years():
        zone = ZoneInfo("America/Los_Angeles")
        changes = 0
        for observance in _observances(ical_timezone_lines()):
            before, after = _offset(observance["TZOFFSETFROM"]), _offset(observance["TZOFFSETTO"])
            rule = rrulestr(observance["RRULE"], dtstart=datetime.strptime(observance["DTSTART"], "%Y%m%dT%H%M%S"))
            for local in rule.between(datetime(2026, 1, 1), datetime(2046, 1, 1)):
                # DTSTART is on the clock before the change, so the instant is local time minus that offset.
                instant = (local - before).replace(tzinfo=UTC)
                assert (instant - timedelta(minutes=1)).astimezone(zone).utcoffset() == before
                assert instant.astimezone(zone).utcoffset() == after
                changes += 1
        assert changes == 40  # two a year, none missed

    def it_refuses_a_time_zone_it_has_no_rules_for(settings):
        settings.TIME_ZONE = "Europe/Berlin"
        with pytest.raises(ImproperlyConfigured):
            ical_timezone_lines()

"""BDD specs for the session_dates template filter (issue #536).

A series option on the catalog card and the class page's other-date rows list every
upcoming date, "Oct 2, Oct 9, Oct 23", so a member knows which days they are
committing to. ``session_date_range`` keeps the first to last span for the register
page's run picker, where an ``<option>`` cannot wrap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from django.utils import timezone
from django.utils.timezone import localtime

from classes.templatetags.classes_tags import session_dates


def _label(moment) -> str:
    return localtime(moment).strftime("%b %-d")


def describe_session_dates():
    def it_lists_every_date_in_start_order_comma_separated():
        base = timezone.now()
        sessions = [
            SimpleNamespace(starts_at=base + timedelta(days=21)),
            SimpleNamespace(starts_at=base),
            SimpleNamespace(starts_at=base + timedelta(days=7)),
        ]
        assert session_dates(sessions) == ", ".join(
            [_label(base), _label(base + timedelta(days=7)), _label(base + timedelta(days=21))]
        )

    def it_renders_a_single_session_as_its_one_date():
        only = timezone.now()
        assert session_dates([SimpleNamespace(starts_at=only)]) == _label(only)

    def it_keeps_every_date_of_a_long_series():
        base = timezone.now()
        sessions = [SimpleNamespace(starts_at=base + timedelta(days=7 * i)) for i in range(8)]
        result = session_dates(sessions)
        assert result.count(", ") == 7
        assert result.startswith(_label(base))
        assert result.endswith(_label(base + timedelta(days=49)))

    def it_uses_local_time_for_the_day():
        # 03:00 UTC on Oct 3 is still the evening of Oct 2 in Portland; the card shows the local day.
        moment = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)
        assert session_dates([SimpleNamespace(starts_at=moment)]) == "Oct 2"

    def it_ignores_sessions_without_a_start():
        base = timezone.now()
        sessions = [SimpleNamespace(starts_at=None), SimpleNamespace(starts_at=base)]
        assert session_dates(sessions) == _label(base)

    def it_returns_empty_for_no_sessions():
        assert session_dates([]) == ""
        assert session_dates(None) == ""

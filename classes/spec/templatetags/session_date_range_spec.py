"""BDD specs for the session_date_range template filter."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone
from django.utils.timezone import localtime

from classes.templatetags.classes_tags import session_date_range


def describe_session_date_range():
    def it_spans_first_to_last_for_multiple_dates():
        base = timezone.now()
        sessions = [
            SimpleNamespace(starts_at=base + timedelta(days=14)),
            SimpleNamespace(starts_at=base),
            SimpleNamespace(starts_at=base + timedelta(days=7)),
        ]
        result = session_date_range(sessions)
        first = localtime(base).strftime("%b %-d")
        last = localtime(base + timedelta(days=14)).strftime("%b %-d")
        assert first in result
        assert last in result
        assert result != first  # it is a span, not a single date

    def it_returns_a_single_date_when_only_one_session():
        only = timezone.now()
        result = session_date_range([SimpleNamespace(starts_at=only)])
        assert result == localtime(only).strftime("%b %-d")

    def it_ignores_sessions_without_a_start():
        result = session_date_range([SimpleNamespace(starts_at=None)])
        assert result == ""

    def it_returns_empty_for_no_sessions():
        assert session_date_range([]) == ""
        assert session_date_range(None) == ""

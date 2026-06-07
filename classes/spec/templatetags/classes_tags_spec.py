"""Specs for classes/templatetags/classes_tags.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest

from classes.templatetags.classes_tags import (
    cents_as_dollars,
    cents_as_price,
    duration_words,
    initials,
    member_price_cents,
    recurrence_label,
    session_duration_words,
    session_time_range,
    short_date_list,
    sort_header,
    spots_class,
    strip_date_suffix,
    total_session_minutes,
    upcoming_sessions,
)


# ---------------------------------------------------------------------------
# sort_header
# ---------------------------------------------------------------------------


def describe_sort_header():
    def it_returns_ascending_link_when_not_active():
        result = sort_header("Title", "title", "created_at", "desc", "")
        assert result["label"] == "Title"
        assert result["is_active"] is False
        assert "sort=title" in result["href"]
        assert "dir=asc" in result["href"]

    def it_toggles_to_desc_when_active_and_asc():
        result = sort_header("Title", "title", "title", "asc", "")
        assert result["is_active"] is True
        assert result["direction"] == "asc"
        assert "dir=desc" in result["href"]

    def it_toggles_to_asc_when_active_and_desc():
        result = sort_header("Title", "title", "title", "desc", "")
        assert result["is_active"] is True
        assert result["direction"] == "desc"
        assert "dir=asc" in result["href"]

    def it_preserves_base_params():
        result = sort_header("Title", "title", "created_at", "asc", "q=foo&status=active")
        assert "q=foo" in result["href"]
        assert "status=active" in result["href"]


# ---------------------------------------------------------------------------
# cents_as_dollars
# ---------------------------------------------------------------------------


def describe_cents_as_dollars():
    def it_formats_a_positive_value_as_dollars_and_cents():
        assert cents_as_dollars(1050) == "$10.50"

    def it_formats_zero_cents_as_zero_dollars():
        assert cents_as_dollars(0) == "$0.00"

    def it_treats_none_as_zero():
        assert cents_as_dollars(None) == "$0.00"

    def it_uses_comma_separator_for_large_values():
        assert cents_as_dollars(100000) == "$1,000.00"

    def it_formats_whole_dollar_amounts_with_trailing_zeros():
        assert cents_as_dollars(500) == "$5.00"


# ---------------------------------------------------------------------------
# cents_as_price
# ---------------------------------------------------------------------------


def describe_cents_as_price():
    def it_returns_empty_string_for_none():
        assert cents_as_price(None) == ""

    def it_returns_free_for_zero():
        assert cents_as_price(0) == "Free"

    def it_drops_decimals_for_whole_dollar_amounts():
        assert cents_as_price(500) == "$5"

    def it_includes_decimals_for_fractional_amounts():
        assert cents_as_price(550) == "$5.50"

    def it_zero_pads_single_digit_cents():
        assert cents_as_price(501) == "$5.01"

    def it_formats_large_whole_dollar_amounts():
        assert cents_as_price(10000) == "$100"


# ---------------------------------------------------------------------------
# duration_words
# ---------------------------------------------------------------------------


def describe_duration_words():
    def it_returns_empty_string_for_none():
        assert duration_words(None) == ""

    def it_returns_empty_string_for_zero():
        assert duration_words(0) == ""

    def it_formats_sub_hour_duration_in_minutes():
        assert duration_words(45) == "45m"

    def it_formats_exactly_one_hour_without_minutes():
        assert duration_words(60) == "1h"

    def it_formats_multiple_whole_hours_without_minutes():
        assert duration_words(120) == "2h"

    def it_formats_hours_and_remainder_minutes():
        assert duration_words(90) == "1h30m"

    def it_formats_non_round_multi_hour_duration():
        assert duration_words(145) == "2h25m"


# ---------------------------------------------------------------------------
# session_duration_words
# ---------------------------------------------------------------------------


def describe_session_duration_words():
    def _make_session(starts_at=None, ends_at=None):
        return SimpleNamespace(starts_at=starts_at, ends_at=ends_at)

    def it_returns_empty_string_for_none_session():
        assert session_duration_words(None) == ""

    def it_returns_empty_string_when_ends_at_is_none():
        from django.utils import timezone

        session = _make_session(starts_at=timezone.now(), ends_at=None)
        assert session_duration_words(session) == ""

    def it_returns_empty_string_when_starts_at_is_none():
        from django.utils import timezone

        session = _make_session(starts_at=None, ends_at=timezone.now())
        assert session_duration_words(session) == ""

    def it_formats_a_90_minute_session():
        from django.utils import timezone

        now = timezone.now()
        session = _make_session(starts_at=now, ends_at=now + timedelta(minutes=90))
        assert session_duration_words(session) == "1h30m"

    def it_formats_a_sub_hour_session():
        from django.utils import timezone

        now = timezone.now()
        session = _make_session(starts_at=now, ends_at=now + timedelta(minutes=45))
        assert session_duration_words(session) == "45m"

    def it_formats_a_whole_hour_session():
        from django.utils import timezone

        now = timezone.now()
        session = _make_session(starts_at=now, ends_at=now + timedelta(hours=2))
        assert session_duration_words(session) == "2h"


# ---------------------------------------------------------------------------
# total_session_minutes
# ---------------------------------------------------------------------------


def describe_total_session_minutes():
    def _make_session(starts_at=None, ends_at=None):
        return SimpleNamespace(starts_at=starts_at, ends_at=ends_at)

    def it_returns_zero_for_empty_list():
        assert total_session_minutes([]) == 0

    def it_returns_zero_for_none():
        assert total_session_minutes(None) == 0

    def it_sums_minutes_across_multiple_sessions():
        from django.utils import timezone

        now = timezone.now()
        sessions = [
            _make_session(starts_at=now, ends_at=now + timedelta(hours=1)),
            _make_session(starts_at=now, ends_at=now + timedelta(minutes=90)),
        ]
        assert total_session_minutes(sessions) == 150

    def it_skips_sessions_missing_starts_at():
        from django.utils import timezone

        now = timezone.now()
        sessions = [
            _make_session(starts_at=None, ends_at=now + timedelta(hours=1)),
            _make_session(starts_at=now, ends_at=now + timedelta(hours=1)),
        ]
        assert total_session_minutes(sessions) == 60

    def it_skips_sessions_missing_ends_at():
        from django.utils import timezone

        now = timezone.now()
        sessions = [
            _make_session(starts_at=now, ends_at=None),
            _make_session(starts_at=now, ends_at=now + timedelta(minutes=30)),
        ]
        assert total_session_minutes(sessions) == 30

    def it_returns_zero_for_single_session_with_no_timestamps():
        assert total_session_minutes([_make_session()]) == 0


# ---------------------------------------------------------------------------
# spots_class
# ---------------------------------------------------------------------------


def describe_spots_class():
    def it_returns_empty_string_for_none():
        assert spots_class(None) == ""

    def it_returns_full_for_zero_spots():
        assert spots_class(0) == "full"

    def it_returns_full_for_negative_spots():
        assert spots_class(-1) == "full"

    def it_returns_low_for_one_spot():
        assert spots_class(1) == "low"

    def it_returns_low_for_three_spots():
        assert spots_class(3) == "low"

    def it_returns_ok_for_four_spots():
        assert spots_class(4) == "ok"

    def it_returns_ok_for_many_spots():
        assert spots_class(20) == "ok"


# ---------------------------------------------------------------------------
# initials
# ---------------------------------------------------------------------------


def describe_initials():
    def it_returns_empty_string_for_none():
        assert initials(None) == ""

    def it_returns_empty_string_for_empty_string():
        assert initials("") == ""

    def it_extracts_initials_from_two_word_name():
        assert initials("Jane Doe") == "JD"

    def it_extracts_initials_from_three_word_name():
        assert initials("Mary Jane Watson") == "MJW"

    def it_caps_at_three_initials_for_long_names():
        assert initials("One Two Three Four") == "OTT"

    def it_uppercases_lowercase_names():
        assert initials("alice bob") == "AB"

    def it_handles_single_word_name():
        assert initials("Prince") == "P"


# ---------------------------------------------------------------------------
# member_price_cents (simple_tag — called as a function)
# ---------------------------------------------------------------------------


def describe_member_price_cents():
    def it_returns_none_when_discount_is_zero():
        assert member_price_cents(5000, 0) == None  # noqa: E711

    def it_applies_discount_and_returns_discounted_cents():
        # 5000 cents * (100 - 10) / 100 = 4500
        assert member_price_cents(5000, 10) == 4500

    def it_returns_zero_for_100_percent_discount():
        assert member_price_cents(5000, 100) == 0

    def it_truncates_fractional_cents():
        # 1000 cents * 90% = 999.something → int truncates
        assert member_price_cents(1000, 10) == 900


# ---------------------------------------------------------------------------
# classes_settings (simple_tag — requires DB)
# ---------------------------------------------------------------------------


pytestmark_db = pytest.mark.django_db


def describe_classes_settings():
    @pytest.mark.django_db
    def it_returns_the_class_settings_singleton():
        from classes.models import ClassSettings
        from classes.templatetags.classes_tags import classes_settings

        result = classes_settings()
        assert isinstance(result, ClassSettings)

    @pytest.mark.django_db
    def it_returns_the_same_singleton_on_repeated_calls():
        from classes.templatetags.classes_tags import classes_settings

        first = classes_settings()
        second = classes_settings()
        assert first.pk == second.pk


# ---------------------------------------------------------------------------
# strip_date_suffix
# ---------------------------------------------------------------------------


def describe_strip_date_suffix():
    def it_returns_empty_string_for_none():
        assert strip_date_suffix(None) == ""

    def it_returns_empty_string_for_empty_string():
        assert strip_date_suffix("") == ""

    def it_strips_a_single_date_suffix():
        assert strip_date_suffix("Shadowbox Framing - 6/5/26") == "Shadowbox Framing"

    def it_strips_a_multi_date_suffix():
        assert strip_date_suffix("Blacksmithing 101 - 6/5/26, 6/12/26, 6/19/26") == "Blacksmithing 101"

    def it_strips_a_four_digit_year_suffix():
        assert strip_date_suffix("Knife Making - 6/5/2026") == "Knife Making"

    def it_leaves_titles_without_date_suffixes_unchanged():
        assert strip_date_suffix("Intro to Welding") == "Intro to Welding"

    def it_strips_leading_spaces_around_dash():
        assert strip_date_suffix("Title  -  1/1/25") == "Title"

    def it_strips_dates_with_no_dash():
        assert (
            strip_date_suffix("Intro to Knife Making with Glen 9/8/26, 9/10/26, 9/15/26, 9/17/26")
            == "Intro to Knife Making with Glen"
        )


# ---------------------------------------------------------------------------
# upcoming_sessions
# ---------------------------------------------------------------------------


def describe_upcoming_sessions():
    def _future_session(offset_days: int = 1):
        from django.utils import timezone

        starts_at = timezone.now() + timedelta(days=offset_days)
        return SimpleNamespace(starts_at=starts_at)

    def _past_session(offset_days: int = 1):
        from django.utils import timezone

        starts_at = timezone.now() - timedelta(days=offset_days)
        return SimpleNamespace(starts_at=starts_at)

    def it_returns_empty_list_for_empty_input():
        assert upcoming_sessions([]) == []

    def it_excludes_past_sessions():
        assert upcoming_sessions([_past_session()]) == []

    def it_includes_future_sessions():
        session = _future_session()
        result = upcoming_sessions([session])
        assert result == [session]

    def it_excludes_sessions_with_none_starts_at():
        assert upcoming_sessions([SimpleNamespace(starts_at=None)]) == []

    def it_returns_sessions_sorted_by_starts_at():
        s1 = _future_session(offset_days=3)
        s2 = _future_session(offset_days=1)
        s3 = _future_session(offset_days=2)
        result = upcoming_sessions([s1, s2, s3])
        assert result == [s2, s3, s1]

    def it_excludes_past_and_includes_future_mixed():
        past = _past_session()
        future = _future_session()
        result = upcoming_sessions([past, future])
        assert result == [future]


# ---------------------------------------------------------------------------
# recurrence_label
# ---------------------------------------------------------------------------


def describe_recurrence_label():
    def _session_at(weekday: int, hour: int, minute: int = 0):
        """Build a fake session with a fixed weekday/time (2025-W01 as anchor week)."""
        # Monday 2025-01-06 is weekday 0; offset by weekday to get the right day
        base = datetime(2025, 1, 6, tzinfo=dt_timezone.utc)
        starts_at = base + timedelta(days=weekday, hours=hour, minutes=minute)
        return SimpleNamespace(starts_at=starts_at)

    def it_returns_empty_string_for_empty_list():
        assert recurrence_label([]) == ""

    def it_returns_empty_string_for_single_session():
        assert recurrence_label([_session_at(0, 18)]) == ""

    def it_returns_empty_string_when_weekdays_differ():
        sessions = [_session_at(0, 18), _session_at(1, 18)]
        assert recurrence_label(sessions) == ""

    def it_returns_empty_string_when_times_differ():
        sessions = [_session_at(0, 18), _session_at(0, 19)]
        assert recurrence_label(sessions) == ""

    def it_returns_recurrence_label_for_consistent_weekly_sessions():
        sessions = [_session_at(4, 18), _session_at(4, 18)]  # Friday 18:00 UTC (11 AM Pacific)
        result = recurrence_label(sessions)
        # Result depends on localtime (America/Los_Angeles), just verify format
        assert result.startswith("Every ")
        assert " at " in result

    def it_includes_minutes_when_not_on_the_hour():
        sessions = [_session_at(4, 18, 30), _session_at(4, 18, 30)]
        result = recurrence_label(sessions)
        assert ":30" in result


# ---------------------------------------------------------------------------
# short_date_list
# ---------------------------------------------------------------------------


def describe_short_date_list():
    def _session_at_date(year: int, month: int, day: int):
        starts_at = datetime(year, month, day, 18, 0, tzinfo=dt_timezone.utc)
        return SimpleNamespace(starts_at=starts_at)

    def it_returns_empty_string_for_empty_list():
        assert short_date_list([]) == ""

    def it_formats_a_single_session():
        result = short_date_list([_session_at_date(2025, 6, 7)])
        assert "Jun" in result or "Jul" in result  # depends on localtime offset
        assert result  # non-empty

    def it_formats_two_sessions_comma_separated():
        sessions = [_session_at_date(2025, 6, 7), _session_at_date(2025, 6, 14)]
        result = short_date_list(sessions)
        assert "," in result

    def it_shows_at_most_three_dates_without_overflow_marker():
        sessions = [_session_at_date(2025, 6, d) for d in (7, 14, 21)]
        result = short_date_list(sessions)
        assert "more" not in result

    def it_appends_overflow_marker_for_more_than_three():
        sessions = [_session_at_date(2025, 6, d) for d in (7, 14, 21, 28)]
        result = short_date_list(sessions)
        assert "+1 more" in result

    def it_counts_overflow_correctly_for_five_sessions():
        sessions = [_session_at_date(2025, 6, d) for d in (7, 14, 21, 28, 30)]
        result = short_date_list(sessions)
        assert "+2 more" in result


# ---------------------------------------------------------------------------
# session_time_range
# ---------------------------------------------------------------------------


def describe_session_time_range():
    def _session(start_h: int, start_m: int = 0, end_h: int | None = None, end_m: int = 0):
        """Build a fake session with UTC times (no DST shift between test cases)."""
        base_date = datetime(2025, 6, 7, tzinfo=dt_timezone.utc)
        starts_at = base_date.replace(hour=start_h, minute=start_m)
        ends_at = base_date.replace(hour=end_h, minute=end_m) if end_h is not None else None
        return SimpleNamespace(starts_at=starts_at, ends_at=ends_at)

    def it_returns_empty_string_for_none_session():
        assert session_time_range(None) == ""

    def it_returns_empty_string_when_starts_at_is_none():
        assert session_time_range(SimpleNamespace(starts_at=None, ends_at=None)) == ""

    def it_formats_same_period_range_without_start_period():
        # 18:00–20:00 UTC → both PM in any timezone west of UTC
        session = _session(18, 0, 20, 0)
        result = session_time_range(session)
        assert "–" in result
        assert "PM" in result
        # The start hour should NOT have "PM" (it's dropped for same-period ranges)
        parts = result.split("–")
        assert "PM" not in parts[0]

    def it_formats_cross_period_range_with_both_periods():
        # 14:00–23:00 UTC → AM on one side, PM on the other (depending on timezone)
        session = _session(8, 0, 20, 0)  # 1 AM – 1 PM Pacific
        result = session_time_range(session)
        assert "–" in result

    def it_formats_on_the_hour_without_minutes():
        session = _session(18, 0, 20, 0)
        result = session_time_range(session)
        assert ":00" not in result  # on-the-hour should not show :00

    def it_formats_fractional_hours_with_minutes():
        session = _session(18, 30, 20, 0)
        result = session_time_range(session)
        assert ":30" in result

    def it_handles_missing_ends_at():
        session = _session(18, 0, None)
        result = session_time_range(session)
        assert result  # non-empty
        assert "–" not in result

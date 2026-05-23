"""Specs for classes/templatetags/classes_tags.py."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from classes.templatetags.classes_tags import (
    cents_as_dollars,
    cents_as_price,
    duration_words,
    initials,
    member_price_cents,
    session_duration_words,
    spots_class,
    total_session_minutes,
)


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

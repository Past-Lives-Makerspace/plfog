"""Specs for the natural-language ``when`` parser behind the Discord ``/create`` command.

Every row of the spec's grammar table, every typed error, the overnight roll, and the
range-beats-duration rule — all against a frozen ``now`` (Tue 2026-08-25 10:00 local),
so weekday math is deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from membership.when_text import MAX_DAYS_AHEAD, WhenError, parse_when

# Tuesday morning, site-local, naive — the injected clock for every spec.
_NOW = datetime(2026, 8, 25, 10, 0)


def _parse(text: str, *, duration_minutes: int = 60, now: datetime = _NOW):
    return parse_when(text, duration_minutes=duration_minutes, now=now)


def describe_parse_when():
    def describe_relative_days():
        def it_parses_next_friday_with_a_time():
            result = _parse("next friday 6pm")
            assert result.error is None
            assert result.start == datetime(2026, 8, 28, 18, 0)
            assert result.end == datetime(2026, 8, 28, 19, 0)  # default 60-minute length

        def it_treats_bare_and_this_weekday_like_next():
            assert _parse("friday 6pm").start == datetime(2026, 8, 28, 18, 0)
            assert _parse("this friday 6pm").start == datetime(2026, 8, 28, 18, 0)

        def it_keeps_todays_weekday_today_when_the_time_is_still_ahead():
            # Tuesday morning: "tuesday 6pm" means today, not next week.
            assert _parse("tuesday 6pm").start == datetime(2026, 8, 25, 18, 0)

        def it_rolls_todays_weekday_a_week_when_the_time_has_passed():
            assert _parse("tuesday 6pm", now=datetime(2026, 8, 25, 20, 0)).start == datetime(2026, 9, 1, 18, 0)

        def it_parses_tomorrow():
            assert _parse("tomorrow 7pm").start == datetime(2026, 8, 26, 19, 0)

        def it_parses_today_and_tonight():
            assert _parse("today 8pm").start == datetime(2026, 8, 25, 20, 0)
            assert _parse("tonight 8pm").start == datetime(2026, 8, 25, 20, 0)

        def it_treats_a_bare_time_as_today():
            assert _parse("6pm").start == datetime(2026, 8, 25, 18, 0)

    def describe_explicit_dates():
        def it_parses_an_iso_date_with_a_time():
            assert _parse("2026-08-29 6pm").start == datetime(2026, 8, 29, 18, 0)

        def it_parses_month_name_forms():
            assert _parse("aug 29 6pm").start == datetime(2026, 8, 29, 18, 0)
            assert _parse("august 29 18:00").start == datetime(2026, 8, 29, 18, 0)

        def it_parses_a_slash_date():
            assert _parse("8/29 6pm").start == datetime(2026, 8, 29, 18, 0)

        def it_rolls_a_yearless_past_month_day_to_next_year():
            # January 5 has passed in August; with no year it means the next one.
            assert _parse("jan 5 6pm").start == datetime(2027, 1, 5, 18, 0)

    def describe_time_ranges():
        def it_gives_a_bare_range_start_the_ends_meridiem():
            result = _parse("tomorrow 7-9pm")
            assert (result.start, result.end) == (datetime(2026, 8, 26, 19, 0), datetime(2026, 8, 26, 21, 0))

        def it_parses_the_to_form():
            result = _parse("tomorrow 7pm to 9pm")
            assert (result.start, result.end) == (datetime(2026, 8, 26, 19, 0), datetime(2026, 8, 26, 21, 0))

        def it_beats_the_duration_option():
            result = _parse("tomorrow 7-9pm", duration_minutes=15)
            assert result.end == datetime(2026, 8, 26, 21, 0)

        def it_rolls_an_overnight_range_to_the_next_day():
            result = _parse("saturday 9pm-1am")
            assert result.start == datetime(2026, 8, 29, 21, 0)
            assert result.end == datetime(2026, 8, 30, 1, 0)

        def it_reads_a_wrapping_bare_start_as_the_opposite_meridiem():
            # "11-1pm" naturally means 11 AM to 1 PM, not 11 PM overnight.
            result = _parse("tomorrow 11-1pm")
            assert (result.start, result.end) == (datetime(2026, 8, 26, 11, 0), datetime(2026, 8, 26, 13, 0))

        def it_parses_a_24_hour_range():
            result = _parse("tomorrow 18:00-20:30")
            assert (result.start, result.end) == (datetime(2026, 8, 26, 18, 0), datetime(2026, 8, 26, 20, 30))

        def it_ignores_a_bare_range_start_with_no_meridiem_anywhere():
            # "7-9" is ambiguous — not accepted as a time, so the whole phrase is rejected.
            assert _parse("tomorrow 7-9").error is WhenError.UNPARSEABLE

    def describe_durations():
        def it_applies_the_duration_when_no_end_is_given():
            result = _parse("tomorrow 6pm", duration_minutes=120)
            assert result.end - result.start == timedelta(minutes=120)

    def describe_typed_errors():
        def it_requires_a_time_of_day():
            assert _parse("next friday").error is WhenError.NO_TIME
            assert _parse("2026-08-29").error is WhenError.NO_TIME

        def it_rejects_word_times_as_unparseable():
            assert _parse("sep 12 noon").error is WhenError.UNPARSEABLE

        def it_rejects_gibberish():
            assert _parse("whenever works 6pm").error is WhenError.UNPARSEABLE
            assert _parse("???").error is WhenError.UNPARSEABLE

        def it_rejects_a_blank_phrase():
            assert _parse("").error is WhenError.UNPARSEABLE
            assert _parse("   ").error is WhenError.UNPARSEABLE

        def it_rejects_a_past_time_today():
            assert _parse("today 8am").error is WhenError.IN_PAST

        def it_rejects_an_explicit_past_date():
            assert _parse("aug 1 2026 6pm").error is WhenError.IN_PAST
            assert _parse("yesterday 6pm").error is WhenError.UNPARSEABLE  # no relative past forms

        def it_rejects_a_start_more_than_a_year_out():
            assert _parse("2031-08-29 6pm").error is WhenError.TOO_FAR
            just_inside = _NOW + timedelta(days=MAX_DAYS_AHEAD - 1)
            assert _parse(f"{just_inside:%Y-%m-%d} 6pm").error is None

    def describe_almost_times():
        def it_rejects_an_impossible_am_pm_hour():
            # "13pm" matches the time-token shape but no time format — the phrase fails whole.
            assert _parse("tomorrow 13pm").error is WhenError.UNPARSEABLE

        def it_rejects_a_range_whose_end_has_no_meridiem_for_a_bare_start():
            # "7-18:00": the bare 7 has no am/pm to inherit, and the leftover day phrase
            # ("tomorrow 7-") is unreadable once the 18:00 is peeled off as a single time.
            assert _parse("tomorrow 7-18:00").error is WhenError.UNPARSEABLE

        def it_rejects_a_range_with_an_impossible_bare_start():
            assert _parse("tomorrow 13-2pm").error is WhenError.UNPARSEABLE

        def it_rejects_a_range_with_an_impossible_end():
            assert _parse("tomorrow 7-13pm").error is WhenError.UNPARSEABLE

        def it_prefers_the_shorter_overnight_when_both_readings_wrap():
            # "11-1am": both 11 AM and 11 PM overshoot 1 AM — 11 PM (a 2-hour overnight)
            # is the natural reading, not a 14-hour 11 AM event.
            result = _parse("tomorrow 11-1am")
            assert result.start == datetime(2026, 8, 26, 23, 0)
            assert result.end == datetime(2026, 8, 27, 1, 0)

    def describe_normalization():
        def it_is_case_and_whitespace_insensitive():
            result = _parse("  Next   FRIDAY   6 PM ")
            assert result.start == datetime(2026, 8, 28, 18, 0)

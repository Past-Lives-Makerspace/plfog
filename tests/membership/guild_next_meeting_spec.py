"""BDD specs for guild meeting-date computation (_compute_next_meeting) + Guild.next_meeting_at."""

from __future__ import annotations

from datetime import date

from membership.models import Guild, _compute_next_meeting
from tests.membership.factories import GuildFactory

JUL1 = date(2026, 7, 1)  # a Wednesday (Python weekday 2)


def describe_compute_next_meeting():
    def it_returns_none_when_marked_tba():
        assert (
            _compute_next_meeting(JUL1, cadence="monthly", weekday=3, week_of_month=3, override=None, is_tba=True)
            is None
        )

    def it_uses_a_future_override_over_the_cadence():
        override = date(2026, 7, 9)
        assert (
            _compute_next_meeting(JUL1, cadence="weekly", weekday=4, week_of_month=None, override=override, is_tba=False)
            == override
        )

    def it_ignores_a_past_override_and_falls_back_to_cadence():
        past = date(2026, 6, 1)
        assert _compute_next_meeting(
            JUL1, cadence="weekly", weekday=4, week_of_month=None, override=past, is_tba=False
        ) == date(2026, 7, 3)

    def it_finds_the_next_weekly_weekday():
        assert _compute_next_meeting(
            JUL1, cadence="weekly", weekday=4, week_of_month=None, override=None, is_tba=False
        ) == date(2026, 7, 3)

    def it_returns_today_when_today_is_the_weekly_day():
        assert (
            _compute_next_meeting(JUL1, cadence="weekly", weekday=2, week_of_month=None, override=None, is_tba=False)
            == JUL1
        )

    def it_finds_the_nth_weekday_of_this_month():
        assert _compute_next_meeting(
            JUL1, cadence="monthly", weekday=3, week_of_month=3, override=None, is_tba=False
        ) == date(2026, 7, 16)

    def it_rolls_to_next_month_when_the_date_has_passed():
        assert _compute_next_meeting(
            date(2026, 7, 20), cadence="monthly", weekday=3, week_of_month=3, override=None, is_tba=False
        ) == date(2026, 8, 20)

    def it_supports_the_last_weekday_of_the_month():
        assert _compute_next_meeting(
            JUL1, cadence="monthly", weekday=3, week_of_month=5, override=None, is_tba=False
        ) == date(2026, 7, 30)

    def it_returns_none_when_no_weekday_is_set():
        assert (
            _compute_next_meeting(JUL1, cadence="weekly", weekday=None, week_of_month=None, override=None, is_tba=False)
            is None
        )

    def it_returns_none_for_no_cadence():
        assert (
            _compute_next_meeting(JUL1, cadence="none", weekday=3, week_of_month=3, override=None, is_tba=False) is None
        )


def describe_guild_next_meeting_at():
    def it_computes_a_date_from_the_guilds_config(db):
        guild = GuildFactory(
            meeting_cadence=Guild.MeetingCadence.MONTHLY, meeting_weekday=3, meeting_week_of_month=3
        )
        # Computed against real "today"; a monthly recurrence always has a next occurrence.
        assert guild.next_meeting_at is not None

    def it_is_none_when_marked_tba(db):
        guild = GuildFactory(
            meeting_is_tba=True, meeting_cadence=Guild.MeetingCadence.WEEKLY, meeting_weekday=3
        )
        assert guild.next_meeting_at is None

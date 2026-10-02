"""BDD specs for the CommunityEvent model: constraints, queryset, monthly recurrence,
display properties, and the one-shot announce()."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory


def _aware(y: int, m: int, d: int, hour: int = 12) -> datetime:
    """A timezone-aware datetime at ``hour`` local time (noon by default, so the local
    date never rolls over relative to UTC)."""
    return timezone.make_aware(datetime(y, m, d, hour, 0))


def describe_CommunityEvent():
    def describe_constraints():
        def it_rejects_end_equal_to_or_before_start(db):
            guild = GuildFactory()
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="Bad",
                    event_type=CommunityEvent.EventType.GUILD_MEETING,
                    guild=guild,
                    starts_at=_aware(2026, 7, 11),
                    ends_at=_aware(2026, 7, 11),
                )

        def it_allows_end_after_start(db):
            guild = GuildFactory()
            event = CommunityEvent.objects.create(
                title="Good",
                event_type=CommunityEvent.EventType.GUILD_MEETING,
                guild=guild,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert event.pk is not None

        def it_requires_a_guild_for_a_guild_meeting(db):
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="No guild",
                    event_type=CommunityEvent.EventType.GUILD_MEETING,
                    guild=None,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )

        def it_allows_a_guild_on_a_general_event(db):
            # The fourth shape (#505): a guild hosting something open to everyone.
            guild = GuildFactory()
            event = CommunityEvent.objects.create(
                title="Guild open house",
                event_type=CommunityEvent.EventType.COMMUNITY,
                guild=guild,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert event.pk is not None

        def it_allows_a_general_event_with_no_guild(db):
            event = CommunityEvent.objects.create(
                title="Potluck",
                event_type=CommunityEvent.EventType.COMMUNITY,
                guild=None,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert event.pk is not None

        def it_rejects_a_guild_on_a_lead_meeting(db):
            guild = GuildFactory()
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="Lead meeting with guild",
                    event_type=CommunityEvent.EventType.LEAD_MEETING,
                    guild=guild,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )

        def it_allows_the_four_valid_shapes(db):
            guild = GuildFactory()
            CommunityEventFactory(guild_meeting=True, guild=guild)
            CommunityEventFactory(community=True)
            CommunityEventFactory(lead_meeting=True)
            CommunityEventFactory(guild_hosted=True, guild=guild)
            assert CommunityEvent.objects.count() == 4

    def describe_meta_and_str():
        def it_orders_by_starts_at_ascending(db):
            late = CommunityEventFactory(starts_at=_aware(2026, 9, 1, 18), ends_at=_aware(2026, 9, 1, 20))
            early = CommunityEventFactory(starts_at=_aware(2026, 7, 1, 18), ends_at=_aware(2026, 7, 1, 20))
            assert list(CommunityEvent.objects.all()) == [early, late]

        def it_shows_the_guild_name_for_a_guild_event(db):
            guild = GuildFactory(name="Metal Guild")
            event = CommunityEventFactory(guild=guild, title="Forge Night")
            assert "Metal Guild" in str(event)
            assert "Forge Night" in str(event)

        def it_shows_site_wide_for_a_community_event(db):
            event = CommunityEventFactory(community=True, title="Potluck")
            assert "Site-wide" in str(event)

    def describe_queryset():
        def it_upcoming_excludes_a_past_nonrecurring_event(db):
            now = timezone.now()
            CommunityEventFactory(starts_at=now - timedelta(days=3), ends_at=now - timedelta(days=2))
            assert not CommunityEvent.objects.upcoming().exists()

        def it_upcoming_includes_a_future_nonrecurring_event(db):
            now = timezone.now()
            event = CommunityEventFactory(starts_at=now + timedelta(days=2), ends_at=now + timedelta(days=2, hours=2))
            assert list(CommunityEvent.objects.upcoming()) == [event]

        def it_upcoming_includes_a_past_anchored_monthly_series(db):
            now = timezone.now()
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=now - timedelta(days=400),
                ends_at=now - timedelta(days=400) + timedelta(hours=2),
            )
            assert list(CommunityEvent.objects.upcoming()) == [event]

        def it_candidates_for_window_includes_a_past_anchored_monthly(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=_aware(2020, 1, 11, 18),
                ends_at=_aware(2020, 1, 11, 20),
            )
            window = CommunityEvent.objects.candidates_for_window(date(2026, 7, 1), date(2026, 7, 31))
            assert list(window) == [event]

        def it_candidates_for_window_excludes_a_nonrecurring_event_outside(db):
            CommunityEventFactory(starts_at=_aware(2026, 1, 11, 18), ends_at=_aware(2026, 1, 11, 20))
            window = CommunityEvent.objects.candidates_for_window(date(2026, 7, 1), date(2026, 7, 31))
            assert not window.exists()

        def it_for_guild_and_site_wide_partition(db):
            guild = GuildFactory()
            mine = CommunityEventFactory(guild=guild)
            CommunityEventFactory(community=True)
            assert list(CommunityEvent.objects.for_guild(guild)) == [mine]
            assert list(CommunityEvent.objects.site_wide().filter(event_type="community"))

    def describe_recurrence():
        def it_occurrence_ordinal_returns_2_for_a_2nd_saturday(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert event._occurrence_ordinal() == 2

        def it_occurrence_ordinal_returns_minus_1_for_a_5th_weekday(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 5, 30, 18), ends_at=_aware(2026, 5, 30, 20))
            assert event._occurrence_ordinal() == -1

        def it_occurrences_in_returns_the_start_for_a_nonrecurring_in_window(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            occ = event.occurrences_in(date(2026, 7, 1), date(2026, 7, 31))
            assert occ == [event.starts_at]

        def it_occurrences_in_returns_empty_out_of_window(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert event.occurrences_in(date(2026, 8, 1), date(2026, 8, 31)) == []

        def it_occurrences_in_expands_monthly_same_nth_weekday(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday of July
                ends_at=_aware(2026, 7, 11, 20),
            )
            occ = event.occurrences_in(date(2026, 8, 1), date(2026, 10, 31))
            assert len(occ) == 3
            for d in occ:
                local = timezone.localtime(d)
                assert local.weekday() == 5  # Saturday
                assert (local.day - 1) // 7 + 1 == 2  # the 2nd Saturday
                assert local.hour == 18  # time-of-day preserved
            assert [timezone.localtime(d).month for d in occ] == [8, 9, 10]

        def it_occurrences_in_expands_every_2_months_on_the_interval_grid(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.EVERY_2_MONTHS,
                starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday of July
                ends_at=_aware(2026, 7, 11, 20),
            )
            occ = event.occurrences_in(date(2026, 7, 1), date(2026, 12, 31))
            # July anchor, then Sep, then Nov — every other month, never August/October.
            assert [timezone.localtime(d).month for d in occ] == [7, 9, 11]

        def it_occurrences_in_expands_yearly_same_month_and_weekday(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.YEARLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            occ = event.occurrences_in(date(2026, 1, 1), date(2028, 12, 31))
            assert [timezone.localtime(d).year for d in occ] == [2026, 2027, 2028]
            assert all(timezone.localtime(d).month == 7 for d in occ)

        def it_occurrences_in_expands_semi_monthly_to_two_per_month(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.SEMI_MONTHLY,
                starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday → also the 4th Saturday
                ends_at=_aware(2026, 7, 11, 20),
            )
            occ = event.occurrences_in(date(2026, 7, 1), date(2026, 7, 31))
            days = [timezone.localtime(d).day for d in occ]
            assert days == [11, 25]  # 2nd and 4th Saturday

        def it_occurrences_in_never_spills_a_second_date_into_the_next_month(db):
            # Jul 23 is the 4th Thursday; two weeks on is Aug 6. The iCal rule (BYDAY=4TH) has no
            # such date, and listing it depended on whether the window began in July.
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.SEMI_MONTHLY,
                starts_at=_aware(2026, 7, 23, 18),
                ends_at=_aware(2026, 7, 23, 20),
            )
            from_july = event.occurrences_in(date(2026, 7, 1), date(2026, 8, 31))
            from_august = event.occurrences_in(date(2026, 8, 1), date(2026, 8, 31))
            assert [timezone.localtime(d).date() for d in from_july] == [date(2026, 7, 23), date(2026, 8, 27)]
            assert [timezone.localtime(d).date() for d in from_august] == [date(2026, 8, 27)]

        def describe_ical_rrule():
            def it_is_blank_for_a_nonrecurring_event(db):
                event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
                assert event.ical_rrule() == ""

            def it_emits_monthly_byday(db):
                event = CommunityEventFactory(
                    recurrence=CommunityEvent.Recurrence.MONTHLY,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )
                assert event.ical_rrule() == "FREQ=MONTHLY;BYDAY=2SA"

            def it_emits_an_interval_for_every_n_months(db):
                event = CommunityEventFactory(
                    recurrence=CommunityEvent.Recurrence.EVERY_3_MONTHS,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )
                assert event.ical_rrule() == "FREQ=MONTHLY;INTERVAL=3;BYDAY=2SA"

            def it_emits_a_yearly_rule_pinned_to_the_month(db):
                event = CommunityEventFactory(
                    recurrence=CommunityEvent.Recurrence.YEARLY,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )
                assert event.ical_rrule() == "FREQ=YEARLY;BYMONTH=7;BYDAY=2SA"

            def it_lists_both_weeks_for_twice_a_month(db):
                event = CommunityEventFactory(
                    recurrence=CommunityEvent.Recurrence.SEMI_MONTHLY,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )
                assert event.ical_rrule() == "FREQ=MONTHLY;BYDAY=2SA,4SA"

    def describe_badge_label():
        def it_reads_public_event_for_a_public_audience(db):
            event = CommunityEventFactory(google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC)
            assert event.badge_label == "Public event"

        def it_reads_member_event_for_a_member_audience(db):
            event = CommunityEventFactory(google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER)
            assert event.badge_label == "Member event"

        def it_keeps_the_guild_lead_meetings_own_name(db):
            # The cross-guild leadership meeting is a recognisable thing; badging it "Member
            # event" would hide what it is.
            event = CommunityEventFactory(
                lead_meeting=True, google_calendar_target=CommunityEvent.GoogleCalendarTarget.MEMBER
            )
            assert event.badge_label == "Guild Lead Meeting"

        def it_names_a_lead_meeting_the_same_way_whatever_calendar_it_is_on(db):
            event = CommunityEventFactory(
                lead_meeting=True, google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC
            )
            assert event.badge_label == "Guild Lead Meeting"

        def it_keeps_studio_hours_own_name_rather_than_calling_them_an_event(db):
            # Ambient standing hours are not a happening (CONTEXT.md), so "Public event"
            # would misdescribe what a member is looking at on the calendar.
            event = CommunityEventFactory(
                studio_hours=True, google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC
            )
            assert event.badge_label == "Studio hours"

        def it_never_calls_a_self_naming_type_an_event(db):
            # The two exceptions live in one list; this is the rule that list exists for.
            for event_type in CommunityEvent.SELF_NAMING_TYPES:
                event = CommunityEventFactory(
                    event_type=event_type,
                    guild=GuildFactory() if event_type == CommunityEvent.EventType.STUDIO_HOURS else None,
                )
                assert "event" not in event.badge_label.lower()

        def it_badges_a_public_guild_meeting_as_a_public_event(db):
            event = CommunityEventFactory(
                guild_meeting=True, google_calendar_target=CommunityEvent.GoogleCalendarTarget.PUBLIC
            )
            assert event.badge_label == "Public event"

    def describe_for_member():
        def it_includes_a_public_event_a_guild_hosts_for_a_member_outside_that_guild(db):
            from tests.membership.factories import MemberFactory

            outsider = MemberFactory()
            event = CommunityEventFactory(guild_hosted=True, guild=GuildFactory())
            assert list(CommunityEvent.objects.for_member(outsider)) == [event]

        def it_excludes_another_guilds_meeting(db):
            from tests.membership.factories import MemberFactory

            outsider = MemberFactory()
            CommunityEventFactory(guild_meeting=True, guild=GuildFactory())
            assert list(CommunityEvent.objects.for_member(outsider)) == []

        def it_excludes_another_guilds_studio_hours(db):
            from tests.membership.factories import MemberFactory

            outsider = MemberFactory()
            CommunityEventFactory(studio_hours=True, guild=GuildFactory())
            assert list(CommunityEvent.objects.for_member(outsider)) == []

        def it_includes_the_members_own_guilds_meeting_exactly_once(db):
            from tests.membership.factories import GuildMembershipFactory, MemberFactory

            member = MemberFactory()
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=member)
            event = CommunityEventFactory(guild_meeting=True, guild=guild)
            assert list(CommunityEvent.objects.for_member(member)) == [event]

        def it_lists_a_hosted_event_once_for_a_member_of_the_hosting_guild(db):
            # Both sides of the OR match here; without distinct() the join duplicates the row.
            from tests.membership.factories import GuildMembershipFactory, MemberFactory

            member = MemberFactory()
            guild = GuildFactory()
            GuildMembershipFactory(guild=guild, member=member)
            event = CommunityEventFactory(guild_hosted=True, guild=guild)
            assert list(CommunityEvent.objects.for_member(member)) == [event]

    def describe_display():
        def it_absolute_url_is_prefixed_with_member_base_url(db, settings):
            settings.MEMBER_BASE_URL = "https://members.test"
            event = CommunityEventFactory()
            assert event.absolute_url.startswith("https://members.test")

        def it_when_display_shows_a_range_without_repeats_for_nonrecurring(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            text = event.when_display
            assert "–" in text
            assert "Repeats monthly" not in text

        def it_when_display_appends_the_recurrence_label(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.EVERY_3_MONTHS,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert "Every 3 months" in event.when_display

        def it_when_display_for_names_one_date_at_the_events_length_without_the_cadence(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert event.when_display_for(_aware(2026, 7, 25, 18)) == "Sat, Jul 25 · 6:00 PM – 8:00 PM"

    def describe_reminder_sends():
        def it_sends_a_one_off_reminder_its_days_before_the_start(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            sends = event.reminder_sends(7, _aware(2026, 7, 1, 0), _aware(2026, 7, 11, 23))
            assert sends == [(_aware(2026, 7, 4, 18), _aware(2026, 7, 11, 18))]

        def it_leaves_out_a_send_outside_the_window(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert (
                event.reminder_sends(
                    7,
                    _aware(
                        2026,
                        7,
                        4,
                        18,
                    )
                    + timedelta(minutes=1),
                    _aware(2026, 7, 11, 23),
                )
                == []
            )
            assert event.reminder_sends(7, _aware(2026, 7, 1, 0), _aware(2026, 7, 4, 17)) == []

        def it_sends_before_each_date_of_a_series(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            sends = event.reminder_sends(1, _aware(2026, 7, 12, 0), _aware(2026, 7, 31, 23))
            assert [date_start for _send, date_start in sends] == [
                _aware(2026, 7, 18, 18),
                _aware(2026, 7, 25, 18),
                _aware(2026, 8, 1, 18),
            ]
            assert all(date_start - send == timedelta(days=1) for send, date_start in sends)

        def it_keeps_the_wall_clock_time_across_daylight_saving(db):
            # Nov 1, 2026 is the fall-back change, between the send and the date.
            event = CommunityEventFactory(starts_at=_aware(2026, 11, 5, 18), ends_at=_aware(2026, 11, 5, 20))
            ((send, _date),) = event.reminder_sends(7, _aware(2026, 10, 28, 0), _aware(2026, 10, 30, 0))
            assert timezone.localtime(send).hour == 18

        def it_finds_a_date_just_after_midnight_from_a_utc_now_across_fall_back(db):
            # Nov 1 2026 falls back; a day before Nov 2 at 12:30 AM is Nov 1 at 12:30 AM, 25
            # real hours earlier, which a real-time date window slid past.
            start = timezone.make_aware(datetime(2026, 11, 2, 0, 30))
            event = CommunityEventFactory(starts_at=start, ends_at=start + timedelta(hours=1))
            send = timezone.make_aware(datetime(2026, 11, 1, 0, 30))
            frm = send.astimezone(UTC) - timedelta(minutes=5)
            assert event.reminder_sends(1, frm, frm + timedelta(minutes=15)) == [(send, start)]

        def it_finds_a_late_evening_date_from_a_utc_now_across_spring_forward(db):
            # Mar 14 2027 springs forward; a day before 11:30 PM that night is 23 real hours.
            start = timezone.make_aware(datetime(2027, 3, 14, 23, 30))
            event = CommunityEventFactory(starts_at=start, ends_at=start + timedelta(minutes=30))
            send = timezone.make_aware(datetime(2027, 3, 13, 23, 30))
            frm = send.astimezone(UTC) - timedelta(minutes=5)
            assert event.reminder_sends(1, frm, frm + timedelta(minutes=15)) == [(send, start)]

        def it_treats_zero_days_as_the_moment_a_date_begins(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert event.reminder_sends(0, _aware(2026, 7, 11, 17), _aware(2026, 7, 11, 19)) == [
                (_aware(2026, 7, 11, 18), _aware(2026, 7, 11, 18))
            ]

    def describe_next_reminder_send():
        def it_finds_a_one_off_send_still_ahead(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert event.next_reminder_send(3, after=_aware(2026, 7, 1)) == (
                _aware(2026, 7, 8, 18),
                _aware(2026, 7, 11, 18),
            )

        def it_has_none_once_a_one_off_send_has_passed(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert event.next_reminder_send(3, after=_aware(2026, 7, 9)) is None

        def it_skips_a_series_date_that_is_too_close_for_the_next(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            # Jul 15: the Jul 18 date is 3 days off, too close for a 7-day reminder.
            assert event.next_reminder_send(7, after=_aware(2026, 7, 15)) == (
                _aware(2026, 7, 18, 18),
                _aware(2026, 7, 25, 18),
            )

        def it_finds_a_yearly_series_next_send_a_year_out(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.YEARLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            found = event.next_reminder_send(7, after=_aware(2026, 7, 10))
            assert found is not None
            assert timezone.localtime(found[1]).year == 2027

    def describe_announce():
        def it_picks_guild_published_for_a_guild_event(db):
            guild = GuildFactory()
            event = CommunityEventFactory(guild=guild)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.args[0] == "event.guild_published"

        def it_picks_community_published_for_a_community_event(db):
            event = CommunityEventFactory(community=True)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.args[0] == "event.community_published"

        def it_picks_lead_meeting_published_for_a_lead_meeting(db):
            event = CommunityEventFactory(lead_meeting=True)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.args[0] == "event.lead_meeting_published"

        def it_refuses_to_name_a_key_for_studio_hours(db):
            # The dict this replaced raised KeyError here. Answering "the guild's members"
            # instead would be a silent wrong answer about who gets emailed.
            event = CommunityEventFactory(studio_hours=True)
            with pytest.raises(ValueError, match="never announced"):
                event.announce_event_key()

        def it_picks_guild_published_for_a_public_event_a_guild_hosts(db):
            # The guild decides the audience, not the type: a guild's open house must not
            # email the whole membership (#505).
            event = CommunityEventFactory(guild_hosted=True, guild=GuildFactory())
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.args[0] == "event.guild_published"

        def it_passes_the_guild_in_context_and_an_absolute_url(db, settings):
            settings.MEMBER_BASE_URL = "https://members.test"
            guild = GuildFactory()
            event = CommunityEventFactory(guild=guild)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["context"]["guild"] == guild
            assert kwargs["url"].startswith("https://members.test")
            assert kwargs["period"] == f"event:{event.pk}:published"

        def it_carries_no_guild_for_a_site_wide_event(db):
            event = CommunityEventFactory(community=True)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.kwargs["context"]["guild"] is None

        def it_is_idempotent_via_period(db):
            from core.models import EventDelivery

            event = CommunityEventFactory(community=True)
            event.announce()
            after_first = EventDelivery.objects.filter(period=f"event:{event.pk}:published").count()
            event.announce()
            after_second = EventDelivery.objects.filter(period=f"event:{event.pk}:published").count()
            assert after_first >= 1
            assert after_first == after_second


def describe_public_url_and_qr():
    def it_public_url_is_the_event_page_under_member_base_url(db, settings):
        settings.MEMBER_BASE_URL = "https://members.test"
        event = CommunityEventFactory()
        assert event.public_url == f"https://members.test/events/{event.pk}/"

    def it_qr_url_and_absolute_url_equal_the_public_url(db, settings):
        # Events have no slug, so all three resolve to the same stable pk URL.
        settings.MEMBER_BASE_URL = "https://members.test"
        event = CommunityEventFactory()
        assert event.qr_url == event.public_url
        assert event.absolute_url == event.public_url

    def it_absolute_url_points_at_the_event_page_not_the_calendar(db):
        event = CommunityEventFactory()
        assert f"/events/{event.pk}/" in event.absolute_url
        assert "/calendar/" not in event.absolute_url

    def it_qr_svg_is_scalable(db):
        svg = CommunityEventFactory().qr_svg()
        assert "<svg" in svg
        assert "viewBox" in svg  # scales to its box rather than drawing tiny

    def it_qr_png_bytes_has_a_png_magic_header(db):
        assert CommunityEventFactory().qr_png_bytes().startswith(b"\x89PNG")


def describe_ics_document():
    def it_wraps_a_single_vevent_in_a_vcalendar(db):
        event = CommunityEventFactory(
            community=True,
            title="Potluck",
            location="Common Area",
            description="Bring a dish.",
            starts_at=_aware(2026, 7, 15, 18),
            ends_at=_aware(2026, 7, 15, 20),
        )
        doc = event.ics_document(event.starts_at)
        assert doc.startswith("BEGIN:VCALENDAR")
        assert doc.rstrip().endswith("END:VCALENDAR")
        assert doc.count("BEGIN:VEVENT") == 1
        assert doc.count("END:VEVENT") == 1
        assert f"UID:community-{event.pk}@pastlives" in doc
        assert "DTSTART;TZID=America/Los_Angeles:20260715T180000" in doc
        assert "DTEND;TZID=America/Los_Angeles:20260715T200000" in doc
        assert "SUMMARY:Potluck" in doc

    def it_omits_rrule_for_a_non_recurring_event(db):
        event = CommunityEventFactory(recurrence=CommunityEvent.Recurrence.NONE)
        doc = event.ics_document(event.starts_at)
        # The time zone block carries its own yearly rules; the event carries none.
        assert "RRULE" not in doc.split("BEGIN:VEVENT")[1]

    def it_emits_exactly_one_rrule_for_a_recurring_event(db):
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday
            ends_at=_aware(2026, 7, 11, 20),
        )
        doc = event.ics_document(event.starts_at)
        assert doc.split("BEGIN:VEVENT")[1].count("RRULE:") == 1
        assert f"RRULE:{event.ical_rrule()}" in doc

    def it_ical_escapes_location_and_description(db):
        event = CommunityEventFactory(
            community=True,
            location="Room A, B; C",
            description="Line one\nLine two",
        )
        doc = event.ics_document(event.starts_at)
        assert "LOCATION:Room A\\, B\\; C" in doc
        assert "DESCRIPTION:Line one\\nLine two" in doc

    def it_includes_a_url_property_when_video_url_is_set(db):
        event = CommunityEventFactory(community=True, video_url="https://meet.google.com/abc-defg-hij")
        doc = event.ics_document(event.starts_at)
        assert "URL:https://meet.google.com/abc-defg-hij" in doc

    def it_omits_url_when_video_url_is_blank(db):
        event = CommunityEventFactory(community=True, video_url="")
        doc = event.ics_document(event.starts_at)
        assert "URL:" not in doc

    def it_matches_the_lines_used_by_the_combined_export(db):
        # The combined calendar export builds its CommunityEvent VEVENT from the same
        # ics_vevent_lines(), so the per-event .ics and the export never drift.
        event = CommunityEventFactory(community=True, title="Shared", location="Shop")
        lines = event.ics_vevent_lines(event.starts_at)
        assert lines[0] == "BEGIN:VEVENT"
        assert lines[-1] == "END:VEVENT"
        for line in lines:
            assert line in event.ics_document(event.starts_at)

    def it_starts_a_series_on_the_date_given_and_keeps_its_length(db):
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday
            ends_at=timezone.make_aware(datetime(2026, 7, 11, 20, 30)),
        )
        doc = event.ics_document(_aware(2026, 10, 10, 18))  # October's 2nd Saturday
        assert "DTSTART;TZID=America/Los_Angeles:20261010T180000" in doc
        assert "DTEND;TZID=America/Los_Angeles:20261010T203000" in doc
        assert f"RRULE:{event.ical_rrule()}" in doc

    def it_keeps_an_evening_series_on_its_own_weekday_in_every_calendar(db):
        # 6 PM on the first Wednesday is Thursday in UTC. Written in UTC, the first-Wednesday
        # rule put every later date on a Tuesday; read back the way a calendar app reads it,
        # each date must be a first Wednesday at 6 PM, through the November clock change.
        import icalendar
        import recurring_ical_events

        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=_aware(2026, 9, 2, 18),
            ends_at=_aware(2026, 9, 2, 20),
        )
        calendar = icalendar.Calendar.from_ical(event.ics_document(_aware(2026, 10, 7, 18)))
        starts = [
            timezone.localtime(occurrence["DTSTART"].dt)
            for occurrence in recurring_ical_events.of(calendar).between(date(2026, 10, 1), date(2027, 1, 31))
        ]
        assert [(s.date(), s.hour) for s in starts] == [
            (date(2026, 10, 7), 18),
            (date(2026, 11, 4), 18),
            (date(2026, 12, 2), 18),
            (date(2027, 1, 6), 18),
        ]


def describe_occurrence_start():
    def _monthly() -> CommunityEvent:
        # The 2nd Saturday of every month, from July.
        return CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=_aware(2026, 7, 11, 18),
            ends_at=_aware(2026, 7, 11, 20),
        )

    def it_is_the_start_of_the_date_named(db):
        assert _monthly().occurrence_start(date(2026, 10, 10)) == _aware(2026, 10, 10, 18)

    def it_is_the_next_date_when_none_is_named(db):
        event = _monthly()
        assert event.occurrence_start(None) == event.next_occurrence_start()

    def it_is_the_next_date_when_the_named_one_is_not_a_date_of_the_event(db):
        event = _monthly()
        assert event.occurrence_start(date(2026, 10, 11)) == event.next_occurrence_start()

    def it_is_a_one_offs_own_start_whatever_is_named(db):
        event = CommunityEventFactory(community=True)
        assert event.occurrence_start(None) == event.starts_at
        assert event.occurrence_start(timezone.localdate(event.starts_at)) == event.starts_at


def describe_public_url_on():
    def it_names_the_date_for_a_series(db, settings):
        settings.MEMBER_BASE_URL = "https://members.test"
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=_aware(2026, 9, 2, 21),  # Wednesday 9 PM: Thursday in UTC
            ends_at=_aware(2026, 9, 2, 22),
        )
        assert (
            event.public_url_on(_aware(2026, 10, 7, 21)) == f"https://members.test/events/{event.pk}/?date=2026-10-07"
        )

    def it_is_the_plain_page_for_a_one_off(db):
        event = CommunityEventFactory(community=True)
        assert event.public_url_on(event.starts_at) == event.public_url

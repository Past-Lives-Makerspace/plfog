"""BDD specs for the pure seed helpers in ``membership/calendar_seed.py`` — guild matching
(name / alias / ambiguous / none), event-type classification, RRULE→Recurrence mapping, and
the VEVENT walk. All DB-free / no network — fixture bytes and unsaved model instances only."""

from __future__ import annotations

from membership.calendar_seed import (
    AmbiguousMatch,
    classify_type,
    guild_for_title,
    parse_seed_vevents,
    recurrence_for_rrule,
)
from membership.models import CommunityEvent, Guild

CERAMICS = Guild(name="Ceramics Guild")
FOOD = Guild(name="Food Independence Guild")
GLASS = Guild(name="Glass Guild")
GUILDS = [CERAMICS, FOOD, GLASS]


def describe_guild_for_title():
    def it_matches_a_guild_by_name_substring():
        assert guild_for_title("Ceramics Guild Studio Hours", GUILDS) is CERAMICS

    def it_matches_a_guild_by_an_acronym_alias():
        assert guild_for_title("FIG Guild Meeting", GUILDS) is FOOD

    def it_does_not_match_an_alias_hidden_inside_another_word():
        # "fig" must be a whole word — "Configure Kiln" must not resolve to Food Independence.
        assert guild_for_title("Configure Kiln Settings", GUILDS) is None

    def it_returns_an_ambiguous_sentinel_when_two_guilds_match():
        result = guild_for_title("Ceramics Guild & Glass Guild Joint Meeting", GUILDS)
        assert isinstance(result, AmbiguousMatch)
        assert [guild.name for guild in result.guilds] == ["Ceramics Guild", "Glass Guild"]

    def it_returns_none_when_nothing_matches():
        assert guild_for_title("Random Potluck", GUILDS) is None


def describe_classify_type():
    def it_classifies_a_meeting():
        assert classify_type("Ceramics Guild Meeting") == CommunityEvent.EventType.GUILD_MEETING

    def it_classifies_studio_hours():
        assert classify_type("Glass Guild Studio Hours") == CommunityEvent.EventType.STUDIO_HOURS

    def it_prefers_meeting_over_studio_hours_when_both_appear():
        assert classify_type("Studio Hours + Meeting") == CommunityEvent.EventType.GUILD_MEETING

    def it_returns_none_for_an_unclassified_title():
        assert classify_type("Glass Guild Happy Hour") is None


def describe_recurrence_for_rrule():
    def it_maps_weekly():
        assert recurrence_for_rrule({"FREQ": ["WEEKLY"]}) == CommunityEvent.Recurrence.WEEKLY

    def it_maps_monthly_and_every_n_months_by_interval():
        assert recurrence_for_rrule({"FREQ": ["MONTHLY"]}) == CommunityEvent.Recurrence.MONTHLY
        assert recurrence_for_rrule({"FREQ": ["MONTHLY"], "INTERVAL": [2]}) == CommunityEvent.Recurrence.EVERY_2_MONTHS
        assert recurrence_for_rrule({"FREQ": ["MONTHLY"], "INTERVAL": [3]}) == CommunityEvent.Recurrence.EVERY_3_MONTHS

    def it_maps_yearly():
        assert recurrence_for_rrule({"FREQ": ["YEARLY"]}) == CommunityEvent.Recurrence.YEARLY

    def it_maps_no_rrule_to_none():
        assert recurrence_for_rrule(None) == CommunityEvent.Recurrence.NONE

    def it_maps_an_unmapped_freq_to_none():
        assert recurrence_for_rrule({"FREQ": ["DAILY"]}) == CommunityEvent.Recurrence.NONE


_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:hours@google.com
SUMMARY:Ceramics Guild Studio Hours
DTSTART:20260707T180000Z
DTEND:20260707T210000Z
LOCATION:Studio B
DESCRIPTION:Drop in.
RRULE:FREQ=WEEKLY;BYDAY=TU
END:VEVENT
BEGIN:VEVENT
UID:noend@google.com
SUMMARY:Malformed no DTEND
DTSTART:20260707T180000Z
END:VEVENT
END:VCALENDAR
"""


def describe_parse_seed_vevents():
    def it_reads_the_master_vevent_keeping_the_rrule():
        events = parse_seed_vevents(_ICS)
        assert len(events) == 1  # the DTEND-less VEVENT is skipped
        event = events[0]
        assert event.uid == "hours@google.com"
        assert event.summary == "Ceramics Guild Studio Hours"
        assert event.location == "Studio B"
        assert event.description == "Drop in."
        assert recurrence_for_rrule(event.rrule) == CommunityEvent.Recurrence.WEEKLY

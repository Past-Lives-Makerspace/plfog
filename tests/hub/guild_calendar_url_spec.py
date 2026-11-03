"""The guild form's calendar link must be an iCal feed (#1093).

Tech Guild saved a single event link, and the nightly sync failed on it every night.
"""

from __future__ import annotations

import pytest

from hub.forms import GuildEditForm
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_GOOGLE_FEED = (
    "https://calendar.google.com/calendar/ical/zq7guildfeed%40group.calendar.google.com/private-0a1b2c3d/basic.ics"
)


def _form(calendar_url: str) -> GuildEditForm:
    guild = GuildFactory()
    return GuildEditForm(data={"name": guild.name, "calendar_url": calendar_url}, instance=guild)


def describe_GuildEditForm_calendar_url():
    def it_accepts_a_google_secret_ical_address():
        form = _form(_GOOGLE_FEED)
        assert form.is_valid(), form.errors
        assert form.save().calendar_url == _GOOGLE_FEED

    def it_accepts_a_google_public_ical_address():
        form = _form("https://calendar.google.com/calendar/ical/zq7guildfeed%40gmail.com/public/basic.ics")
        assert form.is_valid(), form.errors

    def it_accepts_a_blank_link():
        form = _form("")
        assert form.is_valid(), form.errors
        assert form.save().calendar_url == ""

    @pytest.mark.parametrize(
        "url",
        [
            "https://calendar.google.com/calendar/event?eid=zq7eventid",
            "https://calendar.google.com/calendar/u/0?cid=zq7guildfeed",
            "https://calendar.google.com/calendar/embed?src=zq7guildfeed%40group.calendar.google.com",
            "https://calendar.google.com/calendar/ical/zq7guildfeed%40group.calendar.google.com/private-0a1b2c3d",
            "https://calendar.google.com/zq7guildfeed/basic.ics",
        ],
    )
    def it_refuses_a_google_link_that_is_not_the_ical_address(url):
        form = _form(url)
        assert not form.is_valid()
        assert "Secret address in iCal format" in form.errors["calendar_url"][0]

    @pytest.mark.parametrize(
        "url",
        [
            "https://zq7calendars.example.org/guild/feed.ics",
            "https://zq7calendars.example.org/guild/FEED.ICS",
            "https://zq7calendars.example.org/export?format=ical",
            "https://zq7calendars.example.org/ical/guild",
        ],
    )
    def it_accepts_another_hosts_feed_ending_in_ics_or_naming_ical(url):
        form = _form(url)
        assert form.is_valid(), form.errors

    def it_refuses_another_hosts_page_that_is_not_a_feed():
        form = _form("https://zq7calendars.example.org/guild/events")
        assert not form.is_valid()
        assert "calendar_url" in form.errors

    def it_words_the_refusal_without_dashes():
        form = _form("https://calendar.google.com/calendar/event?eid=zq7eventid")
        assert not form.is_valid()
        message = form.errors["calendar_url"][0]
        assert "-" not in message and "—" not in message and "–" not in message

"""BDD specs for the two thin #public-calendar command wrappers.

The service logic lives in ``hub.discord_calendar_posts`` (specced in
``tests/hub/discord_calendar_posts_spec.py``); these only pin the wrapper contract —
delegate, and report the count (or the no-op) on stdout.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def describe_post_weekly_calendar_digest():
    def it_reports_a_noop_when_nothing_posts():
        out = StringIO()
        with patch("hub.discord_calendar_posts.post_weekly_digest", return_value=0):
            call_command("post_weekly_calendar_digest", stdout=out)
        assert "No digest posted" in out.getvalue()

    def it_reports_the_item_count_when_the_digest_posts():
        out = StringIO()
        with patch("hub.discord_calendar_posts.post_weekly_digest", return_value=4):
            call_command("post_weekly_calendar_digest", stdout=out)
        assert "4 item(s)" in out.getvalue()


def describe_announce_calendar_events():
    def it_reports_a_noop_when_nothing_is_new():
        out = StringIO()
        with patch("hub.discord_calendar_posts.announce_new_events", return_value=0):
            call_command("announce_calendar_events", stdout=out)
        assert "No new events" in out.getvalue()

    def it_reports_the_posted_count():
        out = StringIO()
        with patch("hub.discord_calendar_posts.announce_new_events", return_value=2):
            call_command("announce_calendar_events", stdout=out)
        assert "Announced 2 new event(s)" in out.getvalue()

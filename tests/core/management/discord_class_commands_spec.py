"""BDD specs for the two thin #classes command wrappers.

The service logic lives in ``hub.discord_class_posts`` (specced in
``tests/hub/discord_class_posts_spec.py``); these only pin the wrapper contract —
delegate, and report the count (or the no-op) on stdout.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def describe_post_weekly_classes_digest():
    def it_reports_a_noop_when_nothing_posts():
        out = StringIO()
        with patch("hub.discord_class_posts.post_weekly_classes_digest", return_value=0):
            call_command("post_weekly_classes_digest", stdout=out)
        assert "No digest posted" in out.getvalue()

    def it_reports_the_embed_count_when_the_digest_posts():
        out = StringIO()
        with patch("hub.discord_class_posts.post_weekly_classes_digest", return_value=2):
            call_command("post_weekly_classes_digest", stdout=out)
        assert "2 embed(s)" in out.getvalue()


def describe_announce_new_classes():
    def it_reports_a_noop_when_nothing_is_new():
        out = StringIO()
        with patch("hub.discord_class_posts.announce_new_classes", return_value=0):
            call_command("announce_new_classes", stdout=out)
        assert "No new classes" in out.getvalue()

    def it_reports_the_posted_count():
        out = StringIO()
        with patch("hub.discord_class_posts.announce_new_classes", return_value=3):
            call_command("announce_new_classes", stdout=out)
        assert "Announced 3 new class(es)" in out.getvalue()
